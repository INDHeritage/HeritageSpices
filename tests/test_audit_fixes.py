"""Fixes from the September 2026 audit: weights, courier phone, webhook shapes, licence number, taglines."""
import hashlib
import hmac
import json

import app as appmod
import nimbus_api


def test_pouch_weight_comes_from_the_size_in_the_name():
    w = appmod.item_weight_kg
    assert w('Garam Masala - 50g', 55) == 0.055
    assert w('Garam Masala - 100g', 105) == 0.105
    assert w('Rice 1kg', 200) == 1.005
    assert w('Unsized blend', 40) == 0.06 and w('Unsized blend', 90) == 0.11   # fallback only


def test_five_large_pouches_stay_within_a_realistic_weight():
    assert round(5 * appmod.item_weight_kg('Garam Masala - 100g', 105), 3) == 0.525


def test_courier_phone_is_the_last_ten_digits(monkeypatch):
    sent = {}

    class R:
        status_code = 200
        def __init__(self, body): self.body = body
        def json(self): return self.body

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith('/orders'):
            sent.update(json['shipping_address'])
            return R({'success': True, 'data': {'order_id': 'N1'}})
        return R({'success': True, 'data': {'awb': 'A1', 'courier_name': 'X'}})

    monkeypatch.setattr(nimbus_api, 'is_configured', lambda: True)
    monkeypatch.setattr(nimbus_api.requests, 'post', fake_post)
    for raw in ('+91 89994 49765', '08999449765', '918999449765', '8999449765'):
        nimbus_api.create_shipment({'order_number': 'HS1', 'items': [], 'weight_kg': 0.1,
                                    'consignee': {'name': 'A', 'address': 'x', 'city': 'c', 'state': 's',
                                                  'pincode': '441222', 'phone': raw}})
        assert sent['phone'] == 8999449765, raw


def _signed(client, payload, monkeypatch):
    monkeypatch.setenv('NIMBUS_WEBHOOK_SECRET', 'sekret')
    body = json.dumps(payload).encode()
    sig = hmac.new(b'sekret', body, hashlib.sha256).hexdigest()
    return client.post('/api/nimbus/webhook', data=body, headers={'x-nimbus-signature': sig, 'Content-Type': 'application/json'})


def test_webhook_accepts_flat_and_wrapped_payloads(client, make_order, monkeypatch):
    order, _ = make_order()
    order.awb_number = 'AWB1'
    order.shipping_status = 'shipped'
    appmod.db.session.commit()
    assert _signed(client, {'awb': 'AWB1', 'status': 'Out for delivery'}, monkeypatch).status_code == 200
    assert appmod.SpiceOrder.query.first().shipping_status == 'out for delivery'
    assert _signed(client, {'event': 'tracking.updated', 'data': {'awb': 'AWB1', 'shipStatus': 'Delivered'}}, monkeypatch).status_code == 200
    assert appmod.SpiceOrder.query.first().shipping_status == 'delivered'


def test_licence_number_is_defined_once_and_matches_the_pouch(client):
    assert appmod.FSSAI_LICENSE == '21521275000514'
    for path in ('/', '/products'):
        html = client.get(path).get_data(as_text=True)
        assert appmod.FSSAI_LICENSE in html and '21521175000514' not in html, path


def test_pouch_taglines_are_used(client):
    html = client.get('/').get_data(as_text=True)
    assert 'Every Dish. Every Time.' in html and 'Rooted in Tradition. Rich in Flavor.' in html


def test_chat_widget_is_not_loaded_up_front(client):
    html = client.get('/').get_data(as_text=True)
    assert 'embed.tawk.to' in html and 'loadTawk' in html


def test_visit_logging_thread_cleans_up_inside_its_app_context(monkeypatch, capsys):
    import threading
    monkeypatch.setitem(appmod.app.config, 'TESTING', False)
    started = []
    real = threading.Thread

    class T(real):
        def start(self):
            started.append(self); super().start(); self.join()

    monkeypatch.setattr(appmod.threading, 'Thread', T)
    with appmod.app.test_request_context('/', headers={'User-Agent': 'Mozilla/5.0 Chrome/120'}):
        appmod.track_visit(None)
    assert started
    assert 'outside of application context' not in capsys.readouterr().err


def test_webhook_keeps_every_status_and_tracking_page_shows_them(client, make_order, monkeypatch):
    from conftest import login
    order, _ = make_order()
    order.awb_number = 'AWB9'; order.shipping_status = 'shipped'
    appmod.db.session.commit()
    for st in ('Picked Up', 'In Transit', 'In Transit', 'Out for delivery'):
        assert _signed(client, {'awb': 'AWB9', 'status': st, 'location': 'Nagpur'}, monkeypatch).status_code == 200
    rows = appmod.ShipmentEvent.query.order_by(appmod.ShipmentEvent.id).all()
    assert [r.status for r in rows] == ['Picked Up', 'In Transit', 'Out for delivery']   # repeat not stored twice
    monkeypatch.setattr(nimbus_api, 'track_shipment', lambda awb: {'success': False, 'history': [], 'current_status': 'Unknown'})
    login(client, order.user_email)
    html = client.get(f'/orders/{order.id}/track').get_data(as_text=True)
    assert 'Picked Up' in html and 'In Transit' in html and 'Out for delivery' in html


def test_failed_delivery_alerts_the_owner(client, make_order, monkeypatch):
    order, _ = make_order()
    order.awb_number = 'AWB7'; order.shipping_status = 'shipped'
    appmod.db.session.commit()
    sent = []
    monkeypatch.setattr(appmod, 'send_telegram_notification', lambda m: sent.append(m))
    _signed(client, {'awb': 'AWB7', 'status': 'Undelivered - customer not available'}, monkeypatch)
    assert sent and 'HS-TEST0001' in sent[0]
    sent.clear()
    _signed(client, {'awb': 'AWB7', 'status': 'In Transit'}, monkeypatch)
    assert not sent


def test_one_flat_charge_for_every_fallback(monkeypatch):
    monkeypatch.setattr(nimbus_api, 'is_configured', lambda: False)
    rates = nimbus_api.get_shipping_rates('441222', 0.11)
    assert len(rates) == 1 and rates[0]['rate'] == nimbus_api.SHIPPING_FLAT_INR


def test_booking_returns_the_delivery_date(monkeypatch):
    class R:
        status_code = 200
        def __init__(self, body): self.body = body
        def json(self): return self.body

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith('/orders'):
            return R({'success': True, 'data': {'order_id': 'N1'}})
        return R({'success': True, 'data': {'awb': 'A1', 'courier_name': 'X', 'edd': '2026-10-03T00:00:00Z'}})

    monkeypatch.setattr(nimbus_api, 'is_configured', lambda: True)
    monkeypatch.setattr(nimbus_api.requests, 'post', fake_post)
    r = nimbus_api.create_shipment({'order_number': 'HS1', 'items': [], 'weight_kg': 0.1,
                                    'consignee': {'name': 'A', 'address': 'x', 'city': 'c', 'state': 's',
                                                  'pincode': '441222', 'phone': '8999449765'}})
    assert r['success'] and r['estimated_delivery'] == '03 Oct 2026'


def test_public_pages_no_longer_claim_organic_or_worldwide(client):
    for path in ('/', '/products', '/about', '/faq'):
        html = client.get(path).get_data(as_text=True).lower()
        assert '100% organic' not in html and 'worldwide' not in html and 'across the globe' not in html, path
