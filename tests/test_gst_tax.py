"""GST maths for online orders: financial years, quarters, state codes, per-product rates, tax breakup."""
import random
from datetime import date

import pytest

import gst_tax as g


# ---------- financial year and QRMP quarters ----------

def test_financial_year_labels_and_quarters():
    assert g.fy_label(date(2026, 10, 14)) == '26-27' and g.fy_label(date(2027, 3, 31)) == '26-27'
    assert g.fy_label(date(2026, 3, 31)) == '25-26' and g.fy_label(date(2026, 4, 1)) == '26-27'
    assert g.quarter_of(date(2026, 4, 1)) == (2026, 1) and g.quarter_of(date(2026, 9, 30)) == (2026, 2)
    assert g.quarter_of(date(2026, 10, 1)) == (2026, 3) and g.quarter_of(date(2027, 1, 1)) == (2026, 4)
    assert g.quarter_of(date(2027, 3, 31)) == (2026, 4)


def test_quarter_ranges_including_the_one_that_crosses_the_new_year():
    assert g.quarter_range(2026, 1) == (date(2026, 4, 1), date(2026, 6, 30))
    assert g.quarter_range(2026, 2) == (date(2026, 7, 1), date(2026, 9, 30))
    assert g.quarter_range(2026, 3) == (date(2026, 10, 1), date(2026, 12, 31))
    assert g.quarter_range(2026, 4) == (date(2027, 1, 1), date(2027, 3, 31))
    assert g.quarter_label(2026, 2) == 'Q2 FY 2026-27 (Jul-Sep 2026)'


def test_every_day_of_a_year_falls_inside_its_own_quarter():
    d = date(2026, 4, 1)
    while d < date(2027, 4, 1):
        fy, q = g.quarter_of(d)
        s, e = g.quarter_range(fy, q)
        assert s <= d <= e
        d = date.fromordinal(d.toordinal() + 1)


def test_qrmp_due_dates():
    assert g.quarter_due_dates(2026, 2) == {'gstr1': date(2026, 10, 13), 'gstr3b': date(2026, 10, 22)}
    assert g.quarter_due_dates(2026, 3) == {'gstr1': date(2027, 1, 13), 'gstr3b': date(2027, 1, 22)}
    assert g.quarter_due_dates(2026, 4)['gstr3b'] == date(2027, 4, 22)


def test_invoice_numbers_use_the_website_series_and_fit_16_characters():
    assert g.invoice_number('HSW', '26-27', 1) == 'HSW/26-27/0001'
    assert g.invoice_number('HSW', '26-27', 12345) == 'HSW/26-27/12345'
    assert len(g.invoice_number('HSW', '26-27', 9999)) <= 16
    assert g.invoice_number('HSW', '26-27', 1) != 'HS/26-27/A-0001'      # never clashes with the shop series


# ---------- states ----------

@pytest.mark.parametrize('name,code', [('Maharashtra', '27'), ('  MAHARASHTRA ', '27'), ('Delhi', '07'), ('New Delhi', '07'),
                                       ('Odisha', '21'), ('Orissa', '21'), ('Jammu & Kashmir', '01'), ('Tamil Nadu', '33'),
                                       ('Telangana', '36'), ('Andhra Pradesh', '37'), ('Daman and Diu', '26'), ('MH', '27')])
def test_state_codes(name, code):
    assert g.state_code(name) == code


def test_unknown_states_are_reported_as_unknown_not_guessed():
    assert g.state_code('Narnia') is None and g.state_code(None) is None and g.state_code('') is None


def test_state_label_matches_how_gstr1_shows_place_of_supply():
    assert g.state_label('27') == '27-Maharashtra' and g.state_label('07') == '07-Delhi'


# ---------- per-product settings ----------

