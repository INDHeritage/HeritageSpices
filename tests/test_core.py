"""Regression tests for the things that lose money or leak content if they break."""
import hashlib
import hmac
import json
import os

import app as appmod
from conftest import ADMIN, login


# ---------- Science Hub is hidden, and paid notes are not publicly served ----------

def test_science_hub_hidden_from_public(client):
    for path in ('/science-hub', '/science-hub/viewer/sci1', '/api/notes/5', '/api/notes/sci1/5'):
        assert client.get(path).status_code == 404, path


def test_science_hub_link_not_in_nav_for_public(client):
    assert 'href="/science-hub"' not in client.get('/').get_data(as_text=True)


def test_admin_can_still_open_science_hub(client):
    login(client, ADMIN)
    assert client.get('/science-hub').status_code == 200


def test_paid_notes_are_not_under_static():
    static_notes = os.path.join(appmod.app.root_path, 'static', 'notes')
    assert not os.path.exists(static_notes), 'paid notes must not live in the public static folder'


def test_notes_api_requires_purchase_when_hub_enabled(client, monkeypatch):
    monkeypatch.setattr(appmod, 'SCIENCE_HUB_ENABLED', True)
    assert client.get('/api/notes/5').status_code == 401


# ---------- Access control ----------

def test_admin_pages_blocked_for_normal_users(client):
    login(client, 'someone@example.com')
    assert client.get('/admin/dashboard').status_code == 403
    assert client.get('/admin/orders').status_code == 403


def test_admin_pages_open_for_admin(client):
    login(client, ADMIN)
    assert client.get('/admin/orders').status_code == 200


def test_track_order_only_visible_to_owner_or_admin(client, make_order):
    order, _ = make_order()
    login(client, 'stranger@example.com')
    assert client.get(f'/orders/{order.id}/track').status_code == 404
    login(client, 'buyer@example.com')
    assert client.get(f'/orders/{order.id}/track').status_code == 200
    login(client, ADMIN)
    assert client.get(f'/orders/{order.id}/track').status_code == 200


# ---------- Payments ----------

def test_finalize_paid_order_is_idempotent(make_order):
    order, product = make_order(stock=10, qty=2)
    o1, newly1 = appmod.finalize_paid_order('order_TEST1', 'pay_1')
    o2, newly2 = appmod.finalize_paid_order('order_TEST1', 'pay_1')
    appmod.db.session.refresh(product)
    assert newly1 is True and newly2 is False
    assert o1.payment_status == 'paid'
    assert product.stock == 8, 'stock must be reduced once, not once per call'


def test_finalize_unknown_order_returns_none(app):
    assert appmod.finalize_paid_order('order_DOES_NOT_EXIST', 'pay_x') == (None, False)


def test_stock_never_goes_negative(make_order):
    order, product = make_order(stock=1, qty=3)
    appmod.finalize_paid_order('order_TEST1', 'pay_1')
    appmod.db.session.refresh(product)
    assert product.stock == 0


def _webhook(client, payload, secret='whsec_test'):
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post('/api/razorpay/webhook', data=body, headers={
        'X-Razorpay-Signature': sig, 'Content-Type': 'application/json'})


def _captured(amount, order_id='order_TEST1', payment_id='pay_web1'):
    return {'event': 'payment.captured', 'payload': {'payment': {'entity': {
        'id': payment_id, 'order_id': order_id, 'amount': amount}}}}


def test_webhook_marks_order_paid_even_if_browser_never_called_back(client, make_order):
    order, _ = make_order(total_paise=14700)
    assert _webhook(client, _captured(14700)).status_code == 200
    appmod.db.session.refresh(order)
    assert order.payment_status == 'paid'
    assert order.razorpay_payment_id == 'pay_web1'


def test_webhook_rejects_bad_signature(client, make_order):
    order, _ = make_order()
    r = _webhook(client, _captured(14700), secret='wrong-secret')
    assert r.status_code == 403
    appmod.db.session.refresh(order)
    assert order.payment_status == 'pending'


def test_webhook_ignores_amount_mismatch(client, make_order):
    order, _ = make_order(total_paise=14700)
    _webhook(client, _captured(100))  # paid 1 rupee for a 147 rupee order
    appmod.db.session.refresh(order)
    assert order.payment_status == 'pending'


def test_webhook_twice_does_not_double_decrement(client, make_order):
    order, product = make_order(stock=10, qty=2)
    _webhook(client, _captured(14700))
    _webhook(client, _captured(14700))
    appmod.db.session.refresh(product)
    assert product.stock == 8


def test_verify_payment_rejects_other_users_order(client, make_order, monkeypatch):
    class _Util:
        def verify_payment_signature(self, params):
            return True

    class _Client:
        utility = _Util()

    monkeypatch.setattr(appmod, 'razorpay_client', _Client())
    order, _ = make_order(email='buyer@example.com')
    body = {'razorpay_payment_id': 'pay_1', 'razorpay_order_id': 'order_TEST1', 'razorpay_signature': 'sig'}

    login(client, 'stranger@example.com')
    assert client.post('/checkout/verify-payment', json=body).status_code == 404

    login(client, 'buyer@example.com')
    assert client.post('/checkout/verify-payment', json=body).status_code == 200
    assert client.post('/checkout/verify-payment', json=body).status_code == 200  # retry is harmless


# ---------- Shipping status mapping ----------

def test_normalize_shipping_status():
    n = appmod.normalize_shipping_status
    assert n('Delivered') == 'delivered'
    assert n('OUT_FOR_DELIVERY') == 'out for delivery'
    assert n('In Transit') == 'shipped'
    assert n('pickup_pending') == 'shipped'
    assert n('Data Received') == 'shipped'
    assert n('RTO Initiated') == 'rto'
    assert n('Cancelled') == 'cancelled'
    assert n('Undelivered - attempt failed') == 'shipped'
    assert n('some brand new courier status') is None
    assert n('') is None and n(None) is None


# ---------- Headers, slugs, sitemap ----------

def test_security_headers_present(client):
    r = client.get('/')
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    assert r.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert 'Referrer-Policy' in r.headers


def test_slugify():
    assert appmod.slugify("Garam Masala: The Secret Behind India's Soul!") == 'garam-masala-the-secret-behind-india-s-soul'
    assert appmod.slugify('  ') == 'post'


def test_sitemap_is_valid_xml_with_encoded_urls(client):
    import xml.etree.ElementTree as ET
    db = appmod.db
    db.session.add(appmod.Blog(title="T", slug="garam-masala:-india’s-legacy-", author='a', category='c',
                               date='2026-01-01', content='x'))
    db.session.commit()
    r = client.get('/sitemap.xml')
    ET.fromstring(r.data)  # raises if not well-formed
    text = r.get_data(as_text=True)
    assert '’' not in text and ':-' not in text
