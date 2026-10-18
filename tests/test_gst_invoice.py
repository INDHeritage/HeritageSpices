"""GST tax invoices for online orders: numbering, snapshot, PDF, access, and the new-table safeguard."""
import json
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

import app as appmod
import gst_tax
import receipts
import sales_stats
from conftest import ADMIN

FY = gst_tax.fy_label(sales_stats.ist_today())


def _pay(order):
    appmod.finalize_paid_order(order.razorpay_order_id, 'pay_' + order.order_number)


def _second_order(product, n=2, rz='order_TEST2', state='Maharashtra', total=14700, subtotal=11000, email='buyer@example.com'):
    o = appmod.SpiceOrder(order_number=f'HS-TEST000{n}', user_email=email, full_name='Buyer Two', phone='9999999998',
                          address='2 Test Road', city='Pune', state=state, pincode='411001', subtotal=subtotal,
                          shipping_cost=3700, total_amount=total, razorpay_order_id=rz, payment_status='pending')
    appmod.db.session.add(o)
    appmod.db.session.flush()
    appmod.db.session.add(appmod.OrderItem(order_id=o.id, product_id=product.id, product_name=product.name,
                                           quantity=2, unit_price=5500))
    appmod.db.session.commit()
    return o


def _login(client, email):
    with client.session_transaction() as sess:
        sess['user'] = {'email': email, 'name': 'X'}


# ---------- issuing ----------

def test_paying_issues_the_next_invoice_number_in_the_website_series(make_order):
    order, product = make_order()
    _pay(order)
    inv = appmod.GstInvoice.query.filter_by(order_id=order.id).one()
    assert inv.invoice_number == f'HSW/{FY}/0001' and inv.seq == 1 and inv.invoice_date == sales_stats.ist_today()
    o2 = _second_order(product)
    _pay(o2)
    assert appmod.GstInvoice.query.filter_by(order_id=o2.id).one().invoice_number == f'HSW/{FY}/0002'


def test_an_order_never_gets_two_invoices(make_order):
    order, _ = make_order()
    _pay(order)
    _pay(order)                                    # a retry / webhook arriving again
    a = appmod.ensure_gst_invoice(order)
    b = appmod.ensure_gst_invoice(order)
    assert a.id == b.id and appmod.GstInvoice.query.count() == 1


def test_numbers_restart_for_each_financial_year(make_order, monkeypatch):
    order, product = make_order()
    _pay(order)
    monkeypatch.setattr(sales_stats, 'ist_today', lambda now_utc=None: date(2027, 4, 2))
    o2 = _second_order(product)
    _pay(o2)
    inv = appmod.GstInvoice.query.filter_by(order_id=o2.id).one()
    assert inv.invoice_number == 'HSW/27-28/0001' and inv.fy == '27-28'


def test_the_same_number_can_never_be_stored_twice(make_order):
    order, product = make_order()
    _pay(order)
    o2 = _second_order(product)
    appmod.db.session.add(appmod.GstInvoice(order_id=o2.id, fy=FY, seq=1, invoice_number='HSW/x/0009',
                                            invoice_date=date.today(), data='{}'))
    with pytest.raises(IntegrityError):
        appmod.db.session.commit()
    appmod.db.session.rollback()


def test_a_clash_between_two_simultaneous_payments_takes_the_next_number(make_order, monkeypatch):
    """Both payments see 'highest used is 0' at the same moment: the second one hits the unique rule and retries."""
    order, product = make_order()
    o2 = _second_order(product)
    _pay(order)                                                          # takes 0001
    real_query, state = appmod.db.session.query, {'n': 0}

    class StaleMax:                                                      # 'the highest number so far is 0'
        def filter_by(self, **kw):
            return self
        def scalar(self):
            return 0

    def query(*a, **k):
        state['n'] += 1
        return StaleMax() if state['n'] == 1 else real_query(*a, **k)
    monkeypatch.setattr(appmod.db.session, 'query', query)
    inv = appmod.ensure_gst_invoice(o2)
    assert inv.invoice_number == f'HSW/{FY}/0002' and state['n'] == 2


def test_invoice_records_who_sold_what_and_the_tax(make_order):
    order, _ = make_order()
    _pay(order)
    doc = appmod.GstInvoice.query.one().doc
    assert doc['seller']['legal_name'] == 'NAHID PARWIN AKIL SHEIKH' and doc['seller']['trade_name'] == 'HERITAGE SPICES'
    assert doc['seller']['gstin'] == '27KYIPS1412N1ZT'
    t = doc['breakup']['totals']
    assert (t['taxable'], t['tax'], t['cgst'], t['sgst'], t['igst'], t['invoice_value']) == (14000, 700, 350, 350, 0, 14700)
    assert doc['breakup']['pos_code'] == '27' and doc['buyer']['state'] == 'Maharashtra'


def test_an_issued_invoice_does_not_change_when_settings_change_later(make_order):
    order, product = make_order()
    _pay(order)
    before = appmod.GstInvoice.query.one().data
    appmod.gst_set('product_tax', json.dumps({str(product.id): {'hsn': '2103', 'rate': 12, 'confirmed': True}}))
    appmod.gst_set('shipping_rate', '18')
    appmod.gst_set('address', 'Somewhere else')
    assert appmod.GstInvoice.query.one().data == before
    assert appmod.ensure_gst_invoice(order).data == before


def test_invoice_uses_each_products_own_rate_when_set(make_order):
    order, product = make_order()
    appmod.gst_set('product_tax', json.dumps({str(product.id): {'hsn': '2103', 'rate': 12, 'confirmed': True}}))
    _pay(order)
    lines = appmod.GstInvoice.query.one().doc['breakup']['lines']
    assert lines[0]['hsn'] == '2103' and lines[0]['rate'] == 12 and lines[0]['confirmed'] is True


