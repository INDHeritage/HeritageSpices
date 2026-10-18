"""GST maths for online orders: financial-year helpers, state codes, per-product rates, and the tax
breakup of an order whose prices already include GST.

Everything is in paise (integers). Nothing here touches the database, so each rule is easy to test.
A customer pays the listed price; tax is worked back out of it, never added on top.
"""
import json
import math
from datetime import date
from fractions import Fraction

# ---- seller (as registered with GST; overridable in Admin > GST) ----
SELLER = {
    'legal_name': 'NAHID PARWIN AKIL SHEIKH',
    'trade_name': 'HERITAGE SPICES',
    'gstin': '27KYIPS1412N1ZT',
    'state': 'Maharashtra',
    'state_code': '27',
    'address': 'Sindewahi, Chandrapur, Maharashtra 441222',
}
ONLINE_INVOICE_PREFIX = 'HSW'          # the shop's own series is HS/26-27/A-0001, so this can never clash
DEFAULT_PRODUCT_TAX = {'hsn': '0910', 'rate': 5, 'confirmed': False}
DEFAULT_SHIPPING_RATE = 5

# ---- GST state codes (the first two digits of any GSTIN) ----
STATE_CODES = {
    'jammu and kashmir': '01', 'himachal pradesh': '02', 'punjab': '03', 'chandigarh': '04', 'uttarakhand': '05',
    'haryana': '06', 'delhi': '07', 'rajasthan': '08', 'uttar pradesh': '09', 'bihar': '10', 'sikkim': '11',
    'arunachal pradesh': '12', 'nagaland': '13', 'manipur': '14', 'mizoram': '15', 'tripura': '16', 'meghalaya': '17',
    'assam': '18', 'west bengal': '19', 'jharkhand': '20', 'odisha': '21', 'chhattisgarh': '22', 'madhya pradesh': '23',
    'gujarat': '24', 'dadra and nagar haveli and daman and diu': '26', 'maharashtra': '27', 'karnataka': '29',
    'goa': '30', 'lakshadweep': '31', 'kerala': '32', 'tamil nadu': '33', 'puducherry': '34',
    'andaman and nicobar islands': '35', 'telangana': '36', 'andhra pradesh': '37', 'ladakh': '38',
}
_ALIASES = {
    'orissa': 'odisha', 'nct of delhi': 'delhi', 'new delhi': 'delhi', 'pondicherry': 'puducherry',
    'uttaranchal': 'uttarakhand', 'jammu & kashmir': 'jammu and kashmir', 'j&k': 'jammu and kashmir',
    'dadra and nagar haveli': 'dadra and nagar haveli and daman and diu', 'daman and diu': 'dadra and nagar haveli and daman and diu',
    'andaman & nicobar islands': 'andaman and nicobar islands', 'andaman and nicobar': 'andaman and nicobar islands',
    'mh': 'maharashtra',
}


def norm_state(name):
    s = str(name or '').strip().lower().replace('&', 'and')
    s = ' '.join(s.replace('.', ' ').replace(',', ' ').split())
    return _ALIASES.get(s, s)


def state_code(name):
    """'Maharashtra' -> '27'; None if the name is not recognised."""
    return STATE_CODES.get(norm_state(name))


def state_label(code):
    """'27' -> '27-Maharashtra' (how GSTR-1 shows place of supply)."""
    for n, c in STATE_CODES.items():
        if c == code:
            return f"{code}-{n.title().replace(' And ', ' and ')}"
    return str(code or 'Unknown')


# ---- financial year (April to March) and QRMP quarters ----

def fy_start_year(d):
    return d.year if d.month >= 4 else d.year - 1


def fy_label(d):
    """date(2026, 10, 14) -> '26-27'."""
    y = fy_start_year(d)
    return f"{y % 100:02d}-{(y + 1) % 100:02d}"


def quarter_of(d):
    """(financial-year start year, quarter 1-4): Apr-Jun = 1, Jul-Sep = 2, Oct-Dec = 3, Jan-Mar = 4."""
    return fy_start_year(d), ((d.month - 4) % 12) // 3 + 1


def quarter_range(fy_year, q):
    start_month = 4 + 3 * (q - 1)
    year = fy_year + (1 if start_month > 12 else 0)
    month = start_month - 12 if start_month > 12 else start_month
    start = date(year, month, 1)
    end_month, end_year = month + 3, year
    if end_month > 12:
        end_month, end_year = end_month - 12, year + 1
    nxt = date(end_year, end_month, 1)
    return start, date.fromordinal(nxt.toordinal() - 1)


def quarter_label(fy_year, q):
    s, e = quarter_range(fy_year, q)
    return f"Q{q} FY {fy_year}-{(fy_year + 1) % 100:02d} ({s.strftime('%b')}-{e.strftime('%b %Y')})"


def quarter_due_dates(fy_year, q):
    """Usual QRMP due dates: GSTR-1 on the 13th and GSTR-3B on the 22nd (Maharashtra) of the month after the
    quarter. The portal can extend them, so always check it."""
    _, end = quarter_range(fy_year, q)
    y, m = (end.year + 1, 1) if end.month == 12 else (end.year, end.month + 1)
    return {'gstr1': date(y, m, 13), 'gstr3b': date(y, m, 22)}


