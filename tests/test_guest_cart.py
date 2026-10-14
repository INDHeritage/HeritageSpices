"""Guest cart: shoppers can add to cart and see it without an account (Google Merchant Center's
"Login required" policy); only checkout needs a login, and the guest cart moves onto the account at sign-in."""
import pytest

import app as appmod


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    appmod._redeem_attempts.clear()
    yield
    appmod._redeem_attempts.clear()


@pytest.fixture()
def products(client):
    a = appmod.Product(name='Garam Masala - 50g', description='x', price='60', stock=50)
    b = appmod.Product(name='Garam Masala - 100g', description='x', price='110', stock=50)
    appmod.db.session.add_all([a, b])
    appmod.db.session.commit()
    return a.id, b.id


def _add(client, pid, qty=2):
    return client.post('/cart/add', json={'product_id': pid, 'quantity': qty})


def _sign_in(client, monkeypatch, email='buyer@example.com'):
    monkeypatch.setattr(appmod, 'GOOGLE_ONE_TAP_ENABLED', True)
    monkeypatch.setattr(appmod, 'verify_google_credential',
                        lambda c: {'email': email, 'name': 'Buyer', 'picture': None})
    assert client.post('/auth/google-one-tap', json={'credential': 'x'}).status_code == 200


def test_guest_can_add_to_cart_without_logging_in(client, products):
    r = _add(client, products[0])
    assert r.status_code == 200 and r.get_json()['cart_count'] == 1


def test_guest_can_view_the_cart_with_no_login_redirect(client, products):
    _add(client, products[0], 3)
    r = client.get('/cart')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Garam Masala - 50g' in html
    assert 'sign in with google at checkout' in html.lower() and 'cart stays saved' in html


def test_empty_cart_page_is_public_too(client):
    assert client.get('/cart').status_code == 200
    assert client.get('/cart/count').get_json() == {'count': 0}


def test_two_browsers_have_separate_carts(app, client, products):
    other = app.test_client()
    _add(client, products[0])
    assert other.get('/cart/count').get_json() == {'count': 0}
    assert client.get('/cart/count').get_json() == {'count': 1}


def test_guest_can_update_and_remove_their_own_items_only(app, client, products):
    _add(client, products[0], 2)
    item = appmod.CartItem.query.one()
    assert client.post('/cart/update', json={'item_id': item.id, 'quantity': 4}).get_json()['cart_total'] == 240

    stranger = app.test_client()
    assert stranger.post('/cart/update', json={'item_id': item.id, 'quantity': 5}).status_code == 404
    stranger.post('/cart/remove', json={'item_id': item.id})
    assert appmod.CartItem.query.count() == 1          # the stranger could not delete it

    assert client.post('/cart/remove', json={'item_id': item.id}).get_json()['cart_count'] == 0


def test_limits_still_apply_to_guests(client, products):
    assert _add(client, products[0], 1).status_code == 400            # minimum 2
    assert _add(client, products[0], 5).status_code == 200
    assert _add(client, products[0], 2).status_code == 400            # would exceed 5


def test_guest_carts_are_rate_limited_per_connection(app, products):
    codes = [_add(app.test_client(), products[0]).status_code for _ in range(appmod.GUEST_CART_NEW_PER_HOUR + 1)]
    assert codes[:-1] == [200] * appmod.GUEST_CART_NEW_PER_HOUR and codes[-1] == 429


def test_checkout_still_needs_a_login_and_returns_there_afterwards(client, products):
    _add(client, products[0])
    r = client.get('/checkout')
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')
    with client.session_transaction() as sess:
        assert sess['post_login_next'] == '/checkout'


def test_signing_in_moves_the_guest_cart_onto_the_account(client, products, monkeypatch):
    _add(client, products[0], 2)
    _add(client, products[1], 3)
    _sign_in(client, monkeypatch)
    assert appmod.CartItem.query.filter(appmod.CartItem.user_email.like('guest:%')).count() == 0
    mine = {i.product_id: i.quantity for i in appmod.CartItem.query.filter_by(user_email='buyer@example.com')}
    assert mine == {products[0]: 2, products[1]: 3}
    with client.session_transaction() as sess:
        assert 'guest_cart' not in sess
    assert client.get('/cart/count').get_json() == {'count': 2}


def test_merge_adds_quantities_for_the_same_product_and_caps_at_five(client, products, monkeypatch):
    appmod.db.session.add(appmod.CartItem(user_email='buyer@example.com', product_id=products[0], quantity=4))
    appmod.db.session.commit()
    _add(client, products[0], 3)
    _sign_in(client, monkeypatch)
    rows = appmod.CartItem.query.filter_by(user_email='buyer@example.com').all()
    assert len(rows) == 1 and rows[0].quantity == 5
    assert appmod.CartItem.query.count() == 1


def test_admin_cart_list_ignores_guest_carts(client, products):
    from conftest import ADMIN
    _add(client, products[0])
    with client.session_transaction() as sess:
        sess['user'] = {'email': ADMIN, 'name': 'Admin'}
        sess.pop('guest_cart', None)
    html = client.get('/admin/carts').get_data(as_text=True)
    assert 'guest:' not in html


def test_old_guest_carts_are_purged_when_a_new_one_starts(app, client, products):
    from datetime import datetime, timedelta
    appmod.db.session.add(appmod.CartItem(user_email='guest:stale', product_id=products[0], quantity=2,
                                          added_at=datetime.utcnow() - timedelta(days=45)))
    appmod.db.session.add(appmod.CartItem(user_email='guest:recent', product_id=products[0], quantity=2,
                                          added_at=datetime.utcnow() - timedelta(days=2)))
    appmod.db.session.commit()
    _add(client, products[1])
    emails = {i.user_email for i in appmod.CartItem.query.all()}
    assert 'guest:stale' not in emails and 'guest:recent' in emails


@pytest.mark.parametrize('path', ['/refer', '/orders', '/my-coupons'])
def test_account_pages_still_need_login_and_bring_you_back_after_signing_in(client, monkeypatch, path):
    r = client.get(path)
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')

    monkeypatch.setattr(appmod.google, 'authorize_access_token', lambda: {'nonce': 'n'})
    monkeypatch.setattr(appmod.google, 'parse_id_token',
                        lambda token, nonce=None: {'email': 'back@example.com', 'name': 'Back', 'picture': None})
    r = client.get('/auth')
    assert r.status_code == 302 and r.headers['Location'].endswith(path)
    assert client.get(path).status_code == 200            # and now it opens


def test_plain_sign_in_still_lands_on_the_homepage(client, monkeypatch):
    monkeypatch.setattr(appmod.google, 'authorize_access_token', lambda: {'nonce': 'n'})
    monkeypatch.setattr(appmod.google, 'parse_id_token',
                        lambda token, nonce=None: {'email': 'plain@example.com', 'name': 'Plain', 'picture': None})
    r = client.get('/auth')
    from urllib.parse import urlparse
    assert r.status_code == 302 and urlparse(r.headers['Location']).path == '/'
