"""Batch lab-test certificates: look-up status, Google + e-mailed-code gate, admin upload."""
import io
import re
from datetime import datetime, timedelta

import pytest

import app as appmod
import batch_certs
import notifications
from conftest import ADMIN, login

PDF = b'%PDF-1.4\n%fake but valid-looking\n'
CUSTOMER = 'buyer@example.com'


@pytest.fixture()
def mails(monkeypatch):
    """Capture e-mails instead of sending them; pretend e-mail is configured."""
    sent = []
    monkeypatch.setattr(notifications, 'is_configured', lambda: True)
    monkeypatch.setattr(notifications, 'send_email', lambda to, subject, text, html=None, attachment=None: sent.append((to, subject, text)) or True)
    appmod._redeem_attempts.clear()
    return sent


def make_batch(code='GM-1', with_cert=True):
    b = appmod.Batch(code=code, product_name='Garam Masala - 50g', mfg_date='Oct 2026')
    appmod.db.session.add(b)
    appmod.db.session.flush()
    if with_cert:
        appmod.db.session.add(appmod.BatchCertificate(batch_id=b.id, title='Lab report', ext='pdf', mimetype='application/pdf',
                                                      size=len(PDF), data=PDF, uploaded_by=ADMIN))
    appmod.db.session.commit()
    return b


def otp_from(mails):
    return re.search(r'is (\d{6})', mails[-1][2]).group(1)


# ---------- pure helpers ----------
def test_normalize_code():
    assert batch_certs.normalize_code('  gm-26/10 a<b> ') == 'GM-26/10AB'
    assert batch_certs.normalize_code('') == '' and batch_certs.normalize_code(None) == ''
    assert len(batch_certs.normalize_code('x' * 100)) == batch_certs.CODE_MAX_LEN


def test_status_for():
    assert batch_certs.status_for(None, 0) == 'not_found'
    assert batch_certs.status_for(object(), 0) == 'coming_soon'
    assert batch_certs.status_for(object(), 2) == 'available'


def test_otp_hash_is_bound_to_email_and_batch():
    h = batch_certs.hash_otp('k', 'A@x.com', 'GM-1', '123456')
    assert batch_certs.otp_matches('k', 'a@x.com', 'GM-1', '123456', h)
    assert not batch_certs.otp_matches('k', 'b@x.com', 'GM-1', '123456', h)
    assert not batch_certs.otp_matches('k', 'a@x.com', 'GM-2', '123456', h)
    assert not batch_certs.otp_matches('k', 'a@x.com', 'GM-1', '000000', h)
    assert not batch_certs.otp_matches('k', 'a@x.com', 'GM-1', 'abcdef', h)
    assert re.fullmatch(r'\d{6}', batch_certs.new_otp())


def test_check_upload_rejects_fakes_and_oversize():
    assert batch_certs.check_upload('r.PDF', PDF) == ('pdf', 'application/pdf')
    with pytest.raises(ValueError):
        batch_certs.check_upload('r.pdf', b'MZ not a pdf')
    with pytest.raises(ValueError):
        batch_certs.check_upload('r.exe', PDF)
    with pytest.raises(ValueError):
        batch_certs.check_upload('r.pdf', PDF + b'0' * batch_certs.MAX_FILE_BYTES)
    with pytest.raises(ValueError):
        batch_certs.check_upload('r.pdf', b'')


def test_can_send_cooldown_and_hourly_cap():
    now = datetime(2026, 10, 1, 12, 0, 0)
    assert batch_certs.can_send(None, 0, now) == (True, 0)
    ok, wait = batch_certs.can_send(now - timedelta(seconds=10), 1, now)
    assert not ok and 40 < wait <= 51
    assert batch_certs.can_send(now - timedelta(minutes=5), 1, now)[0]
    assert not batch_certs.can_send(now - timedelta(minutes=5), 5, now)[0]


