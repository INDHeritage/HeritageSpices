"""The shop (offline) half of the sales dashboard: the read-only link to the billing database,
turning its numbers into ours, and combining both channels."""
from datetime import date, datetime

import pytest
import requests

import app as appmod
import billing_client as bc
import sales_stats as ss
from conftest import ADMIN

URL, KEY, EMAIL, PASSWORD = 'https://shop.example.supabase.co', 'anon-key-123', 'dash@example.com', 'p@ss-word-xyz'
S, E = date(2026, 10, 1), date(2026, 10, 3)

RAW = {   # the shape dashboard_api.sql returns (paise)
    'from': '2026-10-01', 'to': '2026-10-03', 'bills': 4, 'salesPaise': 100000, 'taxablePaise': 95238, 'gstPaise': 4762,
    'cashPaise': 40000, 'upiPaise': 30000, 'cardPaise': 10000, 'udhaarGivenPaise': 20000, 'returnsPaise': 5000,
    'netSalesPaise': 95000, 'udhaarCollectedPaise': 8000, 'expensesPaise': 12000, 'udhaarOutstandingPaise': 150000,
    'customersWithDue': 3,
    'daily': [{'date': '2026-10-01', 'salesPaise': 60000, 'bills': 2}, {'date': '2026-10-03', 'salesPaise': 40000, 'bills': 2}],
    'topProducts': [{'product': 'Garam Masala 100g', 'qty': 20, 'salesPaise': 70000},
                    {'product': 'Turmeric Powder 200g', 'qty': 5, 'salesPaise': 30000}],
    'bySalesperson': [{'name': 'Nahid Parwin', 'salesPaise': 100000, 'bills': 4}],
    'lowStock': [{'product': 'Garam Masala', 'size': '50g', 'qty': 2, 'reorderLevel': 10}],
}


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k, v in (('BILLING_SUPABASE_URL', URL), ('BILLING_SUPABASE_ANON_KEY', KEY),
                 ('BILLING_DASHBOARD_EMAIL', EMAIL), ('BILLING_DASHBOARD_PASSWORD', PASSWORD)):
        monkeypatch.setenv(k, v)
    bc.reset()
    yield
    bc.reset()


@pytest.fixture()
def server(monkeypatch):
    """A fake shop database: records every call so tests can check what was sent."""
    calls = {'login': 0, 'rpc': 0, 'last_rpc': None, 'login_status': 200, 'rpc_statuses': [200], 'rpc_body': RAW,
             'login_payload': None}

    def post(url, **kw):
        if url.endswith('/auth/v1/token'):
            calls['login'] += 1
            calls['login_payload'] = kw
            if calls['login_status'] != 200:
                return Resp(calls['login_status'], {'error': 'x'})
            return Resp(200, {'access_token': f"tok{calls['login']}", 'expires_in': 3600})
        if url.endswith('/rest/v1/rpc/dashboard_summary'):
            calls['rpc'] += 1
            calls['last_rpc'] = kw
            status = calls['rpc_statuses'].pop(0) if len(calls['rpc_statuses']) > 1 else calls['rpc_statuses'][0]
            return Resp(status, calls['rpc_body'] if status == 200 else {'message': 'nope'})
        raise AssertionError('unexpected call ' + url)

    monkeypatch.setattr(bc.requests, 'post', post)
    return calls


# ---------- the connection ----------

def test_not_configured_is_detected(monkeypatch):
    monkeypatch.setenv('BILLING_DASHBOARD_PASSWORD', '')
    assert bc.configured() is False
    with pytest.raises(bc.BillingUnavailable, match='not connected'):
        bc.get_summary(S, E)


def test_signs_in_then_asks_for_the_right_period_read_only(server):
    data = bc.get_summary(S, E)
    assert data['salesPaise'] == 100000
    assert server['login_payload']['json'] == {'email': EMAIL, 'password': PASSWORD}
    rpc = server['last_rpc']
    assert rpc['json'] == {'p_from': '2026-10-01', 'p_to': '2026-10-03'}
    assert rpc['headers']['Authorization'] == 'Bearer tok1' and rpc['headers']['apikey'] == KEY


