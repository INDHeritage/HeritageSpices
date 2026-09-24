"""SEO regression tests: one H1, unique titles/descriptions, valid structured data, clean URLs."""
import json
import os
import re
from html.parser import HTMLParser

import pytest

import app as appmod

PUBLIC_PAGES = ['/', '/products', '/about', '/contact', '/faq', '/privacy', '/terms', '/refund', '/disclaimer', '/blog']


class _Head(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ''; self._t = False; self.meta = {}; self.canonical = None
        self.h1 = []; self._h1 = False; self.ld = []; self._ld = False; self.imgs = []

    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == 'title': self._t = True
        if tag == 'meta' and (a.get('name') or a.get('property')):
            self.meta[(a.get('name') or a.get('property')).lower()] = a.get('content', '')
        if tag == 'link' and a.get('rel') == 'canonical': self.canonical = a.get('href')
        if tag == 'h1': self._h1 = True; self.h1.append('')
        if tag == 'img': self.imgs.append(a)
        if tag == 'script' and a.get('type') == 'application/ld+json': self._ld = True; self.ld.append('')

    def handle_endtag(self, tag):
        if tag == 'title': self._t = False
        if tag == 'h1': self._h1 = False
        if tag == 'script': self._ld = False

    def handle_data(self, d):
        if self._t: self.title += d
        if self._h1: self.h1[-1] += d
        if self._ld: self.ld[-1] += d


def parse(client, path):
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code)
    h = _Head(); h.feed(r.get_data(as_text=True))
    return h


def _blocks(h):
    return [json.loads(x) for x in h.ld]  # raises if any block is invalid JSON


def _post(title='Garam Masala: The Secret Behind the Soul of Indian Cooking', slug='garam-masala:-the-secret-behind-the-soul-of-indian-cooking',
          date='2026-02-01', image='https://i.ibb.co/x/pic.jpg'):
    b = appmod.Blog(title=title, slug=slug, author='Team Heritage', category='Recipes', date=date,
                    content='<p>In every Indian kitchen there is one blend that holds a special place. ' + 'Spice ' * 60 + '</p>',
                    image_url=image)
    appmod.db.session.add(b)
    appmod.db.session.commit()
    return b


@pytest.fixture()
def site(client):
    appmod.db.session.add(appmod.Product(name='Garam Masala - 50g', description='Line one.\nLine two with "quotes" and a \\ backslash',
                                         price='55', stock=10, image_url='https://i.ibb.co/x/p.png'))
    _post()
    appmod.db.session.commit()
    appmod._recent_blogs_cache['data'] = None
    return client


# ---------- headings, titles, descriptions ----------

@pytest.mark.parametrize('path', PUBLIC_PAGES)
def test_every_public_page_has_exactly_one_h1(site, path):
    assert len(parse(site, path).h1) == 1, path


def test_titles_and_descriptions_are_unique_and_a_sensible_length(site):
    titles, descs = {}, {}
    for path in PUBLIC_PAGES:
        h = parse(site, path)
        title, desc = h.title.strip(), h.meta.get('description', '')
        assert 10 <= len(title) <= 65, (path, title)
        assert 50 <= len(desc) <= 160, (path, len(desc), desc)
        assert title not in titles, f'{path} shares a title with {titles[title]}'
        assert desc not in descs, f'{path} shares a description with {descs[desc]}'
        titles[title], descs[desc] = path, path


def test_descriptions_do_not_promise_products_that_are_not_sold(site):
    for path in ('/', '/products'):
        d = parse(site, path).meta['description'].lower()
        assert 'goda' not in d and 'turmeric' not in d


# ---------- canonical + social tags ----------

@pytest.mark.parametrize('path', PUBLIC_PAGES)
def test_canonical_is_absolute_www_without_trailing_slash(site, path):
    c = parse(site, path).canonical
    assert c.startswith('https://www.indianheritagespices.com')
    assert c == 'https://www.indianheritagespices.com/' or not c.endswith('/')


