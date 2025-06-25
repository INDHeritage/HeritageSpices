from flask import Flask, render_template, redirect, url_for, session, request, jsonify
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask_cors import CORS
import os
import pandas as pd

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

# Enable CORS for frontend communication
CORS(app, supports_credentials=True)

# Google OAuth2 Configuration
app.config['GOOGLE_CLIENT_ID'] = os.getenv("GOOGLE_CLIENT_ID")
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv("GOOGLE_CLIENT_SECRET")
app.config['GOOGLE_DISCOVERY_URL'] = "https://accounts.google.com/.well-known/openid-configuration"

# Register Google OAuth client
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    server_metadata_url=app.config['GOOGLE_DISCOVERY_URL'],
    client_kwargs={'scope': 'openid email profile'},
)

# Utility function to save user data
def save_user_info(user_info):
    path = 'users.csv'
    new_user = {
        'name': user_info['name'],
        'email': user_info['email'],
        'picture': user_info.get('picture')
    }

    if os.path.exists(path):
        df = pd.read_csv(path)
        if new_user['email'] not in df['email'].values:
            df = pd.concat([df, pd.DataFrame([new_user])], ignore_index=True)
            df.to_csv(path, index=False)
    else:
        pd.DataFrame([new_user]).to_csv(path, index=False)

# Home route
@app.route('/')
def home():
    return render_template("index.html", user=session.get('user'))

# Login route
@app.route('/login')
def login():
    redirect_uri = url_for('auth_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

# Google OAuth callback
@app.route('/auth')
def auth_callback():
    token = google.authorize_access_token()
    user_info = google.parse_id_token(token, nonce=token.get('nonce'))

    session['user'] = {
        'name': user_info['name'],
        'email': user_info['email'],
        'picture': user_info.get('picture')
    }

    save_user_info(user_info)

    # Check if customer details exist
    detail_path = 'customer_details.csv'
    if not os.path.exists(detail_path) or session['user']['email'] not in pd.read_csv(detail_path)['email'].values:
        return redirect('/collect-details')

    return redirect('/')

# Route to collect additional customer details
@app.route('/collect-details', methods=['GET', 'POST'])
def collect_details():
    if 'user' not in session:
        return redirect('/')

    email = session['user']['email']
    file_path = 'customer_details.csv'

    if request.method == 'POST':
        form_data = request.get_json(silent=True) or request.form
        new_record = {
            'email': email,
            'phone': form_data.get('phone'),
            'location': form_data.get('location'),
            'interest': form_data.get('interest')
        }

        if os.path.exists(file_path):
            df = pd.read_csv(file_path)
            if email not in df['email'].values:
                df = pd.concat([df, pd.DataFrame([new_record])], ignore_index=True)
                df.to_csv(file_path, index=False)
        else:
            pd.DataFrame([new_record]).to_csv(file_path, index=False)

        return redirect('/')

    return render_template("collect_details.html", user=session['user'])

# Admin route to view all registered users
@app.route('/admin/users')
def admin_users():
    if os.path.exists('users.csv'):
        df = pd.read_csv('users.csv')
        return jsonify(df.to_dict(orient='records'))
    return jsonify([])

# Logout route
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# Run the app
if __name__ == '__main__':
    app.run(debug=True)
