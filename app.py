import html as html_lib
import time
import threading
import re
from xml.sax.saxutils import escape as xml_escape
import urllib.parse
import unicodedata
from datetime import datetime, timedelta
from flask import Flask, render_template, redirect, url_for, session, request, jsonify, flash
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import os
import csv
from functools import wraps
from flask import abort, request
import json
import uuid
import requests
import html
from flask import send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_migrate import Migrate
from flask_wtf import CSRFProtect
import razorpay
import hmac
import hashlib
import nimbus_api
import notifications
import io
import qrcode
from flask import send_file
import bleach
from sqlalchemy.orm import joinedload

# -------------------------
# 🔐 Load environment
# -------------------------
load_dotenv()

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# Admin accounts. Comma-separated ADMIN_EMAILS env var; defaults to the store owner so
# existing behaviour is unchanged if the variable is not set.
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv('ADMIN_EMAILS', 'heritage.spices.pvtltd@gmail.com').split(',') if e.strip()}

def is_admin_user(user):
    """True if the given session user dict belongs to an admin account."""
    return bool(user) and str(user.get('email', '')).strip().lower() in ADMIN_EMAILS

def slugify(text):
    """URL-safe ASCII slug: 'Garam Masala: The Secret!' -> 'garam-masala-the-secret'."""
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode('ascii')
    text = re.sub(r'[^a-zA-Z0-9]+', '-', text).strip('-').lower()
    return text or 'post'

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin_user(session.get('user')):
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

_BLOG_ALLOWED_TAGS = [
    'p', 'br', 'strong', 'b', 'em', 'i', 'u', 's',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'a', 'img', 'blockquote', 'code', 'pre',
    'span', 'div', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr'
]
_BLOG_ALLOWED_ATTRS = {
    'a': ['href', 'title', 'target', 'rel'],
    'img': ['src', 'alt', 'title', 'width', 'height'],
    '*': ['class']
}

def sanitize_blog_html(content):
    """Blog content is authored as raw HTML by the admin (plain textarea,
    no WYSIWYG) -- sanitize before saving so this can never become a stored
    XSS vector even if the admin account itself is ever compromised."""
    return bleach.clean(content, tags=_BLOG_ALLOWED_TAGS, attributes=_BLOG_ALLOWED_ATTRS, strip=True)





app = Flask(__name__, template_folder='templates')
_flask_secret_key = os.getenv("FLASK_SECRET_KEY")
if not _flask_secret_key:
    # Never fall back to a fixed, guessable string -- that would let anyone
    # forge a session cookie (e.g. claiming the admin email) and get full
    # admin access. A fresh random key per process start is still not
    # ideal (sessions won't survive a restart), but it closes the real
    # vulnerability. Set FLASK_SECRET_KEY in the environment for stable
    # sessions across deploys/restarts.
    import secrets as _secrets
    _flask_secret_key = _secrets.token_hex(32)
    print("WARNING: FLASK_SECRET_KEY is not set. Using a random one-time key "
          "for this process -- all sessions will be invalidated on restart. "
          "Set FLASK_SECRET_KEY in your environment for production.")
app.secret_key = _flask_secret_key
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Session cookie hardening. SECURE is gated on Render's own env var (set
# automatically in their runtime) so local dev over plain HTTP still works --
# a Secure cookie is silently dropped by browsers on non-HTTPS origins.
app.config['SESSION_COOKIE_SECURE'] = bool(os.getenv('RENDER'))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024  # 8MB request body cap

csrf = CSRFProtect(app)

# Static files (images, CSS, JS) can be cached by browsers for a week instead of being
# re-validated on every page view. Paid course material is excluded below.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = timedelta(days=7)

# gzip/brotli for HTML, JSON, CSS and JS. Optional: the app still runs if the package
# is not installed (e.g. an old environment), it just sends uncompressed responses.
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    print("Flask-Compress not installed -- responses will not be compressed.")

@app.after_request
def set_security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    if os.getenv('RENDER'):
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    # Paid notes must never be stored by a shared cache / CDN.
    if request.path.startswith('/api/notes'):
        resp.headers['Cache-Control'] = 'private, no-store'
    return resp

# --- SQLAlchemy Setup ---
db_url = os.getenv('DATABASE_URL')
if not db_url:
    db_url = "sqlite:///:memory:"
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
if db_url.startswith('postgres'):
    # Recycle connections before Supabase's idle timeout closes them. (pool_pre_ping is
    # deliberately NOT used: it adds a SELECT 1 round trip to every request, ~0.3 s here.)
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_recycle': 280}
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --- Blog Model ---
class Blog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    author = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.String(300))

# --- Subscriber Model ---
class Subscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)

# --- Contact Message Model ---
class ContactMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

# --- Wholesale Inquiry Model ---
class WholesaleInquiry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(200), nullable=False)
    business_name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

# --- Product QR Scan Model (anonymous analytics, no personal data collected) ---
class ProductScan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(200), nullable=False)
    ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

# --- Product Model ---
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.String(100), nullable=True)
    image_url = db.Column(db.String(300), nullable=True)
    category = db.Column(db.String(100), nullable=True)
    stock = db.Column(db.Integer, nullable=True)
    meesho_link = db.Column(db.String(300), nullable=True)
    reviews = db.relationship('ProductReview', backref='product', lazy=True, cascade='all, delete-orphan')

    @property
    def approved_reviews(self):
        return [r for r in self.reviews if r.status == 'approved']
        
    @property
    def avg_rating(self):
        approved = self.approved_reviews
        if not approved:
            return 0.0
        return round(sum(r.rating for r in approved) / len(approved), 1)

# --- Product Review Model ---
class ProductReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    user_name = db.Column(db.String(100), nullable=False)
    user_email = db.Column(db.String(200), nullable=False)
    rating = db.Column(db.Integer, nullable=False, default=5)
    review_text = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='pending') # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- User Model (replaces users.csv) ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    picture = db.Column(db.String(500))
    referral_code = db.Column(db.String(12), unique=True, nullable=True)
    referred_by_email = db.Column(db.String(200), nullable=True)

# --- Visit Model (replaces visits.csv) ---
class Visit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip = db.Column(db.String(50))
    user_agent = db.Column(db.Text)
    email = db.Column(db.String(200), default='Guest')

# --- Customer Detail Model (replaces customer_details.csv) ---
class CustomerDetail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    location = db.Column(db.String(200))
    interest = db.Column(db.String(200))

# --- Upper ID Model (Tuition Student Codes) ---
class UpperID(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    student_name = db.Column(db.String(200))
    has_used_digital_offer = db.Column(db.Boolean, default=False)
    has_used_physical_offer = db.Column(db.Boolean, default=False)

# --- Science Hub Order Model ---
class ScienceOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(200), nullable=False)
    product_type = db.Column(db.String(20))  # 'digital' or 'physical'
    amount = db.Column(db.Integer, nullable=False)  # in paise
    upper_id_used = db.Column(db.String(50), nullable=True)
    razorpay_order_id = db.Column(db.String(100))
    razorpay_payment_id = db.Column(db.String(100))
    payment_status = db.Column(db.String(20), default='pending')
    # Physical book delivery fields
    full_name = db.Column(db.String(200))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    pincode = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- Digital Access Model ---
class DigitalAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(200), nullable=False)
    access_token = db.Column(db.String(100), unique=True, nullable=False)
    granted_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- Site Settings (key-value store for toggles) ---
class SiteSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(500), nullable=False)

# --- Coupon (targeted, single-use, locked to one customer email) ---
class Coupon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    user_email = db.Column(db.String(200), nullable=False, index=True)
    discount_type = db.Column(db.String(10), nullable=False)  # 'percent' or 'flat'
    discount_value = db.Column(db.Float, nullable=False)
    max_discount = db.Column(db.Integer, nullable=True)  # optional cap in rupees, percent coupons only
    expires_at = db.Column(db.DateTime, nullable=True)
    used = db.Column(db.Boolean, default=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def is_valid_for(self, email):
        if self.used or self.user_email.lower() != email.lower():
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True

    def compute_discount(self, subtotal_rupees):
        if self.discount_type == 'percent':
            amount = subtotal_rupees * (self.discount_value / 100.0)
            if self.max_discount is not None:
                amount = min(amount, self.max_discount)
        else:
            amount = self.discount_value
        # Defense in depth: never let a bad discount_value/max_discount (e.g. a
        # negative number entered by mistake) turn into a discount that's
        # negative or exceeds the cart -- that would increase what's charged.
        return max(0, int(min(amount, subtotal_rupees)))

# --- Referral (A refers B; reward only granted after B's first paid order) ---
class Referral(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    referrer_email = db.Column(db.String(200), nullable=False)   # A
    referred_email = db.Column(db.String(200), nullable=False, unique=True)  # B -- one referrer per person
    status = db.Column(db.String(20), default='pending')  # pending | rewarded
    signup_at = db.Column(db.DateTime, server_default=db.func.now())
    rewarded_at = db.Column(db.DateTime, nullable=True)
    order_id = db.Column(db.Integer, db.ForeignKey('spice_order.id'), nullable=True)

# --- Points Ledger (balance is always the sum of a user's transactions) ---
class PointsTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(200), nullable=False, index=True)
    points = db.Column(db.Integer, nullable=False)  # positive = earned, negative = redeemed
    reason = db.Column(db.String(200), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('spice_order.id'), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

def get_points_balance(email):
    total = db.session.query(db.func.sum(PointsTransaction.points)).filter_by(user_email=email).scalar()
    return total or 0

# --- Cart Item ---
class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(200), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    product = db.relationship('Product', backref='cart_items')

# --- Spice Order ---
class SpiceOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), unique=True, nullable=False)
    user_email = db.Column(db.String(200), nullable=False, index=True)
    
    # Customer delivery details
    full_name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    address = db.Column(db.Text, nullable=False)
    city = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(100), nullable=False)
    pincode = db.Column(db.String(10), nullable=False)
    
    # Pricing (all in paise)
    subtotal = db.Column(db.Integer, nullable=False)
    shipping_cost = db.Column(db.Integer, default=0)
    total_amount = db.Column(db.Integer, nullable=False)
    coupon_id = db.Column(db.Integer, db.ForeignKey('coupon.id'), nullable=True)
    discount_amount = db.Column(db.Integer, default=0)
    points_redeemed = db.Column(db.Integer, default=0)
    points_discount_amount = db.Column(db.Integer, default=0)

    # Payment (Razorpay only, no COD)
    razorpay_order_id = db.Column(db.String(100), index=True)
    razorpay_payment_id = db.Column(db.String(100))
    payment_status = db.Column(db.String(20), default='pending')
    
    # Shipping (NimbusPost)
    nimbus_order_id = db.Column(db.String(100))
    awb_number = db.Column(db.String(100))
    courier_name = db.Column(db.String(100))
    shipping_status = db.Column(db.String(50), default='processing')
    estimated_delivery = db.Column(db.String(50))
    label_url = db.Column(db.String(500))
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('OrderItem', backref='order', lazy=True)

# --- Order Line Items ---
class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('spice_order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(200))
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Integer, nullable=False)  # in paise

# --- Blacklisted Pincodes (admin managed) ---
class BlacklistedPincode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pincode = db.Column(db.String(10), unique=True, nullable=False)
    city = db.Column(db.String(100))
    reason = db.Column(db.String(200))

# --- Site Settings Helpers ---
# Settings are read on almost every page (the global template context alone looks up four
# of them). Each database round trip from Render to Supabase costs ~0.3 s, so the whole
# table is cached in memory for a minute and refreshed with a single query.
_SETTINGS_TTL_SECONDS = 60
_settings_cache = {'data': None, 'loaded_at': 0.0}

def _load_settings():
    now = time.time()
    if _settings_cache['data'] is None or now - _settings_cache['loaded_at'] > _SETTINGS_TTL_SECONDS:
        _settings_cache['data'] = {row.key: row.value for row in SiteSetting.query.all()}
        _settings_cache['loaded_at'] = now
    return _settings_cache['data']

def get_setting(key, default=''):
    """Get a site setting value by key (cached for up to a minute)."""
    return _load_settings().get(key, default)

def set_setting(key, value):
    """Set a site setting value"""
    setting = SiteSetting.query.filter_by(key=key).first()
    if setting:
        setting.value = str(value)
    else:
        setting = SiteSetting(key=key, value=str(value))
        db.session.add(setting)
    db.session.commit()
    _settings_cache['data'] = None  # next read reloads, so admin changes show up immediately

# --- Customer messages: free e-mail + free WhatsApp click-to-chat links ---
def order_tracking_url(order):
    """Public courier tracking page when we have an AWB, otherwise our own tracking page."""
    if order.awb_number:
        return f"https://ship.nimbuspost.com/shipping/tracking/{order.awb_number}"
    return f"{notifications.site_url()}/orders/{order.id}/track"

def notify_customer(kind, order):
    """E-mail the customer: kind is 'confirmed', 'shipped' or 'delivered'. Never raises --
    a mail problem must not be able to affect a payment, shipment or webhook."""
    try:
        if kind == 'confirmed':
            subject, text, html = notifications.order_confirmed(order)
        elif kind == 'shipped':
            subject, text, html = notifications.order_shipped(order, order_tracking_url(order))
        elif kind == 'delivered':
            subject, text, html = notifications.order_delivered(order)
        else:
            return False
        return notifications.send_email(order.user_email, subject, text, html)
    except Exception as e:
        print(f"Customer notification ({kind}) failed:", e)
        return False

def _wa_number(phone):
    """Digits-only WhatsApp number with India's country code, or None if it doesn't look valid."""
    digits = ''.join(ch for ch in str(phone or '') if ch.isdigit())
    if len(digits) == 10:
        return '91' + digits
    if len(digits) == 12 and digits.startswith('91'):
        return digits
    if len(digits) == 11 and digits.startswith('0'):
        return '91' + digits[1:]
    return None

def wa_link(order):
    """A wa.me link that opens WhatsApp with a ready-written message to this order's customer.
    Free (no API): the admin just presses Send. Returns None if the phone number is unusable."""
    number = _wa_number(order.phone)
    if not number:
        return None
    name = (order.full_name or 'there').split()[0]
    status = (order.shipping_status or 'processing').lower()
    if status == 'delivered':
        msg = (f"Hello {name}, your Heritage Spices order {order.order_number} has been delivered. "
               f"We hope you enjoy it! If anything is not right, just reply here.")
    elif status in ('shipped', 'out for delivery', 'rto'):
        msg = (f"Hello {name}, your Heritage Spices order {order.order_number} has been shipped"
               f"{' via ' + order.courier_name if order.courier_name else ''}. "
               f"Tracking: {order_tracking_url(order)}")
        if order.awb_number:
            msg += f" (AWB {order.awb_number})"
    elif status == 'cancelled':
        msg = (f"Hello {name}, your Heritage Spices order {order.order_number} has been cancelled. "
               f"Any payment will be refunded to your original payment method within 5-7 business days.")
    else:
        msg = (f"Hello {name}, thank you for your Heritage Spices order {order.order_number} "
               f"(Rs {order.total_amount // 100}). We are packing it now and will share tracking details soon.")
    return f"https://wa.me/{number}?text={urllib.parse.quote(msg)}"

app.jinja_env.globals['wa_link'] = wa_link

def google_site_verification_tokens():
    """Tokens from GOOGLE_SITE_VERIFICATION (comma/space separated). Google gives one per product
    (Search Console, Merchant Center, ...) and all of them go in <meta name="google-site-verification">."""
    raw = os.getenv('GOOGLE_SITE_VERIFICATION', '')
    return [t for t in re.split(r'[,;\s]+', raw) if re.fullmatch(r'[A-Za-z0-9_-]{10,}', t)]

def store_whatsapp_link():
    """Link for the storefront 'WhatsApp us' button, from the WHATSAPP_NUMBER env var (None if unset)."""
    number = _wa_number(os.getenv('WHATSAPP_NUMBER', ''))
    if not number:
        return None
    text = urllib.parse.quote("Hi Heritage Spices, I have a question about your spices.")
    return f"https://wa.me/{number}?text={text}"

# --- Telegram Bot Notification ---
def send_telegram_notification(message):
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram notification error: {e}")

# --- DB Initialization Command ---
@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("Database initialized!")

# -------------------------
# 🌐 Google OAuth Config
# -------------------------
app.config['GOOGLE_CLIENT_ID'] = os.getenv("GOOGLE_CLIENT_ID")
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv("GOOGLE_CLIENT_SECRET")
app.config['GOOGLE_DISCOVERY_URL'] = "https://accounts.google.com/.well-known/openid-configuration"

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    server_metadata_url=app.config['GOOGLE_DISCOVERY_URL'],
    client_kwargs={
        'scope': 'openid email profile',
        'prompt': 'select_account'
    }
)

# -------------------------
# 💳 Razorpay Config
# -------------------------
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) if RAZORPAY_KEY_ID else None

# -------------------------
# 📦 Helper Functions
# -------------------------
def save_user(user_info):
    """Save user info to database. Returns True if this is a brand-new user."""
    existing = User.query.filter_by(email=user_info['email']).first()
    if not existing:
        new_user = User(
            name=user_info['name'],
            email=user_info['email'],
            picture=user_info.get('picture')
        )
        db.session.add(new_user)
        db.session.commit()
        return True
    return False

def describe_coupon(coupon, first_order=False):
    """Human-readable summary of a coupon's discount, e.g. '10% off your first order (up to Rs.100)'."""
    suffix = "your first order" if first_order else "your order"
    if coupon.discount_type == 'percent':
        desc = f"{int(coupon.discount_value)}% off {suffix}"
        if coupon.max_discount:
            desc += f" (up to ₹{coupon.max_discount})"
    else:
        desc = f"₹{int(coupon.discount_value)} off {suffix}"
    return desc

def issue_welcome_coupon(email):
    """One-time first-signup coupon. Discount type/value/cap/expiry are all
    admin-editable via Store Settings; returns None if disabled there."""
    if get_setting('welcome_coupon_enabled', 'true') != 'true':
        return None

    discount_type = get_setting('welcome_coupon_type', 'percent')
    discount_value = float(get_setting('welcome_coupon_value', '10') or 10)
    max_discount_raw = get_setting('welcome_coupon_max', '100')
    max_discount = int(max_discount_raw) if max_discount_raw else None
    expiry_days = int(get_setting('welcome_coupon_expiry_days', '30') or 30)

    prefix = 'WELCOME' if discount_type == 'percent' else 'WELCOMEFLAT'
    code = f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
    coupon = Coupon(
        code=code,
        user_email=email,
        discount_type=discount_type,
        discount_value=discount_value,
        max_discount=max_discount if discount_type == 'percent' else None,
        expires_at=(datetime.utcnow() + timedelta(days=expiry_days)) if expiry_days else None
    )
    db.session.add(coupon)
    db.session.commit()
    return code

