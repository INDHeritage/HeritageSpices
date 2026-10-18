"""Sales dashboard: date ranges, the GST that is already inside our prices, what counts as a sale, admin-only."""
import random
from datetime import date, datetime

import pytest

import app as appmod
import sales_stats as ss
from conftest import ADMIN


# ---------- date ranges (India time, April-March financial year) ----------

def test_financial_year_starts_on_1_april():
    assert ss.financial_year_start(date(2026, 10, 14)) == date(2026, 4, 1)
    assert ss.financial_year_start(date(2027, 2, 3)) == date(2026, 4, 1)
    assert ss.financial_year_start(date(2027, 4, 1)) == date(2027, 4, 1)
    assert ss.financial_year_start(date(2027, 3, 31)) == date(2026, 4, 1)


def test_named_ranges():
    t = date(2026, 10, 14)
    assert ss.resolve_range('today', t)[:2] == (t, t)
    assert ss.resolve_range('yesterday', t)[:2] == (date(2026, 10, 13), date(2026, 10, 13))
    assert ss.resolve_range('7d', t)[:2] == (date(2026, 10, 8), t)
    assert ss.resolve_range('30d', t)[:2] == (date(2026, 9, 15), t)
    assert ss.resolve_range('month', t)[:2] == (date(2026, 10, 1), t)
    assert ss.resolve_range('last_month', t)[:2] == (date(2026, 9, 1), date(2026, 9, 30))
    assert ss.resolve_range('last_month', date(2027, 1, 5))[:2] == (date(2026, 12, 1), date(2026, 12, 31))
    assert ss.resolve_range('fy', t)[:2] == (date(2026, 4, 1), t)


def test_custom_range_is_validated_and_never_crashes():
    t = date(2026, 10, 14)
    assert ss.resolve_range('custom', t, '2026-09-01', '2026-09-10')[:2] == (date(2026, 9, 1), date(2026, 9, 10))
    assert ss.resolve_range('custom', t, '2026-09-10', '2026-09-01')[:2] == (date(2026, 9, 1), date(2026, 9, 10))
    for bad in [('garbage', 'x'), (None, None), ('2026-02-30', '2026-03-01'), ('2000-01-01', '2026-01-01')]:
        assert ss.resolve_range('custom', t, *bad)[2] == 'month'
    assert ss.resolve_range('nonsense', t)[2] == 'month'


def test_an_evening_utc_order_belongs_to_the_next_indian_day():
    assert ss.to_ist_date(datetime(2026, 10, 14, 19, 0)) == date(2026, 10, 15)
    assert ss.to_ist_date(datetime(2026, 10, 14, 18, 29)) == date(2026, 10, 14)


# ---------- GST that is already inside the price ----------

def test_gst_is_worked_back_out_of_the_price_not_added():
    same = ss.split_gst(11000, 'Maharashtra')           # a Rs 110 pack, 5% included
    assert same == {'taxable': 10476, 'gst': 524, 'cgst': 262, 'sgst': 262, 'igst': 0}
    other = ss.split_gst(11000, 'Karnataka')
    assert other == {'taxable': 10476, 'gst': 524, 'cgst': 0, 'sgst': 0, 'igst': 524}
    assert ss.split_gst(0, 'Maharashtra')['gst'] == 0


def test_state_match_ignores_case_and_spaces():
    assert ss.split_gst(10500, '  MAHARASHTRA ')['igst'] == 0
    assert ss.split_gst(10500, None)['igst'] == 500        # unknown state -> treated as inter-state


def test_gst_split_always_adds_up_to_the_total():
    rnd = random.Random(7)
    for _ in range(500):
        total = rnd.randint(0, 2_000_000)
        for state in ('Maharashtra', 'Goa'):
            g = ss.split_gst(total, state)
            assert g['taxable'] + g['gst'] == total
            assert g['cgst'] + g['sgst'] + g['igst'] == g['gst']
            assert abs(g['cgst'] - g['sgst']) <= 1


# ---------- what counts as a sale ----------

