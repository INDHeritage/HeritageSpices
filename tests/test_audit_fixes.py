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
