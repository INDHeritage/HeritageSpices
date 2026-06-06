from datetime import datetime
from flask import Flask, render_template, redirect, url_for, session, request, jsonify, flash
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask_cors import CORS
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
from flask_migrate import Migrate
from flask_wtf import CSRFProtect

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
app.secret_key = os.getenv("FLASK_SECRET_KEY")
CORS(app, supports_credentials=True)
csrf = CSRFProtect(app)

# --- SQLAlchemy Setup ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
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
# 📦 Helper Functions
# -------------------------
def save_user(user_info):
    """Save user info to database"""
    existing = User.query.filter_by(email=user_info['email']).first()
    if not existing:
        new_user = User(
            name=user_info['name'],
            email=user_info['email'],
            picture=user_info.get('picture')
        )
        db.session.add(new_user)
        db.session.commit()

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

        save_user(user_info)

        # Check if additional details are already filled
        existing_detail = CustomerDetail.query.filter_by(email=session['user']['email']).first()
        if not existing_detail:
            return redirect('/collect-details')

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

@app.route('/ads.txt')
def ads_txt():
    return send_from_directory('static', 'ads.txt')

@app.route('/robots.txt')
def robots():
    return (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Allow: /\n"
        "Sitemap: https://www.heritagespices.shop/sitemap.xml\n",
        200,
        {'Content-Type': 'text/plain'}
    )

@app.route('/sitemap.xml')
def sitemap():
    base = "https://www.heritagespices.shop"
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

# ✅ Admin: Unified Dashboard
@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('user') or session['user']['email'] != 'heritage.spices.pvtltd@gmail.com':
        abort(403)
    
    total_users = User.query.count()
    total_visits = Visit.query.count()
    total_products = Product.query.count()
    total_blogs = Blog.query.count()
    total_messages = ContactMessage.query.count()
    
    return render_template('admin_dashboard.html', 
                           total_users=total_users, 
                           total_visits=total_visits,
                           total_products=total_products,
                           total_blogs=total_blogs,
                           total_messages=total_messages)

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
    return {
        'year': datetime.now().year,
        'is_logged_in': bool(user),
        'user': user
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


@app.template_filter()
def truncate(s, length=100):
    return s if len(s) <= length else s[:length] + "..."



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

# -------------------------
# 🚀 Run App
# -------------------------
if __name__ == '__main__':
    # --- TEMP: Create DB tables if missing ---
    with app.app_context():
        db.create_all()
        print("Database tables created!")
    app.run(debug=True)
    