def test_login_is_reused_and_results_are_cached_for_five_minutes(server):
    bc.get_summary(S, E)
    bc.get_summary(S, E)                       # same period: served from the cache
    bc.get_summary(S, date(2026, 10, 4))       # new period: asks again, but does not sign in again
    assert server['login'] == 1 and server['rpc'] == 2


def test_an_expired_login_is_renewed_once_automatically(server):
    server['rpc_statuses'] = [401, 200]
    assert bc.get_summary(S, E)['bills'] == 4
    assert server['login'] == 2 and server['rpc'] == 2


@pytest.mark.parametrize('login_status', [400, 401, 422])
def test_wrong_password_gives_a_clear_message_without_leaking_it(server, login_status):
    server['login_status'] = login_status
    with pytest.raises(bc.BillingUnavailable) as e:
        bc.get_summary(S, E)
    assert 'refused the dashboard login' in str(e.value)
    for secret in (PASSWORD, KEY, EMAIL):
        assert secret not in str(e.value)


def test_missing_function_and_blocked_reader_have_helpful_messages(server):
    server['rpc_statuses'] = [404]
    with pytest.raises(bc.BillingUnavailable, match='dashboard_api.sql'):
        bc.get_summary(S, E)
    bc.reset()
    server['rpc_statuses'] = [403]
    with pytest.raises(bc.BillingUnavailable, match='Accountant'):
        bc.get_summary(S, E)


def test_server_errors_and_junk_replies_never_escape_as_crashes(server):
    server['rpc_statuses'] = [500]
    with pytest.raises(bc.BillingUnavailable, match='code 500'):
        bc.get_summary(S, E)
    bc.reset()
    server['rpc_statuses'] = [200]
    server['rpc_body'] = {'unexpected': True}
    with pytest.raises(bc.BillingUnavailable, match='unexpected'):
        bc.get_summary(S, E)