def test_inter_state_order_is_igst_with_its_place_of_supply(make_order):
    order, product = make_order()
    o2 = _second_order(product, state='Karnataka')
    _pay(o2)
    br = appmod.GstInvoice.query.filter_by(order_id=o2.id).one().doc['breakup']
    assert br['intra'] is False and br['pos_code'] == '29' and br['totals']['igst'] == 700 and br['totals']['cgst'] == 0


def test_an_invoice_problem_never_undoes_a_payment_or_stops_the_email(make_order, monkeypatch):
    sent = []
    monkeypatch.setattr(appmod.notifications, 'send_email', lambda *a, **k: sent.append(a) or True)
    monkeypatch.setattr(appmod, 'ensure_gst_invoice', lambda o: (_ for _ in ()).throw(RuntimeError('boom')))
    order, _ = make_order()
    appmod.finalize_paid_order('order_TEST1', 'pay_x')
    assert appmod.SpiceOrder.query.one().payment_status == 'paid'
    assert appmod.GstInvoice.query.count() == 0 and len(sent) == 1


# ---------- the PDF ----------

def test_tax_invoice_pdf_is_built_for_both_intra_and_inter_state_orders(make_order):
    order, product = make_order()
    _pay(order)
    inv = appmod.GstInvoice.query.one()
    assert receipts.build_receipt_pdf(order, inv).startswith(b'%PDF')
    o2 = _second_order(product, state='Kerala')
    _pay(o2)
    assert receipts.build_receipt_pdf(o2, appmod.GstInvoice.query.filter_by(order_id=o2.id).one()).startswith(b'%PDF')


def test_names_in_other_scripts_do_not_break_the_pdf(make_order):
    order, _ = make_order()
    order.full_name, order.address = 'नाहिद शेख', 'सिंदेवाही, चंद्रपूर'
    appmod.db.session.commit()
    assert receipts.build_receipt_pdf(order).startswith(b'%PDF')           # plain receipt
    _pay(order)
    assert receipts.build_receipt_pdf(order, appmod.GstInvoice.query.one()).startswith(b'%PDF')


def test_orders_without_an_invoice_still_get_the_plain_receipt(make_order):
    order, _ = make_order()
    assert receipts.build_receipt_pdf(order).startswith(b'%PDF')


# ---------- who can see it ----------

def test_customer_can_download_their_own_invoice_but_nobody_elses(client, make_order):
    order, _ = make_order(email='buyer@example.com')
    _pay(order)
    assert client.get(f'/orders/{order.id}/invoice.pdf').status_code == 302          # not logged in -> login
    _login(client, 'stranger@example.com')
    assert client.get(f'/orders/{order.id}/invoice.pdf').status_code == 404
    _login(client, 'buyer@example.com')
    r = client.get(f'/orders/{order.id}/invoice.pdf')
    assert r.status_code == 200 and r.mimetype == 'application/pdf' and r.data.startswith(b'%PDF')
    assert f'HSW-{FY}-0001.pdf' in r.headers['Content-Disposition']
    _login(client, ADMIN)
    assert client.get(f'/orders/{order.id}/invoice.pdf').status_code == 200


def test_no_invoice_means_a_404_not_a_crash(client, make_order):
    order, _ = make_order(email='buyer@example.com')
    _login(client, 'buyer@example.com')
    assert client.get(f'/orders/{order.id}/invoice.pdf').status_code == 404


def test_receipt_page_shows_the_tax_invoice_details(client, make_order):
    order, _ = make_order(email='buyer@example.com')
    _pay(order)
    _login(client, 'buyer@example.com')
    html = client.get(f'/orders/{order.id}/receipt').get_data(as_text=True)
    assert 'Tax Invoice' in html and f'HSW/{FY}/0001' in html
    assert 'NAHID PARWIN AKIL SHEIKH' in html and '27KYIPS1412N1ZT' in html and 'HERITAGE SPICES' in html
    assert 'nothing extra was charged' in html and '₹140.00' in html


def test_receipt_page_for_an_old_order_still_works(client, make_order):
    order, _ = make_order(email='buyer@example.com')
    _login(client, 'buyer@example.com')
    html = client.get(f'/orders/{order.id}/receipt').get_data(as_text=True)
    assert 'Receipt' in html and 'Tax Invoice' not in html


# ---------- new tables are shielded from the public database API ----------

def test_row_level_security_is_a_no_op_on_sqlite(app):
    assert appmod.ensure_row_level_security() == []


def test_row_level_security_is_switched_on_for_unprotected_postgres_tables(monkeypatch):
    ran, state = [], {'committed': False}

    class Result:
        def fetchall(self):
            return [('gst_invoice',), ('weird"name',)]

    class Session:
        def execute(self, stmt):
            ran.append(stmt)
            return Result()
        def commit(self):
            state['committed'] = True
        def rollback(self):
            state['rolled_back'] = True

    fake = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql')), session=Session(),
                           text=lambda s: s)
    monkeypatch.setattr(appmod, 'db', fake)
    assert appmod.ensure_row_level_security() == ['gst_invoice', 'weird"name']
    assert 'ALTER TABLE public."gst_invoice" ENABLE ROW LEVEL SECURITY' in ran
    assert 'ALTER TABLE public."weird""name" ENABLE ROW LEVEL SECURITY' in ran       # quoted safely
    assert state['committed'] is True


def test_row_level_security_check_can_never_stop_the_app_from_starting(monkeypatch):
    class Session:
        def execute(self, stmt):
            raise RuntimeError('permission denied')
        def rollback(self):
            pass
    fake = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql')), session=Session(),
                           text=lambda s: s)
    monkeypatch.setattr(appmod, 'db', fake)
    assert appmod.ensure_row_level_security() == []
