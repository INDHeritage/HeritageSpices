from datetime import datetime
from flask import Flask, render_template, redirect, url_for, session, request, jsonify
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
    
    blogs = load_blogs()
    blog_urls = [f"/blog/{b['slug']}" for b in blogs]

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

def load_blogs():
    try:
        response = requests.get(BLOG_DRIVE_URL)
        response.raise_for_status()

        # Fix encoding: decode the wrong bytes (Latin-1), re-encode to proper UTF-8
        raw_bytes = response.content
        step1 = raw_bytes.decode('latin1')                # Step 1: Decode from Latin-1
        fixed_text = step1.encode('utf-8').decode('utf-8')  # Step 2: Re-decode to clean UTF-8

        # ✅ Parse clean JSON
        return json.loads(fixed_text)
    except Exception as e:
        print(f"⚠️ Error loading blogs: {e}")
        return []

    
'''
def save_blogs(blog_list):
    with open(BLOG_FILE, 'w') as f:
        json.dump(blog_list, f, indent=2)
'''        
'''
@app.route('/admin/blogs', methods=['GET', 'POST'])
@admin_required
def manage_blogs():
    if session.get('user', {}).get('email') != 'heritage.spices.pvtltd@gmail.com':
        abort(403)

    blogs = load_blogs()

    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        category = request.form.get('category', 'General')
        slug = title.lower().replace(' ', '-').replace(',', '').replace('.', '')

        # 🖼️ Handle image upload
        image_url = None
        image = request.files.get('image')
        if image and image.filename:
            filename = f"{uuid.uuid4().hex}_{image.filename}"
            image.save(os.path.join('static/uploads', filename))
            image_url = f"/static/uploads/{filename}"

        # If image was uploaded, inject it into content
        if image_url:
            content = f'<img src="{image_url}" class="img-fluid mb-3" alt="blog image">\n' + content

        new_blog = {
            "id": uuid.uuid4().hex,
            "title": title,
            "slug": slug,
            "author": session.get('user', {}).get('name', 'Admin'),
            "category": category,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "content": content
        }
        blogs.insert(0, new_blog)
        save_blogs(blogs)
        return redirect('/admin/blogs?password=' + os.getenv("ADMIN_PASSWORD"))

    return render_template("admin_blog.html", blogs=blogs, user=session.get('user'))

'''


# 📝 Blog Post Data
blogs = [
    {
        "id": 1,
        "title": "How to Use Garam Masala in Everyday Cooking",
        "slug": "use-garam-masala-everyday",
        "author": "Team Heritage",
        "date": "June 25, 2025",
        "content": """
            <p>Garam masala adds rich aroma and warmth to Indian curries, soups, and even roasted vegetables. 
            Just sprinkle a teaspoon near the end of cooking for best flavor.</p>
            <p>You can also use it in lentils, scrambled eggs, or even roasted nuts.</p>
        """
    },
    {
        "id": 2,
        "title": "5 Homemade Spice Mixes You Can Make at Home",
        "slug": "homemade-spice-mixes",
        "author": "Chef Meera",
        "date": "June 20, 2025",
        "content": """
            <p>Want to skip the store? Try mixing your own chaat masala, pav bhaji masala, or sambar powder with fresh ground spices.</p>
            <p>Store them in airtight jars and enjoy real flavor!</p>
        """
    }
]


@app.route("/blog")
def blog():
    blogs = load_blogs()
    query = request.args.get("q", "").lower()
    category = request.args.get("category", "")
    user = session.get('user')
    is_logged_in = bool(user)

    filtered = [
        b for b in blogs
        if (query in b["title"].lower() or query in b["content"].lower())
        and (category == "" or b["category"].lower() == category.lower())
    ]
    categories = sorted(set(b["category"] for b in blogs))

    admin_password = os.getenv("ADMIN_PASSWORD")
    return render_template(
        "blog_list.html",
        blogs=filtered,
        categories=categories,
        query=query,
        selected_category=category,
        user=session.get('user'),
        is_logged_in=bool(session.get('user')),
        admin_password=admin_password
    )




@app.route("/blog/<slug>")
def blog_detail(slug):
    blogs = load_blogs()
    post = next((b for b in blogs if b["slug"] == slug), None)
    user = session.get('user')
    is_logged_in = bool(user)

    if post:
        post["content"] = html.unescape(post["content"])  # ✅ Decode emojis & symbols properly
        return render_template("blog_detail.html", post=post, user=user, is_logged_in=is_logged_in)
    else:
        return "Post not found", 404



@app.route('/admin/blogs/delete/<id>', methods=['POST'])
def delete_blog(id):
    # ✅ Only allow admin user
    if session.get('user', {}).get('email') != 'heritage.spices.pvtltd@gmail.com':
        abort(403)

    password = request.form.get('password')
    if password != ADMIN_PASSWORD:
        abort(403)

    blogs = load_blogs()
    blogs = [b for b in blogs if b['id'] != id]
    save_blogs(blogs)
    return redirect('/blog')




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
    app.run(debug=True)
    