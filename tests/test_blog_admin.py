"""The admin password must never be written into a page, and deleting a post needs an admin session."""
import os

from conftest import ADMIN, login
import app as appmod


def _post(app):
    b = appmod.Blog(title='T', slug='t', author='a', category='c', date='2026-01-01', content='<p>x</p>')
    appmod.db.session.add(b)
    appmod.db.session.commit()
    return b


def test_admin_pages_do_not_leak_password(app, client, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'S3cretPass!')
    b = _post(app)
    login(client, ADMIN)
    for url in ('/blog', f'/blog/{b.slug}', '/admin/blogs'):
        r = client.get(url, follow_redirects=True)
        assert r.status_code == 200
        assert b'S3cretPass!' not in r.data
        assert b'name="password"' not in r.data


def test_delete_blog_requires_admin(app, client):
    b = _post(app)
    assert client.post(f'/admin/blogs/delete/{b.id}').status_code == 403
    login(client, 'someone@example.com')
    assert client.post(f'/admin/blogs/delete/{b.id}').status_code == 403
    assert appmod.db.session.get(appmod.Blog, b.id) is not None
    login(client, ADMIN)
    assert client.post(f'/admin/blogs/delete/{b.id}').status_code == 302
    assert appmod.db.session.get(appmod.Blog, b.id) is None
