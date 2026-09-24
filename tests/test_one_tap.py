"""Google One Tap sign-in: the server must verify the token and behave exactly like the normal button."""
import pytest

import app as appmod
from conftest import ADMIN


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    appmod._redeem_attempts.clear()
    yield
    appmod._redeem_attempts.clear()


@pytest.fixture()
def one_tap_on(monkeypatch):
    monkeypatch.setattr(appmod, 'GOOGLE_ONE_TAP_ENABLED', True)
    monkeypatch.setitem(appmod.app.config, 'GOOGLE_CLIENT_ID', 'test-client-id.apps.googleusercontent.com')


def _google(email='new.customer@example.com', name='New Customer'):
    return {'email': email, 'name': name, 'picture': None, 'email_verified': True, 'iss': 'https://accounts.google.com'}


def _post(client, credential='fake-jwt'):
    return client.post('/auth/google-one-tap', json={'credential': credential})


def test_endpoint_is_off_by_default(client):
    assert _post(client).status_code == 404


def test_valid_token_logs_in_and_issues_welcome_coupon(client, one_tap_on, monkeypatch):
    monkeypatch.setattr(appmod, 'verify_google_credential', lambda c: _google())
    r = _post(client)
    assert r.status_code == 200 and r.get_json()['success'] is True
    with client.session_transaction() as sess:
        assert sess['user']['email'] == 'new.customer@example.com'
    assert appmod.User.query.filter_by(email='new.customer@example.com').count() == 1
    assert appmod.Coupon.query.filter_by(user_email='new.customer@example.com').count() == 1


def test_returning_user_does_not_get_a_second_welcome_coupon(client, one_tap_on, monkeypatch):
    monkeypatch.setattr(appmod, 'verify_google_credential', lambda c: _google())
    _post(client)
    client.get('/logout')
    _post(client)
    assert appmod.User.query.filter_by(email='new.customer@example.com').count() == 1
    assert appmod.Coupon.query.filter_by(user_email='new.customer@example.com').count() == 1


def test_bad_token_is_rejected_and_nobody_is_logged_in(client, one_tap_on, monkeypatch):
    def reject(credential):
        raise ValueError('bad token')
    monkeypatch.setattr(appmod, 'verify_google_credential', reject)
    r = _post(client, 'forged')
    assert r.status_code == 401
    with client.session_transaction() as sess:
        assert 'user' not in sess


def test_missing_credential_is_rejected(client, one_tap_on):
    assert _post(client, credential=None).status_code == 401


def test_referral_code_is_linked_like_the_normal_login(client, one_tap_on, monkeypatch):
    appmod.db.session.add(appmod.User(name='Ref', email='friend@example.com', referral_code='ABC123'))
    appmod.db.session.commit()
    with client.session_transaction() as sess:
        sess['ref_code'] = 'ABC123'
    monkeypatch.setattr(appmod, 'verify_google_credential', lambda c: _google())
    _post(client)
    assert appmod.Referral.query.filter_by(referrer_email='friend@example.com',
                                           referred_email='new.customer@example.com').count() == 1


def test_admin_account_signing_in_with_one_tap_is_admin(client, one_tap_on, monkeypatch):
    monkeypatch.setattr(appmod, 'verify_google_credential', lambda c: _google(ADMIN, 'Owner'))
    _post(client)
    assert client.get('/admin/dashboard').status_code == 200


def test_endpoint_is_rate_limited(client, one_tap_on, monkeypatch):
    monkeypatch.setattr(appmod, 'verify_google_credential', lambda c: (_ for _ in ()).throw(ValueError('x')))
    codes = [_post(client).status_code for _ in range(12)]
    assert codes[0] == 401 and codes[-1] == 429


# ---- the real verifier: everything Google's library doesn't check for us ----

def _patch_google(monkeypatch, claims):
    from google.oauth2 import id_token
    monkeypatch.setattr(id_token, 'verify_oauth2_token', lambda credential, request, audience: dict(claims))


def test_verifier_accepts_a_good_token(one_tap_on, monkeypatch):
    _patch_google(monkeypatch, _google())
    assert appmod.verify_google_credential('x')['email'] == 'new.customer@example.com'


@pytest.mark.parametrize('claims', [
    {**_google(), 'email_verified': False},
    {**_google(), 'iss': 'https://evil.example.com'},
    {**_google(), 'email': ''},
])
def test_verifier_rejects_unverified_or_foreign_tokens(one_tap_on, monkeypatch, claims):
    _patch_google(monkeypatch, claims)
    with pytest.raises(ValueError):
        appmod.verify_google_credential('x')


def test_verifier_needs_a_client_id(monkeypatch):
    monkeypatch.setitem(appmod.app.config, 'GOOGLE_CLIENT_ID', None)
    with pytest.raises(ValueError):
        appmod.verify_google_credential('x')


# ---- what the visitor's browser is given ----

def test_popup_script_only_for_logged_out_visitors_on_safe_pages(client, one_tap_on):
    assert 'accounts.google.com/gsi/client' in client.get('/').get_data(as_text=True)
    assert 'accounts.google.com/gsi/client' not in client.get('/cart').get_data(as_text=True)
    with client.session_transaction() as sess:
        sess['user'] = {'email': 'x@example.com', 'name': 'X'}
    assert 'accounts.google.com/gsi/client' not in client.get('/').get_data(as_text=True)


def test_popup_script_absent_when_feature_is_off(client):
    assert 'accounts.google.com/gsi/client' not in client.get('/').get_data(as_text=True)


def test_add_to_cart_login_prompt_helper_is_always_available(client):
    html = client.get('/').get_data(as_text=True)
    assert 'heritageAskLogin' in html