# ---------- public look-up ----------
def test_lookup_states(client):
    make_batch('GM-1', with_cert=True)
    make_batch('GM-2', with_cert=False)
    assert b'test report available' in client.get('/batch?code=gm-1').data
    assert b'report coming soon' in client.get('/batch?code=GM-2').data
    assert b"couldn&#39;t find that batch" in client.get('/batch?code=NOPE').data or b"find that batch" in client.get('/batch?code=NOPE').data
    r = client.get('/batch')
    assert r.status_code == 200 and b'Check your batch' in r.data


def test_lookup_never_exposes_file_or_titles_private_data(client):
    make_batch('GM-1')
    r = client.get('/batch?code=GM-1')
    assert b'%PDF' not in r.data and b'/batch/file/' not in r.data


def test_lookup_is_rate_limited(client, mails):
    for _ in range(40):
        client.get('/batch?code=X1')
    assert b'Too many look-ups' in client.get('/batch?code=X1').data


def test_scan_page_has_batch_form(client, app):
    p = appmod.Product(name='Garam Masala - 50g', description='x', price='60', stock=5)
    appmod.db.session.add(p)
    appmod.db.session.commit()
    assert b'action="/batch"' in client.get(f'/scan/{p.id}').data


# ---------- the gate ----------
def test_visitor_is_sent_to_google_and_returns_to_the_gate(client, mails):
    make_batch('GM-1')
    r = client.get('/batch/access?code=GM-1')
    assert r.status_code == 200 and b'Continue with Google' in r.data
    with client.session_transaction() as s:
        assert s['post_login_next'] == '/batch/access?code=GM-1'


def test_file_is_locked_without_login_or_code(client, mails):
    b = make_batch('GM-1')
    cid = b.certificates[0].id
    r = client.get(f'/batch/file/{cid}')
    assert r.status_code == 302 and '/batch/access' in r.headers['Location']
    login(client, CUSTOMER)                           # signed in but no code yet
    r = client.get(f'/batch/file/{cid}')
    assert r.status_code == 302 and '/batch/access' in r.headers['Location']
    assert client.get('/batch/certificates?code=GM-1').status_code == 302


def test_full_flow_email_code_unlocks_view_and_download(client, mails):
    b = make_batch('GM-1')
    cid = b.certificates[0].id
    login(client, CUSTOMER)
    assert b'E-mail me the code' in client.get('/batch/access?code=GM-1').data
    r = client.post('/batch/send', data={'code': 'GM-1'})
    assert r.status_code == 302
    assert mails[-1][0] == CUSTOMER and 'verification code' in mails[-1][1]
    otp = otp_from(mails)
    stored = appmod.BatchAccessCode.query.one()
    assert otp not in stored.code_hash and stored.code_hash != otp          # never stored in the clear
    assert b'name="otp"' in client.get('/batch/access?code=GM-1').data
    r = client.post('/batch/verify', data={'code': 'GM-1', 'otp': otp})
    assert r.status_code == 302 and '/batch/certificates' in r.headers['Location']
    page = client.get('/batch/certificates?code=GM-1')
    assert page.status_code == 200 and b'Lab report' in page.data
    view = client.get(f'/batch/file/{cid}')
    assert view.status_code == 200 and view.data == PDF and view.mimetype == 'application/pdf'
    assert 'no-store' in view.headers['Cache-Control'] and view.headers['X-Content-Type-Options'] == 'nosniff'
    dl = client.get(f'/batch/file/{cid}?download=1')
    assert 'attachment' in dl.headers['Content-Disposition'] and '.pdf' in dl.headers['Content-Disposition']
    log = appmod.BatchCertView.query.order_by(appmod.BatchCertView.id).all()
    assert [(x.action, x.email) for x in log] == [('view', CUSTOMER), ('download', CUSTOMER)]