def _order(i, day=15, hour=10, total=11000, ship='delivered', pay='paid', state='Maharashtra', email=None,
           items=None, shipping=3500, pid=None):
    return {
        'id': i, 'created_at': datetime(2026, 10, day, hour, 0), 'user_email': email or f'c{i}@example.com',
        'state': state, 'city': 'Nagpur', 'subtotal': total - shipping, 'shipping_cost': shipping, 'total_amount': total,
        'discount_amount': 0, 'points_discount_amount': 0, 'payment_status': pay, 'shipping_status': ship,
        'razorpay_payment_id': pid or f'pay_{i}',
        'items': items or [{'name': 'Garam Masala - 100g', 'qty': 2, 'unit_price': 3750}],
    }


S, E = date(2026, 10, 14), date(2026, 10, 16)


def test_only_paid_orders_count_and_cancelled_and_rto_come_off_net():
    orders = [_order(1), _order(2, ship='processing'), _order(3, pay='pending'), _order(4, pay='failed'),
              _order(5, ship='cancelled', total=20000), _order(6, ship='rto', total=30000)]
    r = ss.summarize_online(orders, None, S, E)
    assert r['gross'] == 11000 + 11000 + 20000 + 30000
    assert r['cancelled'] == 20000 and r['cancelled_orders'] == 1
    assert r['rto'] == 30000 and r['rto_orders'] == 1
    assert r['net'] == 22000 and r['orders'] == 2 and r['avg_order'] == 11000


def test_orders_outside_the_period_are_left_out_using_indian_dates():
    orders = [_order(1, day=13), _order(2, day=14, hour=10), _order(3, day=16, hour=19),   # 19:00 UTC = 17 Oct IST
              _order(4, day=17)]
    r = ss.summarize_online(orders, None, S, E)
    assert r['orders'] == 1 and r['net'] == 11000


def test_shipping_is_reported_separately_from_product_sales():
    r = ss.summarize_online([_order(1), _order(2)], None, S, E)
    assert r['shipping'] == 7000 and r['products_incl'] == 22000 - 7000


def test_gst_total_is_summed_per_order_with_the_right_split():
    r = ss.summarize_online([_order(1, state='Maharashtra'), _order(2, state='Delhi')], None, S, E)
    assert r['gst']['gst'] == 1048 and r['gst']['cgst'] == 262 and r['gst']['sgst'] == 262 and r['gst']['igst'] == 524
    assert r['gst']['taxable'] + r['gst']['gst'] == r['net']


def test_daily_series_has_no_gaps_and_totals_match():
    r = ss.summarize_online([_order(1, day=14), _order(2, day=16)], None, S, E)
    assert [d['date'] for d in r['daily']] == ['2026-10-14', '2026-10-15', '2026-10-16']
    assert [d['orders'] for d in r['daily']] == [1, 0, 1]
    assert sum(d['net'] for d in r['daily']) == r['net']


def test_top_products_states_and_returning_customers():
    a = [{'name': 'Garam Masala - 50g', 'qty': 1, 'unit_price': 6000}]
    orders = [_order(1, email='old@x.com'), _order(2, email='new@x.com', items=a, state='Goa'), _order(3, email='old@x.com')]
    r = ss.summarize_online(orders, None, S, E, earlier_customers={'old@x.com'})
    assert r['top_products'][0]['name'] == 'Garam Masala - 100g' and r['top_products'][0]['qty'] == 4
    assert {x['state'] for x in r['states']} == {'Maharashtra', 'Goa'}
    assert r['customers'] == 2 and r['returning_customers'] == 1


def test_status_counts_cover_every_paid_order_including_cancelled():
    orders = [_order(1, ship='processing'), _order(2, ship='shipped'), _order(3, ship='cancelled')]
    assert ss.summarize_online(orders, None, S, E)['status_counts'] == {'processing': 1, 'shipped': 1, 'cancelled': 1}


# ---------- Razorpay fees ----------

def test_fees_methods_and_net_received():
    orders = [_order(1, pid='pay_upi'), _order(2, pid='pay_card'), _order(3, pid='pay_missing')]
    payments = {'pay_upi': {'method': 'upi', 'amount': 11000, 'fee': 0, 'tax': 0, 'refunded': 0},
                'pay_card': {'method': 'card', 'amount': 11000, 'fee': 260, 'tax': 47, 'refunded': 0}}
    r = ss.summarize_online(orders, payments, S, E)
    by = {m['method']: m for m in r['payment_methods']}
    assert by['upi']['received'] == 11000 and by['upi']['fee'] == 0
    assert by['card']['received'] == 11000 - 260 - 47
    assert by['unknown']['orders'] == 1                           # a paid order Razorpay did not return
    assert r['fee_total'] == 260 and r['fee_tax_total'] == 47
    assert r['received_total'] == 33000 - 260 - 47


