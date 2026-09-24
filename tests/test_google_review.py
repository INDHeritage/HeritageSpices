"""'Review us on Google' link: appears only when configured, never invents a rating."""
import app as appmod
import notifications


def test_nothing_shown_without_a_link(client, monkeypatch):
    monkeypatch.delenv('GOOGLE_REVIEW_URL', raising=False)
    assert 'google-review-badge' not in client.get('/').get_data(as_text=True)


def test_badge_shows_link_only(client, monkeypatch):
    monkeypatch.setenv('GOOGLE_REVIEW_URL', 'https://g.page/r/abc123/review')
    monkeypatch.delenv('GOOGLE_RATING', raising=False)
    html = client.get('/').get_data(as_text=True)
    assert 'href="https://g.page/r/abc123/review"' in html and 'Review us on Google' in html
    assert 'reviews)' not in html


def test_badge_shows_typed_in_rating(client, monkeypatch):
    monkeypatch.setenv('GOOGLE_REVIEW_URL', 'https://g.page/r/abc123/review')
    monkeypatch.setenv('GOOGLE_RATING', '4.8')
    monkeypatch.setenv('GOOGLE_REVIEW_COUNT', '12')
    html = client.get('/').get_data(as_text=True)
    assert '4.8' in html and '(12 reviews)' in html


def test_bad_values_are_ignored(client, monkeypatch):
    monkeypatch.setenv('GOOGLE_REVIEW_URL', 'https://g.page/r/abc123/review')
    monkeypatch.setenv('GOOGLE_RATING', '9')
    monkeypatch.setenv('GOOGLE_REVIEW_COUNT', 'x')
    assert appmod.google_review_info()['rating'] is None
    monkeypatch.setenv('GOOGLE_REVIEW_URL', 'javascript:alert(1)')
    assert appmod.google_review_info() is None


def test_delivered_email_includes_link_when_set(monkeypatch):
    class O: order_number = 'HS1'; full_name = 'Asha'
    monkeypatch.setenv('GOOGLE_REVIEW_URL', 'https://g.page/r/abc123/review')
    _, text, html = notifications.order_delivered(O)
    assert 'https://g.page/r/abc123/review' in text and 'Review us on Google' in html
    monkeypatch.delenv('GOOGLE_REVIEW_URL')
    _, text, html = notifications.order_delivered(O)
    assert 'g.page' not in text and 'Review us on Google' not in html
