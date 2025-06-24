from flask import Flask, redirect, url_for, session, render_template, request
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import os
import pandas as pd

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

# OAuth Config
app.config['GOOGLE_CLIENT_ID'] = os.getenv("GOOGLE_CLIENT_ID")
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv("GOOGLE_CLIENT_SECRET")
app.config['GOOGLE_DISCOVERY_URL'] = "https://accounts.google.com/.well-known/openid-configuration"

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    server_metadata_url=app.config['GOOGLE_DISCOVERY_URL'],
    client_kwargs={'scope': 'openid email profile'},
)

# Save user info to users.csv
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
        df = pd.DataFrame([new_entry])
        df.to_csv(file, index=False)

@app.route('/')
def index():
    user = session.get('user')
    return render_template('index.html', user=user)

@app.route('/login')
def login():
    redirect_uri = url_for('auth', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth')
def auth():
    token = google.authorize_access_token()
    user_info = google.parse_id_token(token, nonce=token.get('nonce'))

    session['user'] = {
        'name': user_info['name'],
        'email': user_info['email'],
        'picture': user_info['picture']
    }

    save_user(user_info)

    # Redirect if customer details not saved
    details_file = 'customer_details.csv'
    if os.path.exists(details_file):
        df = pd.read_csv(details_file)
        if user_info['email'] not in df['email'].values:
            return redirect('/collect-details')
    else:
        return redirect('/collect-details')

    return redirect('/')

@app.route('/collect-details', methods=['GET', 'POST'])
def collect_details():
    if 'user' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        details_file = 'customer_details.csv'
        data = {
            'email': session['user']['email'],
            'phone': request.form['phone'],
            'location': request.form['location'],
            'interest': request.form['interest']
        }

        if os.path.exists(details_file):
            df = pd.read_csv(details_file)
            if data['email'] not in df['email'].values:
                df = pd.concat([df, pd.DataFrame([data])], ignore_index=True)
                df.to_csv(details_file, index=False)
        else:
            df = pd.DataFrame([data])
            df.to_csv(details_file, index=False)

        return redirect('/')

    return render_template('collect_details.html', user=session['user'])

@app.route('/admin/users')
def view_users():
    df = pd.read_csv('users.csv')
    return df.to_html(classes='table table-bordered', index=False)

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/')

if __name__ == '__main__':
    from waitress import serve  # optional: gunicorn is also fine
    serve(app, host='0.0.0.0', port=8080)
