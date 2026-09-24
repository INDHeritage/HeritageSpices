"""Google Merchant Center feed + verification tag, and the farm wording change."""
import os
import re
import xml.etree.ElementTree as ET

import pytest

import app as appmod

G = '{http://base.google.com/ns/1.0}'


def _add(name, price, stock=50, image='https://i.ibb.co/x/pic.png', description='A fine blend of spices.'):
    p = appmod.Product(name=name, description=description, price=price, image_url=image, stock=stock)
    appmod.db.session.add(p)
    appmod.db.session.commit()
    return p


def _feed(client):
    r = client.get('/feeds/google-merchant.xml')
    assert r.status_code == 200
    root = ET.fromstring(r.data)          # raises if the XML is not well-formed
    return r, root.find('channel').findall('item')


def _field(item, name):
    node = item.find(G + name)
    return node.text if node is not None else None


# ---------- the feed ----------

def test_feed_is_public_well_formed_rss(client):
    _add('Garam Masala - 50g', '55.0')
    r, items = _feed(client)
    assert r.headers['Content-Type'].startswith('application/xml') and len(items) == 1


def test_feed_has_every_attribute_google_requires(client):
    p = _add('Garam Masala - 50g', '55.0')
    _, (item,) = _feed(client)
    assert _field(item, 'id') == f'HS-{p.id}'
    assert _field(item, 'title') == 'Garam Masala - 50g'
    assert _field(item, 'description') == 'A fine blend of spices.'
    assert _field(item, 'link') == f'https://www.indianheritagespices.com/product/{p.id}'
    assert _field(item, 'image_link') == 'https://i.ibb.co/x/pic.png'
    assert _field(item, 'availability') == 'in_stock'
    assert _field(item, 'price') == '55.00 INR'
    assert _field(item, 'brand') == 'Heritage Spices'
    assert _field(item, 'condition') == 'new'
    assert _field(item, 'mpn') == f'HS-{p.id}'
    assert _field(item, 'google_product_category') == '4608'


def test_feed_price_and_availability_match_the_product_page(client):
    p = _add('Garam Masala - 100g', '105.0', stock=0)
    _, (item,) = _feed(client)
    page = client.get(f'/product/{p.id}').get_data(as_text=True)
    assert '₹105' in page and _field(item, 'price') == '105.00 INR'
    assert 'Currently out of stock' in page and _field(item, 'availability') == 'out_of_stock'


def test_feed_description_matches_the_page_text_not_html(client):
    p = _add('Garam Masala - 50g', '55.0', description='Line one.\nLine <b>two</b> & more')
    _, (item,) = _feed(client)
    assert _field(item, 'description') == 'Line one. Line two & more'
    assert 'Line two & more' in client.get(f'/product/{p.id}').get_data(as_text=True).replace('&amp;', '&')


def test_feed_escapes_special_characters(client):
    _add('Tom & "Jerry" <Spice> - 50g', '55.0', description='5 < 6 & 7 > 3')
    _, (item,) = _feed(client)
    assert _field(item, 'title') == 'Tom & "Jerry" <Spice> - 50g'


def test_feed_carries_shipping_weight_for_the_weight_based_rate(client):
    _add('Garam Masala - 50g', '55.0')
    _add('Garam Masala - 100g', '105.0')
    _, items = _feed(client)
    assert sorted(_field(i, 'shipping_weight') for i in items) == ['0.055 kg', '0.105 kg']


def test_sizes_of_one_product_share_a_group_id(client):
    _add('Garam Masala - 50g', '55.0')
    _add('Garam Masala - 100g', '105.0')
    _add('Turmeric - 100g', '80.0')
    _, items = _feed(client)
    groups = {_field(i, 'title'): _field(i, 'item_group_id') for i in items}
    assert groups['Garam Masala - 50g'] == groups['Garam Masala - 100g'] == 'garam-masala'
    assert groups['Turmeric - 100g'] is None   # a single size needs no group