def test_product_tax_settings_are_cleaned_and_unreadable_ones_ignored():
    raw = '{"1": {"hsn": "0910 99", "rate": 5, "confirmed": true}, "2": {"rate": "12.0"}, "3": {"rate": "abc"}, "4": {"rate": 99}}'
    cfg = g.parse_product_tax(raw)
    assert cfg['1'] == {'hsn': '091099', 'rate': 5, 'confirmed': True}
    assert cfg['2']['rate'] == 12 and cfg['2']['hsn'] == '0910' and cfg['2']['confirmed'] is False
    assert '3' not in cfg and '4' not in cfg
    assert g.parse_product_tax('not json') == {} and g.parse_product_tax('') == {} and g.parse_product_tax(None) == {}


def test_a_product_without_settings_gets_the_unconfirmed_default():
    t = g.tax_for(7, {})
    assert t == {'hsn': '0910', 'rate': 5, 'confirmed': False}
    assert g.tax_for(7, {'7': {'hsn': '2103', 'rate': 12, 'confirmed': True}})['rate'] == 12


# ---------- splitting a tax-inclusive amount ----------

def test_tax_inclusive_split_examples():
    assert g.tax_inclusive_split(10500, 5) == (10000, 500)
    assert g.tax_inclusive_split(11000, 5) == (10476, 524)
    assert g.tax_inclusive_split(1000, 0) == (1000, 0)
    assert g.tax_inclusive_split(11200, 12) == (10000, 1200)
    assert g.tax_inclusive_split(0, 5) == (0, 0)


def test_allocate_adds_up_exactly_and_follows_the_weights():
    assert g.allocate(100, [1, 1, 1]) == [34, 33, 33] or sum(g.allocate(100, [1, 1, 1])) == 100
    assert g.allocate(0, [5, 5]) == [0, 0] and g.allocate(10, [0, 0]) == [0, 0]
    rnd = random.Random(1)
    for _ in range(500):
        w = [rnd.randint(0, 9000) for _ in range(rnd.randint(1, 5))]
        t = rnd.randint(0, 20000)
        out = g.allocate(t, w)
        assert len(out) == len(w) and all(x >= 0 for x in out)
        assert sum(out) == (t if sum(w) else 0)


# ---------- the tax breakup of an order ----------

def _order(**kw):
    base = {'items': [{'product_id': 1, 'name': 'Garam Masala - 50g', 'qty': 2, 'unit_price': 5500}],
            'discount_amount': 0, 'points_discount_amount': 0, 'shipping_cost': 3700, 'total_amount': 14700,
            'state': 'Maharashtra'}
    base.update(kw)
    return base


def test_real_august_order_is_rs140_taxable_and_rs7_tax():
    b = g.order_breakup(_order())
    assert b['intra'] is True and b['pos_code'] == '27'
    t = b['totals']
    assert t['taxable'] == 14000 and t['tax'] == 700 and t['invoice_value'] == 14700
    assert (t['cgst'], t['sgst'], t['igst']) == (350, 350, 0)
    assert [ln['kind'] for ln in b['lines']] == ['item', 'shipping']


def test_other_state_gets_igst_only():
    t = g.order_breakup(_order(state='Karnataka'))['totals']
    assert (t['cgst'], t['sgst'], t['igst']) == (0, 0, 700) and t['taxable'] == 14000


def test_discounts_are_shared_across_items_and_tax_is_on_what_was_paid():
    order = _order(items=[{'product_id': 1, 'name': 'A', 'qty': 1, 'unit_price': 6000},
                          {'product_id': 2, 'name': 'B', 'qty': 1, 'unit_price': 11000}],
                   discount_amount=1000, points_discount_amount=700, shipping_cost=3500, total_amount=6000 + 11000 - 1700 + 3500)
    b = g.order_breakup(order)
    items = [ln for ln in b['lines'] if ln['kind'] == 'item']
    assert sum(ln['discount'] for ln in items) == 1700
    assert items[0]['discount'] < items[1]['discount']                    # bigger item takes the bigger share
    assert b['totals']['invoice_value'] == order['total_amount']


