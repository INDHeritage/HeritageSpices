"""Customer e-mails, free WhatsApp links, and the courier-retry fix."""
import hashlib
import hmac
import json

import pytest

import app as appmod
import nimbus_api
import notifications
from conftest import ADMIN, login


@pytest.fixture()
def outbox(monkeypatch):
    """Turn e-mail on and capture what would have been sent."""
    sent = []
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_USER', 'shop@example.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'secret')
    monkeypatch.setattr(notifications, 'RUN_IN_BACKGROUND', False)
    monkeypatch.setattr(notifications, '_send_now',
                        lambda to, subject, text, html: sent.append((to, subject, text, html)))
    return sent


# ---------- Customer e-mails (free, SMTP) ----------

def test_no_email_is_sent_until_smtp_is_configured(make_order, monkeypatch):
    monkeypatch.delenv('SMTP_HOST', raising=False)
    calls = []
    monkeypatch.setattr(notifications, '_send_now', lambda *a: calls.append(a))
    make_order()
    appmod.finalize_paid_order('order_TEST1', 'pay_1')
    assert calls == []


def test_paying_sends_one_confirmation_email(make_order, outbox):
    make_order(email='buyer@example.com')
    appmod.finalize_paid_order('order_TEST1', 'pay_1')
    appmod.finalize_paid_order('order_TEST1', 'pay_1')  # duplicate call must not send a second mail
    assert len(outbox) == 1
    to, subject, text, html = outbox[0]
    assert to == 'buyer@example.com'
    assert 'HS-TEST0001' in subject and '₹147' in text and 'Garam Masala' in html


def test_email_content_is_html_escaped(make_order, outbox):
    order, _ = make_order()
    order.full_name = '<script>alert(1)</script>'
    appmod.db.session.commit()
    appmod.finalize_paid_order('order_TEST1', 'pay_1')
    assert '<script>' not in outbox[0][3]


def test_a_mail_failure_never_breaks_the_payment(make_order, monkeypatch):
    for var in ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD'):
        monkeypatch.setenv(var, 'x')
    monkeypatch.setattr(notifications, 'RUN_IN_BACKGROUND', False)

    def boom(*args):
        raise RuntimeError('smtp down')

    monkeypatch.setattr(notifications, '_send_now', boom)
    make_order()
    order, newly = appmod.finalize_paid_order('order_TEST1', 'pay_1')
    assert newly is True and order.payment_status == 'paid'


def test_courier_delivered_webhook_sends_delivery_email_once(client, make_order, outbox, monkeypatch):
    monkeypatch.setenv('NIMBUS_WEBHOOK_SECRET', 'nimbus_secret')
    order, _ = make_order()
    order.awb_number = 'AWB1'
    order.shipping_status = 'shipped'
    order.payment_status = 'paid'
    appmod.db.session.commit()

    def hook(status):
        body = json.dumps({'awb': 'AWB1', 'status': status}).encode()
        sig = hmac.new(b'nimbus_secret', body, hashlib.sha256).hexdigest()
        return client.post('/api/nimbus/webhook', data=body, headers={
            'x-nimbus-signature': sig, 'Content-Type': 'application/json'})

    assert hook('In Transit').status_code == 200
    assert outbox == []
    hook('Delivered')
    hook('Delivered')
    assert len(outbox) == 1 and 'delivered' in outbox[0][1].lower()


# ---------- Free WhatsApp click-to-chat links ----------

def test_wa_link_builds_a_ready_message(make_order):
    order, _ = make_order()
    order.phone = '+91 99999-99999'
    link = appmod.wa_link(order)
    assert link.startswith('https://wa.me/919999999999?text=')
    assert 'HS-TEST0001' in link

    order.shipping_status = 'shipped'
    order.awb_number = 'AWB9'
    order.courier_name = 'Xpressbees'
    link = appmod.wa_link(order)
    assert 'AWB9' in link and 'nimbuspost' in link


def test_wa_link_rejects_bad_numbers(make_order):
    order, _ = make_order()
    order.phone = '12345'
    assert appmod.wa_link(order) is None


def test_admin_orders_page_shows_whatsapp_button_for_paid_orders(client, make_order):
    order, _ = make_order()
    order.payment_status = 'paid'
    order.phone = '9999999999'
    appmod.db.session.commit()
    login(client, ADMIN)
    assert 'WhatsApp customer' in client.get('/admin/orders').get_data(as_text=True)


def test_storefront_whatsapp_button_only_when_number_configured(client, monkeypatch):
    monkeypatch.delenv('WHATSAPP_NUMBER', raising=False)
    assert 'WhatsApp us' not in client.get('/').get_data(as_text=True)
    monkeypatch.setenv('WHATSAPP_NUMBER', '8459593058')
    html = client.get('/').get_data(as_text=True)
    assert 'https://wa.me/918459593058' in html and 'mobile-cta-bar' in html


# ---------- Courier: a retry re-books instead of creating a duplicate order ----------

class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def _shipment_data(**extra):
    data = {'order_number': 'HS-1',
            'consignee': {'name': 'A', 'address': 'x', 'city': 'c', 'state': 's',
                          'pincode': '441222', 'phone': '9999999999'},
            'items': [{'name': 'Garam', 'quantity': 2, 'price': 55}], 'weight_kg': 0.12}
    data.update(extra)
    return data


def test_courier_retry_reuses_created_order(monkeypatch):
    monkeypatch.setattr(nimbus_api, 'NIMBUS_API_TOKEN', 't')
    monkeypatch.setattr(nimbus_api, 'NIMBUS_API_SECRET', 's')
    calls = []
    book_results = [_Resp(400, {'success': False, 'error': 'no courier'}),
                    _Resp(200, {'success': True, 'data': {'awb': 'AWB1', 'courier_name': 'X', 'label_url': 'u'}})]

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        if url.endswith('/orders'):
            return _Resp(201, {'success': True, 'data': {'order_id': 'ORD-1'}})
        return book_results.pop(0)

    monkeypatch.setattr(nimbus_api.requests, 'post', fake_post)

    first = nimbus_api.create_shipment(_shipment_data())
    assert first['success'] is False and first['nimbus_order_id'] == 'ORD-1'

    second = nimbus_api.create_shipment(_shipment_data(nimbus_order_id=first['nimbus_order_id']))
    assert second['success'] is True and second['awb_number'] == 'AWB1'
    assert sum(url.endswith('/orders') for url in calls) == 1, 'the courier order must be created only once'


def test_courier_retry_starts_fresh_if_remembered_order_is_gone(monkeypatch):
    monkeypatch.setattr(nimbus_api, 'NIMBUS_API_TOKEN', 't')
    monkeypatch.setattr(nimbus_api, 'NIMBUS_API_SECRET', 's')
    created = {'n': 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith('/orders'):
            created['n'] += 1
            return _Resp(201, {'success': True, 'data': {'order_id': 'ORD-NEW'}})
        if json['order_id'] == 'ORD-OLD':
            return _Resp(404, {'success': False, 'error': 'not found'})
        return _Resp(200, {'success': True, 'data': {'awb': 'AWB2', 'courier_name': 'X'}})

    monkeypatch.setattr(nimbus_api.requests, 'post', fake_post)
    result = nimbus_api.create_shipment(_shipment_data(nimbus_order_id='ORD-OLD'))
    assert result['success'] is True and result['nimbus_order_id'] == 'ORD-NEW' and created['n'] == 1