def test_network_trouble_becomes_a_plain_message(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError('dns failure with secret-host')
    monkeypatch.setattr(bc.requests, 'post', boom)
    with pytest.raises(bc.BillingUnavailable, match='could not be reached') as e:
        bc.get_summary(S, E)
    assert 'secret-host' not in str(e.value)


# ---------- the shop's numbers in our shape ----------

def test_shop_numbers_are_normalised():
    o = ss.normalize_offline(RAW)
    assert (o['sales'], o['returns'], o['net'], o['bills'], o['avg_bill']) == (100000, 5000, 95000, 4, 25000)
    assert (o['cash'], o['upi'], o['card'], o['udhaar_given']) == (40000, 30000, 10000, 20000)
    assert o['udhaar_outstanding'] == 150000 and o['customers_with_due'] == 3 and o['expenses'] == 12000
    assert o['daily']['2026-10-01'] == {'sales': 60000, 'bills': 2}
    assert o['low_stock'][0] == {'product': 'Garam Masala', 'size': '50g', 'qty': 2, 'reorder': 10}


def test_normalising_tolerates_missing_and_text_numbers():
    o = ss.normalize_offline({'salesPaise': '1200.0', 'bills': None, 'daily': None, 'topProducts': None})
    assert o['sales'] == 1200 and o['bills'] == 0 and o['avg_bill'] == 0 and o['net'] == 1200 and o['daily'] == {}


def test_product_names_match_across_website_and_billing_app():
    assert ss.product_key('Garam Masala - 100g') == ss.product_key('Garam Masala 100g') == 'garammasala100g'
    assert ss.product_key('Garam Masala - 50g') != ss.product_key('Garam Masala 100g')


def _online(net=22000):
    orders = [{'id': 1, 'created_at': datetime(2026, 10, 2, 10), 'user_email': 'a@x.com', 'state': 'Goa', 'city': 'Panaji',
               'subtotal': net - 3500, 'shipping_cost': 3500, 'total_amount': net, 'discount_amount': 0,
               'points_discount_amount': 0, 'payment_status': 'paid', 'shipping_status': 'delivered',
               'razorpay_payment_id': 'p1', 'items': [{'name': 'Garam Masala - 100g', 'qty': 2, 'unit_price': 9250}]}]
    return ss.summarize_online(orders, None, S, E)


def test_combined_totals_share_daily_and_products():
    both = ss.combine(_online(), ss.normalize_offline(RAW), S, E)
    assert both['online_net'] == 22000 and both['offline_net'] == 95000 and both['net'] == 117000
    assert both['online_share'] + both['offline_share'] == 100 and both['online_share'] == 19
    assert both['orders'] == 1 + 4
    assert [(d['date'], d['online'], d['offline']) for d in both['daily']] == [
        ('2026-10-01', 0, 60000), ('2026-10-02', 22000, 0), ('2026-10-03', 0, 40000)]
    gm = next(p for p in both['products'] if 'garam' in p['name'].lower())
    assert gm['on_qty'] == 2 and gm['off_qty'] == 20 and gm['qty'] == 22      # one row for both spellings
    assert both['gst_total'] == _online()['gst']['gst'] + 4762


def test_combine_works_without_shop_data():
    both = ss.combine(_online(), None, S, E)
    assert both['net'] == 22000 and both['offline_net'] == 0 and both['online_share'] == 100
    assert all(d['offline'] == 0 for d in both['daily'])


# ---------- the page ----------

def _login(client, email=ADMIN):
    with client.session_transaction() as sess:
        sess['user'] = {'email': email, 'name': 'X'}


@pytest.fixture()
def page(client, monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_razorpay_payments', lambda s, e: {})
    return client


def test_all_view_shows_both_channels_and_the_split(page, server):
    _login(page)
    html = page.get('/admin/sales?range=custom&from=2026-10-01&to=2026-10-03').get_data(as_text=True)
    assert '₹950.00' in html                       # shop net
    assert 'Total net sales' in html and 'Counter and wholesale billing' in html
    assert 'Nahid Parwin' in html and '₹1,500.00' in html          # salesperson and udhaar outstanding
    assert 'Garam Masala 100g' not in html or 'Garam Masala' in html


def test_offline_view_shows_only_the_shop_numbers(page, server):
    _login(page)
    html = page.get('/admin/sales?range=custom&from=2026-10-01&to=2026-10-03&channel=offline').get_data(as_text=True)
    assert 'Sales by salesperson' in html and 'Low stock at the shop' in html and 'Udhaar and expenses' in html
    assert 'Razorpay fee' not in html                                # online-only section stays out
    assert 'Not connected' not in html


def test_online_view_never_calls_the_shop(page, server):
    _login(page)
    page.get('/admin/sales?channel=online')
    assert server['login'] == 0 and server['rpc'] == 0


def test_shop_problems_become_a_note_and_online_still_shows(page, server):
    server['login_status'] = 401
    _login(page)
    r = page.get('/admin/sales?channel=all')
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'Shop sales could not be loaded' in html and 'refused the dashboard login' in html
    assert PASSWORD not in html and KEY not in html and EMAIL not in html
    assert 'How online customers paid' in html


def test_not_configured_shows_setup_hint(page, monkeypatch):
    monkeypatch.setenv('BILLING_SUPABASE_URL', '')
    _login(page)
    html = page.get('/admin/sales?channel=all').get_data(as_text=True)
    assert 'not connected yet' in html and 'BILLING_' in html


def test_billing_address_redirects_to_the_dashboard_for_admins_only(page, server):
    assert page.get('/admin/billing').status_code == 403
    _login(page)
    r = page.get('/admin/billing?channel=offline')
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/sales?channel=offline')


def test_non_admins_cannot_trigger_a_shop_lookup(page, server):
    _login(page, 'stranger@example.com')
    assert page.get('/admin/sales?channel=offline').status_code == 403
    assert server['login'] == 0 and server['rpc'] == 0
