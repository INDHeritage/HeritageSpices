from datetime import datetime
from flask import Flask, render_template, redirect, url_for, session, request, jsonify, flash
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask_cors import CORS
import os
import pandas as pd
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

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        password = request.args.get('password')
        if password != ADMIN_PASSWORD:
            abort(403)  # return "Forbidden"
        return f(*args, **kwargs)
    return decorated_function



# -------------------------
# 🔐 Load environment
# -------------------------
load_dotenv()

app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv("FLASK_SECRET_KEY")
CORS(app, supports_credentials=True)

# --- SQLAlchemy Setup ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blog.db'
db = SQLAlchemy(app)

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
    """Save user info to users.csv"""
    file = 'users.csv'
    new_entry = {
        'name': user_info['name'],
        'email': user_info['email'],
        'picture': user_info.get('picture')
    }

    if os.path.exists(file):
        df = pd.read_csv(file)
        if new_entry['email'] not in df['email'].values:
            df = pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True)
            df.to_csv(file, index=False)
    else:
        pd.DataFrame([new_entry]).to_csv(file, index=False)

def track_visit(user=None):
    """Log each visit to visits.csv"""
    visit = {
        'timestamp': datetime.now().isoformat(),
        'ip': request.remote_addr,
        'user_agent': request.headers.get('User-Agent'),
        'email': user['email'] if user else 'Guest'
    }
    file = 'visits.csv'
    with open(file, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=visit.keys())
        if f.tell() == 0:
            writer.writeheader()
        writer.writerow(visit)

# -------------------------
# 🌐 Routes
# -------------------------

# ✅ Homepage
@app.route('/')
def index():
    user = session.get('user')
    is_logged_in = bool(user)
    track_visit(user)
    return render_template("index.html", user=user, is_logged_in=is_logged_in)

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
        details_file = 'customer_details.csv'
        if not os.path.exists(details_file) or session['user']['email'] not in pd.read_csv(details_file)['email'].values:
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
    file = 'customer_details.csv'

    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        new_data = {
            'email': email,
            'phone': data.get('phone'),
            'location': data.get('location'),
            'interest': data.get('interest')
        }

        if os.path.exists(file):
            df = pd.read_csv(file)
            if email not in df['email'].values:
                df = pd.concat([df, pd.DataFrame([new_data])], ignore_index=True)
                df.to_csv(file, index=False)
        else:
            pd.DataFrame([new_data]).to_csv(file, index=False)

        return redirect('/')

    return render_template("collect_details.html", user=session['user'])


@app.route('/about')
def about():
    return render_template('about.html', user=session.get('user'), is_logged_in=bool(session.get('user')))

@app.route('/contact')
def contact():
    return render_template('contact.html', user=session.get('user'), is_logged_in=bool(session.get('user')))

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
    static_urls = ['/', '/about', '/contact', '/privacy', '/blog']
    
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
    # Save to database or send to Mailchimp, etc.
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

    if post:
        return render_template("blog_detail.html", post=post, user=user, is_logged_in=is_logged_in)
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
        author = session.get('user', {}).get('name', 'Admin')
        date = datetime.now().strftime("%Y-%m-%d")

        # Handle image upload
        image_url = None
        image = request.files.get('image')
        if image and image.filename:
            filename = f"{uuid.uuid4().hex}_{image.filename}"
            upload_path = os.path.join('static/uploads', filename)
            image.save(upload_path)
            image_url = f"/static/uploads/{filename}"

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


@app.route('/admin/blogs/edit/<int:blog_id>', methods=['GET', 'POST'])
def edit_blog(blog_id):
    user = session.get('user')
    if not user or user['email'] != 'heritage.spices.pvtltd@gmail.com':
        abort(403)
    blog = Blog.query.get_or_404(blog_id)
    if request.method == 'POST':
        blog.title = request.form['title']
        blog.content = request.form['content']
        # Handle image upload
        if 'image' in request.files and request.files['image'].filename:
            image = request.files['image']
            image_filename = secure_filename(image.filename)
            image.save(os.path.join(app.static_folder, 'uploads', image_filename))
            blog.image_url = image_filename
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
    file = 'users.csv'
    if os.path.exists(file):
        df = pd.read_csv(file)
        return jsonify(df.to_dict(orient='records'))
    return jsonify([])