# --- Refer & Earn helpers ---

# --- Simple in-process rate limiter, reused for a few different endpoints ---
# (coupon/points redemption keyed by user email, contact form keyed by IP).
# A caller could otherwise brute-force coupon codes or spam-flood a public
# form with unlimited attempts. In-memory is fine at this app's scale (single
# instance); resets on restart, which is an acceptable tradeoff for a small
# store rather than adding a new dependency/storage backend.
_redeem_attempts = {}
_REDEEM_MAX_ATTEMPTS = 10
_REDEEM_WINDOW_SECONDS = 300

def _redeem_rate_limited(key, max_attempts=None, window=None):
    max_attempts = max_attempts or _REDEEM_MAX_ATTEMPTS
    window = window or _REDEEM_WINDOW_SECONDS
    now = datetime.utcnow().timestamp()
    attempts = _redeem_attempts.setdefault(key, [])
    attempts[:] = [t for t in attempts if now - t < window]
    if len(attempts) >= max_attempts:
        return True
    attempts.append(now)
    return False

class SimplePagination:
    """Mimics Flask-SQLAlchemy's Pagination interface for a plain Python list --
    needed for admin_carts, which groups CartItem rows by customer in Python
    rather than via a single query, so .paginate() isn't available there."""
    def __init__(self, items_slice, page, per_page, total):
        self.items = items_slice
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = max(1, (total + per_page - 1) // per_page)
        self.has_prev = page > 1
        self.has_next = page < self.pages
        self.prev_num = page - 1
        self.next_num = page + 1

    def iter_pages(self, left_edge=1, right_edge=1, left_current=2, right_current=2):
        last = 0
        for num in range(1, self.pages + 1):
            if num <= left_edge or (self.page - left_current - 1 < num < self.page + right_current) or num > self.pages - right_edge:
                if last + 1 != num:
                    yield None
                yield num
                last = num

def get_or_create_referral_code(user_email):
    """Lazily generate a unique referral code for a user the first time it's needed."""
    import secrets, string
    user = User.query.filter_by(email=user_email).first()
    if not user:
        return None
    if user.referral_code:
        return user.referral_code
    for _ in range(10):
        code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        if not User.query.filter_by(referral_code=code).first():
            user.referral_code = code
            db.session.commit()
            return code
    return None  # exhausted retries -- astronomically unlikely at this scale

def issue_referral_reward_coupon(email):
    """Flat rs-off coupon for the referrer (A), issued once B's first order is paid."""
    if get_setting('referral_enabled', 'true') != 'true':
        return None
    amount = float(get_setting('referral_reward_amount', '50') or 50)
    expiry_days = int(get_setting('referral_reward_expiry_days', '30') or 30)
    code = f"REFER-{uuid.uuid4().hex[:6].upper()}"
    coupon = Coupon(
        code=code,
        user_email=email,
        discount_type='flat',
        discount_value=amount,
        expires_at=(datetime.utcnow() + timedelta(days=expiry_days)) if expiry_days else None
    )
    db.session.add(coupon)
    db.session.commit()
    return code

def award_referral_points(email, order_id):
    """Points credit for the referred person (B), issued once their first order is paid."""
    points = int(get_setting('referral_points_awarded', '50') or 50)
    db.session.add(PointsTransaction(user_email=email, points=points, reason='Referral bonus - first purchase', order_id=order_id))
    db.session.commit()
    return points

def _display_name(email):
    row = User.query.filter_by(email=email).first()
    return (row.name if row and row.name else (email or '').split('@')[0]) or 'a friend'

def mask_email(email):
    """'ashok.kumar@gmail.com' -> 'as***@gmail.com' (so a referrer doesn't see a friend's full address)."""
    local, _, domain = (email or '').partition('@')
    return f"{local[:2]}***@{domain}" if domain else (email or '')

app.jinja_env.filters['mask_email'] = mask_email

def notify_referral_joined(referrer_email, friend_email):
    """Someone signed up with a referral link: tell the referrer and the owner. Never raises."""
    try:
        referrer, friend = _display_name(referrer_email), _display_name(friend_email)
        amount = int(float(get_setting('referral_reward_amount', '50') or 50))
        subject, text, html = notifications.referral_joined(referrer, friend, amount)
        notifications.send_email(referrer_email, subject, text, html)
        send_telegram_notification(
            f"🤝 <b>New referral signup</b>\n{html_lib.escape(friend)} joined using "
            f"{html_lib.escape(referrer)}'s link (waiting on their first order)")
    except Exception as e:
        print("Referral signup notification failed:", e)

def notify_referral_rewarded(referrer_email, friend_email, coupon_code, points):
    """A referral was rewarded: tell the referrer (coupon), the friend (points) and the owner. Never raises."""
    try:
        referrer, friend = _display_name(referrer_email), _display_name(friend_email)
        amount = int(float(get_setting('referral_reward_amount', '50') or 50))
        expiry_days = int(get_setting('referral_reward_expiry_days', '30') or 30)
        if coupon_code:
            subject, text, html = notifications.referral_reward_for_referrer(referrer, friend, coupon_code, amount, expiry_days)
            notifications.send_email(referrer_email, subject, text, html)
        if points:
            subject, text, html = notifications.referral_points_for_friend(friend, points, get_points_balance(friend_email))
            notifications.send_email(friend_email, subject, text, html)
        send_telegram_notification(
            f"🎁 <b>Referral rewarded</b>\n{html_lib.escape(referrer)} gets coupon {html_lib.escape(coupon_code or '-')}; "
            f"{html_lib.escape(friend)} gets {points} points")
    except Exception as e:
        print("Referral reward notification failed:", e)

def process_referral_reward(referred_email, order_id):
    """Called right after an order is confirmed paid. Only rewards on the
    referred person's FIRST paid order, and only once per referral."""
    if get_setting('referral_enabled', 'true') != 'true':
        return
    referral = Referral.query.filter_by(referred_email=referred_email, status='pending').first()
    if not referral:
        return

    # Confirm this is genuinely their first paid order (excluding the one just paid)
    prior_paid = SpiceOrder.query.filter(
        SpiceOrder.user_email == referred_email,
        SpiceOrder.payment_status == 'paid',
        SpiceOrder.id != order_id
    ).count()
    if prior_paid > 0:
        return

    min_order_raw = get_setting('referral_min_order_value', '')
    if min_order_raw:
        order = SpiceOrder.query.get(order_id)
        if order and (order.total_amount / 100.0) < float(min_order_raw):
            return

    max_referrals_raw = get_setting('referral_max_per_referrer', '')
    if max_referrals_raw:
        already_rewarded = Referral.query.filter_by(referrer_email=referral.referrer_email, status='rewarded').count()
        if already_rewarded >= int(max_referrals_raw):
            referral.status = 'capped'
            db.session.commit()
            return

    coupon_code = issue_referral_reward_coupon(referral.referrer_email)
    points = award_referral_points(referred_email, order_id)
    referral.status = 'rewarded'
    referral.rewarded_at = datetime.utcnow()
    referral.order_id = order_id
    db.session.commit()
    notify_referral_rewarded(referral.referrer_email, referred_email, coupon_code, points)

def compute_points_redemption(requested_points, subtotal_rupees, balance):
    """Returns (points_to_actually_deduct, rupee_discount), clamped by the
    user's real balance and the admin-configured max-% of order cap."""
    points_per_rupee = float(get_setting('points_per_rupee', '10') or 10)
    max_percent = float(get_setting('max_points_redeem_percent', '50') or 50)
    if points_per_rupee <= 0 or requested_points <= 0:
        return 0, 0
    requested_points = max(0, min(int(requested_points), balance))
    max_rupees_cap = subtotal_rupees * (max_percent / 100.0)
    max_points_cap = int(max_rupees_cap * points_per_rupee)
    used_points = min(requested_points, max_points_cap)
    discount_rupees = int(used_points / points_per_rupee)
    return used_points, discount_rupees

_BOT_UA = re.compile(r'bot|crawl|spider|slurp|monitor|uptime|pingdom|curl|wget|python-requests|'
                     r'go-http-client|headless|preview|facebookexternalhit|lighthouse', re.I)

def _is_bot(user_agent):
    return not user_agent or bool(_BOT_UA.search(user_agent))

def track_visit(user=None):
    """Log a human visit. Skips crawlers, uptime monitors and HEAD checks (they inflate the
    visit count and each one costs two database round trips), and writes in a background
    thread so the visitor never waits for it."""
    ua = request.headers.get('User-Agent')
    if request.method == 'HEAD' or _is_bot(ua):
        return
    ip, email = request.remote_addr, (user['email'] if user else 'Guest')

    def _write():
        try:
            with app.app_context():
                db.session.add(Visit(timestamp=datetime.utcnow(), ip=ip, user_agent=ua, email=email))
                db.session.commit()
        except Exception as e:
            print("Visit logging failed:", e)
        finally:
            db.session.remove()

    if app.config.get('TESTING'):
        _write()
    else:
        threading.Thread(target=_write, daemon=True).start()

# -------------------------
# 🌐 Routes
# -------------------------

# ✅ Homepage
@app.route('/')
def index():
    user = session.get('user')
    is_logged_in = bool(user)
    track_visit(user)
    products = Product.query.limit(3).all()
    return render_template("index.html", user=user, is_logged_in=is_logged_in, products=products,
                           recent_blogs=get_recent_blogs(3),
                           referral_enabled=get_setting('referral_enabled', 'true') == 'true',
                           refer_reward_amount=get_setting('referral_reward_amount', '50'),
                           refer_points_awarded=get_setting('referral_points_awarded', '50'))

@app.before_request
def strip_trailing_slash():
    """/products/ and /blog/ used to be 404s (a lost link-equity trap); send them to the one real URL."""
    if request.method == 'GET' and len(request.path) > 1 and request.path.endswith('/'):
        target = request.path.rstrip('/') or '/'
        qs = request.query_string.decode()
        return redirect(target + ('?' + qs if qs else ''), 301)
    return None

@app.before_request
def capture_referral_code():
    ref = request.args.get('ref')
    if ref:
        session['ref_code'] = ref.strip().upper()[:12]

# ✅ Login via Google
@app.route('/login')
def login():
    redirect_uri = url_for('auth', _external=True)
    return google.authorize_redirect(redirect_uri)

def complete_login(user_info):
    """Log the visitor in and run the once-per-signup extras. Shared by the "Login with Google"
    button (/auth) and the Google One Tap popup (/auth/google-one-tap) so both behave identically:
    saves the user, links a referral, issues the welcome coupon."""
    session['user'] = {
        'name': user_info['name'],
        'email': user_info['email'],
        'picture': user_info.get('picture')
    }

    is_new_user = save_user(user_info)

    # Always clear any pending ?ref= code from the session on login,
    # whether or not it actually gets used below -- otherwise a stale
    # code (e.g. from an *existing* user like A clicking B's link, which
    # correctly grants nothing since A isn't a new signup) could sit in
    # a shared browser's session and wrongly attach to some other
    # person's later signup on that same device.
    ref_code = session.pop('ref_code', None)

    # Link this signup to whoever referred them, only for a genuinely
    # brand-new account. An existing user clicking someone else's link
    # and logging back in must never create a referral or reward either
    # side -- they're not a new signup. Only ever set once, at signup,
    # never overwritten afterward.
    if is_new_user and ref_code:
        referrer = User.query.filter_by(referral_code=ref_code).first()
        if referrer and referrer.email.lower() != user_info['email'].lower():
            new_user = User.query.filter_by(email=user_info['email']).first()
            new_user.referred_by_email = referrer.email
            db.session.add(Referral(referrer_email=referrer.email, referred_email=user_info['email']))
            db.session.commit()
            flash(f"You were referred by a friend! Complete your first order and you'll earn bonus points.", 'success')
            notify_referral_joined(referrer.email, user_info['email'])

    has_any_coupon = Coupon.query.filter_by(user_email=user_info['email']).first() is not None

    # Treat "never received a welcome coupon before" the same as "new" --
    # covers every existing account from before this feature existed,
    # not just brand-new sign-ups from now on. Only issues once per
    # person: after this, has_any_coupon will be True on future logins.
    if is_new_user or not has_any_coupon:
        code = issue_welcome_coupon(user_info['email'])
        if code:
            coupon = Coupon.query.filter_by(code=code).first()
            desc = describe_coupon(coupon, first_order=True)
            flash(f"Welcome! Here's {desc}: use code {code} at checkout. (You can find this anytime under \"My Coupons\".)", 'success')
    else:
        # Already has at least one coupon on record -- remind them if
        # any of their coupons is still valid and unused
        valid_coupon = next(
            (c for c in Coupon.query.filter_by(user_email=user_info['email'], used=False).all()
             if not c.expires_at or c.expires_at > datetime.utcnow()),
            None
        )
        if valid_coupon:
            desc = describe_coupon(valid_coupon)
            flash(f"You have a coupon code available! {desc}: use code {valid_coupon.code} at checkout.", 'success')


# ✅ OAuth Callback
@app.route('/auth')
def auth():
    try:
        token = google.authorize_access_token()
        user_info = google.parse_id_token(token, nonce=token.get('nonce'))
        complete_login(user_info)
        return redirect('/')
    except Exception as e:
        print("OAuth error:", e)
        return "OAuth Failed", 500


# ✅ Google One Tap: the small "Continue as <your Google account>" popup.
# Google gives the page a signed ID token (a JWT); we verify it against Google's public keys
# here on the server -- never trust the browser's claim about who the visitor is.
GOOGLE_ONE_TAP_ENABLED = os.getenv('GOOGLE_ONE_TAP_ENABLED', 'false').strip().lower() == 'true'
_ONE_TAP_SKIP_PATHS = ('/checkout', '/cart', '/admin', '/login', '/auth', '/orders', '/collect-details', '/logout')

def verify_google_credential(credential):
    """Return the verified Google profile for a One Tap credential, or raise ValueError."""
    if not credential or not app.config.get('GOOGLE_CLIENT_ID'):
        raise ValueError('missing credential or client id')
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
    info = id_token.verify_oauth2_token(credential, google_requests.Request(), app.config['GOOGLE_CLIENT_ID'])
    if info.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
        raise ValueError('bad issuer')
    if not info.get('email') or not info.get('email_verified'):
        raise ValueError('unverified e-mail')
    info.setdefault('name', info['email'].split('@')[0])
    return info

@app.route('/auth/google-one-tap', methods=['POST'])
def google_one_tap():
    if not GOOGLE_ONE_TAP_ENABLED:
        abort(404)
    if _redeem_rate_limited(f"onetap:{request.remote_addr}"):
        return jsonify({'success': False, 'error': 'Too many attempts. Please try again later.'}), 429
    credential = (request.get_json(silent=True) or {}).get('credential')
    try:
        user_info = verify_google_credential(credential)
    except Exception as e:
        print("One Tap verification failed:", e)
        return jsonify({'success': False, 'error': 'Sign-in could not be verified.'}), 401
    complete_login(user_info)
    return jsonify({'success': True})


# ✅ Collect Additional User Details
@app.route('/collect-details', methods=['GET', 'POST'])
def collect_details():
    if 'user' not in session:
        return jsonify({'error': 'Login required'}), 401

    email = session['user']['email']

    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form

        existing = CustomerDetail.query.filter_by(email=email).first()
        if not existing:
            new_detail = CustomerDetail(
                email=email,
                phone=data.get('phone'),
                location=data.get('location'),
                interest=data.get('interest')
            )
            db.session.add(new_detail)
            db.session.commit()

        return redirect('/')

    return render_template("collect_details.html", user=session['user'])


@app.route('/about')
def about():
    return render_template('about.html', user=session.get('user'), is_logged_in=bool(session.get('user')))

# --- Contact form spam defence -------------------------------------------------------------
# Live data showed one bot sending bursts of identical link-filled messages (50 of 61 stored
# messages) plus a "what's your price" template in many languages. Layers, cheapest first:
#   honeypot field -> signed "time trap" -> rate limit -> link filter -> duplicate filter ->
#   optional Cloudflare Turnstile. Rejected messages get the same friendly "thank you" so the
#   bot can't tell it was caught.
CONTACT_MIN_SECONDS = 4          # a human needs longer than this to fill the form
CONTACT_MAX_AGE_SECONDS = 86400  # a page left open for over a day is treated as stale
_LINK_RE = re.compile(r'https?://|www\.|\b[\w-]+\.(?:com|net|org|ru|xyz|info|ly|me|io)/', re.I)

def _form_serializer():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(app.secret_key, salt='contact-form')

def make_form_token():
    return _form_serializer().dumps(int(time.time()))

def form_token_age(token):
    """Seconds since the form was rendered, or None if the token is missing/forged/stale."""
    try:
        issued = _form_serializer().loads(token or '', max_age=CONTACT_MAX_AGE_SECONDS)
    except Exception:
        return None
    return time.time() - issued

def looks_like_spam(text):
    return bool(_LINK_RE.search(text or ''))

def turnstile_passed(response_token):
    """Cloudflare Turnstile check. Always True when Turnstile isn't configured."""
    secret = os.getenv('TURNSTILE_SECRET')
    if not secret:
        return True
    if not response_token:
        return False
    try:
        r = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify',
                          data={'secret': secret, 'response': response_token, 'remoteip': request.remote_addr},
                          timeout=8)
        return bool(r.json().get('success'))
    except Exception as e:
        print("Turnstile check failed:", e)
        return True  # don't lock real customers out if Cloudflare is unreachable

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        thanks = lambda: (flash('Thank you for contacting us! We will get back to you soon.', 'success'),
                          redirect('/contact'))[1]

        # 1) Honeypot: hidden field real visitors never fill.
        if request.form.get('website'):
            return thanks()

        # 2) Time trap: bots post instantly; the token proves when the form was rendered.
        age = form_token_age(request.form.get('form_ts'))
        if age is None or age < CONTACT_MIN_SECONDS:
            return thanks()

        # 3) Rate limit: 3 messages per hour per IP (counted even when rejected below).
        if _redeem_rate_limited(f"contact:{request.remote_addr}", max_attempts=3, window=3600):
            flash('Too many messages sent recently. Please try again later.', 'warning')
            return redirect('/contact')

        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip()
        message = (request.form.get('message') or '').strip()
        if not (name and email and message):
            return thanks()

        # 4) Links: genuine enquiries about spices rarely need one; nearly all spam does.
        if looks_like_spam(message) or looks_like_spam(name):
            return thanks()

        # 5) Duplicates: the same text (from any address) or the same sender+text in the last 24 h.
        since = datetime.utcnow() - timedelta(hours=24)
        if ContactMessage.query.filter(ContactMessage.timestamp >= since,
                                       db.func.lower(ContactMessage.message) == message.lower()).first():
            return thanks()

        # 6) Optional Cloudflare Turnstile (set TURNSTILE_SITE_KEY + TURNSTILE_SECRET).
        if not turnstile_passed(request.form.get('cf-turnstile-response')):
            flash('Please complete the security check and try again.', 'warning')
            return redirect('/contact')

        db.session.add(ContactMessage(name=name[:100], email=email[:200], message=message[:5000]))
        db.session.commit()
        send_telegram_notification(
            f"📩 <b>New contact message</b>\n<b>From:</b> {html_lib.escape(name)} ({html_lib.escape(email)})\n"
            f"{html_lib.escape(message[:300])}")
        return thanks()
    return render_template('contact.html', form_ts=make_form_token(),
                           turnstile_site_key=os.getenv('TURNSTILE_SITE_KEY', ''))

