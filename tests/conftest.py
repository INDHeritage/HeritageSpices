import os
import sys

import pytest

# The app reads its settings at import time, so isolate it BEFORE importing:
# an in-memory SQLite database and no third-party credentials. (Without this the
# app would pick up DATABASE_URL from a local .env and talk to the real database.)
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['FLASK_SECRET_KEY'] = 'test-secret-key'
os.environ['RAZORPAY_WEBHOOK_SECRET'] = 'whsec_test'
for var in ('RENDER', 'NIMBUS_API_TOKEN', 'NIMBUS_API_SECRET', 'TELEGRAM_BOT_TOKEN',
            'TELEGRAM_CHAT_ID', 'SCIENCE_HUB_ENABLED', 'ADMIN_EMAILS'):
    os.environ.pop(var, None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

# Safety net: refuse to run a single test against anything but a throwaway database.
if not appmod.app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
    pytest.exit('Refusing to run tests: app is not using a SQLite test database.', returncode=2)

ADMIN = 'heritage.spices.pvtltd@gmail.com'


@pytest.fixture()
def app():
    appmod.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with appmod.app.app_context():
        appmod.db.drop_all()
        appmod.db.create_all()
        yield appmod.app
        appmod.db.session.remove()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, email, name='Test User'):
    with client.session_transaction() as sess:
        sess['user'] = {'email': email, 'name': name}


@pytest.fixture()
def make_order(app):
    """Create a product + an unpaid order for it, return (order, product)."""
    def _make(stock=10, qty=2, email='buyer@example.com', rz_order='order_TEST1', total_paise=14700):
        db = appmod.db
        product = appmod.Product(name='Garam Masala - 50g', description='test', price='55', stock=stock)
        db.session.add(product)
        db.session.flush()
        order = appmod.SpiceOrder(
            order_number='HS-TEST0001', user_email=email, full_name='Buyer', phone='9999999999',
            address='1 Test Road', city='Sindewahi', state='Maharashtra', pincode='441222',
            subtotal=11000, shipping_cost=3700, total_amount=total_paise,
            razorpay_order_id=rz_order, payment_status='pending')
        db.session.add(order)
        db.session.flush()
        db.session.add(appmod.OrderItem(order_id=order.id, product_id=product.id,
                                        product_name=product.name, quantity=qty, unit_price=5500))
        db.session.commit()
        return order, product
    return _make
