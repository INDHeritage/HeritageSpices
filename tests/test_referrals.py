"""Referral updates: who is told what, points visibility for customers and the admin."""
import pytest

import app as appmod
import notifications
from conftest import ADMIN, login


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_USER', 'shop@example.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'secret')
    monkeypatch.setattr(notifications, 'RUN_IN_BACKGROUND', False)
    monkeypatch.setattr(notifications, '_send_now', lambda to, subject, text, html: sent.append((to, subject, text, html)))
    return sent


@pytest.fixture()
def telegram(monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, 'send_telegram_notification', lambda msg: sent.append(msg))
    return sent


def _user(email, name, code=None):
    row = appmod.User(name=name, email=email, referral_code=code)
    appmod.db.session.add(row)
    appmod.db.session.commit()
    return row


def _mails_to(outbox, email):
    return [m for m in outbox if m[0] == email]


# ---------- signup through a referral link ----------

def test_referrer_is_told_when_a_friend_signs_up(client, outbox, telegram):
    _user('asha@example.com', 'Asha', code='ASHA12')
    with appmod.app.test_request_context('/'):
        from flask import session
        session['ref_code'] = 'ASHA12'
        appmod.complete_login({'name': 'Ravi', 'email': 'ravi@example.com', 'picture': None})
    mails = _mails_to(outbox, 'asha@example.com')
    assert len(mails) == 1 and 'Ravi' in mails[0][1] and 'joined' in mails[0][1]
    assert appmod.Referral.query.filter_by(referred_email='ravi@example.com').count() == 1
    assert any('Ravi' in t and 'Asha' in t for t in telegram)


def test_no_referral_message_for_a_normal_signup(outbox, telegram):
    with appmod.app.test_request_context('/'):
        appmod.complete_login({'name': 'Ravi', 'email': 'ravi@example.com', 'picture': None})
    assert _mails_to(outbox, 'ravi@example.com') == []
    assert not any('referral' in t.lower() for t in telegram)


# ---------- reward on the friend's first paid order ----------

def _pending_referral(referrer='asha@example.com', friend='buyer@example.com'):
    _user(referrer, 'Asha')
    _user(friend, 'Buyer')
    appmod.db.session.add(appmod.Referral(referrer_email=referrer, referred_email=friend, status='pending'))
    appmod.db.session.commit()


def test_first_order_rewards_both_and_tells_both(make_order, outbox, telegram):
    _pending_referral()
    make_order(email='buyer@example.com')
    appmod.finalize_paid_order('order_TEST1', 'pay_1')

    referrer_mails = [m for m in _mails_to(outbox, 'asha@example.com') if 'reward' in m[1].lower()]
    assert len(referrer_mails) == 1 and 'REFER-' in referrer_mails[0][2]
    friend_mails = [m for m in _mails_to(outbox, 'buyer@example.com') if 'bonus points' in m[1]]
    assert len(friend_mails) == 1
    assert appmod.get_points_balance('buyer@example.com') == 50
    assert appmod.Referral.query.filter_by(referred_email='buyer@example.com').first().status == 'rewarded'
    assert any('Referral rewarded' in t for t in telegram)


def test_reward_messages_are_sent_only_once(make_order, outbox):
    _pending_referral()
    make_order(email='buyer@example.com')
    appmod.finalize_paid_order('order_TEST1', 'pay_1')
    appmod.finalize_paid_order('order_TEST1', 'pay_1')
    assert len([m for m in outbox if 'reward' in m[1].lower() or 'bonus points' in m[1]]) == 2


# ---------- what customers see ----------

def test_customer_sees_points_history_and_masked_friend_emails(client):
    _user('asha@example.com', 'Asha', code='ASHA12')
    appmod.db.session.add(appmod.Referral(referrer_email='asha@example.com', referred_email='ravi.kumar@gmail.com',
                                          status='pending'))
    appmod.db.session.add(appmod.PointsTransaction(user_email='asha@example.com', points=50, reason='Referral bonus'))
    appmod.db.session.add(appmod.PointsTransaction(user_email='asha@example.com', points=-20, reason='Redeemed at checkout'))
    appmod.db.session.commit()
    login(client, 'asha@example.com')
    html = client.get('/refer').get_data(as_text=True)
    assert 'Your points history' in html and 'Referral bonus' in html and '+50' in html and '-20' in html
    assert 'ra***@gmail.com' in html and 'ravi.kumar@gmail.com' not in html