# ✅ Admin: View All Visits
@app.route('/admin/visits')
@admin_required
def view_visits():
    file = 'visits.csv'
    if os.path.exists(file):
        df = pd.read_csv(file)
        return jsonify(df.to_dict(orient='records'))
    return jsonify([])

# ✅ Frontend Analytics
@app.route('/track-visit', methods=['POST'])
def track_frontend_visit():
    data = request.get_json()
    data['ip'] = request.remote_addr
    file = 'frontend_visits.csv'

    with open(file, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())
        if f.tell() == 0:
            writer.writeheader()
        writer.writerow(data)

    return '', 204

# ✅ Privacy Policy
@app.route('/privacy')
def privacy():
    return render_template("privacy.html", now=datetime.now())

# ✅ Inject year globally
@app.context_processor
def inject_year():
    return {'year': datetime.now().year}


@app.route('/')
def home():
    user = session.get('user')
    is_logged_in = bool(user)
    track_visit(user)
    featured_products = [
        {
            'id': 1,
            'name': 'Garam Masala – 50g',
            'description': 'Bold and aromatic blend of Indian spices.',
            'price': 99,
            'image': 'garam50.png'
        },
        {
            'id': 2,
            'name': 'Turmeric Powder – 100g',
            'description': 'Pure turmeric from Kerala farms.',
            'price': 79,
            'image': 'turmeric100.png'
        }
    ]
    return render_template('home.html', user=user, is_logged_in=is_logged_in, featured_products=featured_products)

@app.route('/product/<int:product_id>')
def product_detail(product_id):
    featured_products = [
        {
            'id': 1,
            'name': 'Garam Masala – 50g',
            'description': 'Bold and aromatic blend of Indian spices.',
            'price': 99,
            'image': 'garam50.png',
            'meesho_link': 'https://www.meesho.com/s/p/98mug4?utm_source=s_cc'
        },
        {
            'id': 2,
            'name': 'Turmeric Powder – 100g',
            'description': 'Pure turmeric from Kerala farms.',
            'price': 79,
            'image': 'turmeric100.png',
            'meesho_link': 'https://www.meesho.com/s/p/98mxwl?utm_source=s_cc'
        }
    ]
    
    product = next((p for p in featured_products if p['id'] == product_id), None)
    if not product:
        return "Product not found", 404

    # Generate WhatsApp message URL
    msg = f"Hi Heritage Spices, I want to order {product['name']}."
    whatsapp_url = f"https://wa.me/918999449765?text={msg.replace(' ', '%20')}"

    return render_template("product_detail.html", product=product, whatsapp_url=whatsapp_url)

# --- Products Page Route ---
@app.route('/products')
def products():
    # Use the same featured_products as in product_detail for now
    featured_products = [
        {
            'id': 1,
            'name': 'Garam Masala – 50g',
            'description': 'Bold and aromatic blend of Indian spices.',
            'price': 99,
            'image': 'garam50.png',
            'meesho_link': 'https://www.meesho.com/s/p/98mug4?utm_source=s_cc'
        },
        {
            'id': 2,
            'name': 'Turmeric Powder – 100g',
            'description': 'Pure turmeric from Kerala farms.',
            'price': 79,
            'image': 'turmeric100.png',
            'meesho_link': 'https://www.meesho.com/s/p/98mxwl?utm_source=s_cc'
        }
    ]
    return render_template('products.html', products=featured_products, user=session.get('user'), is_logged_in=bool(session.get('user')))

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


@app.template_filter()
def truncate(s, length=100):
    return s if len(s) <= length else s[:length] + "..."



@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403

# -------------------------
# 🚀 Run App
# -------------------------
if __name__ == '__main__':
    # --- TEMP: Create DB tables if missing ---
    with app.app_context():
        db.create_all()
        print("Database tables created!")
    app.run(debug=True)
    