def test_code_cannot_be_reused_or_used_for_another_batch_or_user(client, mails):
    make_batch('GM-1')
    make_batch('GM-2')
    login(client, CUSTOMER)
    client.post('/batch/send', data={'code': 'GM-1'})
    otp = otp_from(mails)
    client.post('/batch/verify', data={'code': 'GM-2', 'otp': otp})          # other batch
    assert client.get('/batch/certificates?code=GM-2').status_code == 302
    login(client, 'other@example.com')                                      # other person
    client.post('/batch/verify', data={'code': 'GM-1', 'otp': otp})
    assert client.get('/batch/certificates?code=GM-1').status_code == 302
    login(client, CUSTOMER)
    client.post('/batch/verify', data={'code': 'GM-1', 'otp': otp})
    assert client.get('/batch/certificates?code=GM-1').status_code == 200
    with client.session_transaction() as s:
        s.pop('batch_grants')
    client.post('/batch/verify', data={'code': 'GM-1', 'otp': otp})          # same code again
    assert client.get('/batch/certificates?code=GM-1').status_code == 302


def test_grant_is_per_batch(client, mails):
    make_batch('GM-1')
    b2 = make_batch('GM-2')
    login(client, CUSTOMER)
    client.post('/batch/send', data={'code': 'GM-1'})
    client.post('/batch/verify', data={'code': 'GM-1', 'otp': otp_from(mails)})
    assert client.get('/batch/file/%d' % b2.certificates[0].id).status_code == 302


def test_wrong_codes_lock_out_the_code(client, mails):
    make_batch('GM-1')
    login(client, CUSTOMER)
    client.post('/batch/send', data={'code': 'GM-1'})
    good = otp_from(mails)
    bad = '000000' if good != '000000' else '111111'
    for _ in range(batch_certs.OTP_MAX_WRONG_TRIES):
        client.post('/batch/verify', data={'code': 'GM-1', 'otp': bad})
    client.post('/batch/verify', data={'code': 'GM-1', 'otp': good})          # right code, but locked now
    assert client.get('/batch/certificates?code=GM-1').status_code == 302


def test_expired_code_is_refused(client, mails):
    make_batch('GM-1')
    login(client, CUSTOMER)
    client.post('/batch/send', data={'code': 'GM-1'})
    row = appmod.BatchAccessCode.query.one()
    row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    appmod.db.session.commit()
    client.post('/batch/verify', data={'code': 'GM-1', 'otp': otp_from(mails)})
    assert client.get('/batch/certificates?code=GM-1').status_code == 302


def test_send_has_cooldown_and_needs_login_and_email(client, mails, monkeypatch):
    make_batch('GM-1')
    r = client.post('/batch/send', data={'code': 'GM-1'})
    assert r.headers['Location'].endswith('/login') and not mails
    login(client, CUSTOMER)
    client.post('/batch/send', data={'code': 'GM-1'})
    client.post('/batch/send', data={'code': 'GM-1'})                      # inside the cooldown
    assert len(mails) == 1
    monkeypatch.setattr(notifications, 'is_configured', lambda: False)
    appmod.BatchAccessCode.query.delete()
    appmod.db.session.commit()
    client.post('/batch/send', data={'code': 'GM-1'})
    assert len(mails) == 1


def test_unknown_or_certless_batch_has_no_gate(client, mails):
    make_batch('GM-2', with_cert=False)
    login(client, CUSTOMER)
    for code in ('GM-2', 'NOPE'):
        assert client.get(f'/batch/access?code={code}').status_code == 302
        assert client.post('/batch/send', data={'code': code}).status_code == 302
    assert not mails


def test_post_routes_need_csrf(app, mails):
    app.config['WTF_CSRF_ENABLED'] = True
    c = app.test_client()
    login(c, CUSTOMER)
    assert c.post('/batch/send', data={'code': 'GM-1'}).status_code == 400
    assert c.post('/batch/verify', data={'code': 'GM-1', 'otp': '123456'}).status_code == 400