@app.route('/wholesale-inquiry', methods=['GET', 'POST'])
def wholesale_inquiry():
    user = session.get('user')
    if not user:
        flash('Please login to submit a wholesale inquiry.', 'warning')
        return redirect('/login')

    if request.method == 'POST':
        business_name = request.form.get('business_name', '').strip()
        phone = request.form.get('phone', '').strip()
        city = request.form.get('city', '').strip()
        message = request.form.get('message', '').strip()

        if not business_name or not phone or not city:
            flash('Business name, phone number, and city are required.', 'danger')
            return render_template('wholesale_form.html', user=user, is_logged_in=True,
                                    business_name=business_name, phone=phone, city=city, message=message)

        inquiry = WholesaleInquiry(
            user_email=user['email'],
            business_name=business_name,
            phone=phone,
            city=city,
            message=message or None
        )
        db.session.add(inquiry)
        db.session.commit()

        send_telegram_notification(
            f"🏢 <b>New Wholesale Inquiry</b>\n"
            f"Business: {business_name}\n"
            f"Phone: {phone}\n"
            f"City: {city}\n"
            f"Email: {user['email']}\n"
            f"Message: {message or '-'}"
        )

        flash('Thank you! Your wholesale inquiry has been submitted. Our team will reach out within 48 hours.', 'success')
        return redirect('/#wholesale')

    return render_template('wholesale_form.html', user=user, is_logged_in=True)

@app.route('/ads.txt')
def ads_txt():
    return send_from_directory('static', 'ads.txt')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static/images', 'favicon.png', mimetype='image/png')

@app.route('/healthz')
def healthz():
    """Uptime/health check for Render. Deliberately touches no database and logs no visit."""
    return 'ok', 200, {'Content-Type': 'text/plain', 'Cache-Control': 'no-store'}

@app.route('/robots.txt')
def robots():
    lines = [
        "User-agent: *",
        # private / transactional pages that should never appear in search results
        "Disallow: /admin/",
        "Disallow: /api/",
        "Disallow: /cart",
        "Disallow: /checkout",
        "Disallow: /orders",
        "Disallow: /login",
        "Disallow: /logout",
        "Disallow: /auth",
        "Disallow: /refer",
        "Disallow: /my-coupons",
        "Disallow: /collect-details",
        "Allow: /",
        "",
        "Sitemap: https://www.indianheritagespices.com/sitemap.xml",
        "",
    ]
    return "\n".join(lines), 200, {'Content-Type': 'text/plain'}


@app.route('/sitemap.xml')
def sitemap():
    base = "https://www.indianheritagespices.com"
    static_urls = ['/', '/about', '/contact', '/privacy', '/blog', '/products', '/faq', '/terms', '/refund']

    entries = [(u, None) for u in static_urls]
    entries += [(f"/product/{p.id}", None) for p in Product.query.order_by(Product.id).all()]
    for b in get_all_blogs():
        lastmod = b.date if re.fullmatch(r'\d{4}-\d{2}-\d{2}', b.date or '') else None
        entries.append((blog_path(b), lastmod))

    sitemap_xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap_xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for url, lastmod in entries:
        lm = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        sitemap_xml += f"  <url><loc>{xml_escape(base + url)}</loc>{lm}</url>\n"
    sitemap_xml += '</urlset>'
    return sitemap_xml, 200, {'Content-Type': 'application/xml'}


@app.route("/subscribe", methods=["POST"])
def subscribe():
    if _redeem_rate_limited(f"subscribe:{request.remote_addr}"):
        flash("Thanks for subscribing!", "success")  # don't tip off a bot that it's rate-limited
        return redirect("/")

    email = request.form.get("email")
    if email and not Subscriber.query.filter_by(email=email).first():
        new_sub = Subscriber(email=email)
        db.session.add(new_sub)
        db.session.commit()
    flash("Thanks for subscribing!", "success")
    return redirect("/")


BLOG_DRIVE_URL = "https://drive.google.com/uc?export=download&id=1SqjuYdwGnPIMbzMMtb5oMmUgrF8YXC_B"  # your new file

# --- Blog CRUD Helpers ---
def get_all_blogs():
    return Blog.query.order_by(Blog.id.desc()).all()

# Home page "from our journal" strip. Cached for 5 minutes: without this every homepage visit
# would pay another ~0.3 s database round trip just to list three posts.
_recent_blogs_cache = {'data': None, 'loaded_at': 0.0}

def get_recent_blogs(limit=3):
    now = time.time()
    if _recent_blogs_cache['data'] is None or now - _recent_blogs_cache['loaded_at'] > 300:
        cards = []
        for b in Blog.query.order_by(Blog.id.desc()).limit(limit).all():
            plain = re.sub(r'\s+', ' ', html_lib.unescape(re.sub(r'<[^>]+>', ' ', b.content or ''))).strip()
            cards.append({
                'title': b.title,
                'category': b.category,
                'image_url': b.image_url,
                'excerpt': (plain[:110].rsplit(' ', 1)[0] + '...') if len(plain) > 110 else plain,
                'url_slug': slugify(b.slug),
            })
        _recent_blogs_cache['data'] = cards
        _recent_blogs_cache['loaded_at'] = now
    return _recent_blogs_cache['data']

def get_blog_by_slug(slug):
    return Blog.query.filter_by(slug=slug).first()

def get_blog_by_id(blog_id):
    return Blog.query.filter_by(id=blog_id).first()

# --- Blog List Route ---
@app.route("/blog")
def blog():
    query = request.args.get("q", "").lower()
    category = request.args.get("category", "")
    user = session.get('user')
    is_logged_in = bool(user)

    blogs = get_all_blogs()
    filtered = [
        b for b in blogs
        if (query in b.title.lower() or query in b.content.lower())
        and (category == "" or b.category.lower() == category.lower())
    ]
    categories = sorted(set(b.category for b in blogs))

    admin_password = os.getenv("ADMIN_PASSWORD")
    return render_template(
        "blog_list.html",
        blogs=filtered,
        categories=categories,
        query=query,
        selected_category=category,
        user=user,
        is_logged_in=is_logged_in,
        admin_password=admin_password
    )

# --- Blog Detail Route ---
def blog_path(post):
    """Public URL path for a post. Older posts were saved with slugs containing ':' and curly
    quotes; the public URL is always the clean ASCII form of the slug (no database change)."""
    return "/blog/" + slugify(post.slug)

app.jinja_env.globals['blog_path'] = blog_path

@app.template_filter('demote_h1')
def demote_h1(html):
    """A page should have one H1 (the post title); headings inside the body start at H2."""
    return re.sub(r'<(/?)h1(?![0-9A-Za-z])', lambda m: '<' + m.group(1) + 'h2', html or '', flags=re.I)

@app.route("/blog/<slug>")
def blog_detail(slug):
    all_blogs = get_all_blogs()
    exact = next((b for b in all_blogs if b.slug == slug), None)
    if exact and slugify(exact.slug) != slug:
        # Old-style URL: permanently redirect to the clean one so search engines keep just one.
        return redirect(blog_path(exact), 301)
    post = exact or next((b for b in all_blogs if slugify(b.slug) == slug), None)
    if not post:
        return "Post not found", 404

    user = session.get('user')
    admin_password = os.getenv("ADMIN_PASSWORD")
    recent_posts = [b for b in all_blogs if b.id != post.id][:3]

    site = notifications.site_url()
    page_url = site + blog_path(post)
    description = _plain_text(post.content, 155)
    article = {
        '@context': 'https://schema.org', '@type': 'Article',
        'headline': post.title[:110], 'description': description,
        'author': {'@type': 'Person', 'name': post.author or 'Heritage Spices'},
        'publisher': {'@type': 'Organization', 'name': 'Heritage Spices',
                      'logo': {'@type': 'ImageObject', 'url': f"{site}/static/images/logo.png"}},
        'mainEntityOfPage': {'@type': 'WebPage', '@id': page_url},
    }
    if post.image_url:
        article['image'] = [post.image_url]
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', post.date or ''):
        article['datePublished'] = post.date
        article['dateModified'] = post.date
    breadcrumbs = {
        '@context': 'https://schema.org', '@type': 'BreadcrumbList',
        'itemListElement': [
            {'@type': 'ListItem', 'position': 1, 'name': 'Home', 'item': site + '/'},
            {'@type': 'ListItem', 'position': 2, 'name': 'Journal', 'item': site + '/blog'},
            {'@type': 'ListItem', 'position': 3, 'name': post.title[:110], 'item': page_url},
        ],
    }
    return render_template("blog_detail.html", post=post, recent_posts=recent_posts, user=user,
                           is_logged_in=bool(user), admin_password=admin_password, page_url=page_url,
                           description=description, json_ld=[article, breadcrumbs])

# --- Admin Add Blog Route ---
@app.route('/admin/blogs', methods=['GET', 'POST'])
def manage_blogs():
    if not is_admin_user(session.get('user')):
        abort(403)

    if request.method == 'POST':
        title = request.form['title']
        content = sanitize_blog_html(request.form['content'])
        category = request.form.get('category', 'General')
        slug = slugify(title)
        # Handle duplicate slug collision
        existing = Blog.query.filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"
            
        author = session.get('user', {}).get('name', 'Admin')
        date = datetime.now().strftime("%Y-%m-%d")

        # Handle image upload to imgbb
        image_url = None
        image = request.files.get('image')
        if image and image.filename:
            import base64
            imgbb_api_key = os.getenv('IMGBB_API_KEY')
            image_data = base64.b64encode(image.read()).decode('utf-8')
            payload = {
                'key': imgbb_api_key,
                'image': image_data
            }
            response = requests.post('https://api.imgbb.com/1/upload', data=payload)
            if response.status_code == 200:
                image_url = response.json()['data']['url']

        new_blog = Blog(
            title=title,
            slug=slug,
            author=author,
            category=category,
            date=date,
            content=content,
            image_url=image_url
        )
        db.session.add(new_blog)
        db.session.commit()
        return redirect('/admin/blogs')

    blogs = get_all_blogs()
    admin_password = os.getenv("ADMIN_PASSWORD")
    return render_template("admin_blog.html", blogs=blogs, user=session.get('user'), admin_password=admin_password)

# --- Admin Delete Blog Route ---
@app.route('/admin/blogs/delete/<int:id>', methods=['POST'])
def delete_blog(id):
    if not is_admin_user(session.get('user')):
        abort(403)
    password = request.form.get('password')
    if not ADMIN_PASSWORD or password != ADMIN_PASSWORD:
        abort(403)
    blog = get_blog_by_id(id)
    if blog:
        db.session.delete(blog)
        db.session.commit()
    return redirect('/blog')

# --- Admin Delete Product Route ---
@app.route('/admin/products/delete/<int:id>', methods=['POST'])
def delete_product(id):
    if not is_admin_user(session.get('user')):
        abort(403)
    product = Product.query.get_or_404(id)
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted!', 'success')
    return redirect('/products')


@app.route('/admin/blogs/edit/<int:blog_id>', methods=['GET', 'POST'])
def edit_blog(blog_id):
    user = session.get('user')
    if not is_admin_user(user):
        abort(403)
    blog = Blog.query.get_or_404(blog_id)
    if request.method == 'POST':
        blog.title = request.form['title']
        blog.content = sanitize_blog_html(request.form['content'])
        # Handle image upload to imgbb
        if 'image' in request.files and request.files['image'].filename:
            image = request.files['image']
            import base64
            imgbb_api_key = os.getenv('IMGBB_API_KEY')
            image_data = base64.b64encode(image.read()).decode('utf-8')
            payload = {
                'key': imgbb_api_key,
                'image': image_data
            }
            response = requests.post('https://api.imgbb.com/1/upload', data=payload)
            if response.status_code == 200:
                blog.image_url = response.json()['data']['url']
        db.session.commit()
        flash('Blog updated successfully!')
        return redirect(url_for('blog_detail', slug=blog.slug))
    return render_template('admin_blog_edit.html', blog=blog)


# ✅ Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ✅ Admin: View All Users
@app.route('/admin/users')
@admin_required
def view_users():
    users = User.query.all()
    return jsonify([{'name': u.name, 'email': u.email, 'picture': u.picture} for u in users])

# ✅ Admin: View All Visits
@app.route('/admin/visits')
@admin_required
def view_visits():
    visits = Visit.query.order_by(Visit.id.desc()).limit(500).all()
    return jsonify([{
        'timestamp': v.timestamp.isoformat() if v.timestamp else None,
        'ip': v.ip,
        'user_agent': v.user_agent,
        'email': v.email
    } for v in visits])

@app.route('/test-telegram')
@admin_required
def test_telegram():
    try:
        msg = "👋 <b>TEST MESSAGE!</b>\n\nIf you are reading this on your phone, your Heritage Spices Telegram integration is working perfectly! 🚀"
        send_telegram_notification(msg)
        return "Test message sent! Check your Telegram app. (If it didn't arrive, double-check that your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are correct in Render)."
    except Exception as e:
        return f"Error: {e}"