def test_each_product_uses_its_own_rate_and_shipping_has_its_own():
    cfg = {'1': {'hsn': '0910', 'rate': 5, 'confirmed': True}, '2': {'hsn': '2103', 'rate': 12, 'confirmed': True}}
    order = _order(items=[{'product_id': 1, 'name': 'A', 'qty': 1, 'unit_price': 10500},
                          {'product_id': 2, 'name': 'B', 'qty': 1, 'unit_price': 11200}],
                   shipping_cost=0, total_amount=21700)
    b = g.order_breakup(order, cfg)
    assert b['by_rate'][5]['taxable'] == 10000 and b['by_rate'][12]['taxable'] == 10000
    assert b['totals']['tax'] == 500 + 1200
    assert [ln['hsn'] for ln in b['lines']] == ['0910', '2103']
    b2 = g.order_breakup(_order(), cfg, shipping_rate=18)
    ship = next(ln for ln in b2['lines'] if ln['kind'] == 'shipping')
    assert ship['rate'] == 18 and ship['taxable'] == round(3700 / 1.18)


def test_an_unconfirmed_product_is_flagged_so_the_page_can_warn():
    b = g.order_breakup(_order())
    assert b['lines'][0]['confirmed'] is False and b['lines'][1]['confirmed'] is True


def test_zero_rated_line_has_no_tax():
    b = g.order_breakup(_order(), {'1': {'hsn': '0910', 'rate': 0, 'confirmed': True}}, shipping_rate=0)
    assert b['totals']['tax'] == 0 and b['totals']['taxable'] == 14700


def test_unrecognised_state_is_treated_as_other_state_with_no_code():
    b = g.order_breakup(_order(state='Narnia'))
    assert b['pos_code'] is None and b['intra'] is False and b['totals']['igst'] == 700


def test_odd_data_never_breaks_the_invoice_total():
    b = g.order_breakup(_order(total_amount=14705))            # stored total differs by Rs 0.05
    assert b['totals']['invoice_value'] == 14705
    over = g.order_breakup(_order(discount_amount=999999, total_amount=3700))
    assert over['totals']['invoice_value'] == 3700 and all(ln['paid'] >= 0 for ln in over['lines'])
    assert g.order_breakup(_order(items=[], shipping_cost=0, total_amount=0))['totals']['tax'] == 0


def test_breakup_invariants_hold_for_random_orders():
    rnd = random.Random(42)
    cfg = {'1': {'hsn': '0910', 'rate': 5, 'confirmed': True}, '2': {'hsn': '2103', 'rate': 12, 'confirmed': True},
           '3': {'hsn': '0910', 'rate': 0, 'confirmed': True}}
    for _ in range(600):
        items = [{'product_id': rnd.choice([1, 2, 3]), 'name': 'x', 'qty': rnd.randint(1, 5), 'unit_price': rnd.randint(1000, 30000)}
                 for _ in range(rnd.randint(1, 3))]
        gross = sum(i['qty'] * i['unit_price'] for i in items)
        disc, pts = rnd.randint(0, gross // 3), rnd.randint(0, gross // 4)
        ship = rnd.choice([0, 3500, 4900])
        order = {'items': items, 'discount_amount': disc, 'points_discount_amount': pts, 'shipping_cost': ship,
                 'total_amount': gross - disc - pts + ship, 'state': rnd.choice(['Maharashtra', 'Goa', 'Kerala'])}
        b = g.order_breakup(order, cfg)
        t = b['totals']
        assert t['invoice_value'] == order['total_amount']
        assert t['taxable'] + t['tax'] == t['invoice_value']
        assert t['cgst'] + t['sgst'] + t['igst'] == t['tax']
        for ln in b['lines']:
            assert ln['taxable'] + ln['cgst'] + ln['sgst'] + ln['igst'] == ln['paid']
            assert ln['paid'] >= 0
        assert (t['igst'] == 0) == b['intra'] or t['tax'] == 0
