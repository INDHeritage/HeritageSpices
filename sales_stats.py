"""Maths for the admin sales dashboard: Indian-time date ranges, the GST that is already inside our
prices, and the online-channel summary. Pure functions on plain dicts so every rule is easy to test."""
from collections import defaultdict
from datetime import date, datetime, timedelta

IST = timedelta(hours=5, minutes=30)
DEFAULT_GST_RATE = 5            # garam masala, HSN 0910 -- same rate the shop's billing app uses
SELLER_STATE = 'maharashtra'    # Sindewahi: same-state sales are CGST+SGST, other states IGST

RANGE_LABELS = [
    ('today', 'Today'), ('yesterday', 'Yesterday'), ('7d', 'Last 7 days'), ('30d', 'Last 30 days'),
    ('month', 'This month'), ('last_month', 'Last month'), ('fy', 'This financial year'), ('custom', 'Custom'),
]


def ist_today(now_utc=None):
    """Today's date in India (the server clock is UTC)."""
    return ((now_utc or datetime.utcnow()) + IST).date()


def to_ist_date(dt_utc):
    return (dt_utc + IST).date()


def _parse(d):
    try:
        return datetime.strptime(d, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def financial_year_start(today):
    """Indian financial year runs 1 April to 31 March."""
    return date(today.year if today.month >= 4 else today.year - 1, 4, 1)


def resolve_range(key, today, custom_from=None, custom_to=None):
    """(start, end, key) inclusive dates for a range name. Unknown names fall back to this month."""
    if key == 'today':
        return today, today, key
    if key == 'yesterday':
        y = today - timedelta(days=1)
        return y, y, key
    if key == '7d':
        return today - timedelta(days=6), today, key
    if key == '30d':
        return today - timedelta(days=29), today, key
    if key == 'last_month':
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev, key
    if key == 'fy':
        return financial_year_start(today), today, key
    if key == 'custom':
        a, b = _parse(custom_from), _parse(custom_to)
        if a and b:
            if a > b:
                a, b = b, a
            if (b - a).days <= 800:
                return a, b, key
    return today.replace(day=1), today, 'month'


def _half_up_div(n, d):
    return (2 * n + d) // (2 * d)


def split_gst(total_paise, state, rate=DEFAULT_GST_RATE):
    """Our prices already include GST, so nothing is added: this works the tax back out of the total.
    Same state as the seller -> half CGST + half SGST; any other state -> IGST."""
    total = int(total_paise or 0)
    gst = _half_up_div(total * rate, 100 + rate)
    out = {'taxable': total - gst, 'gst': gst, 'cgst': 0, 'sgst': 0, 'igst': 0}
    if str(state or '').strip().lower() == SELLER_STATE:
        out['cgst'] = gst // 2
        out['sgst'] = gst - gst // 2
    else:
        out['igst'] = gst
    return out


def _bucket(order):
    """Which money bucket a paid order belongs to."""
    s = (order.get('shipping_status') or '').lower()
    if s == 'cancelled':
        return 'cancelled'
    if s == 'rto':
        return 'rto'
    return 'net'


def summarize_online(orders, payments, start, end, earlier_customers=(), rate=DEFAULT_GST_RATE):
    """Everything the online half of the dashboard shows, for paid orders dated start..end (IST).

    orders:    dicts with id, created_at (UTC datetime), user_email, state, city, subtotal, shipping_cost,
               total_amount, discount_amount, points_discount_amount, payment_status, shipping_status,
               razorpay_payment_id, items=[{name, qty, unit_price}]   (all amounts in paise)
    payments:  {razorpay_payment_id: {method, amount, fee, tax, refunded}} from Razorpay, or None if unavailable
    earlier_customers: emails that paid for an order before `start` (to tell new from returning buyers)
    """
    earlier = set(earlier_customers)
    in_range = [o for o in orders
                if o.get('payment_status') == 'paid' and start <= to_ist_date(o['created_at']) <= end]

    gross = sum(o['total_amount'] for o in in_range)
    cancelled = [o for o in in_range if _bucket(o) == 'cancelled']
    rto = [o for o in in_range if _bucket(o) == 'rto']
    net_orders = [o for o in in_range if _bucket(o) == 'net']
    net = sum(o['total_amount'] for o in net_orders)

    shipping = sum(o.get('shipping_cost') or 0 for o in net_orders)
    coupon = sum(o.get('discount_amount') or 0 for o in net_orders)
    points = sum(o.get('points_discount_amount') or 0 for o in net_orders)

    gst = {'taxable': 0, 'gst': 0, 'cgst': 0, 'sgst': 0, 'igst': 0}
    for o in net_orders:
        for k, v in split_gst(o['total_amount'], o.get('state'), rate).items():
            gst[k] += v

    # day by day, with zero days filled in so the chart has no gaps
    daily = {}
    d = start
    while d <= end:
        daily[d] = {'date': d.isoformat(), 'orders': 0, 'net': 0}
        d += timedelta(days=1)
    for o in net_orders:
        row = daily[to_ist_date(o['created_at'])]
        row['orders'] += 1
        row['net'] += o['total_amount']

    products = defaultdict(lambda: {'name': '', 'qty': 0, 'revenue': 0})
    for o in net_orders:
        for it in o.get('items', []):
            p = products[it['name']]
            p['name'] = it['name']
            p['qty'] += it['qty']
            p['revenue'] += it['qty'] * it['unit_price']

    states, cities = defaultdict(lambda: {'orders': 0, 'net': 0}), defaultdict(lambda: {'orders': 0, 'net': 0})
    for o in net_orders:
        st = (o.get('state') or 'Unknown').strip().title()
        ct = ((o.get('city') or 'Unknown').strip().title(), st)
        states[st]['orders'] += 1
        states[st]['net'] += o['total_amount']
        cities[ct]['orders'] += 1
        cities[ct]['net'] += o['total_amount']

    status_counts = defaultdict(int)
    for o in in_range:
        status_counts[(o.get('shipping_status') or 'processing').lower()] += 1

    customers = {o['user_email'] for o in net_orders}
    returning = {e for e in customers if e in earlier}

    methods = None
    if payments is not None:
        methods = defaultdict(lambda: {'method': '', 'orders': 0, 'amount': 0, 'fee': 0, 'fee_tax': 0, 'refunded': 0})
        for o in net_orders:
            p = payments.get(o.get('razorpay_payment_id')) or {}
            m = methods[p.get('method') or 'unknown']
            m['method'] = p.get('method') or 'unknown'
            m['orders'] += 1
            m['amount'] += o['total_amount']
            m['fee'] += p.get('fee') or 0
            m['fee_tax'] += p.get('tax') or 0
            m['refunded'] += p.get('refunded') or 0
        for m in methods.values():
            m['received'] = m['amount'] - m['refunded'] - m['fee'] - m['fee_tax']
        methods = sorted(methods.values(), key=lambda m: -m['amount'])

    fee_total = sum(m['fee'] for m in methods) if methods else 0
    fee_tax_total = sum(m['fee_tax'] for m in methods) if methods else 0
    refunded_total = sum(m['refunded'] for m in methods) if methods else 0

    return {
        'start': start.isoformat(), 'end': end.isoformat(),
        'gross': gross, 'cancelled': sum(o['total_amount'] for o in cancelled), 'cancelled_orders': len(cancelled),
        'rto': sum(o['total_amount'] for o in rto), 'rto_orders': len(rto),
        'net': net, 'orders': len(net_orders), 'avg_order': (net // len(net_orders)) if net_orders else 0,
        'products_incl': net - shipping, 'shipping': shipping, 'coupon_discount': coupon, 'points_discount': points,
        'gst': gst,
        'daily': list(daily.values()),
        'top_products': sorted(products.values(), key=lambda p: -p['revenue'])[:10],
        'states': sorted(({'state': k, **v} for k, v in states.items()), key=lambda r: -r['net']),
        'cities': sorted(({'city': k[0], 'state': k[1], **v} for k, v in cities.items()), key=lambda r: -r['net'])[:10],
        'status_counts': dict(status_counts),
        'customers': len(customers), 'returning_customers': len(returning),
        'payment_methods': methods, 'payments_available': payments is not None,
        'fee_total': fee_total, 'fee_tax_total': fee_tax_total, 'refunded_total': refunded_total,
        'received_total': (net - refunded_total - fee_total - fee_tax_total) if payments is not None else None,
    }
