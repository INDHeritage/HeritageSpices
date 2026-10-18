"""Quarterly GST figures: the website's online supplies, the shop's figures from the billing app, the GSTR-1 tables
and GSTR-3B worksheet built from both, and the filing record. Pure functions on plain dicts (money in paise).

These are preparation figures for the owner and the CA. The GST portal's own calculation is the final word.
"""
import json
from collections import defaultdict

import gst_tax
from sales_stats import _i, to_ist_date

STATUSES = ('draft', 'review', 'filed')
HEADS = ('igst', 'cgst', 'sgst')


# ---------- the website's own supplies ----------

def online_supplies(orders, product_tax, shipping_rate, start, end):
    """Paid website orders dated start..end, as GST supplies.

    orders: dicts with id, order_number, created_at (UTC), state, payment_status, shipping_status, discount_amount,
            points_discount_amount, shipping_cost, total_amount, items [{product_id, name, qty, unit_price}], and
            invoice (None, or {invoice_number, invoice_date (date), doc}).
    A cancelled order is not a supply; an order returned to us (RTO) is listed for the CA to decide on. The date of
    supply is the invoice date when an invoice exists, otherwise the order date.
    """
    supplies, cancelled, returned, warnings = [], [], [], []
    unconfirmed, no_invoice, unknown_state = set(), [], []
    docs_issued = []
    for o in orders:
        if o.get('payment_status') != 'paid':
            continue
        inv = o.get('invoice')
        d = inv['invoice_date'] if inv else to_ist_date(o['created_at'])
        if not start <= d <= end:
            continue
        if inv:
            docs_issued.append({'number': inv['invoice_number'], 'cancelled': (o.get('shipping_status') or '') == 'cancelled'})
        status = (o.get('shipping_status') or '').lower()
        row = {'order_number': o['order_number'], 'date': d, 'invoice_number': inv['invoice_number'] if inv else '',
               'value': o['total_amount']}
        if status == 'cancelled':
            cancelled.append(row)
            continue
        if status == 'rto':
            returned.append(row)
            continue
        br = inv['doc'].get('breakup') if inv else None
        if not br:
            br = gst_tax.order_breakup(o, product_tax, shipping_rate)
        supplies.append({**row, 'state': o.get('state') or br.get('place_of_supply') or '', 'pos_code': br.get('pos_code'),
                         'source': 'invoice' if inv else 'computed', 'lines': br['lines'], 'totals': br['totals'],
                         'intra': br.get('intra', True)})
        if not inv:
            no_invoice.append(o['order_number'])
        if not br.get('pos_code'):
            unknown_state.append(o['order_number'])
        for ln in br['lines']:
            if ln['kind'] == 'item' and not ln.get('confirmed'):
                unconfirmed.add(ln['description'])

    totals = {'taxable': 0, 'cgst': 0, 'sgst': 0, 'igst': 0, 'tax': 0, 'value': 0}
    b2cs, hsn = {}, {}
    for s in supplies:
        for k in ('taxable', 'cgst', 'sgst', 'igst', 'tax'):
            totals[k] += s['totals'][k]
        totals['value'] += s['totals']['invoice_value']
        principal = next((ln['hsn'] for ln in s['lines'] if ln['kind'] == 'item'), gst_tax.DEFAULT_PRODUCT_TAX['hsn'])
        for ln in s['lines']:
            key = (s['pos_code'] or '??', float(ln['rate']))
            b = b2cs.setdefault(key, {'pos_code': key[0], 'rate': key[1], 'taxable': 0, 'cgst': 0, 'sgst': 0, 'igst': 0})
            h = hsn.setdefault((ln['hsn'] or principal, float(ln['rate'])),
                               {'hsn': ln['hsn'] or principal, 'rate': float(ln['rate']), 'qty': 0, 'taxable': 0,
                                'cgst': 0, 'sgst': 0, 'igst': 0, 'value': 0})
            for k in ('taxable', 'cgst', 'sgst', 'igst'):
                b[k] += ln[k]
                h[k] += ln[k]
            h['value'] += ln['paid']
            if ln['kind'] == 'item':
                h['qty'] += ln['qty']

    docs = []
    if docs_issued:
        nums = sorted(d['number'] for d in docs_issued)
        docs.append({'series': nums[0].rsplit('/', 1)[0] + '/', 'kind': 'Invoice', 'from': nums[0], 'to': nums[-1],
                     'total': len(nums), 'cancelled': sum(1 for d in docs_issued if d['cancelled'])})

    if no_invoice:
        warnings.append(f"{len(no_invoice)} order(s) have no GST invoice (placed before invoices were switched on): "
                        f"{', '.join(no_invoice)}. They are included using today's tax settings; ask your CA whether to issue invoices.")
    if unconfirmed:
        warnings.append("These products use the default HSN 0910 / 5% because no rate has been confirmed yet: "
                        f"{', '.join(sorted(unconfirmed))}. Confirm HSN and rate with your CA under Tax settings.")
    if unknown_state:
        warnings.append(f"The delivery state of {', '.join(unknown_state)} was not recognised, so it is treated as "
                        "another state (IGST) with an unknown place of supply.")
    if returned:
        warnings.append(f"{len(returned)} order(s) were returned to you (RTO) and are NOT counted as sales: "
                        f"{', '.join(r['order_number'] for r in returned)}. Ask your CA whether a credit note is needed.")
    return {'supplies': supplies, 'cancelled': cancelled, 'returned': returned, 'totals': totals,
            'b2cs': sorted(b2cs.values(), key=lambda r: (r['pos_code'], r['rate'])),
            'hsn': sorted(hsn.values(), key=lambda r: (r['hsn'], r['rate'])), 'docs': docs, 'warnings': warnings}


