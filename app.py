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
import io
import qrcode
from flask import send_file

# -------------------------
# 🔐 Load environment
# -------------------------
load_dotenv()

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function





app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv("FLASK_SECRET_KEY") or "default-fallback-secret-key-12345"
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
csrf = CSRFProtect(app)

# --- SQLAlchemy Setup ---
db_url = os.getenv('DATABASE_URL')
if not db_url:
    db_url = "sqlite:///:memory:"
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
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
    user_email = db.Column(db.String(200), nullable=False)
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

# --- Cart Item ---
class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(200), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    product = db.relationship('Product', backref='cart_items')

# --- Spice Order ---
class SpiceOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), unique=True, nullable=False)
    user_email = db.Column(db.String(200), nullable=False)
    
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
    
    # Payment (Razorpay only, no COD)
    razorpay_order_id = db.Column(db.String(100))
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
def get_setting(key, default=''):
    """Get a site setting value by key"""
    setting = SiteSetting.query.filter_by(key=key).first()
    return setting.value if setting else default

def set_setting(key, value):
    """Set a site setting value"""
    setting = SiteSetting.query.filter_by(key=key).first()
    if setting:
        setting.value = str(value)
    else:
        setting = SiteSetting(key=key, value=str(value))
        db.session.add(setting)
    db.session.commit()

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

def track_visit(user=None):
    """Log each visit to database"""
    visit = Visit(
        timestamp=datetime.utcnow(),
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
        email=user['email'] if user else 'Guest'
    )
    db.session.add(visit)
    db.session.commit()

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
    return render_template("index.html", user=user, is_logged_in=is_logged_in, products=products)

# ✅ Login via Google
@app.route('/login')
def login():
    redirect_uri = url_for('auth', _external=True)
    return google.authorize_redirect(redirect_uri)

# ✅ OAuth Callback
@app.route('/auth')
def auth():
    try:
        token = google.authorize_access_token()
        user_info = google.parse_id_token(token, nonce=token.get('nonce'))

        session['user'] = {
            'name': user_info['name'],
            'email': user_info['email'],
            'picture': user_info.get('picture')
        }

        is_new_user = save_user(user_info)
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

        return redirect('/')
    except Exception as e:
        print("OAuth error:", e)
        return "OAuth Failed", 500

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

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        message = request.form.get('message')
        if name and email and message:
            new_msg = ContactMessage(name=name, email=email, message=message)
            db.session.add(new_msg)
            db.session.commit()
        flash('Thank you for contacting us! We will get back to you soon.', 'success')
        return redirect('/contact')
    return render_template('contact.html')

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

@app.route('/robots.txt')
def robots():
    return (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Allow: /\n"
        "Sitemap: https://www.indianheritagespices.com/sitemap.xml\n",
        200,
        {'Content-Type': 'text/plain'}
    )

@app.route('/sitemap.xml')
def sitemap():
    base = "https://www.indianheritagespices.com"
    static_urls = ['/', '/about', '/contact', '/privacy', '/blog', '/products']
    
    blogs = get_all_blogs()
    blog_urls = [f"/blog/{b.slug}" for b in blogs]

    sitemap_xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap_xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    for url in static_urls + blog_urls:
        sitemap_xml += f"  <url><loc>{base}{url}</loc></url>\n"

    sitemap_xml += '</urlset>'
    return sitemap_xml, 200, {'Content-Type': 'application/xml'}


@app.route("/subscribe", methods=["POST"])
def subscribe():
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
@app.route("/blog/<slug>")
def blog_detail(slug):
    post = get_blog_by_slug(slug)
    user = session.get('user')
    is_logged_in = bool(user)
    admin_password = os.getenv("ADMIN_PASSWORD")

    if post:
        all_blogs = get_all_blogs()
        recent_posts = [b for b in all_blogs if b.id != post.id][:3]
        return render_template("blog_detail.html", post=post, recent_posts=recent_posts, user=user, is_logged_in=is_logged_in, admin_password=admin_password)
    else:
        return "Post not found", 404

