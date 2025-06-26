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
        # Add more as needed
    ]
    return render_template('home.html', featured_products=featured_products)


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