# ✅ Admin: Unified Dashboard
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    days = request.args.get('days', '30')
    start_date = None
    
    if days != 'all':
        try:
            start_date = datetime.utcnow() - timedelta(days=int(days))
        except ValueError:
            start_date = datetime.utcnow() - timedelta(days=30)
            
    # Base queries
    visit_q = Visit.query
    order_q = SpiceOrder.query
    
    if start_date:
        visit_q = visit_q.filter(Visit.timestamp >= start_date)
        order_q = order_q.filter(SpiceOrder.created_at >= start_date)
        
    total_users = User.query.count() # Users don't have created_at, showing all-time
    total_visits = visit_q.count()
    total_products = Product.query.count()
    total_blogs = Blog.query.count()
    total_messages = ContactMessage.query.count()
    total_wholesale_inquiries = WholesaleInquiry.query.count()

    total_spice_orders = order_q.count()
    total_revenue = (order_q.filter_by(payment_status='paid')
                      .with_entities(db.func.sum(SpiceOrder.total_amount)).scalar() or 0) // 100
    pending_shipments = SpiceOrder.query.filter_by(payment_status='paid', shipping_status='processing').count()

    low_stock_products = Product.query.filter(Product.stock != None, Product.stock <= 5).order_by(Product.stock.asc()).all()

    # Prepare Chart Data (Group by date)
    chart_labels = []
    chart_revenue = []
    chart_orders = []
    chart_visits = []

    if days != 'all' and int(days) <= 90:
        num_days = int(days)
        # Generate last N days
        date_list = [(datetime.utcnow() - timedelta(days=x)).date() for x in range(num_days-1, -1, -1)]
        chart_start = datetime.combine(date_list[0], datetime.min.time())

        # Grouped aggregate queries instead of 3 queries per day
        revenue_by_date = dict(
            db.session.query(db.func.date(SpiceOrder.created_at), db.func.sum(SpiceOrder.total_amount))
            .filter(SpiceOrder.created_at >= chart_start, SpiceOrder.payment_status == 'paid')
            .group_by(db.func.date(SpiceOrder.created_at)).all()
        )
        orders_by_date = dict(
            db.session.query(db.func.date(SpiceOrder.created_at), db.func.count(SpiceOrder.id))
            .filter(SpiceOrder.created_at >= chart_start)
            .group_by(db.func.date(SpiceOrder.created_at)).all()
        )
        visits_by_date = dict(
            db.session.query(db.func.date(Visit.timestamp), db.func.count(Visit.id))
            .filter(Visit.timestamp >= chart_start)
            .group_by(db.func.date(Visit.timestamp)).all()
        )

        for d in date_list:
            chart_labels.append(d.strftime('%b %d'))
            chart_revenue.append((revenue_by_date.get(d) or 0) // 100)
            chart_orders.append(orders_by_date.get(d) or 0)
            chart_visits.append(visits_by_date.get(d) or 0)
            
    # Recent activity feed (latest orders, wholesale inquiries, and messages combined)
    recent_activity = []
    for o in SpiceOrder.query.order_by(SpiceOrder.created_at.desc()).limit(5).all():
        recent_activity.append({
            'icon': 'fa-shopping-bag', 'color': 'dark',
            'title': f"New order {o.order_number}",
            'subtitle': f"{o.full_name} · ₹{o.total_amount // 100}",
            'timestamp': o.created_at,
            'link': '/admin/orders'
        })
    for i in WholesaleInquiry.query.order_by(WholesaleInquiry.timestamp.desc()).limit(5).all():
        recent_activity.append({
            'icon': 'fa-store', 'color': 'success',
            'title': f"Wholesale inquiry: {i.business_name}",
            'subtitle': f"{i.city} · {i.phone}",
            'timestamp': i.timestamp,
            'link': '/admin/wholesale-inquiries'
        })
    for m in ContactMessage.query.order_by(ContactMessage.timestamp.desc()).limit(5).all():
        recent_activity.append({
            'icon': 'fa-envelope', 'color': 'primary',
            'title': f"Message from {m.name}",
            'subtitle': (m.message[:60] + '...') if len(m.message) > 60 else m.message,
            'timestamp': m.timestamp,
            'link': '/admin/messages'
        })
    recent_activity.sort(key=lambda x: x['timestamp'], reverse=True)
    recent_activity = recent_activity[:8]

    return render_template('admin_dashboard.html',
                           total_users=total_users,
                           total_visits=total_visits,
                           total_products=total_products,
                           total_blogs=total_blogs,
                           total_messages=total_messages,
                           total_wholesale_inquiries=total_wholesale_inquiries,
                           recent_activity=recent_activity,
                           total_spice_orders=total_spice_orders,
                           total_revenue=total_revenue,
                           pending_shipments=pending_shipments,
                           low_stock_products=low_stock_products,
                           current_filter=days,
                           chart_labels=chart_labels,
                           chart_revenue=chart_revenue,
                           chart_orders=chart_orders,
                           chart_visits=chart_visits)

import csv
import io
from flask import Response

@app.route('/admin/export/<data_type>')
@admin_required
def export_data(data_type):
    days = request.args.get('days', '30')
    start_date = None
    if days != 'all':
        try:
            start_date = datetime.utcnow() - timedelta(days=int(days))
        except ValueError:
            start_date = datetime.utcnow() - timedelta(days=30)
            
    def get_filtered(query, date_field):
        if start_date:
            return query.filter(date_field >= start_date).all()
        return query.all()

    def csv_safe(value):
        # Prevent CSV/formula injection: neutralize leading =, +, -, @ which
        # spreadsheet apps can interpret as formulas when the file is opened.
        s = '' if value is None else str(value)
        if s and s[0] in ('=', '+', '-', '@'):
            return "'" + s
        return s

    output = io.StringIO()
    writer = csv.writer(output)

    if data_type == 'orders':
        orders = get_filtered(SpiceOrder.query, SpiceOrder.created_at)
        writer.writerow(['Order ID', 'Date', 'Email', 'Customer Name', 'Phone', 'City', 'Pincode', 'Amount (INR)', 'Payment Status', 'Shipping Status'])
        for o in orders:
            writer.writerow([csv_safe(o.order_number), o.created_at.strftime('%Y-%m-%d %H:%M'), csv_safe(o.user_email), csv_safe(o.full_name), csv_safe(o.phone), csv_safe(o.city), csv_safe(o.pincode), o.total_amount//100, o.payment_status, o.shipping_status])
        filename = "spice_orders_export.csv"

    elif data_type == 'users':
        users = User.query.all()
        writer.writerow(['Name', 'Email'])
        for u in users:
            writer.writerow([csv_safe(u.name), csv_safe(u.email)])
        filename = "users_export.csv"

    elif data_type == 'visits':
        visits = get_filtered(Visit.query.order_by(Visit.timestamp.desc()).limit(10000), Visit.timestamp)
        writer.writerow(['Date', 'IP', 'User Agent', 'Email'])
        for v in visits:
            writer.writerow([v.timestamp.strftime('%Y-%m-%d %H:%M') if v.timestamp else '', csv_safe(v.ip), csv_safe(v.user_agent), csv_safe(v.email)])
        filename = "visits_export.csv"
        
    else:
        abort(404)
        
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={filename}"}
    )

# ✅ Admin: View Contact Messages
@app.route('/admin/messages')
def admin_messages():
    if not is_admin_user(session.get('user')):
        abort(403)
    
    page = request.args.get('page', 1, type=int)
    pagination = ContactMessage.query.order_by(ContactMessage.timestamp.desc()).paginate(page=page, per_page=20, error_out=False)
    seen = {}
    for m in pagination.items:
        seen[(m.email, m.message)] = seen.get((m.email, m.message), 0) + 1
    # Hint for the "Select spam-looking" button: contains a link, or repeated on this page.
    spam_ids = [m.id for m in pagination.items if looks_like_spam(m.message) or seen[(m.email, m.message)] > 1]
    return render_template('admin_messages.html', messages=pagination.items, pagination=pagination,
                           spam_ids=spam_ids)

# ✅ Admin: Delete several contact messages at once (admin ticks them first)
@app.route('/admin/messages/bulk-delete', methods=['POST'])
@admin_required
def bulk_delete_messages():
    ids = [int(i) for i in request.form.getlist('ids') if i.isdigit()]
    if not ids:
        flash('No messages were selected.', 'warning')
        return redirect('/admin/messages')
    deleted = ContactMessage.query.filter(ContactMessage.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    flash(f'Deleted {deleted} message{"s" if deleted != 1 else ""}.', 'success')
    return redirect('/admin/messages')

# ✅ Admin: Delete Contact Message
@app.route('/admin/messages/delete/<int:id>', methods=['POST'])
def delete_message(id):
    if not is_admin_user(session.get('user')):
        abort(403)
    
    msg = ContactMessage.query.get_or_404(id)
    db.session.delete(msg)
    db.session.commit()
    flash('Message deleted successfully.', 'success')
    return redirect('/admin/messages')

# ✅ Admin: View Wholesale Inquiries
@app.route('/admin/wholesale-inquiries')
def admin_wholesale_inquiries():
    if not is_admin_user(session.get('user')):
        abort(403)

    page = request.args.get('page', 1, type=int)
    pagination = WholesaleInquiry.query.order_by(WholesaleInquiry.timestamp.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('admin_wholesale.html', inquiries=pagination.items, pagination=pagination)

# ✅ Admin: Delete Wholesale Inquiry
@app.route('/admin/wholesale-inquiries/delete/<int:id>', methods=['POST'])
def delete_wholesale_inquiry(id):
    if not is_admin_user(session.get('user')):
        abort(403)

    inquiry = WholesaleInquiry.query.get_or_404(id)
    db.session.delete(inquiry)
    db.session.commit()
    flash('Wholesale inquiry deleted successfully.', 'success')
    return redirect('/admin/wholesale-inquiries')

# ✅ Admin: Who has items in their cart right now (for cart-abandoner outreach)
@app.route('/admin/carts')
def admin_carts():
    if not is_admin_user(session.get('user')):
        abort(403)

    items = CartItem.query.options(joinedload(CartItem.product)).order_by(CartItem.added_at.desc()).all()
    carts_by_email = {}
    for item in items:
        if not item.product:
            continue
        entry = carts_by_email.setdefault(item.user_email, {'cart_items': [], 'subtotal': 0, 'latest': item.added_at})
        entry['cart_items'].append(item)
        entry['subtotal'] += int(float(item.product.price)) * item.quantity
        if item.added_at and (not entry['latest'] or item.added_at > entry['latest']):
            entry['latest'] = item.added_at

    # Sort by most recently active cart first
    carts = sorted(carts_by_email.items(), key=lambda kv: kv[1]['latest'] or datetime.min, reverse=True)

    page = request.args.get('page', 1, type=int)
    per_page = 20
    start = (page - 1) * per_page
    pagination = SimplePagination(carts[start:start + per_page], page, per_page, len(carts))
    return render_template('admin_carts.html', carts=pagination.items, pagination=pagination)

# ✅ Admin: Coupon management
@app.route('/admin/coupons')
def admin_coupons():
    if not is_admin_user(session.get('user')):
        abort(403)

    page = request.args.get('page', 1, type=int)
    pagination = Coupon.query.order_by(Coupon.created_at.desc()).paginate(page=page, per_page=25, error_out=False)

    total_coupons = Coupon.query.count()
    welcome_issued = Coupon.query.filter(Coupon.code.startswith('WELCOME')).count()
    welcome_used = Coupon.query.filter(Coupon.code.startswith('WELCOME'), Coupon.used == True).count()
    return render_template('admin_coupons.html', coupons=pagination.items, pagination=pagination, now=datetime.utcnow(),
                           total_coupons=total_coupons, welcome_issued=welcome_issued, welcome_used=welcome_used)

@app.route('/admin/points')
@admin_required
def admin_points_lookup():
    email = (request.args.get('email') or '').strip()
    if not email:
        return redirect('/admin/referrals')
    return redirect('/admin/points/' + urllib.parse.quote(email, safe='@'))

@app.route('/admin/points/<email>')
@admin_required
def admin_points_detail(email):
    row = User.query.filter(db.func.lower(User.email) == email.lower()).first()
    if not row:
        flash(f"No customer found with the e-mail {email}.", 'warning')
        return redirect('/admin/referrals')
    ledger = PointsTransaction.query.filter_by(user_email=row.email).order_by(PointsTransaction.id.desc()).limit(100).all()
    return render_template(
        'admin_points.html', customer=row, balance=get_points_balance(row.email), ledger=ledger,
        referred_by=Referral.query.filter_by(referred_email=row.email).first(),
        referred_people=Referral.query.filter_by(referrer_email=row.email).order_by(Referral.signup_at.desc()).all(),
        points_per_rupee=float(get_setting('points_per_rupee', '10') or 10))

@app.route('/admin/points/<email>/adjust', methods=['POST'])
@admin_required
def admin_points_adjust(email):
    row = User.query.filter(db.func.lower(User.email) == email.lower()).first_or_404()
    back = '/admin/points/' + urllib.parse.quote(row.email, safe='@')
    try:
        points = int(request.form.get('points', '0'))
    except ValueError:
        points = 0
    reason = (request.form.get('reason') or '').strip()[:150]
    if points == 0 or abs(points) > 100000 or not reason:
        flash('Enter a points amount (not 0) and a reason.', 'danger')
        return redirect(back)
    balance = get_points_balance(row.email)
    if points < 0 and balance + points < 0:
        flash(f"Cannot remove {abs(points)} points: this customer only has {balance}.", 'danger')
        return redirect(back)
    db.session.add(PointsTransaction(user_email=row.email, points=points, reason=f"Admin adjustment: {reason}"))
    db.session.commit()
    flash(f"{'Added' if points > 0 else 'Removed'} {abs(points)} points. New balance: {get_points_balance(row.email)}.", 'success')
    return redirect(back)

@app.route('/admin/referrals')
def admin_referrals():
    if not is_admin_user(session.get('user')):
        abort(403)

    page = request.args.get('page', 1, type=int)
    pagination = Referral.query.order_by(Referral.signup_at.desc()).paginate(page=page, per_page=25, error_out=False)

    total_referrals = Referral.query.count()
    rewarded = Referral.query.filter_by(status='rewarded').count()
    total_points_outstanding = db.session.query(db.func.sum(PointsTransaction.points)).scalar() or 0

    # Per-customer balances -- who currently has how many points
    balances = (
        db.session.query(PointsTransaction.user_email, db.func.sum(PointsTransaction.points).label('balance'))
        .group_by(PointsTransaction.user_email)
        .having(db.func.sum(PointsTransaction.points) > 0)
        .order_by(db.func.sum(PointsTransaction.points).desc())
        .all()
    )

    names = {u.email: u.name for u in User.query.filter(User.email.in_([b[0] for b in balances])).all()} if balances else {}
    return render_template('admin_referrals.html', referrals=pagination.items, pagination=pagination, total_referrals=total_referrals,
                           rewarded=rewarded, total_points_outstanding=total_points_outstanding, balances=balances, names=names)

@app.route('/admin/coupons/create', methods=['GET', 'POST'])
def admin_create_coupon():
    if not is_admin_user(session.get('user')):
        abort(403)

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        discount_type = request.form.get('discount_type')
        discount_value = request.form.get('discount_value', type=float)
        max_discount = request.form.get('max_discount', type=int)
        expires_days = request.form.get('expires_days', type=int)
        custom_code = request.form.get('code', '').strip().upper()

        if not email or discount_type not in ('percent', 'flat') or not discount_value or discount_value <= 0:
            flash('Please fill in a valid email, discount type, and discount value.', 'danger')
            return redirect('/admin/coupons/create')

        if max_discount is not None and max_discount <= 0:
            flash('Max discount must be a positive amount, or left blank for no cap.', 'danger')
            return redirect('/admin/coupons/create')

        code = custom_code or f"SAVE-{uuid.uuid4().hex[:6].upper()}"
        if Coupon.query.filter_by(code=code).first():
            flash('That coupon code already exists. Try a different one.', 'danger')
            return redirect('/admin/coupons/create')

        coupon = Coupon(
            code=code,
            user_email=email,
            discount_type=discount_type,
            discount_value=discount_value,
            max_discount=max_discount if discount_type == 'percent' else None,
            expires_at=(datetime.utcnow() + timedelta(days=expires_days)) if expires_days else None
        )
        db.session.add(coupon)
        db.session.commit()
        flash(f'Coupon {code} created for {email}.', 'success')
        return redirect('/admin/coupons')

    prefill_email = request.args.get('email', '')
    return render_template('admin_coupon_create.html', prefill_email=prefill_email)

@app.route('/admin/coupons/delete/<int:id>', methods=['POST'])
def delete_coupon(id):
    if not is_admin_user(session.get('user')):
        abort(403)

    coupon = Coupon.query.get_or_404(id)
    if coupon.used:
        flash('This coupon has already been used on an order and is kept for record-keeping -- it cannot be deleted.', 'warning')
        return redirect('/admin/coupons')
    db.session.delete(coupon)
    db.session.commit()
    flash('Coupon deleted.', 'success')
    return redirect('/admin/coupons')

# ✅ Frontend Analytics
# ✅ Privacy Policy
@app.route('/privacy')
def privacy():
    return render_template("privacy.html", now=datetime.now())

# ✅ Inject global template variables
@app.context_processor
def inject_globals():
    user = session.get('user')
    welcome_offer_text = None
    if not user and get_setting('welcome_coupon_enabled', 'true') == 'true':
        w_type = get_setting('welcome_coupon_type', 'percent')
        w_value = get_setting('welcome_coupon_value', '10')
        w_max = get_setting('welcome_coupon_max', '')
        try:
            if w_type == 'percent':
                welcome_offer_text = f"{int(float(w_value))}% off"
                if w_max:
                    welcome_offer_text += f" (up to ₹{int(float(w_max))})"
            else:
                welcome_offer_text = f"₹{int(float(w_value))} off"
        except (TypeError, ValueError):
            welcome_offer_text = None

    nav_points_balance = get_points_balance(user['email']) if user else 0

    return {
        'year': datetime.now().year,
        'is_logged_in': bool(user),
        'user': user,
        'welcome_offer_text': welcome_offer_text,
        'nav_points_balance': nav_points_balance
    }


# NOTE: The duplicate home() route and dead product_detail() route with
# hardcoded data have been removed. The index() function at '/' handles
# the homepage, and /products handles the product listing.

# --- Products Page Route ---
@app.route('/products')
def products():
    products = Product.query.options(joinedload(Product.reviews)).all()
    user = session.get('user')
    site = notifications.site_url()
    json_ld = {'@context': 'https://schema.org', '@type': 'ItemList', 'itemListElement': [
        {'@type': 'ListItem', 'position': i, 'url': f"{site}/product/{p.id}", 'name': p.name}
        for i, p in enumerate(products, 1)]}
    return render_template('products.html', products=products, user=user, json_ld=json_ld)

# --- Product detail page ---
def product_base_name(name):
    """'Garam Masala - 50g' -> 'Garam Masala' (sizes of one product share the part before ' - ')."""
    return name.rsplit(' - ', 1)[0].strip() if ' - ' in (name or '') else (name or '')

def product_grams(name):
    """Pack size in grams parsed from a name like 'Garam Masala - 100g' (None if there isn't one)."""
    m = re.search(r'(\d+(?:\.\d+)?)\s*(kg|g)\b', name or '', re.I)
    if not m:
        return None
    return float(m.group(1)) * (1000 if m.group(2).lower() == 'kg' else 1)

def product_price_rupees(product):
    try:
        return int(float(product.price))
    except (TypeError, ValueError):
        return None

def _plain_text(html_text, limit=None):
    plain = re.sub(r'\s+', ' ', html_lib.unescape(re.sub(r'<[^>]+>', ' ', html_text or ''))).strip()
    if limit and len(plain) > limit:
        plain = plain[:limit].rsplit(' ', 1)[0] + '...'
    return plain

@app.route('/product/<int:product_id>')
def product_detail(product_id):
    product = Product.query.options(joinedload(Product.reviews)).filter_by(id=product_id).first_or_404()

    base = product_base_name(product.name)
    sizes = [product]
    if base != product.name:
        like = base.replace('%', '').replace('_', '') + ' - %'
        sizes += Product.query.filter(Product.name.like(like), Product.id != product.id).all()
    sizes.sort(key=lambda p: (product_grams(p.name) or 0, p.id))

    price = product_price_rupees(product)
    grams = product_grams(product.name)
    per_10g = round(price / grams * 10, 1) if price and grams else None

    out_of_stock = product.stock is not None and product.stock <= 0
    low_stock = product.stock is not None and 0 < product.stock <= 5

    reviews = product.approved_reviews
    site = notifications.site_url()
    url = f"{site}/product/{product.id}"
    description = _plain_text(product.description)

    json_ld = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'name': product.name,
        'description': description[:500],
        'sku': f"HS-{product.id}",
        'brand': {'@type': 'Brand', 'name': 'Heritage Spices'},
        'url': url,
    }
    if product.image_url:
        json_ld['image'] = [product.image_url]
    if price is not None:
        json_ld['offers'] = {
            '@type': 'Offer', 'url': url, 'priceCurrency': 'INR', 'price': f"{price:.2f}",
            'availability': 'https://schema.org/OutOfStock' if out_of_stock else 'https://schema.org/InStock',
            'seller': {'@type': 'Organization', 'name': 'Heritage Spices'},
            'hasMerchantReturnPolicy': {'@type': 'MerchantReturnPolicy', 'applicableCountry': 'IN',
                                        'returnPolicyCategory': 'https://schema.org/MerchantReturnNotPermitted'},
            'shippingDetails': {
                '@type': 'OfferShippingDetails',
                'shippingRate': {'@type': 'MonetaryAmount', 'value': 40.00, 'currency': 'INR'},
                'shippingDestination': {'@type': 'DefinedRegion', 'addressCountry': 'IN'},
                'deliveryTime': {
                    '@type': 'ShippingDeliveryTime',
                    'handlingTime': {'@type': 'QuantitativeValue', 'minValue': 1, 'maxValue': 2, 'unitCode': 'd'},
                    'transitTime': {'@type': 'QuantitativeValue', 'minValue': 3, 'maxValue': 7, 'unitCode': 'd'},
                },
            },
        }
    # Only ever describe real, approved reviews -- never invent a rating.
    if reviews:
        json_ld['aggregateRating'] = {'@type': 'AggregateRating', 'ratingValue': str(product.avg_rating),
                                      'reviewCount': str(len(reviews))}
        json_ld['review'] = [{
            '@type': 'Review', 'author': {'@type': 'Person', 'name': r.user_name},
            'datePublished': r.created_at.strftime('%Y-%m-%d') if r.created_at else None,
            'reviewBody': r.review_text,
            'reviewRating': {'@type': 'Rating', 'ratingValue': str(r.rating), 'bestRating': '5'},
        } for r in reviews]

    return render_template('product_detail.html', product=product, sizes=sizes, price=price, per_10g=per_10g,
                           out_of_stock=out_of_stock, low_stock=low_stock, reviews=reviews,
                           description=description, json_ld=json_ld, page_url=url,
                           user=session.get('user'), is_logged_in=bool(session.get('user')))

# --- Google Merchant Center product feed ---
# Merchant Center can fetch this URL on a schedule (Products > Feeds > Add feed > Scheduled fetch).
# Everything here comes from the same data the product page shows, because Google compares the
# feed with the landing page and rejects listings whose price/availability/description differ.
MERCHANT_SHIPPING_INR = os.getenv('MERCHANT_SHIPPING_INR', '35')   # matches the flat rate entered in Merchant Center (0-0.5 kg)
GOOGLE_CATEGORY_SEASONINGS_SPICES = '4608'   # Food, Beverages & Tobacco > Food Items > Seasonings & Spices

@app.route('/feeds/google-merchant.xml')
def google_merchant_feed():
    site = notifications.site_url()
    products = Product.query.order_by(Product.id).all()
    group_sizes = {}
    for pr in products:
        group_sizes[product_base_name(pr.name)] = group_sizes.get(product_base_name(pr.name), 0) + 1

    def tag(name, value):
        return f"      <g:{name}>{xml_escape(str(value))}</g:{name}>"

    items = []
    for pr in products:
        price = product_price_rupees(pr)
        if price is None or not pr.image_url:
            continue  # Google rejects listings without a price or image, so do not send them
        base = product_base_name(pr.name)
        out_of_stock = pr.stock is not None and pr.stock <= 0
        lines = [
            "    <item>",
            tag('id', f"HS-{pr.id}"),
            tag('title', pr.name[:150]),
            tag('description', _plain_text(pr.description)[:5000] or pr.name),
            tag('link', f"{site}/product/{pr.id}"),
            tag('image_link', pr.image_url),
            tag('availability', 'out_of_stock' if out_of_stock else 'in_stock'),
            tag('price', f"{price:.2f} INR"),
            tag('brand', 'Heritage Spices'),
            tag('mpn', f"HS-{pr.id}"),
            tag('condition', 'new'),
            tag('google_product_category', GOOGLE_CATEGORY_SEASONINGS_SPICES),
            tag('product_type', 'Spices > ' + base),
        ]
        if group_sizes.get(base, 0) > 1:
            lines.append(tag('item_group_id', slugify(base)))
        grams = product_grams(pr.name)
        if grams:
            lines.append(tag('shipping_weight', f"{(grams + 5) / 1000:g} kg"))   # packed weight = net + 5 g pouch, as at checkout
        lines += [
            "      <g:shipping>",
            "        <g:country>IN</g:country>",
            "        <g:service>Standard</g:service>",
            f"        <g:price>{float(MERCHANT_SHIPPING_INR):.2f} INR</g:price>",
            "        <g:min_handling_time>1</g:min_handling_time>",
            "        <g:max_handling_time>2</g:max_handling_time>",
            "        <g:min_transit_time>3</g:min_transit_time>",
            "        <g:max_transit_time>7</g:max_transit_time>",
            "      </g:shipping>",
            "    </item>",
        ]
        items.append("\n".join(lines))

    xml = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">',
        "  <channel>",
        "    <title>Heritage Spices</title>",
        f"    <link>{site}/</link>",
        "    <description>Heritage Spices product feed</description>",
        *items,
        "  </channel>",
        "</rss>",
    ])
    return xml, 200, {'Content-Type': 'application/xml; charset=utf-8', 'Cache-Control': 'public, max-age=900'}

# --- QR Code Scan Tracking ---
# No personal data is collected here -- just an anonymous log of which
# product's QR code was scanned, so the owner can see which products get
# scanned most. Redirects straight to the shop page.
@app.route('/scan/<int:product_id>')
def scan_product(product_id):
    product = Product.query.get_or_404(product_id)
    try:
        db.session.add(ProductScan(
            product_id=product.id,
            product_name=product.name,
            ip=request.remote_addr,
            user_agent=request.headers.get('User-Agent', '')[:300]
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print("ProductScan logging error:", e)
    return redirect('/products')

# --- Admin: QR Codes for products (generate + download, view scan counts) ---
@app.route('/admin/qr-codes')
def admin_qr_codes():
    if not is_admin_user(session.get('user')):
        abort(403)

    products = Product.query.all()
    scan_counts = dict(
        db.session.query(ProductScan.product_id, db.func.count(ProductScan.id))
        .group_by(ProductScan.product_id).all()
    )
    last_scanned = dict(
        db.session.query(ProductScan.product_id, db.func.max(ProductScan.timestamp))
        .group_by(ProductScan.product_id).all()
    )
    return render_template('admin_qr_codes.html', products=products,
                           scan_counts=scan_counts, last_scanned=last_scanned)

# --- Admin: Serve a generated QR code PNG for a product ---
@app.route('/admin/qr-codes/<int:product_id>.png')
def admin_qr_code_image(product_id):
    if not is_admin_user(session.get('user')):
        abort(403)

    product = Product.query.get_or_404(product_id)
    scan_url = url_for('scan_product', product_id=product.id, _external=True)

    img = qrcode.make(scan_url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    safe_name = secure_filename(product.name) or f"product-{product.id}"
    as_attachment = request.args.get('download') == '1'
    return send_file(buf, mimetype='image/png', as_attachment=as_attachment,
                     download_name=f"qr-{safe_name}.png")

# --- Admin Add Product Route ---
@app.route('/admin/products/add', methods=['GET', 'POST'])
def add_product():
    if not is_admin_user(session.get('user')):
        abort(403)
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        price = request.form.get('price', type=float)
        category = request.form.get('category')
        stock = request.form.get('stock', type=int)
        meesho_link = request.form.get('meesho_link')
        image = request.files.get('image')
        image_url = None
        if image and image.filename:
            import base64
            imgbb_api_key = os.getenv('IMGBB_API_KEY')
            image_data = base64.b64encode(image.read()).decode('utf-8')
            payload = {'key': imgbb_api_key, 'image': image_data}
            response = requests.post('https://api.imgbb.com/1/upload', data=payload)
            if response.status_code == 200:
                image_url = response.json()['data']['url']
        new_product = Product(
            name=name,
            description=description,
            price=price,
            category=category,
            stock=stock,
            meesho_link=meesho_link,
            image_url=image_url
        )
        db.session.add(new_product)
        db.session.commit()
        flash('Product added!', 'success')
        return redirect('/products')
    return render_template('admin_product_add.html')

@app.route('/admin/products/edit/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    if not is_admin_user(session.get('user')):
        abort(403)
    product = Product.query.get_or_404(product_id)
    if request.method == 'POST':
        product.name = request.form['name']
        product.description = request.form['description']
        product.price = request.form.get('price', type=float)
        product.category = request.form.get('category')
        product.stock = request.form.get('stock', type=int)
        product.meesho_link = request.form.get('meesho_link')
        image = request.files.get('image')
        if image and image.filename:
            import base64
            imgbb_api_key = os.getenv('IMGBB_API_KEY')
            image_data = base64.b64encode(image.read()).decode('utf-8')
            payload = {'key': imgbb_api_key, 'image': image_data}
            response = requests.post('https://api.imgbb.com/1/upload', data=payload)
            if response.status_code == 200:
                product.image_url = response.json()['data']['url']
        db.session.commit()
        flash('Product updated!', 'success')
        return redirect('/products')
    return render_template('admin_product_edit.html', product=product)

# --- Legal Pages Routes ---
@app.route('/terms')
def terms():
    return render_template('terms.html', user=session.get('user'), is_logged_in=bool(session.get('user')))

@app.route('/disclaimer')
def disclaimer():
    return render_template('disclaimer.html', user=session.get('user'), is_logged_in=bool(session.get('user')))

@app.route('/refund')
def refund():
    return render_template('refund.html', user=session.get('user'), is_logged_in=bool(session.get('user')))

_faq_cache = {'items': None}

def faq_items():
    """(question, answer) pairs read from the accordion in templates/faq.html, so the FAQPage
    structured data always matches what visitors can read (Google requires that)."""
    if _faq_cache['items'] is None:
        from html.parser import HTMLParser
        raw = open(os.path.join(app.root_path, 'templates', 'faq.html'), encoding='utf-8').read()
        raw = re.sub(r'\{[%{#].*?[%}#]\}', ' ', raw, flags=re.S)

        class _P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.items, self.mode, self.q, self.a, self.depth = [], None, '', '', 0

            def handle_starttag(self, tag, attrs):
                cls = dict(attrs).get('class', '') or ''
                if tag == 'button' and 'accordion-button' in cls:
                    self.mode, self.q = 'q', ''
                elif tag == 'div' and 'accordion-body' in cls:
                    self.mode, self.a, self.depth = 'a', '', 1
                elif self.mode == 'a' and tag == 'div':
                    self.depth += 1

            def handle_endtag(self, tag):
                if tag == 'button' and self.mode == 'q':
                    self.mode = None
                elif tag == 'div' and self.mode == 'a':
                    self.depth -= 1
                    if self.depth == 0:
                        q = re.sub(r'\s+', ' ', self.q).strip()
                        a = re.sub(r'\s+', ' ', self.a).strip()
                        if q and a:
                            self.items.append((q, a))
                        self.mode = None

            def handle_data(self, data):
                if self.mode == 'q':
                    self.q += data
                elif self.mode == 'a':
                    self.a += data + ' '

        parser = _P()
        try:
            parser.feed(raw)
        except Exception as e:
            print("FAQ parse failed:", e)
        _faq_cache['items'] = parser.items
    return _faq_cache['items']

@app.route('/faq')
def faq():
    items = faq_items()
    json_ld = {'@context': 'https://schema.org', '@type': 'FAQPage', 'mainEntity': [
        {'@type': 'Question', 'name': q, 'acceptedAnswer': {'@type': 'Answer', 'text': a}} for q, a in items
    ]} if items else None
    return render_template('faq.html', json_ld=json_ld)


# -------------------------
# 🔬 Science Hub Routes
# -------------------------

# Paid course material lives OUTSIDE static/ on purpose: Flask serves everything under
# static/ to anyone who knows the URL, which let the paywall be bypassed entirely.
# Files here are only reachable through the access-checked /api/notes routes.
NOTES_ROOT = os.path.join(app.root_path, 'private_content')

# --- Science Hub Content Configuration ---
SCIENCE_CONTENT = {
    'sci1': {'path': 'notes', 'start_page': 1, 'end_page': 148, 'total_pages': 148, 'title': 'SSC 10th - Science 1 Notes'},
    'sci2': {'path': 'notes', 'start_page': 149, 'end_page': 297, 'total_pages': 149, 'title': 'SSC 10th - Science 2 Notes'},
    'practice-sci1': {'path': 'notes/practice-sci1', 'start_page': 1, 'end_page': 0, 'total_pages': 0, 'title': 'Practice Papers - Science 1'},
    'practice-sci2': {'path': 'notes/practice-sci2', 'start_page': 1, 'end_page': 0, 'total_pages': 0, 'title': 'Practice Papers - Science 2'},
}

# Science Hub is hidden from the public for now. Set SCIENCE_HUB_ENABLED=true in the
# environment to bring it back. Admins can still open it while it is hidden (to
# manage content); everyone else gets a normal 404 on every Science Hub URL,
# including the payment endpoints and the notes API.
SCIENCE_HUB_ENABLED = os.getenv('SCIENCE_HUB_ENABLED', 'false').strip().lower() == 'true'

@app.before_request
def hide_science_hub():
    if SCIENCE_HUB_ENABLED:
        return None
    if request.path.startswith('/science-hub') or request.path.startswith('/api/notes'):
        if not is_admin_user(session.get('user')):
            abort(404)
    return None

@app.context_processor
def inject_flags():
    return {
        'is_admin': is_admin_user(session.get('user')),
        'store_whatsapp_link': store_whatsapp_link(),
        'google_site_verification': google_site_verification_tokens(),
        'google_one_tap': bool(GOOGLE_ONE_TAP_ENABLED and app.config.get('GOOGLE_CLIENT_ID')
                               and not session.get('user')
                               and not request.path.startswith(_ONE_TAP_SKIP_PATHS)),
        'google_client_id': app.config.get('GOOGLE_CLIENT_ID') or '',
        # Hidden from everyone (admins included) while the hub is switched off; admins still
        # reach it via Admin > Science Hub Admin or the direct URL.
        'science_hub_enabled': SCIENCE_HUB_ENABLED,
    }

@app.route('/science-hub')
def science_hub():
    user = session.get('user')
    is_logged_in = bool(user)
    
    # Check if user already has digital access
    has_digital_access = False
    if is_logged_in:
        has_digital_access = DigitalAccess.query.filter_by(user_email=user['email']).first() is not None

    # Read toggles from SiteSetting
    digital_enabled = get_setting('science_hub_digital_enabled', 'true') == 'true'
    physical_enabled = get_setting('science_hub_physical_enabled', 'true') == 'true'

    # Count practice paper pages
    import glob
    practice_sci1_dir = os.path.join(NOTES_ROOT, 'notes', 'practice-sci1')
    practice_sci2_dir = os.path.join(NOTES_ROOT, 'notes', 'practice-sci2')
    practice_sci1_count = len(glob.glob(os.path.join(practice_sci1_dir, 'page_*.png'))) if os.path.exists(practice_sci1_dir) else 0
    practice_sci2_count = len(glob.glob(os.path.join(practice_sci2_dir, 'page_*.png'))) if os.path.exists(practice_sci2_dir) else 0

    return render_template('science_hub.html', user=user, is_logged_in=is_logged_in,
                           has_digital_access=has_digital_access, razorpay_key_id=RAZORPAY_KEY_ID,
                           digital_enabled=digital_enabled, physical_enabled=physical_enabled,
                           practice_sci1_count=practice_sci1_count, practice_sci2_count=practice_sci2_count,
                           science_content=SCIENCE_CONTENT)

@app.route('/science-hub/validate-upper-id', methods=['POST'])
def validate_upper_id():
    if _redeem_rate_limited(f"upper-id:{request.remote_addr}"):
        return jsonify({'valid': False, 'message': 'Too many attempts. Please wait a few minutes and try again.'})

    data = request.json
    code = data.get('code')
    product_type = data.get('product_type')  # 'digital' or 'physical'

    if not code:
        return jsonify({'valid': False, 'message': 'Please enter an Upper ID'})

    upper_id = UpperID.query.filter_by(code=code).first()
    
    if not upper_id:
        return jsonify({'valid': False, 'message': 'Invalid Upper ID'})

    if product_type == 'digital':
        if upper_id.has_used_digital_offer:
            return jsonify({'valid': False, 'message': 'This Upper ID has already been used for the Digital offer.'})
        return jsonify({'valid': True, 'price': 40, 'message': f'Offer applied for {upper_id.student_name}!'})
    
    elif product_type == 'physical':
        if upper_id.has_used_physical_offer:
            return jsonify({'valid': False, 'message': 'This Upper ID has already been used for the Physical Book offer.'})
        return jsonify({'valid': True, 'price': 250, 'message': f'Offer applied for {upper_id.student_name}!'})
    
    return jsonify({'valid': False, 'message': 'Invalid product type'})

@app.route('/science-hub/create-order', methods=['POST'])
def create_science_order():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Please login first'}), 401
    
    data = request.json
    product_type = data.get('product_type')
    upper_id_code = data.get('upper_id')
    
    # Determine price
    amount_inr = 100 if product_type == 'digital' else 350
    
    if upper_id_code:
        upper_id = UpperID.query.filter_by(code=upper_id_code).first()
        if upper_id:
            if product_type == 'digital' and not upper_id.has_used_digital_offer:
                amount_inr = 40
            elif product_type == 'physical' and not upper_id.has_used_physical_offer:
                amount_inr = 250
    
    amount_paise = amount_inr * 100
    
    if not razorpay_client:
        return jsonify({'error': 'Razorpay not configured on server'}), 500
        
    try:
        # Create Razorpay order
        order_data = {
            'amount': amount_paise,
            'currency': 'INR',
            'receipt': f'receipt_{uuid.uuid4().hex[:10]}',
            'payment_capture': 1
        }
        rzp_order = razorpay_client.order.create(data=order_data)
        
        # Save order in DB
        new_order = ScienceOrder(
            user_email=user['email'],
            product_type=product_type,
            amount=amount_paise,
            upper_id_used=upper_id_code,
            razorpay_order_id=rzp_order['id'],
            full_name=data.get('full_name'),
            phone=data.get('phone'),
            address=data.get('address'),
            pincode=data.get('pincode')
        )
        db.session.add(new_order)
        db.session.commit()
        
        return jsonify({'order_id': rzp_order['id'], 'amount': amount_paise, 'currency': 'INR', 'key': RAZORPAY_KEY_ID})
        
    except Exception as e:
        print("Error creating order:", e)
        return jsonify({'error': str(e)}), 500

@app.route('/science-hub/verify-payment', methods=['POST'])
def verify_science_payment():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
        
    data = request.json
    razorpay_payment_id = data.get('razorpay_payment_id')
    razorpay_order_id = data.get('razorpay_order_id')
    razorpay_signature = data.get('razorpay_signature')
    
    if not all([razorpay_payment_id, razorpay_order_id, razorpay_signature]):
        return jsonify({'error': 'Missing payment details'}), 400
        
    try:
        # Verify signature
        razorpay_client.utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature
        })
        
        # Signature is valid. Update order status
        order = ScienceOrder.query.filter_by(razorpay_order_id=razorpay_order_id).first()
        if not order:
            return jsonify({'error': 'Order not found'}), 404
            
        order.payment_status = 'paid'
        order.razorpay_payment_id = razorpay_payment_id
        
        # Send Telegram Notification
        msg = f"🚨 <b>NEW SCIENCE HUB ORDER!</b>\n\n<b>Order ID:</b> {order.razorpay_order_id}\n<b>User:</b> {user['email']}\n<b>Type:</b> {order.product_type}\n<b>Amount:</b> ₹{order.amount / 100}"
        send_telegram_notification(msg)
        
        # Mark Upper ID as used if applicable
        if order.upper_id_used:
            upper_id = UpperID.query.filter_by(code=order.upper_id_used).first()
            if upper_id:
                if order.product_type == 'digital':
                    upper_id.has_used_digital_offer = True
                elif order.product_type == 'physical':
                    upper_id.has_used_physical_offer = True
                    
        # Grant Digital Access if applicable
        if order.product_type == 'digital':
            access = DigitalAccess(
                user_email=user['email'],
                access_token=uuid.uuid4().hex
            )
            db.session.add(access)
            
        db.session.commit()
        return jsonify({'success': True, 'message': 'Payment successful!'})
        
    except razorpay.errors.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400
    except Exception as e:
        print("Payment verification error:", e)
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/science-hub/viewer')
@app.route('/science-hub/viewer/<subject>')
def science_viewer(subject='sci1'):
    user = session.get('user')
    if not user:
        flash("Please login to view notes.", "warning")
        return redirect('/science-hub')
        
    # Check access
    access = DigitalAccess.query.filter_by(user_email=user['email']).first()
    if not access:
        flash("You do not have access to the Science Notes. Please purchase the Online Access.", "danger")
        return redirect('/science-hub')
    
    # Validate subject
    if subject not in SCIENCE_CONTENT:
        flash("Invalid subject selected.", "danger")
        return redirect('/science-hub')
    
    content = SCIENCE_CONTENT[subject]
    
    # For practice papers, count files dynamically
    if subject.startswith('practice-'):
        import glob
        practice_dir = os.path.join(NOTES_ROOT, content['path'])
        if os.path.exists(practice_dir):
            pages = glob.glob(os.path.join(practice_dir, 'page_*.png'))
            content = dict(content)  # copy
            content['total_pages'] = len(pages)
            content['end_page'] = len(pages)
    
    return render_template('science_viewer.html', user=user, 
                           total_pages=content['total_pages'], 
                           subject=subject,
                           content_title=content['title'],
                           all_subjects=SCIENCE_CONTENT)

@app.route('/admin/science-hub', methods=['GET', 'POST'])
@admin_required
def admin_science_hub():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_id':
            code = request.form.get('code')
            student_name = request.form.get('student_name')
            if code and student_name:
                existing = UpperID.query.filter_by(code=code).first()
                if not existing:
                    new_id = UpperID(code=code, student_name=student_name)
                    db.session.add(new_id)
                    db.session.commit()
                    flash("Upper ID added successfully", "success")
                else:
                    flash("Upper ID already exists", "danger")
        elif action == 'delete_id':
            id_to_delete = request.form.get('id')
            upper_id = UpperID.query.get(id_to_delete)
            if upper_id:
                db.session.delete(upper_id)
                db.session.commit()
                flash("Upper ID deleted", "success")

    upper_ids = UpperID.query.all()
    orders = ScienceOrder.query.order_by(ScienceOrder.created_at.desc()).all()
    access_grants = DigitalAccess.query.order_by(DigitalAccess.granted_at.desc()).all()
    
    # Count practice paper pages for admin view
    import glob
    practice_sci1_dir = os.path.join(NOTES_ROOT, 'notes', 'practice-sci1')
    practice_sci2_dir = os.path.join(NOTES_ROOT, 'notes', 'practice-sci2')
    practice_sci1_count = len(glob.glob(os.path.join(practice_sci1_dir, 'page_*.png'))) if os.path.exists(practice_sci1_dir) else 0
    practice_sci2_count = len(glob.glob(os.path.join(practice_sci2_dir, 'page_*.png'))) if os.path.exists(practice_sci2_dir) else 0
    
    return render_template('admin_science_hub.html', upper_ids=upper_ids, orders=orders, access_grants=access_grants, practice_sci1_count=practice_sci1_count, practice_sci2_count=practice_sci2_count)

@app.route('/admin/science-hub/upload-practice', methods=['POST'])
@admin_required
def upload_practice_pages():
    subject = request.form.get('subject')  # 'practice-sci1' or 'practice-sci2'
    if subject not in ('practice-sci1', 'practice-sci2'):
        flash("Invalid subject for upload.", "danger")
        return redirect('/admin/science-hub')
    
    files = request.files.getlist('pages')
    if not files:
        flash("No files selected.", "danger")
        return redirect('/admin/science-hub')
    
    content = SCIENCE_CONTENT[subject]
    upload_dir = os.path.join(NOTES_ROOT, content['path'])
    os.makedirs(upload_dir, exist_ok=True)
    
    # Find the next page number
    import glob
    existing = glob.glob(os.path.join(upload_dir, 'page_*.png'))
    next_num = len(existing) + 1
    
    uploaded_count = 0
    for f in files:
        if f and f.filename:
            filename = f'page_{next_num}.png'
            f.save(os.path.join(upload_dir, filename))
            next_num += 1
            uploaded_count += 1
    
    flash(f"Successfully uploaded {uploaded_count} practice paper page(s) to {subject}.", "success")
    return redirect('/admin/science-hub')


@app.route('/admin/science-hub/delete-practice-page', methods=['POST'])
@admin_required  
def delete_practice_page():
    subject = request.form.get('subject')
    page_num_raw = request.form.get('page_num')

    if subject not in ('practice-sci1', 'practice-sci2'):
        flash("Invalid subject.", "danger")
        return redirect('/admin/science-hub')

    try:
        page_num = int(page_num_raw)
        if page_num < 1:
            raise ValueError
    except (TypeError, ValueError):
        flash("Invalid page number.", "danger")
        return redirect('/admin/science-hub')

    content = SCIENCE_CONTENT[subject]
    file_path = os.path.join(NOTES_ROOT, content['path'], f'page_{page_num}.png')
    
    if os.path.exists(file_path):
        os.remove(file_path)
        # Renumber remaining pages
        import glob
        upload_dir = os.path.join(NOTES_ROOT, content['path'])
        pages = sorted(glob.glob(os.path.join(upload_dir, 'page_*.png')))
        for i, page_path in enumerate(pages, 1):
            new_path = os.path.join(upload_dir, f'page_{i}.png')
            if page_path != new_path:
                os.rename(page_path, new_path)
        flash(f"Page deleted and remaining pages renumbered.", "success")
    else:
        flash("Page not found.", "danger")
    
    return redirect('/admin/science-hub')

@app.route('/admin/science-hub/replace-page', methods=['POST'])
@admin_required
def replace_science_page():
    subject = request.form.get('subject')
    page_num = request.form.get('page_num')
    file = request.files.get('page_file')
    
    if subject not in SCIENCE_CONTENT:
        flash("Invalid subject.", "danger")
        return redirect('/admin/science-hub')
        
    if not file or not file.filename:
        flash("No file selected.", "danger")
        return redirect('/admin/science-hub')
        
    try:
        page_num = int(page_num)
    except ValueError:
        flash("Invalid page number.", "danger")
        return redirect('/admin/science-hub')
        
    content = SCIENCE_CONTENT[subject]
    
    # Calculate actual filename
    if subject in ('sci1', 'sci2'):
        if page_num < 1 or page_num > content['total_pages']:
            flash("Page number out of range.", "danger")
            return redirect('/admin/science-hub')
        actual_page = content['start_page'] + page_num - 1
        filepath = os.path.join(NOTES_ROOT, content['path'], f'page_{actual_page}.png')
    else:
        # Practice papers
        import glob
        practice_dir = os.path.join(NOTES_ROOT, content['path'])
        pages = glob.glob(os.path.join(practice_dir, 'page_*.png'))
        if page_num < 1 or page_num > len(pages):
            flash("Page number out of range.", "danger")
            return redirect('/admin/science-hub')
        filepath = os.path.join(practice_dir, f'page_{page_num}.png')
        
    # Save file, overwriting the existing one
    file.save(filepath)
    flash(f"Successfully replaced page {page_num} for {subject}.", "success")
    return redirect('/admin/science-hub')

@app.route('/api/notes/<int:page_num>')
@app.route('/api/notes/<subject>/<int:page_num>')
def get_notes_page(page_num, subject='sci1'):
    user = session.get('user')
    if not user:
        abort(401)
    
    access = DigitalAccess.query.filter_by(user_email=user['email']).first()
    if not access:
        abort(403)
    
    if subject not in SCIENCE_CONTENT:
        abort(404)
    
    content = SCIENCE_CONTENT[subject]
    
    # For sci1/sci2, pages are in the same 'notes' directory but with different numbering
    if subject in ('sci1', 'sci2'):
        actual_page = content['start_page'] + page_num - 1
        if actual_page < content['start_page'] or actual_page > content['end_page']:
            abort(404)
        try:
            return send_from_directory(os.path.join(NOTES_ROOT, content['path']), f'page_{actual_page}.png')
        except Exception:
            abort(404)
    else:
        # Practice papers: files are in their own directory, numbered from 1
        try:
            return send_from_directory(os.path.join(NOTES_ROOT, content['path']), f'page_{page_num}.png')
        except Exception:
            abort(404)

@app.template_filter()
def truncate(s, length=100):
    return s if len(s) <= length else s[:length] + "..."


# -------------------------
# 🛒 Cart Routes
# -------------------------

@app.route('/cart')
def view_cart():
    user = session.get('user')
    if not user:
        flash("Please login to view your cart.", "warning")
        return redirect('/login')
    
    cart_items = CartItem.query.filter_by(user_email=user['email']).options(joinedload(CartItem.product)).all()
    cart_total = sum(int(float(item.product.price)) * item.quantity for item in cart_items if item.product)

    valid_coupons = [c for c in Coupon.query.filter_by(user_email=user['email'], used=False).all()
                      if not c.expires_at or c.expires_at > datetime.utcnow()]

    return render_template('cart.html', cart_items=cart_items, cart_total=cart_total,
                           user=user, is_logged_in=True, valid_coupons=valid_coupons)

@app.route('/cart/add', methods=['POST'])
def add_to_cart():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Please login first'}), 401
    
    # Check if store is open
    if get_setting('store_open', 'true') == 'false':
        return jsonify({'error': 'Store is currently closed'}), 503
    
    data = request.json
    product_id = data.get('product_id')
    quantity = int(data.get('quantity', 2))
    
    if quantity < 2:
        return jsonify({'error': 'Minimum order quantity is 2 pouches.'}), 400
    
    product = Product.query.get(product_id)
    if not product:
        return jsonify({'error': 'Product not found'}), 404
    
    # Check stock
    if product.stock is not None and product.stock < quantity:
        return jsonify({'error': f'Only {product.stock} items available'}), 400
    
    # Max 5 per product
    existing = CartItem.query.filter_by(user_email=user['email'], product_id=product_id).first()
    if existing:
        new_qty = existing.quantity + quantity
        if new_qty > 5:
            return jsonify({'error': 'Maximum 5 per product allowed'}), 400
        existing.quantity = new_qty
    else:
        if quantity > 5:
            return jsonify({'error': 'Maximum 5 per product allowed'}), 400
        item = CartItem(user_email=user['email'], product_id=product_id, quantity=quantity)
        db.session.add(item)
    
    db.session.commit()
    count = CartItem.query.filter_by(user_email=user['email']).count()
    return jsonify({'success': True, 'message': f'{product.name} added to cart!', 'cart_count': count})

@app.route('/cart/update', methods=['POST'])
def update_cart():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Please login first'}), 401
    
    data = request.json
    item_id = data.get('item_id')
    quantity = int(data.get('quantity', 1))
    
    item = CartItem.query.filter_by(id=item_id, user_email=user['email']).first()
    if not item:
        return jsonify({'error': 'Item not found'}), 404
    
    if quantity <= 0:
        db.session.delete(item)
    elif quantity < 2:
        return jsonify({'error': 'Minimum order quantity is 2 pouches.'}), 400
    elif quantity > 5:
        return jsonify({'error': 'Maximum 5 per product allowed'}), 400
    else:
        # Check stock
        if item.product.stock is not None and item.product.stock < quantity:
            return jsonify({'error': f'Only {item.product.stock} items available'}), 400
        item.quantity = quantity
    
    db.session.commit()
    
    # Recalculate totals
    cart_items = CartItem.query.filter_by(user_email=user['email']).options(joinedload(CartItem.product)).all()
    cart_total = sum(int(float(ci.product.price)) * ci.quantity for ci in cart_items if ci.product)
    count = len(cart_items)
    
    return jsonify({'success': True, 'cart_total': cart_total, 'cart_count': count})

@app.route('/cart/remove', methods=['POST'])
def remove_from_cart():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Please login first'}), 401
    
    data = request.json
    item_id = data.get('item_id')
    
    item = CartItem.query.filter_by(id=item_id, user_email=user['email']).first()
    if item:
        db.session.delete(item)
        db.session.commit()
    
    cart_items = CartItem.query.filter_by(user_email=user['email']).options(joinedload(CartItem.product)).all()
    cart_total = sum(int(float(ci.product.price)) * ci.quantity for ci in cart_items if ci.product)
    count = len(cart_items)
    
    return jsonify({'success': True, 'cart_total': cart_total, 'cart_count': count})

@app.route('/cart/count')
def cart_count():
    user = session.get('user')
    if not user:
        return jsonify({'count': 0})
    count = CartItem.query.filter_by(user_email=user['email']).count()
    return jsonify({'count': count})


# -------------------------
# 💳 Checkout Routes
# -------------------------

@app.route('/checkout')
def checkout():
    user = session.get('user')
    if not user:
        flash("Please login to checkout.", "warning")
        return redirect('/login')
    
    if get_setting('store_open', 'true') == 'false':
        flash("Store is currently closed. Please try again later.", "warning")
        return redirect('/products')
    
    cart_items = CartItem.query.filter_by(user_email=user['email']).options(joinedload(CartItem.product)).all()
    if not cart_items:
        flash("Your cart is empty.", "warning")
        return redirect('/cart')
    
    cart_total = sum(int(float(item.product.price)) * item.quantity for item in cart_items if item.product)
    points_balance = get_points_balance(user['email'])

    return render_template('checkout.html', cart_items=cart_items, cart_total=cart_total,
                           user=user, is_logged_in=True, razorpay_key_id=RAZORPAY_KEY_ID,
                           points_balance=points_balance)

@app.route('/checkout/apply-points', methods=['POST'])
def apply_points():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Please login first'}), 401

    if _redeem_rate_limited(user['email']):
        return jsonify({'error': 'Too many attempts. Please wait a few minutes and try again.'}), 429

    requested = (request.json or {}).get('points', 0)
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid points amount'}), 400

    if get_setting('referral_enabled', 'true') != 'true':
        return jsonify({'error': 'Points redemption is currently unavailable.'}), 400

    balance = get_points_balance(user['email'])
    if requested <= 0 or requested > balance:
        return jsonify({'error': f'You only have {balance} points available.'}), 400

    cart_items = CartItem.query.filter_by(user_email=user['email']).options(joinedload(CartItem.product)).all()
    if not cart_items:
        return jsonify({'error': 'Your cart is empty'}), 400

    subtotal_rupees = sum(int(float(item.product.price)) * item.quantity for item in cart_items if item.product)
    used_points, discount = compute_points_redemption(requested, subtotal_rupees, balance)

    if used_points == 0:
        return jsonify({'error': 'Points redemption is capped for this order size. Try a smaller amount.'}), 400

    return jsonify({'success': True, 'points_used': used_points, 'discount': discount, 'balance': balance})

@app.route('/checkout/apply-coupon', methods=['POST'])
def apply_coupon():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Please login first'}), 401

    if _redeem_rate_limited(user['email']):
        return jsonify({'error': 'Too many attempts. Please wait a few minutes and try again.'}), 429

    code = (request.json or {}).get('code', '').strip().upper()
    if not code:
        return jsonify({'error': 'Please enter a coupon code'}), 400

    coupon = Coupon.query.filter_by(code=code).first()
    if not coupon or not coupon.is_valid_for(user['email']):
        return jsonify({'error': 'This coupon is invalid, expired, already used, or not valid for your account.'}), 400

    cart_items = CartItem.query.filter_by(user_email=user['email']).options(joinedload(CartItem.product)).all()
    if not cart_items:
        return jsonify({'error': 'Your cart is empty'}), 400

    subtotal_rupees = sum(int(float(item.product.price)) * item.quantity for item in cart_items if item.product)
    discount = coupon.compute_discount(subtotal_rupees)

    return jsonify({'success': True, 'discount': discount, 'code': coupon.code})

@app.route('/checkout/check-pincode', methods=['POST'])
def check_pincode():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Please login first'}), 401
    
    data = request.json
    pincode = data.get('pincode', '').strip()
    
    if not pincode or len(pincode) != 6 or not pincode.isdigit():
        return jsonify({'available': False, 'message': 'Please enter a valid 6-digit pincode'})
    
    mode = get_setting('delivery_mode', 'hybrid')
    
    if mode == 'manual':
        return jsonify({
            'available': True,
            'couriers': [{'rate': 40, 'courier_name': 'Standard Flat Rate', 'estimated_delivery_days': '5-7'}],
            'message': 'Delivery available'
        })
    
    # Check blacklist
    if mode in ['hybrid', 'manual']:
        pass # manual is already handled above, hybrid handles blacklist
    
    blacklisted = BlacklistedPincode.query.filter_by(pincode=pincode).first()
    if blacklisted:
        return jsonify({'available': False, 'message': f'Delivery not available to {pincode} ({blacklisted.reason or "Restricted area"})'})
    
    # Check with NimbusPost
    result = nimbus_api.check_serviceability(pincode)
    return jsonify(result)

@app.route('/checkout/calculate-shipping', methods=['POST'])
def calculate_shipping():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Please login first'}), 401
    
    data = request.json
    pincode = data.get('pincode', '').strip()
    
    # Calculate total weight from cart
    cart_items = CartItem.query.filter_by(user_email=user['email']).options(joinedload(CartItem.product)).all()
    total_weight_kg = 0.0
    for item in cart_items:
        # Precise weight: product + 5g polythine
        grams = product_grams(item.product.name)   # net weight from the name, e.g. "Garam Masala - 50g"
        if grams:
            item_weight = (grams + 5) / 1000       # + 5 g polythene
        else:
            price = float(item.product.price) if item.product.price else 0
            item_weight = 0.06 if price <= 45 else 0.11
        total_weight_kg += item_weight * item.quantity
    
    mode = get_setting('delivery_mode', 'hybrid')
    
    if mode == 'manual':
        rates = [{
            'courier_id': 'flat',
            'courier_name': 'Standard Flat Rate',
            'rate': 40,
            'estimated_days': '5-7',
            'min_weight': 0.5
        }]
    else:
        rates = nimbus_api.get_shipping_rates(pincode, total_weight_kg)
        
    return jsonify({'rates': rates})

@app.route('/checkout/create-order', methods=['POST'])
def create_checkout_order():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Please login first'}), 401
    
    if get_setting('store_open', 'true') == 'false':
        return jsonify({'error': 'Store is currently closed'}), 503
    
    data = request.json
    
    # Validate delivery details
    full_name = data.get('full_name', '').strip()
    phone = data.get('phone', '').strip()
    address = data.get('address', '').strip()
    city = data.get('city', '').strip()
    state = data.get('state', '').strip()
    pincode = data.get('pincode', '').strip()

    if not all([full_name, phone, address, city, state, pincode]):
        return jsonify({'error': 'Please fill in all delivery details'}), 400

    # Check blacklist again
    blacklisted = BlacklistedPincode.query.filter_by(pincode=pincode).first()
    if blacklisted:
        return jsonify({'error': 'Delivery not available to this pincode'}), 400

    # Get cart items
    cart_items = CartItem.query.filter_by(user_email=user['email']).options(joinedload(CartItem.product)).all()
    if not cart_items:
        return jsonify({'error': 'Cart is empty'}), 400

    # Check stock for all items
    for item in cart_items:
        if item.product.stock is not None and item.product.stock < item.quantity:
            return jsonify({'error': f'{item.product.name} has only {item.product.stock} left in stock'}), 400

    # Recompute shipping server-side instead of trusting the client-submitted rate
    # (the checkout UI only ever offers the cheapest computed option, so this is
    # the same value a legitimate request would have sent).
    total_weight_kg = 0.0
    for item in cart_items:
        price = float(item.product.price) if item.product.price else 0
        item_weight = 0.06 if price <= 45 else 0.11
        total_weight_kg += item_weight * item.quantity

    delivery_mode = get_setting('delivery_mode', 'hybrid')
    if delivery_mode == 'manual':
        shipping_rate = 40
    else:
        computed_rates = nimbus_api.get_shipping_rates(pincode, total_weight_kg)
        shipping_rate = computed_rates[0]['rate'] if computed_rates else 60

    # Calculate totals
    subtotal_rupees = sum(int(float(item.product.price)) * item.quantity for item in cart_items)

    # Re-validate any coupon server-side -- never trust a client-computed
    # discount amount, same principle already applied to the shipping rate.
    coupon = None
    discount_rupees = 0
    coupon_claimed = False
    coupon_code = (data.get('coupon_code') or '').strip().upper()
    if coupon_code:
        coupon = Coupon.query.filter_by(code=coupon_code).first()
        if not coupon or not coupon.is_valid_for(user['email']):
            return jsonify({'error': 'This coupon is invalid, expired, already used, or not valid for your account.'}), 400

        # Atomically claim the coupon right now rather than only marking it
        # used at payment-verify time. Prevents two concurrent checkout
        # attempts with the same coupon both succeeding (a real race
        # before this fix -- coupon.used was only set much later). The
        # conditional UPDATE + rowcount check is the atomic part: if
        # another request claimed it between our validity check and here,
        # rowcount is 0 and we reject cleanly instead of double-applying
        # the discount. Released back below if order creation fails for
        # any other reason (e.g. Razorpay API error) so a coupon is never
        # burned by something unrelated to the customer actually paying.
        claim_rowcount = Coupon.query.filter_by(id=coupon.id, used=False).update({
            'used': True, 'used_at': datetime.utcnow()
        })
        db.session.commit()
        if claim_rowcount == 0:
            return jsonify({'error': 'This coupon was just used. Please try a different code.'}), 400
        coupon_claimed = True

        discount_rupees = coupon.compute_discount(subtotal_rupees)

    # Points are capped against what's left AFTER the coupon discount, so
    # the two combined can never exceed the subtotal.
    remaining_after_coupon = max(0, subtotal_rupees - discount_rupees)
    used_points, points_discount_rupees = 0, 0
    requested_points = data.get('points_to_redeem', 0)
    try:
        requested_points = int(requested_points)
    except (TypeError, ValueError):
        requested_points = 0
    if requested_points > 0 and get_setting('referral_enabled', 'true') == 'true':
        balance = get_points_balance(user['email'])
        used_points, points_discount_rupees = compute_points_redemption(requested_points, remaining_after_coupon, balance)

    subtotal_paise = subtotal_rupees * 100
    discount_paise = discount_rupees * 100
    points_discount_paise = points_discount_rupees * 100
    shipping_paise = int(shipping_rate) * 100
    total_paise = subtotal_paise - discount_paise - points_discount_paise + shipping_paise

    if not razorpay_client:
        if coupon_claimed:
            Coupon.query.filter_by(id=coupon.id).update({'used': False, 'used_at': None})
            db.session.commit()
        return jsonify({'error': 'Payment system not configured'}), 500

    try:
        # Create Razorpay order
        order_number = f'HS-{uuid.uuid4().hex[:8].upper()}'
        rzp_order = razorpay_client.order.create({
            'amount': total_paise,
            'currency': 'INR',
            'receipt': order_number,
            'payment_capture': 1
        })

        # Save order
        new_order = SpiceOrder(
            order_number=order_number,
            user_email=user['email'],
            full_name=full_name,
            phone=phone,
            address=address,
            city=city,
            state=state,
            pincode=pincode,
            subtotal=subtotal_paise,
            shipping_cost=shipping_paise,
            total_amount=total_paise,
            coupon_id=coupon.id if coupon else None,
            discount_amount=discount_paise,
            points_redeemed=used_points,
            points_discount_amount=points_discount_paise,
            razorpay_order_id=rzp_order['id']
        )
        db.session.add(new_order)
        db.session.flush()  # Get new_order.id
        
        # Save order items (snapshot)
        for item in cart_items:
            order_item = OrderItem(
                order_id=new_order.id,
                product_id=item.product_id,
                product_name=item.product.name,
                quantity=item.quantity,
                unit_price=int(float(item.product.price)) * 100
            )
            db.session.add(order_item)
        
        db.session.commit()
        
        return jsonify({
            'order_id': rzp_order['id'],
            'order_number': order_number,
            'amount': total_paise,
            'currency': 'INR',
            'key': RAZORPAY_KEY_ID
        })
        
    except Exception as e:
        print("Checkout order creation error:", e)
        db.session.rollback()
        if coupon_claimed:
            # The coupon claim above was already committed as its own
            # transaction, so this rollback doesn't touch it -- release it
            # explicitly so a Razorpay API error or similar doesn't
            # permanently burn the customer's coupon for something that
            # wasn't their fault.
            Coupon.query.filter_by(id=coupon.id).update({'used': False, 'used_at': None})
            db.session.commit()
        return jsonify({'error': str(e)}), 500

def finalize_paid_order(razorpay_order_id, razorpay_payment_id):
    """Mark an order paid and apply its side effects -- safe to call more than once.

    Called from the browser callback, the Razorpay webhook, and the admin sync
    tool, any of which can fire for the same payment (a retry, a double click,
    or callback + webhook both arriving). Only the first call changes anything;
    later calls return (order, False) without touching stock/points again.
    Returns (order, newly_paid) or (None, False) if no such order exists.
    """
    order = (SpiceOrder.query.filter_by(razorpay_order_id=razorpay_order_id)
             .with_for_update().first())
    if not order:
        return None, False
    if order.payment_status == 'paid':
        db.session.rollback()  # release the row lock
        return order, False

    order.payment_status = 'paid'
    order.razorpay_payment_id = razorpay_payment_id

    # Points are only actually deducted on confirmed payment, never on an
    # abandoned checkout. Re-check the LIVE balance right before writing the
    # debit and clamp to it so the balance can never go negative, even if two
    # concurrent unpaid orders both redeemed against the same balance.
    if order.points_redeemed:
        live_balance = get_points_balance(order.user_email)
        actual_debit = min(order.points_redeemed, live_balance)
        if actual_debit > 0:
            db.session.add(PointsTransaction(
                user_email=order.user_email, points=-actual_debit,
                reason='Redeemed at checkout', order_id=order.id
            ))

    # Decrement stock atomically in the database (a read-modify-write in
    # Python could oversell when two orders are confirmed at the same moment).
    oversold = []
    for item in order.items:
        product = Product.query.get(item.product_id)
        if product is None or product.stock is None:
            continue
        if product.stock < item.quantity:
            oversold.append(f"{product.name} (had {product.stock}, ordered {item.quantity})")
        Product.query.filter(Product.id == item.product_id, Product.stock.isnot(None)).update(
            {'stock': db.case((Product.stock - item.quantity > 0, Product.stock - item.quantity), else_=0)},
            synchronize_session=False
        )

    CartItem.query.filter_by(user_email=order.user_email).delete()
    db.session.commit()

    # Everything below runs only after the payment is safely committed, and a
    # failure in any of it must never undo or fail a successful payment.
    try:
        process_referral_reward(order.user_email, order.id)
    except Exception as e:
        print("Referral reward processing error (order still paid successfully):", e)

    try:
        item_text = ", ".join([f"{i.quantity}x {i.product_name}" for i in order.items])
        msg = (f"🚨 <b>NEW SPICE ORDER!</b>\n\n<b>Order:</b> {order.order_number}\n"
               f"<b>Customer:</b> {order.full_name}\n<b>Amount:</b> ₹{order.total_amount / 100}\n"
               f"<b>Items:</b> {item_text}")
        if oversold:
            msg += "\n\n⚠️ <b>Oversold:</b> " + "; ".join(oversold)
        send_telegram_notification(msg)
    except Exception as e:
        print("Telegram notification error (order still paid successfully):", e)

    notify_customer('confirmed', order)
    return order, True


@app.route('/checkout/verify-payment', methods=['POST'])
def verify_checkout_payment():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json or {}
    razorpay_payment_id = data.get('razorpay_payment_id')
    razorpay_order_id = data.get('razorpay_order_id')
    razorpay_signature = data.get('razorpay_signature')

    if not all([razorpay_payment_id, razorpay_order_id, razorpay_signature]):
        return jsonify({'error': 'Missing payment details'}), 400

    try:
        razorpay_client.utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature
        })

        existing = SpiceOrder.query.filter_by(razorpay_order_id=razorpay_order_id).first()
        if not existing:
            return jsonify({'error': 'Order not found'}), 404
        if existing.user_email != user['email'] and not is_admin_user(user):
            return jsonify({'error': 'Order not found'}), 404

        order, _newly_paid = finalize_paid_order(razorpay_order_id, razorpay_payment_id)
        return jsonify({'success': True, 'message': 'Payment successful!', 'order_number': order.order_number})

    except razorpay.errors.SignatureVerificationError:
        return jsonify({'error': 'Invalid payment signature'}), 400
    except Exception as e:
        db.session.rollback()
        print("Checkout payment verification error:", e)
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/razorpay/webhook', methods=['POST'])
@csrf.exempt
def razorpay_webhook():
    """Server-to-server payment confirmation, independent of the customer's browser.

    Without this, a customer who pays but closes the tab (or whose UPI app
    switch drops the network) before the browser callback fires is charged
    while the order stays 'pending'. Set RAZORPAY_WEBHOOK_SECRET and register
    https://<your-domain>/api/razorpay/webhook in the Razorpay dashboard
    (events: payment.captured, order.paid).
    """
    secret = os.getenv('RAZORPAY_WEBHOOK_SECRET')
    signature = request.headers.get('X-Razorpay-Signature', '')
    if not secret or not signature:
        return jsonify({'error': 'Unauthorized'}), 403

    body = request.get_data()
    expected = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return jsonify({'error': 'Unauthorized'}), 403

    event = request.get_json(silent=True) or {}
    if event.get('event') in ('payment.captured', 'order.paid'):
        payload = event.get('payload') or {}
        payment = (payload.get('payment') or {}).get('entity') or {}
        rz_order_id = payment.get('order_id') or ((payload.get('order') or {}).get('entity') or {}).get('id')
        rz_payment_id = payment.get('id')
        if rz_order_id and rz_payment_id:
            order = SpiceOrder.query.filter_by(razorpay_order_id=rz_order_id).first()
            # Only trust the event if the amount actually paid matches the order.
            if order and payment.get('amount') == order.total_amount:
                finalize_paid_order(rz_order_id, rz_payment_id)
            elif order:
                print(f"Razorpay webhook amount mismatch for {order.order_number}: "
                      f"paid {payment.get('amount')} vs expected {order.total_amount}")
    return jsonify({'success': True}), 200


@app.route('/admin/orders/sync-payments', methods=['POST'])
@admin_required
def sync_pending_payments():
    """Ask Razorpay about recent unpaid orders and confirm any that were actually paid."""
    if not razorpay_client:
        flash("Razorpay is not configured.", "danger")
        return redirect('/admin/orders')

    cutoff = datetime.utcnow() - timedelta(days=7)
    pending = (SpiceOrder.query.filter(SpiceOrder.payment_status == 'pending',
                                       SpiceOrder.created_at >= cutoff,
                                       SpiceOrder.razorpay_order_id.isnot(None))
               .order_by(SpiceOrder.created_at.desc()).limit(50).all())
    recovered, checked = [], 0
    for o in pending:
        checked += 1
        try:
            payments = razorpay_client.order.payments(o.razorpay_order_id).get('items', [])
        except Exception as e:
            print(f"Razorpay sync error for {o.order_number}: {e}")
            continue
        paid = next((p for p in payments if p.get('status') == 'captured' and p.get('amount') == o.total_amount), None)
        if paid:
            finalize_paid_order(o.razorpay_order_id, paid['id'])
            recovered.append(o.order_number)

    if recovered:
        flash(f"Recovered {len(recovered)} paid order(s): {', '.join(recovered)}", "success")
    else:
        flash(f"Checked {checked} unpaid order(s) with Razorpay -- none were actually paid.", "info")
    return redirect('/admin/orders')

@app.route('/orders')
def my_orders():
    user = session.get('user')
    if not user:
        flash("Please login to view your orders.", "warning")
        return redirect('/login')
    
    orders = SpiceOrder.query.filter_by(user_email=user['email']).options(joinedload(SpiceOrder.items)).order_by(SpiceOrder.created_at.desc()).all()
    return render_template('my_orders.html', orders=orders, user=user, is_logged_in=True)

@app.route('/my-coupons')
def my_coupons():
    user = session.get('user')
    if not user:
        flash("Please login to view your coupons.", "warning")
        return redirect('/login')

    coupons = Coupon.query.filter_by(user_email=user['email']).order_by(Coupon.created_at.desc()).all()
    return render_template('my_coupons.html', coupons=coupons, user=user, is_logged_in=True, now=datetime.utcnow())

@app.route('/refer')
def refer_and_earn():
    user = session.get('user')
    if not user:
        flash("Please login to get your referral link.", "warning")
        return redirect('/login')

    code = get_or_create_referral_code(user['email'])
    referral_link = url_for('index', _external=True, ref=code) if code else None

    referrals = Referral.query.filter_by(referrer_email=user['email']).order_by(Referral.signup_at.desc()).all()
    points_balance = get_points_balance(user['email'])
    ledger = PointsTransaction.query.filter_by(user_email=user['email']).order_by(PointsTransaction.id.desc()).limit(20).all()

    return render_template('refer_and_earn.html', user=user, is_logged_in=True,
                           referral_link=referral_link, referrals=referrals, points_balance=points_balance, ledger=ledger,
                           reward_amount=get_setting('referral_reward_amount', '50'),
                           points_awarded=get_setting('referral_points_awarded', '50'),
                           referral_enabled=get_setting('referral_enabled', 'true') == 'true')

@app.route('/orders/<int:order_id>/track')
def track_order(order_id):
    user = session.get('user')
    if not user:
        flash("Please login to track your order.", "warning")
        return redirect('/login')
    
    # The admin can view tracking for any order (e.g. to check status while
    # helping a customer); everyone else can only see their own order.
    if is_admin_user(user):
        order = SpiceOrder.query.filter_by(id=order_id).first_or_404()
    else:
        order = SpiceOrder.query.filter_by(id=order_id, user_email=user['email']).first_or_404()

    # Get tracking from NimbusPost
    tracking = {'current_status': order.shipping_status, 'history': [], 'estimated_delivery': order.estimated_delivery or 'N/A'}
    if order.awb_number:
        tracking = nimbus_api.track_shipment(order.awb_number)
    
    return render_template('order_tracking.html', order=order, tracking=tracking,
                           user=user, is_logged_in=True)

@app.route('/orders/<int:order_id>/receipt')
def order_receipt(order_id):
    user = session.get('user')
    if not user:
        flash("Please login to view your receipt.", "warning")
        return redirect('/login')

    if is_admin_user(user):
        order = SpiceOrder.query.filter_by(id=order_id).first_or_404()
    else:
        order = SpiceOrder.query.filter_by(id=order_id, user_email=user['email']).first_or_404()

    return render_template('order_receipt.html', order=order, user=user, is_logged_in=True)

@app.route('/orders/<int:order_id>/reorder', methods=['POST'])
def reorder(order_id):
    user = session.get('user')
    if not user:
        flash("Please login to reorder.", "warning")
        return redirect('/login')

    order = SpiceOrder.query.filter_by(id=order_id, user_email=user['email']).first_or_404()

    added, skipped = 0, []
    for item in order.items:
        product = Product.query.get(item.product_id)
        if not product:
            skipped.append(item.product_name)
            continue
        if product.stock is not None and product.stock < 2:
            skipped.append(product.name)
            continue

        qty = max(2, item.quantity)
        existing = CartItem.query.filter_by(user_email=user['email'], product_id=product.id).first()
        if existing:
            existing.quantity = min(5, existing.quantity + qty)
        else:
            db.session.add(CartItem(user_email=user['email'], product_id=product.id, quantity=min(5, qty)))
        added += 1

    db.session.commit()

    if added:
        flash(f"Added {added} item{'s' if added != 1 else ''} from this order to your cart." +
              (f" ({', '.join(skipped)} unavailable right now.)" if skipped else ''), 'success')
    else:
        flash("None of the items from this order are available right now.", 'warning')

    return redirect('/cart')


# -------------------------
# ⚙️ Admin Store Settings
# -------------------------

@app.route('/admin/store-settings', methods=['GET', 'POST'])
@admin_required
def admin_store_settings():
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'toggle_store':
            current = get_setting('store_open', 'true')
            set_setting('store_open', 'false' if current == 'true' else 'true')
            flash("Store status updated!", "success")
        
        elif action == 'toggle_science_hub':
            digital = 'true' if request.form.get('science_hub_digital_enabled') else 'false'
            physical = 'true' if request.form.get('science_hub_physical_enabled') else 'false'
            set_setting('science_hub_digital_enabled', digital)
            set_setting('science_hub_physical_enabled', physical)
            flash("Science Hub settings updated!", "success")
        
        elif action == 'update_product':
            product_id = request.form.get('product_id')
            new_stock = request.form.get('stock', type=int)
            new_price = request.form.get('price', type=float)
            product = Product.query.get(product_id)
            if product and new_stock is not None:
                product.stock = new_stock
            if product and new_price is not None:
                product.price = new_price
            if product:
                db.session.commit()
                flash(f"Stock and price updated for {product.name}", "success")
        
        elif action == 'update_notice':
            notice = request.form.get('shipping_notice', '')
            set_setting('shipping_notice', notice)
            flash("Shipping notice updated!", "success")

        elif action == 'update_welcome_coupon':
            enabled = 'true' if request.form.get('welcome_coupon_enabled') else 'false'
            discount_type = request.form.get('welcome_coupon_type', 'percent')
            discount_value = request.form.get('welcome_coupon_value', type=float) or 10
            max_discount = request.form.get('welcome_coupon_max', type=int)
            expiry_days = request.form.get('welcome_coupon_expiry_days', type=int) or 30

            if discount_type not in ('percent', 'flat') or discount_value <= 0:
                flash("Please enter a valid discount type and value.", "danger")
                return redirect('/admin/store-settings')

            if max_discount is not None and max_discount <= 0:
                flash("Max discount must be a positive amount, or left blank for no cap.", "danger")
                return redirect('/admin/store-settings')

            set_setting('welcome_coupon_enabled', enabled)
            set_setting('welcome_coupon_type', discount_type)
            set_setting('welcome_coupon_value', str(discount_value))
            set_setting('welcome_coupon_max', str(max_discount) if max_discount else '')
            set_setting('welcome_coupon_expiry_days', str(expiry_days))
            flash("First-login coupon settings updated!", "success")

        elif action == 'update_referral':
            enabled = 'true' if request.form.get('referral_enabled') else 'false'
            reward_amount = request.form.get('referral_reward_amount', type=float) or 50
            reward_expiry_days = request.form.get('referral_reward_expiry_days', type=int) or 30
            points_awarded = request.form.get('referral_points_awarded', type=int) or 50
            points_per_rupee = request.form.get('points_per_rupee', type=float) or 10
            max_redeem_percent = request.form.get('max_points_redeem_percent', type=float) or 50
            max_per_referrer = request.form.get('referral_max_per_referrer', type=int)
            min_order_value = request.form.get('referral_min_order_value', type=float)

            if reward_amount <= 0 or points_awarded <= 0 or points_per_rupee <= 0 or not (0 < max_redeem_percent <= 100):
                flash("Please enter valid, positive values for the referral reward settings.", "danger")
                return redirect('/admin/store-settings')

            set_setting('referral_enabled', enabled)
            set_setting('referral_reward_amount', str(reward_amount))
            set_setting('referral_reward_expiry_days', str(reward_expiry_days))
            set_setting('referral_points_awarded', str(points_awarded))
            set_setting('points_per_rupee', str(points_per_rupee))
            set_setting('max_points_redeem_percent', str(max_redeem_percent))
            set_setting('referral_max_per_referrer', str(max_per_referrer) if max_per_referrer else '')
            set_setting('referral_min_order_value', str(min_order_value) if min_order_value else '')
            flash("Refer & Earn settings updated!", "success")

        return redirect('/admin/store-settings')

    settings = {
        'store_open': get_setting('store_open', 'true'),
        'science_hub_digital_enabled': get_setting('science_hub_digital_enabled', 'true'),
        'science_hub_physical_enabled': get_setting('science_hub_physical_enabled', 'true'),
        'shipping_notice': get_setting('shipping_notice', ''),
        'welcome_coupon_enabled': get_setting('welcome_coupon_enabled', 'true'),
        'welcome_coupon_type': get_setting('welcome_coupon_type', 'percent'),
        'welcome_coupon_value': get_setting('welcome_coupon_value', '10'),
        'welcome_coupon_max': get_setting('welcome_coupon_max', '100'),
        'welcome_coupon_expiry_days': get_setting('welcome_coupon_expiry_days', '30'),
        'referral_enabled': get_setting('referral_enabled', 'true'),
        'referral_reward_amount': get_setting('referral_reward_amount', '50'),
        'referral_reward_expiry_days': get_setting('referral_reward_expiry_days', '30'),
        'referral_points_awarded': get_setting('referral_points_awarded', '50'),
        'points_per_rupee': get_setting('points_per_rupee', '10'),
        'max_points_redeem_percent': get_setting('max_points_redeem_percent', '50'),
        'referral_max_per_referrer': get_setting('referral_max_per_referrer', ''),
        'referral_min_order_value': get_setting('referral_min_order_value', '')
    }
    products = Product.query.all()
    
    return render_template('admin_store_settings.html', settings=settings, products=products,
                           user=session.get('user'), is_logged_in=True)


# -------------------------
# 🚚 Admin Delivery Zones
# -------------------------

@app.route('/admin/delivery-zones', methods=['GET', 'POST'])
@admin_required
def admin_delivery_zones():
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'set_mode':
            mode = request.form.get('delivery_mode', 'hybrid')
            set_setting('delivery_mode', mode)
            flash("Delivery mode updated!", "success")
        
        elif action == 'add_blacklist':
            pincode = request.form.get('pincode', '').strip()
            city = request.form.get('city', '').strip()
            reason = request.form.get('reason', '').strip()
            if pincode and not BlacklistedPincode.query.filter_by(pincode=pincode).first():
                bp = BlacklistedPincode(pincode=pincode, city=city, reason=reason)
                db.session.add(bp)
                db.session.commit()
                flash(f"Pincode {pincode} blacklisted", "success")
            else:
                flash("Pincode already blacklisted or invalid", "warning")
        
        elif action == 'remove_blacklist':
            bp_id = request.form.get('id')
            bp = BlacklistedPincode.query.get(bp_id)
            if bp:
                db.session.delete(bp)
                db.session.commit()
                flash("Pincode removed from blacklist", "success")
        
        return redirect('/admin/delivery-zones')
    
    delivery_mode = get_setting('delivery_mode', 'hybrid')
    blacklisted = BlacklistedPincode.query.all()
    
    return render_template('admin_delivery_zones.html', delivery_mode=delivery_mode,
                           blacklisted=blacklisted, user=session.get('user'), is_logged_in=True)


# -------------------------
# 📋 Admin Orders
# -------------------------

@app.route('/admin/orders')
@admin_required
def admin_orders():
    page = request.args.get('page', 1, type=int)
    pagination = (SpiceOrder.query.options(joinedload(SpiceOrder.items))
                  .order_by(SpiceOrder.created_at.desc())
                  .paginate(page=page, per_page=25, error_out=False))

    # Totals come from one aggregate query over the whole table, so they stay correct
    # while the list below only loads one page of orders.
    is_paid = SpiceOrder.payment_status == 'paid'
    total, revenue, pending_shipments, delivered = db.session.query(
        db.func.count(SpiceOrder.id),
        db.func.coalesce(db.func.sum(db.case((is_paid, SpiceOrder.total_amount), else_=0)), 0),
        db.func.coalesce(db.func.sum(db.case(((is_paid) & (SpiceOrder.shipping_status == 'processing'), 1), else_=0)), 0),
        db.func.coalesce(db.func.sum(db.case((SpiceOrder.shipping_status == 'delivered', 1), else_=0)), 0),
    ).one()
    stats = {
        'total_orders': total,
        'total_revenue': int(revenue) // 100,
        'pending_shipments': int(pending_shipments),
        'delivered': int(delivered),
    }

    # Orders still unpaid after 30 minutes -- most are abandoned checkouts, but a
    # customer whose browser closed mid-payment can be charged while the order
    # stays here, so surface them and offer a one-click check against Razorpay.
    now = datetime.utcnow()
    stale_pending = (SpiceOrder.query
                     .filter(SpiceOrder.payment_status == 'pending',
                             SpiceOrder.created_at < now - timedelta(minutes=30),
                             SpiceOrder.created_at > now - timedelta(days=7))
                     .order_by(SpiceOrder.created_at.desc()).limit(50).all())

    return render_template('admin_orders.html', orders=pagination.items, pagination=pagination, stats=stats,
                           stale_pending=stale_pending, user=session.get('user'), is_logged_in=True)

@app.route('/admin/orders/ship/<int:order_id>', methods=['POST'])
@admin_required
def ship_order(order_id):
    order = SpiceOrder.query.get_or_404(order_id)
    warehouse = request.form.get('warehouse')
    
    if order.payment_status != 'paid':
        flash("Cannot ship unpaid order.", "danger")
        return redirect('/admin/orders')
    
    if order.shipping_status != 'processing':
        flash("Order already shipped or cancelled.", "warning")
        return redirect('/admin/orders')
    
    total_weight_kg = 0.0
    for item in order.items:
        if item.unit_price <= 4500: # paise
            item_weight = 0.06
        else:
            item_weight = 0.11
        total_weight_kg += item_weight * item.quantity

    # Push to NimbusPost
    shipment_data = {
        'order_number': order.order_number,
        'consignee': {
            'name': order.full_name,
            'address': order.address,
            'city': order.city,
            'state': order.state,
            'pincode': order.pincode,
            'phone': order.phone
        },
        'items': [{'name': item.product_name, 'quantity': item.quantity, 'price': item.unit_price // 100}
                  for item in order.items],
        'total_amount': order.total_amount // 100,
        'weight_kg': total_weight_kg
    }
    if warehouse:
        shipment_data['pickup_location'] = warehouse
    if order.nimbus_order_id:
        shipment_data['nimbus_order_id'] = order.nimbus_order_id  # re-book, don't duplicate
    
    result = nimbus_api.create_shipment(shipment_data)

    if result['success']:
        order.awb_number = result.get('awb_number')
        order.courier_name = result.get('courier_name', 'NimbusPost')
        order.shipping_status = 'shipped'
        order.estimated_delivery = result.get('estimated_delivery', '')
        order.label_url = result.get('label_url')
        order.nimbus_order_id = result.get('nimbus_order_id')
        db.session.commit()
        flash(f"Order {order.order_number} shipped! AWB: {order.awb_number}", "success")
        notify_customer('shipped', order)
    elif not nimbus_api.is_configured():
        # NimbusPost isn't set up at all -- this is an intentional manual-shipping
        # fallback, not a failure, so it's fine to mark it shipped here.
        order.shipping_status = 'shipped'
        order.courier_name = 'Manual'
        db.session.commit()
        flash(f"Order {order.order_number} marked as shipped manually (NimbusPost is not configured).", "warning")
    else:
        # NimbusPost IS configured but the booking actually failed -- do NOT
        # mark this as shipped. No courier was arranged, and doing so would
        # both mislead the customer (their tracking page would say "shipped"
        # when nothing was picked up) and hide the failure from the admin
        # (the Ship button disappears once shipping_status is 'shipped',
        # so there'd be no way to notice and retry).
        db.session.rollback()
        if result.get('nimbus_order_id'):
            # The courier order exists but wasn't booked; remember it so "Ship" retries the booking.
            order.nimbus_order_id = result['nimbus_order_id']
            db.session.commit()
        flash(f"Shipping failed for order {order.order_number} -- order NOT marked as shipped, please fix and retry. NimbusPost said: {result.get('message', 'Unknown error')}", "danger")

    return redirect('/admin/orders')

@app.route('/admin/orders/fetch-label/<int:order_id>', methods=['POST'])
@admin_required
def fetch_label(order_id):
    order = SpiceOrder.query.get_or_404(order_id)

    if order.shipping_status != 'shipped' or not order.nimbus_order_id:
        flash("No NimbusPost shipment to fetch a label for.", "warning")
        return redirect('/admin/orders')

    result = nimbus_api.generate_label(order.nimbus_order_id)
    if result['success'] and result.get('label_url'):
        order.label_url = result['label_url']
        db.session.commit()
        flash(f"Label fetched for order {order.order_number}.", "success")
    else:
        flash(f"Could not fetch label for order {order.order_number}. NimbusPost said: {result.get('message', 'Unknown error')}", "danger")

    return redirect('/admin/orders')

@app.route('/admin/test-email', methods=['POST'])
@admin_required
def admin_test_email():
    """Send a real test e-mail to the logged-in admin and show the exact result, so a wrong
    SMTP setting is obvious right away instead of silently failing on a customer's order."""
    if not notifications.is_configured():
        flash("E-mail is not set up yet. Add EMAIL_WEBHOOK_URL and EMAIL_WEBHOOK_SECRET in Render "
              "(Environment) -- or the SMTP_* settings on a paid Render plan -- then try again.", "warning")
        return redirect('/admin/dashboard')
    to = session['user']['email']
    try:
        notifications._send_now(
            to, "Heritage Spices test e-mail",
            "If you can read this, customer e-mails are working. Order confirmed, shipped and "
            "delivered e-mails will now be sent automatically.",
            "<p>If you can read this, <b>customer e-mails are working</b>. Order confirmed, shipped "
            "and delivered e-mails will now be sent automatically.</p>")
        flash(f"Test e-mail sent to {to}. Check your inbox (and spam folder).", "success")
    except Exception as e:
        flash(f"E-mail failed: {e}", "danger")
    return redirect('/admin/dashboard')

@app.route('/admin/orders/cancel/<int:order_id>', methods=['POST'])
@admin_required
def cancel_order(order_id):
    order = SpiceOrder.query.get_or_404(order_id)
    
    if order.shipping_status not in ['processing']:
        flash("Cannot cancel shipped orders.", "danger")
        return redirect('/admin/orders')
    
    # Cancel on NimbusPost if shipped
    if order.awb_number:
        nimbus_api.cancel_shipment(order.awb_number)
    
    # Restore stock
    for item in order.items:
        product = Product.query.get(item.product_id)
        if product and product.stock is not None:
            product.stock += item.quantity
    
    order.shipping_status = 'cancelled'
    db.session.commit()
    flash(f"Order {order.order_number} cancelled. Stock restored.", "success")
    return redirect('/admin/orders')

# NimbusPost Webhook
# Verification per https://api-v2.nimbuspost.com/docs/reference/v2 (Webhooks section):
# each delivery is signed with HMAC-SHA256 of the raw body in the x-nimbus-signature header.
def normalize_shipping_status(raw):
    """Map a courier's free-text status onto the small set our screens understand:
    shipped / out for delivery / delivered / rto / cancelled. Returns None if the
    text isn't recognised, so an unexpected status can never corrupt an order."""
    r = (raw or '').lower().replace('_', ' ').replace('-', ' ').strip()
    if not r:
        return None
    if 'cancel' in r:
        return 'cancelled'
    if 'rto' in r or 'return' in r:
        return 'rto'
    if 'out for delivery' in r:
        return 'out for delivery'
    if 'deliver' in r and not any(w in r for w in ('undeliver', 'not deliver', 'attempt', 'failed')):
        return 'delivered'
    if any(w in r for w in ('pickup', 'picked', 'booked', 'manifest', 'data received', 'transit',
                            'reached', 'shipped', 'dispatch', 'undeliver', 'ndr', 'attempt')):
        return 'shipped'
    return None


@app.route('/api/nimbus/webhook', methods=['POST'])
@csrf.exempt
def nimbus_webhook():
    webhook_secret = os.getenv('NIMBUS_WEBHOOK_SECRET')
    signature = request.headers.get('x-nimbus-signature', '')
    if not webhook_secret or not signature:
        return jsonify({'error': 'Unauthorized'}), 403

    expected_signature = hmac.new(
        webhook_secret.encode('utf-8'), request.get_data(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json
    if not data:
        return jsonify({'error': 'No data'}), 400

    awb = data.get('awb_number') or data.get('awb') or data.get('awbNumber')
    status = data.get('current_status') or data.get('status')
    # Log only what's needed to debug -- the full payload contains customer PII.
    print(f"NimbusPost webhook: awb={awb} status={status}")

    if awb and status:
        order = SpiceOrder.query.filter_by(awb_number=awb).first()
        new_status = normalize_shipping_status(status)
        # Ignore statuses we don't recognise, and never move a finished order backwards.
        if order and new_status and order.shipping_status not in ('delivered', 'cancelled'):
            order.shipping_status = new_status
            db.session.commit()
            if new_status == 'delivered':
                notify_customer('delivered', order)

    return jsonify({'success': True}), 200


@app.route('/admin/test-nimbus-login')
@admin_required
def test_nimbus_login():
    import os, requests
    email = os.getenv('NIMBUS_EMAIL')
    password = os.getenv('NIMBUS_PASSWORD')
    if not email or not password:
        return "NIMBUS_EMAIL or NIMBUS_PASSWORD not set in environment."
    try:
        url = 'https://api.nimbuspost.com/v1/users/login'
        payload = {'email': email, 'password': password}
        resp = requests.post(url, json=payload, timeout=10)
        # Don't echo the raw response body -- it can contain a live auth
        # token. Just confirm whether login succeeded.
        body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
        return jsonify({
            'status_code': resp.status_code,
            'login_ok': bool(resp.status_code == 200 and body.get('status')),
        })
    except Exception as e:
        print("Nimbus login test error:", e)
        return jsonify({'error': 'Request failed, see server logs.'}), 500

# -------------------------
# Update existing routes
# -------------------------

# Inject cart count into all templates
@app.context_processor
def inject_cart_count():
    user = session.get('user')
    if user:
        count = CartItem.query.filter_by(user_email=user['email']).count()
        return {'cart_count': count}
    return {'cart_count': 0}


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500

# --- Product Review Routes ---
@app.route('/product/<int:product_id>/review', methods=['POST'])
def submit_review(product_id):
    user = session.get('user')
    if not user:
        return jsonify({'success': False, 'message': 'You must be logged in to submit a review.'}), 401

    if _redeem_rate_limited(f"review:{user['email']}"):
        return jsonify({'success': False, 'message': 'Too many reviews submitted recently. Please try again later.'}), 429

    product = Product.query.get_or_404(product_id)
    rating = int(request.form.get('rating', 5))
    review_text = request.form.get('review_text', '').strip()
    
    if not review_text:
        return jsonify({'success': False, 'message': 'Review text is required.'}), 400
        
    new_review = ProductReview(
        product_id=product.id,
        user_name=user.get('name', 'Customer'),
        user_email=user.get('email', ''),
        rating=rating,
        review_text=review_text,
        status='pending'
    )
    db.session.add(new_review)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Review submitted successfully! It will appear once approved.'})

@app.route('/admin/reviews')
@admin_required
def admin_reviews():
    reviews = ProductReview.query.order_by(
        db.case({
            'pending': 1,
            'approved': 2,
            'rejected': 3
        }, value=ProductReview.status),
        ProductReview.created_at.desc()
    ).all()
    return render_template('admin_reviews.html', reviews=reviews)

@app.route('/admin/reviews/<int:review_id>/approve', methods=['POST'])
@admin_required
def approve_review(review_id):
    review = ProductReview.query.get_or_404(review_id)
    review.status = 'approved'
    db.session.commit()
    flash('Review approved.', 'success')
    return redirect(url_for('admin_reviews'))

@app.route('/admin/reviews/<int:review_id>/reject', methods=['POST'])
@admin_required
def reject_review(review_id):
    review = ProductReview.query.get_or_404(review_id)
    review.status = 'rejected'
    db.session.commit()
    flash('Review rejected.', 'warning')
    return redirect(url_for('admin_reviews'))

@app.route('/admin/reviews/<int:review_id>/delete', methods=['POST'])
@admin_required
def delete_review(review_id):
    review = ProductReview.query.get_or_404(review_id)
    db.session.delete(review)
    db.session.commit()
    flash('Review deleted.', 'danger')
    return redirect(url_for('admin_reviews'))

# -------------------------
# 🚀 Run App
# -------------------------
# --- TEMP: Create DB tables if missing ---
with app.app_context():
    db.create_all()
    print("Database tables created/verified!")

if __name__ == '__main__':
    app.run(debug=True)