def test_mask_email():
    assert appmod.mask_email('ravi.kumar@gmail.com') == 'ra***@gmail.com'
    assert appmod.mask_email('a@b.com') == 'a***@b.com'
    assert appmod.mask_email('') == ''


# ---------- what the admin sees / can do ----------

def test_admin_referrals_page_lists_names_with_links(client):
    _user('asha@example.com', 'Asha')
    appmod.db.session.add(appmod.PointsTransaction(user_email='asha@example.com', points=70, reason='Bonus'))
    appmod.db.session.commit()
    login(client, ADMIN)
    html = client.get('/admin/referrals').get_data(as_text=True)
    assert 'Asha' in html and '/admin/points/asha%40example.com' in html and 'Look up a customer' in html


def test_admin_points_page_shows_balance_history_and_relations(client):
    _user('asha@example.com', 'Asha')
    _user('ravi@example.com', 'Ravi')
    appmod.db.session.add(appmod.Referral(referrer_email='asha@example.com', referred_email='ravi@example.com', status='rewarded'))
    appmod.db.session.add(appmod.PointsTransaction(user_email='ravi@example.com', points=50, reason='Referral bonus - first purchase'))
    appmod.db.session.commit()
    login(client, ADMIN)
    html = client.get('/admin/points/ravi@example.com').get_data(as_text=True)
    assert 'Ravi' in html and 'Referral bonus - first purchase' in html and '+50' in html
    assert 'asha@example.com' in html  # referred by
    assert client.get('/admin/points/asha@example.com').status_code == 200


def test_admin_can_look_up_by_email(client):
    _user('asha@example.com', 'Asha')
    login(client, ADMIN)
    r = client.get('/admin/points?email=asha@example.com')
    assert r.status_code == 302 and '/admin/points/asha@example.com' in r.headers['Location']


def test_unknown_customer_lookup_is_friendly(client):
    login(client, ADMIN)
    r = client.get('/admin/points/nobody@example.com', follow_redirects=True)
    assert 'No customer found' in r.get_data(as_text=True)


def test_admin_can_add_and_remove_points_with_a_reason(client):
    _user('asha@example.com', 'Asha')
    login(client, ADMIN)
    client.post('/admin/points/asha@example.com/adjust', data={'points': '100', 'reason': 'Delivery delay apology'})
    assert appmod.get_points_balance('asha@example.com') == 100
    client.post('/admin/points/asha@example.com/adjust', data={'points': '-30', 'reason': 'Correction'})
    assert appmod.get_points_balance('asha@example.com') == 70
    reasons = [t.reason for t in appmod.PointsTransaction.query.all()]
    assert 'Admin adjustment: Delivery delay apology' in reasons


def test_admin_cannot_take_more_points_than_the_customer_has(client):
    _user('asha@example.com', 'Asha')
    appmod.db.session.add(appmod.PointsTransaction(user_email='asha@example.com', points=10, reason='x'))
    appmod.db.session.commit()
    login(client, ADMIN)
    r = client.post('/admin/points/asha@example.com/adjust', data={'points': '-50', 'reason': 'oops'}, follow_redirects=True)
    assert 'only has 10' in r.get_data(as_text=True)
    assert appmod.get_points_balance('asha@example.com') == 10


@pytest.mark.parametrize('data', [{'points': '0', 'reason': 'r'}, {'points': '10', 'reason': ''},
                                  {'points': 'abc', 'reason': 'r'}, {'points': '999999', 'reason': 'r'}])
def test_bad_adjustments_are_rejected(client, data):
    _user('asha@example.com', 'Asha')
    login(client, ADMIN)
    client.post('/admin/points/asha@example.com/adjust', data=data)
    assert appmod.get_points_balance('asha@example.com') == 0


def test_points_tools_are_admin_only(client):
    _user('asha@example.com', 'Asha')
    login(client, 'someone@example.com')
    assert client.get('/admin/points/asha@example.com').status_code == 403
    assert client.post('/admin/points/asha@example.com/adjust', data={'points': '10', 'reason': 'x'}).status_code == 403
    assert appmod.get_points_balance('asha@example.com') == 0