# ---------- the shop's figures (billing app gst_summary) ----------

def _tax_block(raw, keys=('taxable', 'cgst', 'sgst', 'igst', 'tax')):
    raw = raw or {}
    return {k: _i(raw.get(k)) for k in keys}


def normalize_shop_gst(raw):
    """The billing app's gst_summary reply as plain ints, with friendlier key names."""
    raw = raw or {}
    return {
        'totals': _tax_block(raw.get('totals'), ('taxable', 'cgst', 'sgst', 'igst', 'tax', 'invoiceValue')),
        'returns': _tax_block(raw.get('returnsTotals'), ('taxable', 'cgst', 'sgst', 'igst', 'tax', 'value')),
        'net': _tax_block(raw.get('netTotals')),
        'bill_of_supply': _i(raw.get('billOfSupplyValue')),
        'b2b': [{'invoice_number': r.get('invoiceNumber') or '', 'date': r.get('date') or '', 'gstin': r.get('gstin') or '',
                 'customer': r.get('customer') or '', 'place_of_supply': r.get('placeOfSupply') or '',
                 'value': _i(r.get('invoiceValue')),
                 'rates': [{'rate': float(x.get('rate') or 0), 'taxable': _i(x.get('taxable')), 'cgst': _i(x.get('cgst')),
                            'sgst': _i(x.get('sgst')), 'igst': _i(x.get('igst'))} for x in (r.get('rates') or [])]}
                for r in (raw.get('b2b') or [])],
        'b2cs': [{'pos_code': str(r.get('stateCode') or '27').zfill(2), 'rate': float(r.get('rate') or 0),
                  'taxable': _i(r.get('taxable')), 'cgst': _i(r.get('cgst')), 'sgst': _i(r.get('sgst')), 'igst': _i(r.get('igst'))}
                 for r in (raw.get('b2cs') or [])],
        'hsn': [{'section': r.get('section') or 'B2C', 'hsn': r.get('hsn') or '-', 'rate': float(r.get('rate') or 0),
                 'unit': r.get('unit') or 'pcs', 'qty': _i(r.get('qty')), 'taxable': _i(r.get('taxable')),
                 'cgst': _i(r.get('cgst')), 'sgst': _i(r.get('sgst')), 'igst': _i(r.get('igst')), 'value': _i(r.get('value'))}
                for r in (raw.get('hsn') or [])],
        'credit_notes': [{'number': r.get('number') or '', 'date': r.get('date') or '', 'gstin': r.get('gstin') or '',
                          'customer': r.get('customer') or '', 'value': _i(r.get('value')), 'taxable': _i(r.get('taxable')),
                          'tax': _i(r.get('tax')), 'cgst': _i(r.get('cgst')), 'sgst': _i(r.get('sgst')), 'igst': _i(r.get('igst'))}
                         for r in (raw.get('creditNotes') or [])],
        'documents': [{'series': r.get('series') or '', 'kind': r.get('kind') or '', 'from': r.get('from') or '',
                       'to': r.get('to') or '', 'total': _i(r.get('total')), 'cancelled': _i(r.get('cancelled'))}
                      for r in (raw.get('documents') or [])],
        'purchases': [{'date': r.get('date') or '', 'purchase_number': r.get('purchaseNumber') or '',
                       'supplier_invoice': r.get('supplierInvoiceNumber') or '', 'supplier': r.get('supplier') or '',
                       'gstin': r.get('supplierGstin') or '', 'taxable': _i(r.get('taxable')), 'gst': _i(r.get('gst')),
                       'total': _i(r.get('total')), 'igst': _i(r.get('igst')), 'cgst': _i(r.get('cgst')), 'sgst': _i(r.get('sgst'))}
                      for r in (raw.get('purchases') or [])],
        'purchase_totals': _tax_block(raw.get('purchaseTotals'), ('taxable', 'gst', 'igst', 'cgst', 'sgst')),
    }


