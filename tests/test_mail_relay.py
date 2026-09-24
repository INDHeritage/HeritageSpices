"""E-mail over an HTTPS relay (works on Render's free plan, which blocks SMTP) and the nav fix."""
import pytest
import requests

import app as appmod
import notifications
from conftest import ADMIN, login


@pytest.fixture()
def relay(monkeypatch):
    """Configure the HTTPS relay and record every request made to it."""
    for var in ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD'):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv('EMAIL_WEBHOOK_URL', 'https://script.google.com/macros/s/TEST/exec')
    monkeypatch.setenv('EMAIL_WEBHOOK_SECRET', 'relay-secret')
    monkeypatch.setattr(notifications, 'RUN_IN_BACKGROUND', False)
    calls = []

    class Reply:
        def __init__(self, payload, status=200):
            self._payload, self.status_code = payload, status

        def json(self):
            if isinstance(self._payload, Exception):
                raise self._payload
            return self._payload

    state = {'reply': Reply({'ok': True})}

    def fake_post(url, json=None, timeout=None, **kwargs):
        calls.append((url, json))
        return state['reply']

    monkeypatch.setattr(requests, 'post', fake_post)
    return calls, state, Reply


def test_relay_counts_as_configured(relay):
    assert notifications.provider() == 'webhook' and notifications.is_configured()


def test_relay_needs_both_url_and_secret(monkeypatch):
    for var in ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'EMAIL_WEBHOOK_SECRET'):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv('EMAIL_WEBHOOK_URL', 'https://example.com')
    assert notifications.provider() is None


def test_relay_receives_the_message_and_secret(relay):
    calls, _, _ = relay
    assert notifications.send_email('buyer@example.com', 'Subject', 'Body text', '<p>Body</p>') is True
    url, payload = calls[0]
    assert url.startswith('https://script.google.com/')
    assert payload['to'] == 'buyer@example.com' and payload['secret'] == 'relay-secret'
    assert payload['subject'] == 'Subject' and payload['html'] == '<p>Body</p>'


def test_relay_is_preferred_over_smtp(relay, monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.gmail.com')
    monkeypatch.setenv('SMTP_USER', 'x@gmail.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'pw')
    assert notifications.provider() == 'webhook'


def test_relay_refusal_raises_a_readable_error(relay):
    _, state, Reply = relay
    state['reply'] = Reply({'ok': False, 'error': 'unauthorized'})
    with pytest.raises(RuntimeError, match='unauthorized'):
        notifications._send_now('a@b.com', 's', 't', None)


def test_relay_non_json_reply_gives_a_helpful_hint(relay):
    _, state, Reply = relay
    state['reply'] = Reply(ValueError('not json'), status=200)
    with pytest.raises(RuntimeError, match="access 'Anyone'"):
        notifications._send_now('a@b.com', 's', 't', None)


def test_admin_test_button_uses_the_relay_and_reports_success(client, relay):
    calls, _, _ = relay
    login(client, ADMIN)
    r = client.post('/admin/test-email', follow_redirects=True)
    assert 'Test e-mail sent' in r.get_data(as_text=True)
    assert calls and calls[0][1]['to'] == ADMIN


def test_admin_test_button_shows_relay_error(client, relay):
    _, state, Reply = relay
    state['reply'] = Reply({'ok': False, 'error': 'unauthorized'})
    login(client, ADMIN)
    r = client.post('/admin/test-email', follow_redirects=True)
    assert 'unauthorized' in r.get_data(as_text=True)


# ---------- Science Hub link is hidden for everyone while the hub is off ----------

def test_science_hub_link_hidden_even_for_admin(client):
    login(client, ADMIN)
    assert 'href="/science-hub"' not in client.get('/').get_data(as_text=True)


def test_admin_can_still_reach_science_hub_directly(client):
    login(client, ADMIN)
    assert client.get('/science-hub').status_code == 200
    assert 'Science Hub Admin' in client.get('/admin/dashboard').get_data(as_text=True)


def test_science_hub_link_returns_when_enabled(client, monkeypatch):
    monkeypatch.setattr(appmod, 'SCIENCE_HUB_ENABLED', True)
    assert 'href="/science-hub"' in client.get('/').get_data(as_text=True)
