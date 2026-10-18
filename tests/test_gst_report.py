"""Quarterly GST figures: online supplies, the shop's figures, GSTR-1 tables, GSTR-3B worksheet, filing record."""
import json
from datetime import date, datetime

import pytest

import gst_report as gr
import gst_tax

START, END = date(2026, 7, 1), date(2026, 9, 30)

SHOP_RAW = {   # the shape gst_api.sql returns (paise)
    'totals': {'taxable': 100000, 'cgst': 2500, 'sgst': 2500, 'igst': 0, 'tax': 5000, 'invoiceValue': 105000},
    'returnsTotals': {'taxable': 10000, 'cgst': 250, 'sgst': 250, 'igst': 0, 'tax': 500, 'value': 10500},
    'netTotals': {'taxable': 90000, 'cgst': 2250, 'sgst': 2250, 'igst': 0, 'tax': 4500},
    'billOfSupplyValue': 7000,
    'b2b': [{'invoiceNumber': 'HS/26-27/A-0001', 'date': '2026-08-02', 'gstin': '27ABCDE1234F1Z5', 'customer': 'Kirana',
             'placeOfSupply': '27', 'invoiceValue': 52500,
             'rates': [{'rate': 5, 'taxable': 50000, 'cgst': 1250, 'sgst': 1250, 'igst': 0}]}],
    'b2cs': [{'stateCode': '27', 'rate': 5, 'taxable': 50000, 'cgst': 1250, 'sgst': 1250, 'igst': 0}],
    'hsn': [{'section': 'B2B', 'hsn': '0910', 'rate': 5, 'unit': 'pcs', 'qty': 100, 'taxable': 50000, 'cgst': 1250, 'sgst': 1250, 'igst': 0, 'value': 52500},
            {'section': 'B2C', 'hsn': '0910', 'rate': 5, 'unit': 'pcs', 'qty': 100, 'taxable': 50000, 'cgst': 1250, 'sgst': 1250, 'igst': 0, 'value': 52500}],
    'creditNotes': [{'number': 'HS/26-27/CN-1', 'date': '2026-09-01', 'gstin': None, 'customer': 'X', 'value': 10500, 'taxable': 10000,
                     'tax': 500, 'cgst': 250, 'sgst': 250, 'igst': 0}],
    'documents': [{'series': 'HS/26-27/A-', 'kind': 'Invoice', 'from': 'HS/26-27/A-0001', 'to': 'HS/26-27/A-0010', 'total': 10, 'cancelled': 1}],
    'purchases': [{'date': '2026-08-10', 'purchaseNumber': 'P1', 'supplierInvoiceNumber': 'INV9', 'supplier': 'Spice Co',
                   'supplierGstin': '27AAAAA0000A1Z5', 'taxable': 20000, 'gst': 1000, 'total': 21000, 'igst': 0, 'cgst': 500, 'sgst': 500},
                  {'date': '2026-08-12', 'purchaseNumber': 'P2', 'supplierInvoiceNumber': 'X2', 'supplier': 'Other',
                   'supplierGstin': '29BBBBB1111B1Z5', 'taxable': 10000, 'gst': 500, 'total': 10500, 'igst': 500, 'cgst': 0, 'sgst': 0}],
    'purchaseTotals': {'taxable': 30000, 'gst': 1500, 'igst': 500, 'cgst': 500, 'sgst': 500},
}