def test_refunds_come_off_what_you_receive():
    orders = [_order(1, pid='p1')]
    r = ss.summarize_online(orders, {'p1': {'method': 'upi', 'amount': 11000, 'fee': 0, 'tax': 0, 'refunded': 4000}}, S, E)
    assert r['refunded_total'] == 4000 and r['received_total'] == 7000


def test_unreachable_razorpay_leaves_fees_out_instead_of_failing():
    r = ss.summarize_online([_order(1)], None, S, E)
    assert r['payments_available'] is False and r['payment_methods'] is None and r['received_total'] is None
    assert r['net'] == 11000


# ---------- rupee formatting ----------

@pytest.mark.parametrize('paise,text', [(0, '₹0.00'), (5, '₹0.05'), (12550, '₹125.50'), (123456, '₹1,234.56'),
                                         (12345678, '₹1,23,456.78'), (1234567890, '₹1,23,45,678.90'), (-12550, '-₹125.50'),
                                         (None, '₹0.00')])
def test_inr_uses_indian_grouping(paise, text):
    assert appmod.inr_filter(paise) == text


# ---------- the page ----------

@pytest.fixture()
def paid_orders(client):
    p = appmod.Product(name='Garam Masala - 100g', description='x', price='110', stock=50)
    appmod.db.session.add(p)
    appmod.db.session.commit()
    now = datetime.utcnow()
    for n, (ship, total) in enumerate([('delivered', 11000), ('cancelled', 20000)], 1):
        o = appmod.SpiceOrder(order_number=f'HS-T{n}', user_email=f'buyer{n}@example.com', full_name='B', phone='9' * 10,
                              address='a', city='Nagpur', state='Maharashtra', pincode='440001', subtotal=total - 3500,
                              shipping_cost=3500, total_amount=total, payment_status='paid', shipping_status=ship,
                              razorpay_payment_id=f'pay_{n}', created_at=now)
        o.items.append(appmod.OrderItem(product_id=p.id, product_name=p.name, quantity=2, unit_price=3750))
        appmod.db.session.add(o)
    appmod.db.session.commit()
    return client


def _login(client, email):
    with client.session_transaction() as sess:
        sess['user'] = {'email': email, 'name': 'X'}


def test_only_admins_can_open_the_page(paid_orders):
    assert paid_orders.get('/admin/sales').status_code == 403
    _login(paid_orders, 'someone@example.com')
    assert paid_orders.get('/admin/sales').status_code == 403
    _login(paid_orders, ADMIN)
    assert paid_orders.get('/admin/sales').status_code == 200


def test_page_shows_net_sales_gst_and_a_clear_note_when_razorpay_is_unreachable(paid_orders):
    _login(paid_orders, ADMIN)
    html = paid_orders.get('/admin/sales?range=today').get_data(as_text=True)
    assert '₹110.00' in html                                   # only the delivered order counts as net sales
    assert '₹200.00' in html                                   # the cancelled one is listed as cancelled
    assert 'Razorpay could not be reached' in html
    assert 'not a GST return' in html
    assert 'noindex' in html
    assert 'Garam Masala - 100g' in html


def test_offline_channel_is_clearly_not_connected_yet(paid_orders):
    _login(paid_orders, ADMIN)
    assert 'not connected yet' in paid_orders.get('/admin/sales?channel=offline').get_data(as_text=True)
    assert 'online (website) only' in paid_orders.get('/admin/sales?channel=all').get_data(as_text=True)


def test_bad_query_values_fall_back_safely(paid_orders):
    _login(paid_orders, ADMIN)
    for q in ('range=zzz', 'range=custom&from=nope&to=nope', 'channel=hack', 'range=custom&from=2026-13-45&to=x'):
        assert paid_orders.get('/admin/sales?' + q).status_code == 200


def test_page_survives_razorpay_data_and_shows_fees(paid_orders, monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_razorpay_payments',
                        lambda s, e: {'pay_1': {'method': 'card', 'amount': 11000, 'fee': 260, 'tax': 47, 'refunded': 0}})
    _login(paid_orders, ADMIN)
    html = paid_orders.get('/admin/sales?range=today').get_data(as_text=True)
    assert 'Card' in html and '₹2.60' in html and '₹0.47' in html and '₹106.93' in html