# ---------- admin ----------
def test_admin_pages_are_admin_only(client):
    b = make_batch('GM-1')
    cid = b.certificates[0].id
    posts = ['/admin/batches/add', f'/admin/batches/{b.id}/edit', f'/admin/batches/{b.id}/upload',
             f'/admin/batches/cert/{cid}/delete', f'/admin/batches/{b.id}/delete']
    gets = ['/admin/batches', f'/admin/batches/file/{cid}']
    for url in gets:
        assert client.get(url).status_code == 403
    for url in posts:
        assert client.post(url).status_code == 403
    login(client, CUSTOMER)
    for url in gets:
        assert client.get(url).status_code == 403
    for url in posts:
        assert client.post(url).status_code == 403
    assert appmod.Batch.query.count() == 1 and appmod.BatchCertificate.query.count() == 1


def test_admin_adds_batch_uploads_and_customers_see_it_change(client, mails):
    login(client, ADMIN)
    client.post('/admin/batches/add', data={'code': ' gm-9 ', 'mfg_date': 'Oct 2026'})
    b = appmod.Batch.query.one()
    assert b.code == 'GM-9'
    client.post('/admin/batches/add', data={'code': 'GM-9'})                 # duplicate ignored
    assert appmod.Batch.query.count() == 1
    assert b'Coming soon' in client.get('/admin/batches').data
    pub = client.application.test_client()
    assert b'report coming soon' in pub.get('/batch?code=GM-9').data

    r = client.post(f'/admin/batches/{b.id}/upload', data={'title': 'NABL report', 'file': (io.BytesIO(PDF), 'lab.pdf')},
                    content_type='multipart/form-data')
    assert r.status_code == 302
    cert = appmod.BatchCertificate.query.one()
    assert (cert.title, cert.ext, cert.size, cert.data) == ('NABL report', 'pdf', len(PDF), PDF)
    assert b'test report available' in pub.get('/batch?code=GM-9').data
    page = client.get('/admin/batches').data
    assert b'Available' in page and b'NABL report' in page and b'%PDF' not in page
    assert client.get(f'/admin/batches/file/{cert.id}').data == PDF

    client.post(f'/admin/batches/cert/{cert.id}/delete')
    assert b'report coming soon' in pub.get('/batch?code=GM-9').data
    client.post(f'/admin/batches/{b.id}/delete')
    assert appmod.Batch.query.count() == 0


def test_admin_upload_rejects_bad_files(client):
    login(client, ADMIN)
    client.post('/admin/batches/add', data={'code': 'GM-9'})
    b = appmod.Batch.query.one()
    for name, data in (('evil.exe', b'MZ\x90'), ('fake.pdf', b'<html>'), ('empty.pdf', b'')):
        client.post(f'/admin/batches/{b.id}/upload', data={'file': (io.BytesIO(data), name)}, content_type='multipart/form-data')
    client.post(f'/admin/batches/{b.id}/upload', data={}, content_type='multipart/form-data')
    assert appmod.BatchCertificate.query.count() == 0


def test_admin_edit_batch(client):
    login(client, ADMIN)
    p = appmod.Product(name='Garam Masala - 50g', description='x', price='60', stock=5)
    appmod.db.session.add(p)
    appmod.db.session.commit()
    client.post('/admin/batches/add', data={'code': 'GM-9'})
    b = appmod.Batch.query.one()
    client.post(f'/admin/batches/{b.id}/edit', data={'product_id': p.id, 'mfg_date': 'Nov 2026', 'notes': 'n'})
    b = appmod.Batch.query.one()
    assert (b.product_id, b.product_name, b.mfg_date, b.notes) == (p.id, p.name, 'Nov 2026', 'n')


def test_cert_listing_does_not_load_file_bytes(app):
    make_batch('GM-1')
    appmod.db.session.expire_all()
    cert = appmod.BatchCertificate.query.one()
    assert 'data' not in cert.__dict__