@pytest.mark.parametrize('path', PUBLIC_PAGES)
def test_social_tags_match_the_page(site, path):
    h = parse(site, path)
    assert h.meta['og:title'] == h.title.strip()
    assert h.meta['og:description'] == h.meta['description']
    assert h.meta['og:url'] == h.canonical
    assert h.meta['og:image'].startswith('https://') and h.meta['twitter:image'] == h.meta['og:image']
    assert h.meta['og:type'] == 'website'


def test_default_share_image_is_the_right_size():
    from PIL import Image
    path = os.path.join(appmod.app.root_path, 'static', 'images', 'og-default.jpg')
    assert Image.open(path).size == (1200, 630)


# ---------- structured data ----------

@pytest.mark.parametrize('path', PUBLIC_PAGES + ['/product/1'])
def test_all_structured_data_is_valid_json(site, path):
    _blocks(parse(site, path))


def test_organization_data_is_consistent_and_points_at_real_files(site):
    org = next(b for b in _blocks(parse(site, '/')) if b.get('@type') == 'Organization')
    assert org['url'].startswith('https://www.')
    logo_file = org['logo'].split('/static/', 1)[1]
    assert os.path.exists(os.path.join(appmod.app.root_path, 'static', logo_file)), org['logo']
    assert all('twitter.com/search' not in s for s in org['sameAs'])
    assert org['address']['addressLocality'] == 'Sindewahi' and org['address']['postalCode'] == '441222'


def test_products_page_lists_products_with_valid_markup(site):
    h = parse(site, '/products')
    lists = [b for b in _blocks(h) if b.get('@type') == 'ItemList']
    assert lists and lists[0]['itemListElement'][0]['url'].startswith('https://www.indianheritagespices.com/product/')
    assert not [b for b in _blocks(h) if b.get('@type') == 'Product'], 'per-card Product blocks were the broken ones'
    assert len(h.h1) == 1


def test_product_description_with_newlines_and_quotes_cannot_break_the_markup(site):
    # the live /products page shipped invalid JSON because of exactly this
    product = next(b for b in _blocks(parse(site, '/product/1')) if b.get('@type') == 'Product')
    assert 'Line two with "quotes"' in product['description']
    assert product['offers']['shippingDetails']['shippingRate']['currency'] == 'INR'
    assert product['offers']['hasMerchantReturnPolicy']['applicableCountry'] == 'IN'


def test_faq_markup_matches_the_visible_questions(site):
    html = site.get('/faq').get_data(as_text=True)
    faq = next(b for b in _blocks(parse(site, '/faq')) if b.get('@type') == 'FAQPage')
    questions = faq['mainEntity']
    assert len(questions) >= 4
    for q in questions:
        assert q['name'] in html
        text = q['acceptedAnswer']['text']
        assert len(text) > 40 and '<' not in text and '{{' not in text


# ---------- clean URLs ----------

@pytest.mark.parametrize('path,target', [('/products/', '/products'), ('/blog/', '/blog'), ('/faq/', '/faq')])
def test_trailing_slash_redirects_permanently(client, path, target):
    r = client.get(path)
    assert r.status_code == 301 and r.headers['Location'] == target


def test_trailing_slash_redirect_keeps_the_query_string(client):
    r = client.get('/blog/?page=2')
    assert r.status_code == 301 and r.headers['Location'] == '/blog?page=2'


def test_unknown_pages_are_real_404s(client):
    assert client.get('/definitely-not-a-page').status_code == 404


def test_robots_blocks_private_areas_but_not_content(client):
    txt = client.get('/robots.txt').get_data(as_text=True)
    for private in ('/admin/', '/cart', '/checkout', '/orders', '/login', '/api/'):
        assert f'Disallow: {private}' in txt
    for public in ('/product', '/blog', '/static', '/products'):
        assert f'Disallow: {public}' not in txt or public == '/product' and 'Disallow: /products' not in txt
    assert 'Sitemap: https://www.indianheritagespices.com/sitemap.xml' in txt


