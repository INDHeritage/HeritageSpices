"""Individual product pages: SEO data must be truthful, sizes linked, sitemap complete."""
import json
import re

import app as appmod
from conftest import login


def _add_product(name, price, stock=50, image='https://i.ibb.co/x/pic.png', description='A fine <b>blend</b> of spices.'):
    p = appmod.Product(name=name, description=description, price=price, image_url=image, stock=stock)
    appmod.db.session.add(p)
    appmod.db.session.commit()
    return p


def _json_ld(html):
    """The Product block (the base template also carries an Organization block)."""
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        data = json.loads(raw)
        if data.get('@type') == 'Product':
            return data
    raise AssertionError('no Product structured data on the page')


def test_product_page_shows_the_product(client):
    p = _add_product('Garam Masala - 50g', '55.0')
    r = client.get(f'/product/{p.id}')
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'Garam Masala - 50g' in html and '₹55' in html
    assert '₹11.0 per 10 g' in html
    assert 'A fine blend of spices.' in html and '<b>blend</b>' not in html


def test_missing_product_is_404(client):
    assert client.get('/product/99999').status_code == 404


def test_other_sizes_are_linked_and_ordered(client):
    a = _add_product('Garam Masala - 100g', '105.0')
    b = _add_product('Garam Masala - 50g', '55.0')
    _add_product('Turmeric - 100g', '80.0')
    html = client.get(f'/product/{b.id}').get_data(as_text=True)
    assert f'/product/{a.id}' in html and 'pd-size active' in html
    assert html.index('50g</a>') < html.index('100g</a>')
    assert 'Turmeric' not in html.split('Choose size')[1].split('</div>')[0]


def test_structured_data_never_invents_ratings(client):
    p = _add_product('Garam Masala - 50g', '55.0')
    data = _json_ld(client.get(f'/product/{p.id}').get_data(as_text=True))
    assert data['@type'] == 'Product' and data['offers']['price'] == '55.00'
    assert data['offers']['availability'].endswith('InStock')
    assert 'aggregateRating' not in data and 'review' not in data


def test_only_approved_reviews_are_shown_and_counted(client):
    p = _add_product('Garam Masala - 50g', '55.0')
    db = appmod.db
    db.session.add(appmod.ProductReview(product_id=p.id, user_name='Asha', user_email='a@x.com', rating=5,
                                        review_text='Lovely aroma', status='approved'))
    db.session.add(appmod.ProductReview(product_id=p.id, user_name='Spammer', user_email='s@x.com', rating=1,
                                        review_text='buy pills', status='pending'))
    db.session.commit()
    html = client.get(f'/product/{p.id}').get_data(as_text=True)
    assert 'Lovely aroma' in html and 'buy pills' not in html
    data = _json_ld(html)
    assert data['aggregateRating']['reviewCount'] == '1' and data['aggregateRating']['ratingValue'] == '5.0'


def test_review_text_cannot_break_out_of_the_structured_data(client):
    p = _add_product('Garam Masala - 50g', '55.0')
    appmod.db.session.add(appmod.ProductReview(
        product_id=p.id, user_name='X', user_email='x@x.com', rating=4, status='approved',
        review_text='</script><script>alert(1)</script> "quoted" \\ backslash'))
    appmod.db.session.commit()
    html = client.get(f'/product/{p.id}').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in html
    assert _json_ld(html)['review'][0]['reviewBody'].startswith('</script>')  # valid JSON, safely escaped


def test_out_of_stock_hides_the_buy_button(client):
    p = _add_product('Garam Masala - 50g', '55.0', stock=0)
    html = client.get(f'/product/{p.id}').get_data(as_text=True)
    assert 'Currently out of stock' in html and 'id="pd-btn"' not in html
    assert _json_ld(html)['offers']['availability'].endswith('OutOfStock')


def test_low_stock_message_is_only_shown_for_real_low_stock(client):
    p = _add_product('Garam Masala - 50g', '55.0', stock=3)
    assert 'Only 3 left' in client.get(f'/product/{p.id}').get_data(as_text=True)
    q = _add_product('Turmeric - 50g', '40.0', stock=500)
    html = client.get(f'/product/{q.id}').get_data(as_text=True)
    assert 'Only' not in html.split('Choose size')[0] and 'In stock' in html


def test_page_has_its_own_title_description_and_canonical(client):
    p = _add_product('Garam Masala - 50g', '55.0')
    html = client.get(f'/product/{p.id}').get_data(as_text=True)
    assert '<title>Garam Masala - 50g | Heritage Spices</title>' in html
    assert f'rel="canonical" href="https://www.indianheritagespices.com/product/{p.id}"' in html
    assert 'og:image" content="https://i.ibb.co/x/pic.png"' in html


def test_default_social_image_is_an_absolute_url_to_a_real_file(client):
    html = client.get('/about').get_data(as_text=True)
    m = re.search(r'property="og:image" content="([^"]+)"', html)
    assert m.group(1).startswith('https://www.indianheritagespices.com') and m.group(1).endswith('og-default.jpg')
    import os
    assert os.path.exists(os.path.join(appmod.app.root_path, 'static', 'images', 'og-default.jpg'))


def test_sitemap_lists_product_pages(client):
    p = _add_product('Garam Masala - 50g', '55.0')
    assert f'/product/{p.id}</loc>' in client.get('/sitemap.xml').get_data(as_text=True)


def test_cards_link_to_the_product_page(client):
    p = _add_product('Garam Masala - 50g', '55.0')
    assert f'href="/product/{p.id}"' in client.get('/').get_data(as_text=True)
    assert f'href="/product/{p.id}"' in client.get('/products').get_data(as_text=True)


def test_price_per_10g_helper():
    assert appmod.product_grams('Garam Masala - 100g') == 100.0
    assert appmod.product_grams('Rice 1kg') == 1000.0
    assert appmod.product_grams('Garam Masala') is None
    assert appmod.product_base_name('Garam Masala - 50g') == 'Garam Masala'
    assert appmod.product_base_name('Garam Masala') == 'Garam Masala'


def test_product_page_stays_within_query_budget(client):
    from sqlalchemy import event
    a = _add_product('Garam Masala - 100g', '105.0')
    _add_product('Garam Masala - 50g', '55.0')
    human = {'User-Agent': 'Mozilla/5.0 Chrome/120'}
    client.get(f'/product/{a.id}', headers=human)  # warm caches
    seen = []
    engine = appmod.db.engine
    listener = lambda *args: seen.append(args[2])
    event.listen(engine, 'before_cursor_execute', listener)
    try:
        client.get(f'/product/{a.id}', headers=human)
    finally:
        event.remove(engine, 'before_cursor_execute', listener)
    assert len(seen) <= 3, f'product page runs {len(seen)} statements; each costs ~0.3s in production'
