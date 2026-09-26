"""Homepage sections and the query budget that keeps the page fast."""
import app as appmod


def _reset_blog_cache():
    appmod._recent_blogs_cache['data'] = None


def test_home_has_trust_strip_and_dish_picker(client):
    html = client.get('/').get_data(as_text=True)
    assert 'FSSAI Licensed' in html and appmod.FSSAI_LICENSE in html
    assert 'What will you cook today?' in html and 'data-dish="paneer"' in html


def test_recipes_strip_lists_latest_posts_with_clean_excerpts(client):
    _reset_blog_cache()
    db = appmod.db
    db.session.add(appmod.Blog(title='Old post', slug='old-post', author='a', category='Recipes', date='2026-01-01',
                               content='<p>old</p>'))
    db.session.add(appmod.Blog(title='Perfect Garam Masala Chicken', slug='garam-masala-chicken', author='a',
                               category='Recipes', date='2026-02-01',
                               content='<p>Start with <b>fresh</b> spices &amp; a hot pan. ' + 'word ' * 60 + '</p>'))
    db.session.commit()
    html = client.get('/').get_data(as_text=True)
    assert 'From our kitchen' in html
    assert 'Perfect Garam Masala Chicken' in html
    assert '/blog/garam-masala-chicken' in html
    assert '<b>fresh</b>' not in html and 'Start with fresh spices &amp; a hot pan.' in html
    _reset_blog_cache()


def test_recipes_strip_hidden_when_there_are_no_posts(client):
    _reset_blog_cache()
    assert 'From our kitchen' not in client.get('/').get_data(as_text=True)
    _reset_blog_cache()


def test_recipe_links_use_the_clean_url(client):
    _reset_blog_cache()
    appmod.db.session.add(appmod.Blog(title='T', slug='garam-masala:-india’s-legacy', author='a', category='c',
                                      date='2026-01-01', content='x'))
    appmod.db.session.commit()
    html = client.get('/').get_data(as_text=True)
    assert '/blog/garam-masala-indias-legacy' in html
    _reset_blog_cache()


def test_home_stays_within_query_budget_with_new_sections(client, make_order):
    make_order()
    human = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36'}
    client.get('/', headers=human)  # warm the settings and recent-posts caches
    from sqlalchemy import event
    seen = []
    engine = appmod.db.engine
    listener = lambda *a: seen.append(a[2])
    event.listen(engine, 'before_cursor_execute', listener)
    try:
        client.get('/', headers=human)
    finally:
        event.remove(engine, 'before_cursor_execute', listener)
    assert len(seen) <= 4, f'homepage now runs {len(seen)} statements; each costs ~0.3s in production'
