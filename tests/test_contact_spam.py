"""Contact form: real messages get through and alert the owner; the bots seen on the live site do not."""
import re

import pytest
import requests

import app as appmod
from conftest import ADMIN, login


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    appmod._redeem_attempts.clear()
    monkeypatch.setattr(appmod, 'CONTACT_MIN_SECONDS', 0)   # tests fill the form instantly
    monkeypatch.delenv('TURNSTILE_SECRET', raising=False)
    monkeypatch.delenv('TURNSTILE_SITE_KEY', raising=False)
    yield
    appmod._redeem_attempts.clear()


@pytest.fixture()
def telegram(monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, 'send_telegram_notification', lambda msg: sent.append(msg))
    return sent


def _token(client):
    html = client.get('/contact').get_data(as_text=True)
    return re.search(r'name="form_ts" value="([^"]+)"', html).group(1)


def _send(client, name='Asha', email='asha@example.com', message='Do you deliver garam masala to Pune?',
          token='auto', **extra):
    data = {'name': name, 'email': email, 'message': message,
            'form_ts': _token(client) if token == 'auto' else token}
    data.update(extra)
    return client.post('/contact', data=data, follow_redirects=True)


def _stored():
    return appmod.ContactMessage.query.count()


def test_real_message_is_saved_and_the_owner_is_alerted_on_telegram(client, telegram):
    r = _send(client)
    assert 'Thank you for contacting us' in r.get_data(as_text=True)
    assert _stored() == 1
    assert len(telegram) == 1 and 'Asha' in telegram[0] and 'Pune' in telegram[0]


def test_telegram_alert_escapes_html(client, telegram):
    _send(client, name='<b>Evil</b>', message='hello <i>there</i> friend')
    assert '<b>Evil</b>' not in telegram[0] and '&lt;b&gt;Evil' in telegram[0]


def test_bot_that_skips_the_form_token_is_silently_dropped(client, telegram):
    r = _send(client, token='')
    assert 'Thank you for contacting us' in r.get_data(as_text=True)  # the bot is not told
    assert _stored() == 0 and telegram == []


def test_forged_token_is_dropped(client):
    _send(client, token='not-a-real-token')
    assert _stored() == 0


def test_instant_submission_is_dropped(client, monkeypatch):
    monkeypatch.setattr(appmod, 'CONTACT_MIN_SECONDS', 4)
    _send(client)  # form rendered and posted within milliseconds
    assert _stored() == 0


def test_honeypot_field_is_dropped(client):
    _send(client, website='http://spam.example')
    assert _stored() == 0


@pytest.mark.parametrize('text', [
    'Click to win a brand new Lamborghini Aventador immediately https://telegra.ph/Win-a-new-Lamborghini-Aventador-today',
    'Be the next name drawn for the PlayStation 5 Pro www.example-prizes.ru/win',
    'Visit spamsite.com/offer for a great deal',
])
def test_messages_with_links_are_dropped(client, telegram, text):
    _send(client, name='HarryPop', email='hummingbird2283@gmail.com', message=text)
    assert _stored() == 0 and telegram == []


def test_link_in_the_name_field_is_dropped(client):
    _send(client, name='Visit http://spam.example')
    assert _stored() == 0


def test_identical_messages_are_stored_only_once(client):
    for sender in ('a@example.com', 'b@example.com', 'c@example.com'):
        _send(client, email=sender, message='Hi, I wanted to know your price.')
    assert _stored() == 1


def test_a_customer_can_still_write_in_hindi_or_marathi(client):
    _send(client, message='नमस्कार, तुमचा गरम मसाला पुण्याला मिळेल का?')
    assert _stored() == 1


def test_rate_limit_is_three_per_hour_per_visitor(client):
    for i in range(3):
        _send(client, message=f'Question number {i} about your spices')
    r = _send(client, message='Question number 4 about your spices')
    assert 'Too many messages' in r.get_data(as_text=True)
    assert _stored() == 3


def test_missing_fields_are_not_stored(client):
    _send(client, message='')
    assert _stored() == 0


def test_over_long_message_is_trimmed(client):
    _send(client, message='word ' * 5000)
    assert len(appmod.ContactMessage.query.first().message) <= 5000


# ---------- optional Cloudflare Turnstile ----------

def _turnstile(monkeypatch, verdict):
    monkeypatch.setenv('TURNSTILE_SECRET', 'secret')

    class Reply:
        def json(self):
            return {'success': verdict}
    monkeypatch.setattr(requests, 'post', lambda *a, **k: Reply())


def test_turnstile_failure_blocks_the_message(client, monkeypatch):
    _turnstile(monkeypatch, False)
    r = _send(client, **{'cf-turnstile-response': 'tok'})
    assert 'security check' in r.get_data(as_text=True) and _stored() == 0


def test_turnstile_success_allows_the_message(client, monkeypatch):
    _turnstile(monkeypatch, True)
    _send(client, **{'cf-turnstile-response': 'tok'})
    assert _stored() == 1


def test_turnstile_missing_answer_is_rejected_when_configured(client, monkeypatch):
    _turnstile(monkeypatch, True)
    _send(client)
    assert _stored() == 0


def test_turnstile_widget_only_shown_when_configured(client, monkeypatch):
    assert 'cf-turnstile' not in client.get('/contact').get_data(as_text=True)
    monkeypatch.setenv('TURNSTILE_SITE_KEY', 'site-key')
    assert 'cf-turnstile' in client.get('/contact').get_data(as_text=True)


# ---------- admin clean-up tools ----------

def _add(name, email, message):
    m = appmod.ContactMessage(name=name, email=email, message=message)
    appmod.db.session.add(m)
    appmod.db.session.commit()
    return m


def test_admin_bulk_delete_removes_only_the_ticked_messages(client):
    keep = _add('Real', 'real@example.com', 'Do you sell turmeric?')
    bad1 = _add('HarryPop', 'x@x.com', 'win https://telegra.ph/abc')
    bad2 = _add('HarryPop', 'y@x.com', 'win https://telegra.ph/def')
    login(client, ADMIN)
    r = client.post('/admin/messages/bulk-delete', data={'ids': [bad1.id, bad2.id]}, follow_redirects=True)
    assert 'Deleted 2 messages' in r.get_data(as_text=True)
    assert [m.id for m in appmod.ContactMessage.query.all()] == [keep.id]


def test_bulk_delete_with_nothing_selected_changes_nothing(client):
    _add('Real', 'real@example.com', 'Do you sell turmeric?')
    login(client, ADMIN)
    client.post('/admin/messages/bulk-delete', data={})
    assert _stored() == 1


def test_bulk_delete_is_admin_only(client):
    m = _add('Real', 'real@example.com', 'hi there')
    login(client, 'someone@example.com')
    assert client.post('/admin/messages/bulk-delete', data={'ids': [m.id]}).status_code == 403
    assert _stored() == 1


def test_admin_page_marks_link_and_duplicate_messages_as_spam_looking(client):
    good = _add('Real', 'real@example.com', 'Do you sell turmeric?')
    link = _add('Bot', 'b@x.com', 'win now https://telegra.ph/x')
    d1 = _add('Bot2', 'd@x.com', 'same text')
    d2 = _add('Bot2', 'd@x.com', 'same text')
    login(client, ADMIN)
    html = client.get('/admin/messages').get_data(as_text=True)

    def flagged(m):
        return re.search(rf'value="{m.id}"\s+data-spam="1"', html) is not None
    assert flagged(link) and flagged(d1) and flagged(d2) and not flagged(good)