# --- Admin Add Blog Route ---
@app.route('/admin/blogs', methods=['GET', 'POST'])
def manage_blogs():
    if session.get('user', {}).get('email') != 'heritage.spices.pvtltd@gmail.com':
        abort(403)

    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        category = request.form.get('category', 'General')
        slug = title.lower().replace(' ', '-').replace(',', '').replace('.', '')
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
    if session.get('user', {}).get('email') != 'heritage.spices.pvtltd@gmail.com':
        abort(403)
    password = request.form.get('password')
    if password != ADMIN_PASSWORD:
        abort(403)
    blog = get_blog_by_id(id)
    if blog:
        db.session.delete(blog)
        db.session.commit()
    return redirect('/blog')

# --- Admin Delete Product Route ---
@app.route('/admin/products/delete/<int:id>', methods=['POST'])
def delete_product(id):
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
        abort(403)
    product = Product.query.get_or_404(id)
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted!', 'success')
    return redirect('/products')


@app.route('/admin/blogs/edit/<int:blog_id>', methods=['GET', 'POST'])
def edit_blog(blog_id):
    user = session.get('user')
    if not user or user['email'] != 'heritage.spices.pvtltd@gmail.com':
        abort(403)
    blog = Blog.query.get_or_404(blog_id)
    if request.method == 'POST':
        blog.title = request.form['title']
        blog.content = request.form['content']
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
    total_revenue = sum(o.total_amount for o in order_q.filter_by(payment_status='paid').all()) // 100
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
        
        # Group data
        for d in date_list:
            d_start = datetime.combine(d, datetime.min.time())
            d_end = d_start + timedelta(days=1)
            
            day_rev = sum(o.total_amount for o in SpiceOrder.query.filter(SpiceOrder.created_at >= d_start, SpiceOrder.created_at < d_end, SpiceOrder.payment_status=='paid').all()) // 100
            day_orders = SpiceOrder.query.filter(SpiceOrder.created_at >= d_start, SpiceOrder.created_at < d_end).count()
            day_visits = Visit.query.filter(Visit.timestamp >= d_start, Visit.timestamp < d_end).count()
            
            chart_labels.append(d.strftime('%b %d'))
            chart_revenue.append(day_rev)
            chart_orders.append(day_orders)
            chart_visits.append(day_visits)
            
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
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
        abort(403)
    
    messages = ContactMessage.query.order_by(ContactMessage.timestamp.desc()).all()
    return render_template('admin_messages.html', messages=messages)

# ✅ Admin: Delete Contact Message
@app.route('/admin/messages/delete/<int:id>', methods=['POST'])
def delete_message(id):
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
        abort(403)
    
    msg = ContactMessage.query.get_or_404(id)
    db.session.delete(msg)
    db.session.commit()
    flash('Message deleted successfully.', 'success')
    return redirect('/admin/messages')

# ✅ Admin: View Wholesale Inquiries
@app.route('/admin/wholesale-inquiries')
def admin_wholesale_inquiries():
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
        abort(403)

    inquiries = WholesaleInquiry.query.order_by(WholesaleInquiry.timestamp.desc()).all()
    return render_template('admin_wholesale.html', inquiries=inquiries)

# ✅ Admin: Delete Wholesale Inquiry
@app.route('/admin/wholesale-inquiries/delete/<int:id>', methods=['POST'])
def delete_wholesale_inquiry(id):
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
        abort(403)

    inquiry = WholesaleInquiry.query.get_or_404(id)
    db.session.delete(inquiry)
    db.session.commit()
    flash('Wholesale inquiry deleted successfully.', 'success')
    return redirect('/admin/wholesale-inquiries')

# ✅ Admin: Who has items in their cart right now (for cart-abandoner outreach)
@app.route('/admin/carts')
def admin_carts():
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
        abort(403)

    items = CartItem.query.order_by(CartItem.added_at.desc()).all()
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
    return render_template('admin_carts.html', carts=carts)

# ✅ Admin: Coupon management
@app.route('/admin/coupons')
def admin_coupons():
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
        abort(403)

    coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
    welcome_coupons = [c for c in coupons if c.code.startswith('WELCOME')]
    welcome_issued = len(welcome_coupons)
    welcome_used = len([c for c in welcome_coupons if c.used])
    return render_template('admin_coupons.html', coupons=coupons, now=datetime.utcnow(),
                           welcome_issued=welcome_issued, welcome_used=welcome_used)

