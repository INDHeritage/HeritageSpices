from flask import Flask, render_template, redirect, url_for, session, request, jsonify
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask_cors import CORS
import os
import pandas as pd

# Load .env variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

# Enable CORS for frontend
CORS(app, supports_credentials=True)

# Google OAuth config
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
        'prompt': 'select_account'  # 👈 ensures Google shows chooser
    }
)


# Save basic user info to CSV
def save_user(user_info):
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

# Home route
@app.route('/')
def index():
    user = session.get('user')
    is_logged_in = bool(user)
    return render_template("index.html", user=user, is_logged_in=is_logged_in)


# Google login
@app.route('/login')
def login():
    redirect_uri = url_for('auth', _external=True)
    return google.authorize_redirect(redirect_uri)

# Google OAuth callback
@app.route('/auth')
def auth():
    print("🔁 Google auth callback hit")

    try:
        token = google.authorize_access_token()
        print("✅ Token received")

        user_info = google.parse_id_token(token, nonce=token.get('nonce'))
        print("👤 User info:", user_info)

        session['user'] = {
            'name': user_info['name'],
            'email': user_info['email'],
            'picture': user_info.get('picture')
        }

        save_user(user_info)

        details_file = 'customer_details.csv'
        if not os.path.exists(details_file) or session['user']['email'] not in pd.read_csv(details_file)['email'].values:
            print("➡️ Redirecting to collect-details")
            return redirect('/collect-details')

        print("✅ Login complete, redirecting to home")
        return redirect('/')
    except Exception as e:
        print("❌ Error during OAuth:", e)
        return "OAuth failed", 500



# Route to collect additional user details
@app.route('/collect-details', methods=['GET', 'POST'])
def collect_details():
    if 'user' not in session:
        return jsonify({'error': 'Login required'}), 401

    email = session['user']['email']
    details_file = 'customer_details.csv'

    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        record = {
            'email': email,
            'phone': data.get('phone'),
            'location': data.get('location'),
            'interest': data.get('interest')
        }

        if os.path.exists(details_file):
            df = pd.read_csv(details_file)
            if email not in df['email'].values:
                df = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
                df.to_csv(details_file, index=False)
        else:
            pd.DataFrame([record]).to_csv(details_file, index=False)

        return redirect('/')

    # Render form if GET
    return render_template("collect_details.html", user=session['user'])

# Admin route to view all users
@app.route('/admin/users')
def view_users():
    if os.path.exists('users.csv'):
        df = pd.read_csv('users.csv')
        return jsonify(df.to_dict(orient='records'))
    return jsonify([])

# Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# Start the app
if __name__ == '__main__':
    app.run(debug=True)