# ---------- GSTR-1 tables, both channels together ----------

def gstr1_tables(online, shop):
    """B2CS (consumer sales by place of supply and rate), HSN summary and documents issued, merged across
    the website and the shop. `shop` is the normalised billing data or None."""
    sources = [('online', online['b2cs'])] + ([('shop', shop['b2cs'])] if shop else [])
    b2cs = {}
    for src, rows in sources:
        for r in rows:
            m = b2cs.setdefault((r['pos_code'], r['rate']), {'pos_code': r['pos_code'], 'rate': r['rate']})
            for k in ('taxable', 'cgst', 'sgst', 'igst'):
                m[k] = m.get(k, 0) + r[k]
                m[f'{src}_{k}'] = m.get(f'{src}_{k}', 0) + r[k]
    for m in b2cs.values():
        for src in ('online', 'shop'):
            for k in ('taxable', 'cgst', 'sgst', 'igst'):
                m.setdefault(f'{src}_{k}', 0)
        m['state'] = gst_tax.state_label(m['pos_code'])

    hsn = {}
    rows = [('B2C', r) for r in online['hsn']] + [(r['section'], r) for r in (shop['hsn'] if shop else [])]
    for section, r in rows:
        m = hsn.setdefault((section, r['hsn'], r['rate']), {'section': section, 'hsn': r['hsn'], 'rate': r['rate'],
                                                             'qty': 0, 'taxable': 0, 'cgst': 0, 'sgst': 0, 'igst': 0, 'value': 0})
        for k in ('qty', 'taxable', 'cgst', 'sgst', 'igst', 'value'):
            m[k] += r[k]

    docs = [dict(d, channel='Online') for d in online['docs']] + [dict(d, channel='Shop') for d in (shop['documents'] if shop else [])]
    return {'b2cs': sorted(b2cs.values(), key=lambda r: (r['pos_code'], r['rate'])),
            'hsn': sorted(hsn.values(), key=lambda r: (r['section'], r['hsn'], r['rate'])), 'docs': docs,
            'b2b': shop['b2b'] if shop else [], 'credit_notes': shop['credit_notes'] if shop else []}


# ---------- GSTR-3B worksheet ----------

def outward_3b(online, shop):
    """Table 3.1(a): outward taxable supplies, net of the shop's sales returns, plus 3.1(c) nil/exempt value."""
    o = online['totals']
    s = shop['net'] if shop else {'taxable': 0, 'cgst': 0, 'sgst': 0, 'igst': 0, 'tax': 0}
    out = {k: o[k] + s[k] for k in ('taxable', 'cgst', 'sgst', 'igst', 'tax')}
    out['nil_exempt'] = shop['bill_of_supply'] if shop else 0
    out['online'] = {k: o[k] for k in ('taxable', 'cgst', 'sgst', 'igst', 'tax')}
    out['shop'] = dict(s)
    return out