@app.route('/admin/coupons/create', methods=['GET', 'POST'])
def admin_create_coupon():
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
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
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
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
@app.route('/track-visit', methods=['POST'])
def track_frontend_visit():
    data = request.get_json()
    visit = Visit(
        timestamp=datetime.utcnow(),
        ip=request.remote_addr,
        user_agent=data.get('user_agent', request.headers.get('User-Agent')),
        email=data.get('email', 'Guest')
    )
    db.session.add(visit)
    db.session.commit()
    return '', 204

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

    return {
        'year': datetime.now().year,
        'is_logged_in': bool(user),
        'user': user,
        'welcome_offer_text': welcome_offer_text
    }


# NOTE: The duplicate home() route and dead product_detail() route with
# hardcoded data have been removed. The index() function at '/' handles
# the homepage, and /products handles the product listing.

# --- Products Page Route ---
@app.route('/products')
def products():
    products = Product.query.all()
    user = session.get('user')
    return render_template('products.html', products=products, user=user)

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
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
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
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
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
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
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
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
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

@app.route('/faq')
def faq():
    return render_template('faq.html')


# -------------------------
# 🔬 Science Hub Routes
# -------------------------

# --- Science Hub Content Configuration ---
SCIENCE_CONTENT = {
    'sci1': {'path': 'notes', 'start_page': 1, 'end_page': 148, 'total_pages': 148, 'title': 'SSC 10th - Science 1 Notes'},
    'sci2': {'path': 'notes', 'start_page': 149, 'end_page': 297, 'total_pages': 149, 'title': 'SSC 10th - Science 2 Notes'},
    'practice-sci1': {'path': 'notes/practice-sci1', 'start_page': 1, 'end_page': 0, 'total_pages': 0, 'title': 'Practice Papers - Science 1'},
    'practice-sci2': {'path': 'notes/practice-sci2', 'start_page': 1, 'end_page': 0, 'total_pages': 0, 'title': 'Practice Papers - Science 2'},
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
    practice_sci1_dir = os.path.join(app.root_path, 'static', 'notes', 'practice-sci1')
    practice_sci2_dir = os.path.join(app.root_path, 'static', 'notes', 'practice-sci2')
    practice_sci1_count = len(glob.glob(os.path.join(practice_sci1_dir, 'page_*.png'))) if os.path.exists(practice_sci1_dir) else 0
    practice_sci2_count = len(glob.glob(os.path.join(practice_sci2_dir, 'page_*.png'))) if os.path.exists(practice_sci2_dir) else 0

    return render_template('science_hub.html', user=user, is_logged_in=is_logged_in,
                           has_digital_access=has_digital_access, razorpay_key_id=RAZORPAY_KEY_ID,
                           digital_enabled=digital_enabled, physical_enabled=physical_enabled,
                           practice_sci1_count=practice_sci1_count, practice_sci2_count=practice_sci2_count,
                           science_content=SCIENCE_CONTENT)

@app.route('/science-hub/validate-upper-id', methods=['POST'])
def validate_upper_id():
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
        practice_dir = os.path.join(app.root_path, 'static', content['path'])
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
    practice_sci1_dir = os.path.join(app.root_path, 'static', 'notes', 'practice-sci1')
    practice_sci2_dir = os.path.join(app.root_path, 'static', 'notes', 'practice-sci2')
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
    upload_dir = os.path.join(app.root_path, 'static', content['path'])
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
    page_num = request.form.get('page_num')
    
    if subject not in ('practice-sci1', 'practice-sci2'):
        flash("Invalid subject.", "danger")
        return redirect('/admin/science-hub')
    
    content = SCIENCE_CONTENT[subject]
    file_path = os.path.join(app.root_path, 'static', content['path'], f'page_{page_num}.png')
    
    if os.path.exists(file_path):
        os.remove(file_path)
        # Renumber remaining pages
        import glob
        upload_dir = os.path.join(app.root_path, 'static', content['path'])
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
        filepath = os.path.join(app.root_path, 'static', content['path'], f'page_{actual_page}.png')
    else:
        # Practice papers
        import glob
        practice_dir = os.path.join(app.root_path, 'static', content['path'])
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
            return send_from_directory(os.path.join('static', content['path']), f'page_{actual_page}.png')
        except Exception:
            abort(404)
    else:
        # Practice papers: files are in their own directory, numbered from 1
        try:
            return send_from_directory(os.path.join('static', content['path']), f'page_{page_num}.png')
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
    
    cart_items = CartItem.query.filter_by(user_email=user['email']).all()
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
    cart_items = CartItem.query.filter_by(user_email=user['email']).all()
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
    
    cart_items = CartItem.query.filter_by(user_email=user['email']).all()
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
    
    cart_items = CartItem.query.filter_by(user_email=user['email']).all()
    if not cart_items:
        flash("Your cart is empty.", "warning")
        return redirect('/cart')
    
    cart_total = sum(int(float(item.product.price)) * item.quantity for item in cart_items if item.product)
    
    return render_template('checkout.html', cart_items=cart_items, cart_total=cart_total,
                           user=user, is_logged_in=True, razorpay_key_id=RAZORPAY_KEY_ID)

@app.route('/checkout/apply-coupon', methods=['POST'])
def apply_coupon():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Please login first'}), 401

    code = (request.json or {}).get('code', '').strip().upper()
    if not code:
        return jsonify({'error': 'Please enter a coupon code'}), 400

    coupon = Coupon.query.filter_by(code=code).first()
    if not coupon or not coupon.is_valid_for(user['email']):
        return jsonify({'error': 'This coupon is invalid, expired, already used, or not valid for your account.'}), 400

    cart_items = CartItem.query.filter_by(user_email=user['email']).all()
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
    cart_items = CartItem.query.filter_by(user_email=user['email']).all()
    total_weight_kg = 0.0
    for item in cart_items:
        # Precise weight: product + 5g polythine
        price = float(item.product.price) if item.product.price else 0
        if price <= 45:
            item_weight = 0.06  # 55g + 5g = 60g
        else:
            item_weight = 0.11  # 105g + 5g = 110g
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
    cart_items = CartItem.query.filter_by(user_email=user['email']).all()
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
    coupon_code = (data.get('coupon_code') or '').strip().upper()
    if coupon_code:
        coupon = Coupon.query.filter_by(code=coupon_code).first()
        if not coupon or not coupon.is_valid_for(user['email']):
            return jsonify({'error': 'This coupon is invalid, expired, already used, or not valid for your account.'}), 400
        discount_rupees = coupon.compute_discount(subtotal_rupees)

    subtotal_paise = subtotal_rupees * 100
    discount_paise = discount_rupees * 100
    shipping_paise = int(shipping_rate) * 100
    total_paise = subtotal_paise - discount_paise + shipping_paise

    if not razorpay_client:
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
        return jsonify({'error': str(e)}), 500

@app.route('/checkout/verify-payment', methods=['POST'])
def verify_checkout_payment():
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
        
        # Update order
        order = SpiceOrder.query.filter_by(razorpay_order_id=razorpay_order_id).first()
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        order.payment_status = 'paid'
        order.razorpay_payment_id = razorpay_payment_id

        # Mark the coupon used only now -- on confirmed payment, never on an
        # abandoned/failed checkout attempt
        if order.coupon_id:
            coupon = Coupon.query.get(order.coupon_id)
            if coupon:
                coupon.used = True
                coupon.used_at = datetime.utcnow()

        # Decrement stock
        for item in order.items:
            product = Product.query.get(item.product_id)
            if product and product.stock is not None:
                product.stock = max(0, product.stock - item.quantity)
        
        # Clear the cart
        CartItem.query.filter_by(user_email=user['email']).delete()
        
        db.session.commit()
        
        # Send Telegram Notification
        item_text = ", ".join([f"{i.quantity}x {i.product_name}" for i in order.items])
        msg = f"🚨 <b>NEW SPICE ORDER!</b>\n\n<b>Order:</b> {order.order_number}\n<b>Customer:</b> {order.full_name}\n<b>Amount:</b> ₹{order.total_amount / 100}\n<b>Items:</b> {item_text}"
        send_telegram_notification(msg)
        
        return jsonify({'success': True, 'message': 'Payment successful!', 'order_number': order.order_number})
        
    except razorpay.errors.SignatureVerificationError:
        return jsonify({'error': 'Invalid payment signature'}), 400
    except Exception as e:
        print("Checkout payment verification error:", e)
        return jsonify({'error': 'Internal server error'}), 500


# -------------------------
# 📦 Customer Order Routes
# -------------------------

@app.route('/orders')
def my_orders():
    user = session.get('user')
    if not user:
        flash("Please login to view your orders.", "warning")
        return redirect('/login')
    
    orders = SpiceOrder.query.filter_by(user_email=user['email']).order_by(SpiceOrder.created_at.desc()).all()
    return render_template('my_orders.html', orders=orders, user=user, is_logged_in=True)

@app.route('/my-coupons')
def my_coupons():
    user = session.get('user')
    if not user:
        flash("Please login to view your coupons.", "warning")
        return redirect('/login')

    coupons = Coupon.query.filter_by(user_email=user['email']).order_by(Coupon.created_at.desc()).all()
    return render_template('my_coupons.html', coupons=coupons, user=user, is_logged_in=True, now=datetime.utcnow())

@app.route('/orders/<int:order_id>/track')
def track_order(order_id):
    user = session.get('user')
    if not user:
        flash("Please login to track your order.", "warning")
        return redirect('/login')
    
    order = SpiceOrder.query.filter_by(id=order_id, user_email=user['email']).first_or_404()
    
    # Get tracking from NimbusPost
    tracking = {'current_status': order.shipping_status, 'history': [], 'estimated_delivery': order.estimated_delivery or 'N/A'}
    if order.awb_number:
        tracking = nimbus_api.track_shipment(order.awb_number)
    
    return render_template('order_tracking.html', order=order, tracking=tracking,
                           user=user, is_logged_in=True)


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
        'welcome_coupon_expiry_days': get_setting('welcome_coupon_expiry_days', '30')
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
    orders = SpiceOrder.query.order_by(SpiceOrder.created_at.desc()).all()
    
    stats = {
        'total_orders': len(orders),
        'total_revenue': sum(o.total_amount for o in orders if o.payment_status == 'paid') // 100,
        'pending_shipments': sum(1 for o in orders if o.payment_status == 'paid' and o.shipping_status == 'processing'),
        'delivered': sum(1 for o in orders if o.shipping_status == 'delivered')
    }
    
    return render_template('admin_orders.html', orders=orders, stats=stats,
                           user=session.get('user'), is_logged_in=True)

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
    
    result = nimbus_api.create_shipment(shipment_data)
    
    if result['success']:
        order.awb_number = result.get('awb_number')
        order.courier_name = result.get('courier_name', 'NimbusPost')
        order.shipping_status = 'shipped'
        order.estimated_delivery = result.get('estimated_delivery', '')
        order.label_url = result.get('label_url')
        db.session.commit()
        flash(f"Order {order.order_number} shipped! AWB: {order.awb_number}", "success")
    else:
        # Even if NimbusPost is not configured, mark as shipped manually
        order.shipping_status = 'shipped'
        order.courier_name = 'Manual'
        db.session.commit()
        flash(f"Order {order.order_number} marked as shipped. NimbusPost: {result.get('message', 'Not configured')}", "warning")
    
    return redirect('/admin/orders')

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

    print(f"NimbusPost webhook payload: {data}")

    awb = data.get('awb_number') or data.get('awb') or data.get('awbNumber')
    status = data.get('current_status') or data.get('status')

    if awb and status:
        order = SpiceOrder.query.filter_by(awb_number=awb).first()
        if order:
            order.shipping_status = status.lower()
            if 'delivered' in status.lower():
                order.shipping_status = 'delivered'
            db.session.commit()
    
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
        return jsonify({
            'status_code': resp.status_code,
            'response': resp.json()
        })
    except Exception as e:
        return str(e)

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

@app.route('/test-csrf')
def test_csrf():
    from flask import render_template_string
    return render_template_string('''
        <form method="POST">
            {{ csrf_token() }}
            <input type="email" name="email">
            <button type="submit">Test</button>
        </form>
    ''')

# --- Product Review Routes ---
@app.route('/product/<int:product_id>/review', methods=['POST'])
def submit_review(product_id):
    user = session.get('user')
    if not user:
        return jsonify({'success': False, 'message': 'You must be logged in to submit a review.'}), 401
    
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