def invoice_number(prefix, fy, seq):
    """('HSW', '26-27', 1) -> 'HSW/26-27/0001' (a GST invoice number is at most 16 characters)."""
    return f"{prefix}/{fy}/{int(seq):04d}"


# ---- per-product HSN and rate (kept in site settings, editable by the owner or the CA) ----

def parse_product_tax(raw):
    """JSON text of {product_id: {hsn, rate, confirmed}} -> clean dict; anything unreadable is ignored."""
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
    except ValueError:
        return {}
    out = {}
    for k, v in (data.items() if isinstance(data, dict) else []):
        try:
            rate = float(v.get('rate'))
        except (TypeError, ValueError, AttributeError):
            continue
        if not 0 <= rate <= 40:
            continue
        hsn = ''.join(ch for ch in str(v.get('hsn', '')) if ch.isalnum())[:8] or DEFAULT_PRODUCT_TAX['hsn']
        out[str(k)] = {'hsn': hsn, 'rate': int(rate) if rate == int(rate) else rate, 'confirmed': bool(v.get('confirmed'))}
    return out


def tax_for(product_id, cfg):
    return (cfg or {}).get(str(product_id), dict(DEFAULT_PRODUCT_TAX))


# ---- the tax breakup of one order ----

def _half_up(x):
    return math.floor(x + Fraction(1, 2))


def tax_inclusive_split(paid, rate):
    """paid (paise, tax included) at `rate` percent -> (taxable value, tax)."""
    r = Fraction(str(rate))
    taxable = _half_up(Fraction(paid * 100) / (100 + r))
    return taxable, paid - taxable


def allocate(total, weights):
    """Split `total` paise over `weights` in proportion, whole paise only, adding up exactly (largest remainder)."""
    s = sum(weights)
    if total <= 0 or s <= 0:
        return [0] * len(weights)
    exact = [Fraction(total * w, s) for w in weights]
    base = [math.floor(x) for x in exact]
    left = total - sum(base)
    for i in sorted(range(len(weights)), key=lambda i: (exact[i] - base[i], -i), reverse=True)[:left]:
        base[i] += 1
    return base


def order_breakup(order, product_tax=None, shipping_rate=DEFAULT_SHIPPING_RATE):
    """Tax breakup for one online order.

    order: dict with items [{product_id, name, qty, unit_price}], discount_amount, points_discount_amount,
           shipping_cost, total_amount, state (delivery state = place of supply). All money in paise.
    Coupon and points discounts are spread over the items in proportion to their value (the customer paid
    less, so tax is on what was actually paid); shipping is its own line. The lines always add up to
    total_amount exactly.
    """
    code = state_code(order.get('state'))
    intra = code == SELLER['state_code']
    items = order.get('items', [])
    gross = [i['qty'] * i['unit_price'] for i in items]
    discount = min(sum(gross), (order.get('discount_amount') or 0) + (order.get('points_discount_amount') or 0))
    disc = allocate(discount, gross)
    paid = [g - d for g, d in zip(gross, disc)]

    shipping = order.get('shipping_cost') or 0
    total = order.get('total_amount')
    if total is not None and items and sum(paid) + shipping != total:      # never let rounding or odd data drift the total
        paid[-1] += total - (sum(paid) + shipping)

    lines = []
    for i, it in enumerate(items):
        t = tax_for(it.get('product_id'), product_tax)
        lines.append({'kind': 'item', 'description': it.get('name') or 'Item', 'hsn': t['hsn'], 'qty': it['qty'],
                      'unit_price': it['unit_price'], 'gross': gross[i], 'discount': disc[i], 'paid': paid[i],
                      'rate': t['rate'], 'confirmed': t.get('confirmed', False)})
    if shipping:
        lines.append({'kind': 'shipping', 'description': 'Shipping / delivery charges', 'hsn': '', 'qty': 1,
                      'unit_price': shipping, 'gross': shipping, 'discount': 0, 'paid': shipping,
                      'rate': shipping_rate, 'confirmed': True})

    totals = {'taxable': 0, 'cgst': 0, 'sgst': 0, 'igst': 0, 'tax': 0, 'invoice_value': 0}
    by_rate = {}
    for ln in lines:
        taxable, tax = tax_inclusive_split(ln['paid'], ln['rate'])
        cgst = sgst = igst = 0
        if intra:
            cgst, sgst = tax // 2, tax - tax // 2
        else:
            igst = tax
        ln.update(taxable=taxable, cgst=cgst, sgst=sgst, igst=igst)
        br = by_rate.setdefault(ln['rate'], {'taxable': 0, 'cgst': 0, 'sgst': 0, 'igst': 0})
        for k, v in (('taxable', taxable), ('cgst', cgst), ('sgst', sgst), ('igst', igst)):
            br[k] += v
            totals[k] += v
        totals['tax'] += tax
        totals['invoice_value'] += ln['paid']
    return {'place_of_supply': order.get('state') or '', 'pos_code': code, 'intra': intra,
            'lines': lines, 'totals': totals, 'by_rate': by_rate}