def _order(i, day, state='Maharashtra', status='delivered', pay='paid', total=14700, invoice=None, hour=10, month=8):
    return {'id': i, 'order_number': f'HS-{i:04d}', 'created_at': datetime(2026, month, day, hour), 'state': state,
            'payment_status': pay, 'shipping_status': status, 'user_email': 'a@x.com', 'discount_amount': 0,
            'points_discount_amount': 0, 'shipping_cost': 3700, 'total_amount': total, 'invoice': invoice,
            'items': [{'product_id': 1, 'name': 'Garam Masala - 50g', 'qty': 2, 'unit_price': (total - 3700) // 2}]}


def _invoice(order, seq, day=None, month=8):
    br = gst_tax.order_breakup(order, {'1': {'hsn': '0910', 'rate': 5, 'confirmed': True}})
    return {'invoice_number': f'HSW/26-27/{seq:04d}', 'invoice_date': date(2026, month, day or order['created_at'].day), 'doc': {'breakup': br}}


CFG = {'1': {'hsn': '0910', 'rate': 5, 'confirmed': True}}


# ---------- the website's supplies ----------

def test_real_august_order_is_a_maharashtra_b2cs_row():
    r = gr.online_supplies([_order(1, 29)], CFG, 5, START, END)
    assert r['totals']['taxable'] == 14000 and r['totals']['tax'] == 700 and r['totals']['cgst'] == 350
    assert len(r['supplies']) == 1 and r['supplies'][0]['source'] == 'computed'
    assert [(b['pos_code'], b['rate'], b['taxable']) for b in r['b2cs']] == [('27', 5.0, 14000)]


def test_cancelled_orders_are_not_supplies_and_returned_ones_are_flagged():
    orders = [_order(1, 5), _order(2, 6, status='cancelled'), _order(3, 7, status='rto'), _order(4, 8, pay='pending')]
    r = gr.online_supplies(orders, CFG, 5, START, END)
    assert [s['order_number'] for s in r['supplies']] == ['HS-0001']
    assert [c['order_number'] for c in r['cancelled']] == ['HS-0002'] and [x['order_number'] for x in r['returned']] == ['HS-0003']
    assert any('RTO' in w and 'HS-0003' in w for w in r['warnings'])


def test_period_uses_the_invoice_date_then_the_indian_order_date():
    inside = _order(1, 29)
    outside = _order(2, 5, month=10)
    late_utc = _order(3, 30, month=9, hour=19)                   # 19:00 UTC on 30 Sep is 1 Oct in India
    r = gr.online_supplies([inside, outside, late_utc], CFG, 5, START, END)
    assert [s['order_number'] for s in r['supplies']] == ['HS-0001']
    inv = _invoice(_order(4, 20, month=9), 1, day=2, month=10)    # invoice dated 2 Oct: next quarter
    o4 = _order(4, 20, month=9, invoice=inv)
    assert gr.online_supplies([o4], CFG, 5, START, END)['supplies'] == []


def test_an_issued_invoice_is_used_as_it_was_issued_not_recomputed():
    o = _order(1, 10)
    inv = _invoice(o, 1)
    o['invoice'] = inv
    changed = {'1': {'hsn': '2103', 'rate': 12, 'confirmed': True}}                # settings changed after issue
    r = gr.online_supplies([o], changed, 18, START, END)
    assert r['supplies'][0]['source'] == 'invoice' and r['totals']['taxable'] == 14000 and r['b2cs'][0]['rate'] == 5.0
    assert r['supplies'][0]['invoice_number'] == 'HSW/26-27/0001'


def test_documents_issued_show_the_number_range_and_cancelled_count():
    orders = []
    for i, st in enumerate(['delivered', 'cancelled', 'delivered'], 1):
        o = _order(i, 10 + i)
        o['invoice'] = _invoice(o, i)
        o['shipping_status'] = st
        orders.append(o)
    d = gr.online_supplies(orders, CFG, 5, START, END)['docs'][0]
    assert (d['series'], d['from'], d['to'], d['total'], d['cancelled']) == ('HSW/26-27/', 'HSW/26-27/0001', 'HSW/26-27/0003', 3, 1)


def test_hsn_summary_merges_orders_and_attaches_shipping_to_the_goods_hsn():
    r = gr.online_supplies([_order(1, 5), _order(2, 6)], CFG, 5, START, END)
    assert len(r['hsn']) == 1
    h = r['hsn'][0]
    assert h['hsn'] == '0910' and h['rate'] == 5.0 and h['qty'] == 4 and h['taxable'] == 28000 and h['value'] == 29400


def test_warnings_for_missing_invoices_unconfirmed_rates_and_unknown_states():
    r = gr.online_supplies([_order(1, 5, state='Narnia')], {}, 5, START, END)
    text = ' '.join(r['warnings'])
    assert 'no GST invoice' in text and 'HSN 0910 / 5%' in text and 'not recognised' in text
    assert gr.online_supplies([_order(1, 5)], CFG, 5, START, END)['warnings'][0].startswith('1 order(s) have no GST invoice')


def test_other_states_group_separately_by_place_of_supply():
    r = gr.online_supplies([_order(1, 5), _order(2, 6, state='Karnataka'), _order(3, 7, state='Karnataka')], CFG, 5, START, END)
    by = {b['pos_code']: b for b in r['b2cs']}
    assert by['27']['taxable'] == 14000 and by['29']['taxable'] == 28000 and by['29']['igst'] == 1400 and by['29']['cgst'] == 0


# ---------- the shop's figures ----------

def test_shop_figures_are_normalised_with_text_and_missing_numbers_tolerated():
    s = gr.normalize_shop_gst(SHOP_RAW)
    assert s['net']['taxable'] == 90000 and s['bill_of_supply'] == 7000 and s['b2cs'][0]['pos_code'] == '27'
    assert s['b2b'][0]['rates'][0]['rate'] == 5.0 and s['purchases'][1]['igst'] == 500 and s['purchase_totals']['gst'] == 1500
    empty = gr.normalize_shop_gst({'totals': {'taxable': '1200.0'}, 'b2cs': [{'stateCode': 7, 'rate': '5'}]})
    assert empty['totals']['taxable'] == 1200 and empty['b2cs'][0]['pos_code'] == '07' and empty['purchases'] == []
    assert gr.normalize_shop_gst(None)['net']['tax'] == 0


# ---------- GSTR-1 tables ----------

def test_b2cs_merges_online_and_shop_by_state_and_rate_keeping_each_part():
    online = gr.online_supplies([_order(1, 29)], CFG, 5, START, END)
    t = gr.gstr1_tables(online, gr.normalize_shop_gst(SHOP_RAW))
    row = next(r for r in t['b2cs'] if r['pos_code'] == '27')
    assert row['taxable'] == 14000 + 50000 and row['online_taxable'] == 14000 and row['shop_taxable'] == 50000
    assert row['state'] == '27-Maharashtra' and row['cgst'] == 350 + 1250


def test_hsn_keeps_b2b_and_b2c_apart_and_adds_online_to_b2c():
    online = gr.online_supplies([_order(1, 29)], CFG, 5, START, END)
    t = gr.gstr1_tables(online, gr.normalize_shop_gst(SHOP_RAW))
    by = {r['section']: r for r in t['hsn']}
    assert by['B2B']['taxable'] == 50000 and by['B2C']['taxable'] == 50000 + 14000 and by['B2C']['qty'] == 102


def test_without_shop_data_only_online_is_reported():
    online = gr.online_supplies([_order(1, 29)], CFG, 5, START, END)
    t = gr.gstr1_tables(online, None)
    assert t['b2b'] == [] and t['credit_notes'] == [] and t['b2cs'][0]['taxable'] == 14000 and t['b2cs'][0]['shop_taxable'] == 0


def test_documents_are_listed_for_both_channels():
    o = _order(1, 29)
    o['invoice'] = _invoice(o, 1)
    online = gr.online_supplies([o], CFG, 5, START, END)
    docs = gr.gstr1_tables(online, gr.normalize_shop_gst(SHOP_RAW))['docs']
    assert {d['channel'] for d in docs} == {'Online', 'Shop'}


# ---------- GSTR-3B ----------

def test_outward_supplies_add_online_to_the_shops_net_after_returns():
    online = gr.online_supplies([_order(1, 29)], CFG, 5, START, END)
    out = gr.outward_3b(online, gr.normalize_shop_gst(SHOP_RAW))
    assert out['taxable'] == 14000 + 90000 and out['cgst'] == 350 + 2250 and out['igst'] == 0 and out['nil_exempt'] == 7000
    assert out['online']['taxable'] == 14000 and out['shop']['taxable'] == 90000
    assert gr.outward_3b(online, None)['taxable'] == 14000


def test_credit_is_set_off_in_the_order_the_rules_allow():
    r = gr.apply_itc({'igst': 100, 'cgst': 200, 'sgst': 200}, {'igst': 150, 'cgst': 50, 'sgst': 30})
    assert r['cash'] == {'igst': 0, 'cgst': 150, 'sgst': 170} or sum(r['cash'].values()) == 500 - 230
    # IGST owed: paid by IGST credit (100). CGST owed: CGST credit 50, then the 50 IGST credit left over.
    assert r['cash']['igst'] == 0 and r['cash']['cgst'] == 100 and r['cash']['sgst'] == 170
    assert r['itc_left'] == {'igst': 0, 'cgst': 0, 'sgst': 0}
    assert r['used']['igst']['cgst'] == 50 and r['used']['cgst']['cgst'] == 50


def test_cgst_credit_can_never_pay_sgst_and_the_reverse():
    r = gr.apply_itc({'igst': 0, 'cgst': 0, 'sgst': 100}, {'igst': 0, 'cgst': 500, 'sgst': 0})
    assert r['cash']['sgst'] == 100 and r['itc_left']['cgst'] == 500
    r2 = gr.apply_itc({'igst': 100, 'cgst': 0, 'sgst': 0}, {'igst': 0, 'cgst': 60, 'sgst': 70})
    assert r2['cash']['igst'] == 0 and r2['itc_left'] == {'igst': 0, 'cgst': 0, 'sgst': 30}   # IGST owed takes CGST then SGST credit


def test_credit_never_exceeds_what_is_owed_or_available_and_money_balances():
    import random
    rnd = random.Random(5)
    for _ in range(500):
        liab = {h: rnd.randint(0, 5000) for h in gr.HEADS}
        itc = {h: rnd.randint(0, 5000) for h in gr.HEADS}
        r = gr.apply_itc(liab, itc)
        used_total = sum(sum(v.values()) for v in r['used'].values())
        assert sum(r['cash'].values()) + used_total == sum(liab.values())
        assert sum(r['itc_left'].values()) + used_total == sum(itc.values())
        assert all(v >= 0 for v in r['cash'].values()) and all(v >= 0 for v in r['itc_left'].values())


def test_worksheet_adds_late_fee_and_interest_as_cash_only():
    w = gr.worksheet_3b({'igst': 0, 'cgst': 1000, 'sgst': 1000}, {'igst': 0, 'cgst': 400, 'sgst': 400},
                        {'late_fee_cgst': 250, 'late_fee_sgst': 250, 'interest_cgst': 10})
    assert w['cash_tax'] == 1200 and w['late_fee'] == {'cgst': 250, 'sgst': 250} and w['cash_total'] == 1200 + 500 + 10


# ---------- the filing record ----------

def test_filing_records_are_read_safely():
    assert gr.parse_filing('')['status'] == 'draft' and gr.parse_filing('not json')['status'] == 'draft'
    f = gr.parse_filing(json.dumps({'status': 'filed', 'snapshot': {'x': 1}, 'gstr1_arn': 'AD123', 'junk': 5}))
    assert f['status'] == 'filed' and f['gstr1_arn'] == 'AD123' and 'junk' not in f
    assert gr.parse_filing(json.dumps({'status': 'weird'}))['status'] == 'draft'


def test_a_filed_quarter_is_locked_only_once_its_snapshot_exists():
    assert gr.is_locked({'status': 'filed', 'snapshot': {'a': 1}}) is True
    assert gr.is_locked({'status': 'filed', 'snapshot': None}) is False
    assert gr.is_locked({'status': 'review', 'snapshot': {'a': 1}}) is False


def test_input_credit_defaults_to_the_billing_app_and_the_owner_can_override():
    shop = gr.normalize_shop_gst(SHOP_RAW)
    assert gr.itc_claim_from(gr.blank_filing(), shop) == {'igst': 500, 'cgst': 500, 'sgst': 500}
    f = gr.blank_filing()
    f['inputs'] = {'itc_igst': '0', 'itc_cgst': '300', 'itc_sgst': ''}
    assert gr.itc_claim_from(f, shop) == {'igst': 0, 'cgst': 300, 'sgst': 500}      # blank means "use the app's figure"
    assert gr.itc_claim_from(gr.blank_filing(), None) == {'igst': 0, 'cgst': 0, 'sgst': 0}