def test_razorpay_fetch_returns_none_without_keys_and_never_raises(monkeypatch):
    monkeypatch.setattr(appmod, 'razorpay_client', None)
    assert appmod.fetch_razorpay_payments(date(2026, 10, 1), date(2026, 10, 2)) is None
    class Boom:
        class payment:
            @staticmethod
            def all(_): raise RuntimeError('network down')
    monkeypatch.setattr(appmod, 'razorpay_client', Boom)
    appmod._rzp_payments_cache.clear()
    assert appmod.fetch_razorpay_payments(date(2026, 10, 1), date(2026, 10, 2)) is None


def test_razorpay_payments_are_paged_and_only_captured_ones_kept(monkeypatch):
    calls = []
    page1 = [{'id': f'p{i}', 'status': 'captured', 'method': 'upi', 'amount': 100, 'fee': 0, 'tax': 0} for i in range(100)]
    page2 = [{'id': 'pz', 'status': 'failed'}, {'id': 'pq', 'status': 'captured', 'method': 'card', 'amount': 500,
                                                 'fee': 12, 'tax': 2, 'amount_refunded': 100}]
    class Fake:
        class payment:
            @staticmethod
            def all(params):
                calls.append(params['skip'])
                return {'items': page1 if params['skip'] == 0 else page2}
    monkeypatch.setattr(appmod, 'razorpay_client', Fake)
    appmod._rzp_payments_cache.clear()
    got = appmod.fetch_razorpay_payments(date(2026, 10, 1), date(2026, 10, 2))
    assert calls == [0, 100] and len(got) == 101 and 'pz' not in got
    assert got['pq'] == {'method': 'card', 'amount': 500, 'fee': 12, 'tax': 2, 'refunded': 100}


# ---------- checkouts started but not paid ----------

def test_unpaid_checkouts_are_counted_separately_and_never_in_net_sales():
    orders = [_order(1), _order(2, pay='pending', total=28400), _order(3, pay='failed', total=13800),
              _order(4, pay='pending', day=13)]                          # outside the period
    r = ss.summarize_online(orders, None, S, E)
    assert r['net'] == 11000 and r['orders'] == 1
    assert r['unpaid_orders'] == 2 and r['unpaid_value'] == 28400 + 13800
    assert r['checkout_completion'] == 33                                # 1 paid out of 3 started


def test_completion_rate_is_none_when_nobody_started_a_checkout():
    r = ss.summarize_online([], None, S, E)
    assert r['unpaid_orders'] == 0 and r['checkout_completion'] is None


def test_cancelled_paid_orders_still_count_as_completed_checkouts():
    r = ss.summarize_online([_order(1, ship='cancelled'), _order(2, pay='pending')], None, S, E)
    assert r['checkout_completion'] == 50 and r['net'] == 0


def test_page_shows_the_unpaid_checkouts_line(paid_orders):
    now = datetime.utcnow()
    o = appmod.SpiceOrder(order_number='HS-UNPAID', user_email='x@example.com', full_name='B', phone='9' * 10, address='a',
                          city='Nagpur', state='Maharashtra', pincode='440001', subtotal=24900, shipping_cost=3500,
                          total_amount=28400, payment_status='pending', shipping_status='processing', created_at=now)
    appmod.db.session.add(o)
    appmod.db.session.commit()
    _login(paid_orders, ADMIN)
    html = paid_orders.get('/admin/sales?range=today&channel=online').get_data(as_text=True)
    assert 'not paid' in html and '₹284.00' in html and '% of checkouts completed' in html
    assert '₹110.00' in html and 'HS-UNPAID' not in html              # net sales unchanged by the unpaid order


def test_old_dashboard_counts_paid_orders_and_mentions_unpaid_ones(paid_orders):
    o = appmod.SpiceOrder(order_number='HS-STARTED', user_email='y@example.com', full_name='B', phone='9' * 10, address='a',
                          city='Nagpur', state='Maharashtra', pincode='440001', subtotal=24900, shipping_cost=3500,
                          total_amount=28400, payment_status='pending', shipping_status='processing', created_at=datetime.utcnow())
    appmod.db.session.add(o)
    appmod.db.session.commit()
    _login(paid_orders, ADMIN)
    html = paid_orders.get('/admin/dashboard').get_data(as_text=True)
    assert 'Paid Orders' in html and '+ 1 started, not paid' in html
