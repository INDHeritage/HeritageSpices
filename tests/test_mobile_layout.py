"""Phone layout regressions found in a mobile audit."""
import re

import pytest

import app as appmod


@pytest.fixture()
def site(client):
    appmod.db.session.add(appmod.Product(name='Garam Masala - 50g', description='x', price='60', stock=10))
    appmod.db.session.add(appmod.Blog(title='How to Use Garam Masala', slug='how-to-use-garam-masala', author='T',
                                      category='Recipes', date='2026-09-01', content='<p>' + 'Spice. ' * 40 + '</p>'))
    appmod.db.session.commit()
    appmod._recent_blogs_cache['data'] = None
    return client


def test_only_the_site_top_bar_is_styled_as_a_fixed_header(site):
    """A blog post has its own <header> (title/author). The menu-bar styling (fixed, grey, full width,
    z-index 1050) must not spill onto it, or the title block floats over the menu and the article."""
    html = site.get('/blog/how-to-use-garam-masala').get_data(as_text=True)
    assert re.search(r'body\s*>\s*header\s*\{', html)
    assert not re.search(r'(^|\n)\s*header\s*\{', html)
    assert html.count('<header') == 2                      # site bar + article header
    # the article header sits inside the page container, not directly under <body>
    assert html.index('<header class="text-center') > html.index('<div class="container py-4">')


@pytest.mark.parametrize('path', ['/cart', '/'])
def test_no_entrance_animation_slides_in_from_the_side(site, path):
    html = site.get(path).get_data(as_text=True)
    for bad in ('fadeInLeft', 'fadeInRight', 'slideInLeft', 'slideInRight', 'bounceInLeft', 'bounceInRight'):
        assert bad not in html, bad


def test_checkout_template_has_no_sideways_entrance_animations():
    src = open('templates/checkout.html', encoding='utf-8').read()
    assert 'fadeInLeft' not in src and 'fadeInRight' not in src


def test_page_can_never_scroll_sideways(site):
    assert 'overflow-x: clip' in site.get('/').get_data(as_text=True)


def test_cookie_notice_is_compact_on_phones_and_clears_the_sticky_bar(site):
    html = site.get('/').get_data(as_text=True)
    assert 'html.has-mobile-bar #cookie-banner' in html
    assert "classList.add('has-mobile-bar')" in html