def test_sitemap_has_clean_unique_urls_with_lastmod_for_posts(site):
    import xml.etree.ElementTree as ET
    xml = site.get('/sitemap.xml').get_data(as_text=True)
    root = ET.fromstring(xml)
    ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    locs = [u.find('s:loc', ns).text for u in root.findall('s:url', ns)]
    assert len(locs) == len(set(locs))
    assert 'https://www.indianheritagespices.com/blog/garam-masala-the-secret-behind-the-soul-of-indian-cooking' in locs
    assert all(':' not in l.split('://', 1)[1] and '%' not in l for l in locs)
    assert '<lastmod>2026-02-01</lastmod>' in xml
    assert 'https://www.indianheritagespices.com/product/1' in locs


# ---------- blog posts ----------

CLEAN = '/blog/garam-masala-the-secret-behind-the-soul-of-indian-cooking'


def test_blog_post_is_served_on_its_clean_url(site):
    h = parse(site, CLEAN)
    assert h.canonical == 'https://www.indianheritagespices.com' + CLEAN
    assert h.meta['og:type'] == 'article' and h.meta['og:url'] == h.canonical
    assert h.meta['og:image'] == 'https://i.ibb.co/x/pic.jpg'


def test_old_style_blog_url_redirects_to_the_clean_one(site):
    r = site.get('/blog/garam-masala:-the-secret-behind-the-soul-of-indian-cooking')
    assert r.status_code == 301 and r.headers['Location'] == CLEAN
    r = site.get('/blog/garam-masala%3A-the-secret-behind-the-soul-of-indian-cooking')
    assert r.status_code == 301 and r.headers['Location'] == CLEAN


def test_unknown_blog_post_is_404(site):
    assert site.get('/blog/no-such-post').status_code == 404


def test_blog_post_has_article_and_breadcrumb_markup(site):
    blocks = _blocks(parse(site, CLEAN))
    article = next(b for b in blocks if b.get('@type') == 'Article')
    assert article['headline'].startswith('Garam Masala') and article['datePublished'] == '2026-02-01'
    assert article['publisher']['logo']['url'].endswith('logo.png') and article['author']['name'] == 'Team Heritage'
    crumbs = next(b for b in blocks if b.get('@type') == 'BreadcrumbList')
    assert [c['position'] for c in crumbs['itemListElement']] == [1, 2, 3]


def test_short_titles_get_the_brand_suffix_long_ones_do_not(site):
    _post(title='Storing Spices', slug='storing-spices')
    assert parse(site, '/blog/storing-spices').title.strip() == 'Storing Spices | Heritage Spices'
    assert parse(site, CLEAN).title.strip() == 'Garam Masala: The Secret Behind the Soul of Indian Cooking'


def test_blog_pages_link_to_clean_urls_only(site):
    _post(title='Another Post', slug="another:-post’s-slug")
    html = site.get('/blog').get_data(as_text=True) + site.get(CLEAN).get_data(as_text=True)
    hrefs = re.findall(r'href="(/blog/[^"]+)"', html)
    assert hrefs and all(re.fullmatch(r'/blog/[a-z0-9-]+', h) for h in hrefs), hrefs


def test_images_on_the_homepage_all_have_alt_text(site):
    assert all((i.get('alt') or '').strip() for i in parse(site, '/').imgs)


def test_headings_inside_a_post_body_never_add_a_second_h1(site):
    b = _post(title='Body With Heading', slug='body-with-heading')
    b.content = '<h1>Repeated big heading</h1><p>text</p><H1 class="x">Another</H1>'
    appmod.db.session.commit()
    h = parse(site, '/blog/body-with-heading')
    assert len(h.h1) == 1
    assert 'Repeated big heading' in site.get('/blog/body-with-heading').get_data(as_text=True)