def apply_itc(liab, itc):
    """How input credit is set against tax owed, following the order the GST rules use:
    IGST owed is paid from IGST credit, then CGST, then SGST credit; CGST owed from CGST credit, then IGST credit;
    SGST owed from SGST credit, then IGST credit. Returns the cash still payable per head and the credit used.
    This mirrors the portal's logic for planning; the portal decides the actual set-off."""
    liab = {h: max(0, int(liab.get(h, 0))) for h in HEADS}
    left = {h: max(0, int(itc.get(h, 0))) for h in HEADS}
    used = {h: {x: 0 for x in HEADS} for h in HEADS}          # used[credit head][liability head]
    order = {'igst': ('igst', 'cgst', 'sgst'), 'cgst': ('cgst', 'igst'), 'sgst': ('sgst', 'igst')}
    cash = {}
    for owed in ('igst', 'cgst', 'sgst'):
        remaining = liab[owed]
        for credit in order[owed]:
            take = min(remaining, left[credit])
            left[credit] -= take
            used[credit][owed] += take
            remaining -= take
        cash[owed] = remaining
    return {'cash': cash, 'used': used, 'itc_left': left}


def worksheet_3b(outward, itc_claim, inputs=None):
    """Tax payable for the quarter: output tax, input credit claimed (editable), late fee and interest (cash only)."""
    inputs = inputs or {}
    liab = {h: outward[h] for h in HEADS}
    st = apply_itc(liab, itc_claim)
    late_fee = {'cgst': _i(inputs.get('late_fee_cgst')), 'sgst': _i(inputs.get('late_fee_sgst'))}
    interest = {h: _i(inputs.get(f'interest_{h}')) for h in HEADS}
    cash_tax = sum(st['cash'].values())
    return {'liability': liab, 'itc_claim': {h: _i(itc_claim.get(h)) for h in HEADS}, **st,
            'late_fee': late_fee, 'interest': interest,
            'cash_tax': cash_tax, 'cash_total': cash_tax + sum(late_fee.values()) + sum(interest.values())}


# ---------- the filing record (one per quarter, kept in site settings) ----------

def blank_filing():
    return {'status': 'draft', 'inputs': {}, 'gstr1_arn': '', 'gstr1_date': '', 'gstr3b_arn': '', 'gstr3b_date': '',
            'tax_paid': '', 'notes': '', 'snapshot': None, 'filed_at': '', 'history': []}


def parse_filing(raw):
    base = blank_filing()
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        data = {}
    if isinstance(data, dict):
        base.update({k: data[k] for k in base if k in data})
    if base['status'] not in STATUSES:
        base['status'] = 'draft'
    if not isinstance(base['inputs'], dict):
        base['inputs'] = {}
    if not isinstance(base['history'], list):
        base['history'] = []
    return base


def is_locked(filing):
    """A filed quarter is frozen: its figures cannot be edited, only noted."""
    return filing.get('status') == 'filed' and bool(filing.get('snapshot'))


def itc_claim_from(filing, shop):
    """Input credit to claim: the owner's figures if saved, otherwise what the billing app recorded."""
    inp = filing.get('inputs', {})
    base = (shop or {}).get('purchase_totals', {}) if shop else {}
    return {h: (_i(inp[f'itc_{h}']) if inp.get(f'itc_{h}') not in (None, '') else _i(base.get(h))) for h in HEADS}


def snapshot(report):
    """The numbers frozen when a quarter is marked as filed."""
    w, o = report['worksheet'], report['outward']
    return {'outward': {k: o[k] for k in ('taxable', 'cgst', 'sgst', 'igst', 'tax', 'nil_exempt')},
            'itc_claim': w['itc_claim'], 'cash': w['cash'], 'cash_tax': w['cash_tax'], 'late_fee': w['late_fee'],
            'interest': w['interest'], 'cash_total': w['cash_total'],
            'b2cs_rows': len(report['gstr1']['b2cs']), 'online_orders': len(report['online']['supplies']),
            'frozen_on': report['generated']}