def test_incomplete_products_are_left_out_rather_than_rejected(client):
    _add('Garam Masala - 50g', '55.0')
    _add('No price yet', None)
    _add('Not a number', 'ask us')
    _add('No photo', '20.0', image=None)
    _, items = _feed(client)
    assert [_field(i, 'title') for i in items] == ['Garam Masala - 50g']


def test_feed_declares_india_shipping_that_matches_the_published_policy(client):
    _add('Garam Masala - 50g', '55.0')
    _, (item,) = _feed(client)
    ship = item.find(G + 'shipping')
    assert ship.find(G + 'country').text == 'IN' and ship.find(G + 'price').text == '35.00 INR'
    # policy page: ships within 24-48 hours, delivery 3-7 days
    assert (ship.find(G + 'min_handling_time').text, ship.find(G + 'max_handling_time').text) == ('1', '2')
    assert (ship.find(G + 'min_transit_time').text, ship.find(G + 'max_transit_time').text) == ('3', '7')


def test_page_markup_uses_the_same_handling_time_as_the_feed(client):
    import json
    p = _add('Garam Masala - 50g', '55.0')
    html = client.get(f'/product/{p.id}').get_data(as_text=True)
    product = next(json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
                   if '"Product"' in b)
    handling = product['offers']['shippingDetails']['deliveryTime']['handlingTime']
    assert (handling['minValue'], handling['maxValue']) == (1, 2)


def test_feed_is_not_blocked_by_robots(client):
    assert '/feeds' not in client.get('/robots.txt').get_data(as_text=True)


def test_empty_catalogue_still_returns_valid_xml(client):
    _, items = _feed(client)
    assert items == []


# ---------- Google verification tag ----------

def test_no_verification_tag_by_default(client, monkeypatch):
    monkeypatch.delenv('GOOGLE_SITE_VERIFICATION', raising=False)
    assert 'google-site-verification' not in client.get('/').get_data(as_text=True)


def test_verification_tags_are_rendered_for_every_token(client, monkeypatch):
    monkeypatch.setenv('GOOGLE_SITE_VERIFICATION', 'abcdefghij1234567890_-XYZ, second-token-ABCDEF123456')
    html = client.get('/').get_data(as_text=True)
    assert html.count('name="google-site-verification"') == 2
    assert 'content="abcdefghij1234567890_-XYZ"' in html and 'content="second-token-ABCDEF123456"' in html


def test_verification_value_cannot_inject_markup(client, monkeypatch):
    monkeypatch.setenv('GOOGLE_SITE_VERIFICATION', '"><script>alert(1)</script> validtoken1234567890')
    html = client.get('/').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in html
    assert html.count('name="google-site-verification"') == 1     # only the valid token survived


def test_verification_tag_appears_on_every_page(client, monkeypatch):
    monkeypatch.setenv('GOOGLE_SITE_VERIFICATION', 'abcdefghij1234567890')
    for path in ('/', '/products', '/about', '/faq'):
        assert 'google-site-verification' in client.get(path).get_data(as_text=True), path


# ---------- farm wording ----------

def test_kerala_branding_is_gone_from_public_pages(client):
    _add('Garam Masala - 50g', '55.0')
    for path in ('/', '/products', '/about', '/faq', '/blog', '/contact', '/product/1'):
        html = client.get(path).get_data(as_text=True)
        assert 'kerala' not in html.lower(), path


def test_checkout_still_lists_kerala_as_a_state(client):
    root = appmod.app.root_path
    text = open(os.path.join(root, 'templates', 'checkout.html'), encoding='utf-8').read()
    assert '<option value="Kerala">Kerala</option>' in text


def test_farm_photo_file_exists_under_its_new_name(client):
    root = appmod.app.root_path
    assert os.path.exists(os.path.join(root, 'static', 'images', 'farm-fields.webp'))
    assert not os.path.exists(os.path.join(root, 'static', 'images', 'kerala-farmers.webp'))
    assert 'farm-fields.webp' in client.get('/about').get_data(as_text=True)
