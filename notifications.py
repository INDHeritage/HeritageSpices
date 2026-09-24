"""Customer e-mails (order confirmed / shipped / delivered).

Two ways to send, both free. Nothing is sent unless one is configured, so the site behaves
exactly as before until you set it up. Sending happens in a background thread and can never
make an order, payment or shipment fail.

1) HTTPS mail relay (works on Render's FREE plan, which blocks SMTP ports 465/587/25):
       EMAIL_WEBHOOK_URL      the "web app" URL of a small Google Apps Script that sends mail
                              from your own Gmail (see docs/PROJECT_REVIEW.md for the script)
       EMAIL_WEBHOOK_SECRET   a long random string, also pasted into that script
2) Plain SMTP (needs a paid Render plan, or any host that allows outbound SMTP):
       SMTP_HOST      e.g. smtp.gmail.com  or  smtp-relay.brevo.com
       SMTP_PORT      465 (SSL, default) or 587 (STARTTLS)
       SMTP_USER      login / sender address
       SMTP_PASSWORD  the app password (NOT your normal Google password)
       MAIL_FROM      optional, defaults to SMTP_USER
Common:
       MAIL_FROM_NAME optional, defaults to "Heritage Spices"
       SITE_URL       optional, defaults to https://www.indianheritagespices.com
If both are set, the HTTPS relay is used.
"""
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

RUN_IN_BACKGROUND = True  # tests switch this off so sends happen inline

MAROON = '#6b1d14'
GOLD = '#d4a017'


def site_url():
    return os.getenv('SITE_URL', 'https://www.indianheritagespices.com').rstrip('/')


def provider():
    """'webhook', 'smtp' or None (not configured)."""
    if os.getenv('EMAIL_WEBHOOK_URL') and os.getenv('EMAIL_WEBHOOK_SECRET'):
        return 'webhook'
    if os.getenv('SMTP_HOST') and os.getenv('SMTP_USER') and os.getenv('SMTP_PASSWORD'):
        return 'smtp'
    return None


def is_configured():
    return provider() is not None


def _send_now(to, subject, text, html):
    """Send one e-mail with whichever method is configured. Raises on failure."""
    if provider() == 'webhook':
        return _send_webhook(to, subject, text, html)
    return _send_smtp(to, subject, text, html)


def _send_webhook(to, subject, text, html):
    import requests  # local import: only needed on this path
    name = os.getenv('MAIL_FROM_NAME', 'Heritage Spices')
    resp = requests.post(os.getenv('EMAIL_WEBHOOK_URL'), json={
        'secret': os.getenv('EMAIL_WEBHOOK_SECRET'), 'to': to, 'subject': subject,
        'text': text, 'html': html or '', 'name': name,
    }, timeout=25)
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"mail relay gave an unexpected reply (HTTP {resp.status_code}). "
                           f"Check the Apps Script is deployed as a web app with access 'Anyone'.")
    if not data.get('ok'):
        raise RuntimeError(f"mail relay refused the request: {data.get('error', 'unknown error')}")


def _send_smtp(to, subject, text, html):
    host = os.getenv('SMTP_HOST')
    port = int(os.getenv('SMTP_PORT', '465'))
    user = os.getenv('SMTP_USER')
    password = os.getenv('SMTP_PASSWORD')
    sender = os.getenv('MAIL_FROM') or user
    name = os.getenv('MAIL_FROM_NAME', 'Heritage Spices')

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = formataddr((name, sender))
    msg['To'] = to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype='html')

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as server:
            server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(msg)


def send_email(to, subject, text, html=None):
    """Queue an e-mail. Returns True if it was handed off, False if e-mail isn't set up."""
    if not to or not is_configured():
        return False

    def _work():
        try:
            _send_now(to, subject, text, html)
        except Exception as e:  # never let a mail problem touch an order
            print(f"E-mail to {to} failed: {e}")

    if RUN_IN_BACKGROUND:
        threading.Thread(target=_work, daemon=True).start()
    else:
        _work()
    return True


# ---------- message builders ----------

def _rupees(paise):
    return f"₹{(paise or 0) // 100}"


def _items_lines(order):
    return [f"{i.quantity} x {i.product_name}  ({_rupees(i.unit_price * i.quantity)})" for i in order.items]


def _address_lines(order):
    return [order.full_name, order.address, f"{order.city}, {order.state} - {order.pincode}"]


def _wrap_html(title, intro, rows_html, footer_html=''):
    return f"""<!doctype html><html><body style="margin:0;background:#fdf8ef;font-family:Arial,Helvetica,sans-serif;color:#333;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-radius:10px;overflow:hidden;">
<tr><td style="background:{MAROON};padding:18px 24px;color:{GOLD};font-size:20px;font-weight:bold;">Heritage Spices</td></tr>
<tr><td style="padding:24px;">
<h2 style="margin:0 0 8px;color:{MAROON};font-size:20px;">{escape(title)}</h2>
<p style="margin:0 0 16px;line-height:1.5;">{intro}</p>
{rows_html}
{footer_html}
<p style="margin:24px 0 0;font-size:12px;color:#888;">Questions? Just reply to this e-mail.<br>Heritage Spices Pvt. Ltd., Sindewahi, Maharashtra 441222</p>
</td></tr></table></td></tr></table></body></html>"""


def _box(title, lines):
    body = '<br>'.join(escape(str(l)) for l in lines)
    return (f'<div style="background:#faf5ea;border-radius:8px;padding:12px 14px;margin:0 0 12px;">'
            f'<div style="font-size:12px;color:#888;text-transform:uppercase;margin-bottom:4px;">{escape(title)}</div>'
            f'<div style="line-height:1.5;">{body}</div></div>')


def _button(url, label):
    return (f'<p style="margin:16px 0;"><a href="{escape(url, quote=True)}" style="background:{MAROON};color:{GOLD};'
            f'padding:12px 22px;border-radius:24px;text-decoration:none;font-weight:bold;display:inline-block;">'
            f'{escape(label)}</a></p>')


def order_confirmed(order):
    subject = f"Order confirmed: {order.order_number}"
    total = _rupees(order.total_amount)
    text = "\n".join([
        f"Hi {order.full_name},", "",
        f"Thank you! We've received your payment of {total} for order {order.order_number}.",
        "", "Items:", *_items_lines(order), "",
        "Delivering to:", *_address_lines(order), "",
        "We'll e-mail you again with tracking details as soon as it ships.",
        f"View your orders: {site_url()}/orders", "", "Heritage Spices"])
    html = _wrap_html(
        f"Thank you, {order.full_name}!",
        f"We've received your payment of <b>{escape(total)}</b> for order <b>{escape(order.order_number)}</b>. "
        f"We'll e-mail you again with tracking details as soon as it ships.",
        _box('Items', _items_lines(order)) + _box('Delivering to', _address_lines(order)),
        _button(f"{site_url()}/orders", 'View my orders'))
    return subject, text, html


def order_shipped(order, tracking_url):
    subject = f"Your order {order.order_number} has shipped"
    courier = order.courier_name or 'our courier'
    lines = [f"Courier: {courier}", f"Tracking number (AWB): {order.awb_number or '-'}"]
    text = "\n".join([
        f"Hi {order.full_name},", "",
        f"Good news - your order {order.order_number} is on its way with {courier}.",
        f"Tracking number (AWB): {order.awb_number or '-'}",
        f"Track your parcel: {tracking_url}", "",
        "Delivering to:", *_address_lines(order), "", "Heritage Spices"])
    html = _wrap_html(
        "Your order is on its way",
        f"Your order <b>{escape(order.order_number)}</b> has shipped with <b>{escape(courier)}</b>.",
        _box('Tracking', lines) + _box('Delivering to', _address_lines(order)),
        _button(tracking_url, 'Track my parcel'))
    return subject, text, html


def order_delivered(order):
    subject = f"Your order {order.order_number} was delivered"
    text = "\n".join([
        f"Hi {order.full_name},", "",
        f"Your order {order.order_number} has been delivered. We hope you enjoy it!",
        "If anything isn't right, just reply to this e-mail and we'll sort it out.", "",
        f"Cooking ideas: {site_url()}/blog", "", "Heritage Spices"])
    html = _wrap_html(
        "Delivered - enjoy!",
        f"Your order <b>{escape(order.order_number)}</b> has been delivered. We hope you enjoy it! "
        f"If anything isn't right, just reply to this e-mail and we'll sort it out.",
        '', _button(f"{site_url()}/blog", 'Get cooking ideas'))
    return subject, text, html


# ---------- referral messages ----------

def referral_joined(referrer_name, friend_name, reward_amount):
    """To the referrer: a friend just signed up with their link."""
    subject = f"{friend_name} joined Heritage Spices using your link"
    text = "\n".join([
        f"Hi {referrer_name},", "",
        f"Good news - {friend_name} just signed up using your referral link.",
        f"As soon as they complete their first paid order you'll get a Rs {reward_amount} coupon.", "",
        f"See your referrals: {site_url()}/refer", "", "Heritage Spices"])
    html = _wrap_html(
        "Your friend just joined!",
        f"<b>{escape(friend_name)}</b> signed up using your referral link. As soon as they complete their "
        f"first paid order you'll get a <b>\u20b9{escape(str(reward_amount))}</b> coupon.",
        '', _button(f"{site_url()}/refer", 'See my referrals'))
    return subject, text, html


def referral_reward_for_referrer(referrer_name, friend_name, coupon_code, reward_amount, expiry_days):
    """To the referrer: their friend paid, here is the coupon."""
    subject = "You earned a reward: your friend placed their first order"
    expiry = f" It is valid for {expiry_days} days." if expiry_days else ""
    text = "\n".join([
        f"Hi {referrer_name},", "",
        f"{friend_name} placed their first order, so here is your reward: Rs {reward_amount} off.",
        f"Coupon code: {coupon_code}.{expiry}",
        "Use it at checkout, or find it anytime under My Coupons.", "",
        f"{site_url()}/my-coupons", "", "Heritage Spices"])
    html = _wrap_html(
        "You earned a reward!",
        f"<b>{escape(friend_name)}</b> placed their first order, so here is your reward: "
        f"<b>\u20b9{escape(str(reward_amount))} off</b>.{escape(expiry)}",
        _box('Your coupon code', [coupon_code]),
        _button(f"{site_url()}/my-coupons", 'View my coupons'))
    return subject, text, html


def referral_points_for_friend(friend_name, points, balance):
    """To the referred friend: bonus points were credited."""
    subject = f"You earned {points} bonus points"
    text = "\n".join([
        f"Hi {friend_name},", "",
        f"Thanks for your first order! We've added {points} bonus points to your account.",
        f"Your balance is now {balance} points - use them at checkout on your next order.", "",
        f"{site_url()}/refer", "", "Heritage Spices"])
    html = _wrap_html(
        f"{points} bonus points added",
        f"Thanks for your first order! We've added <b>{points} points</b> to your account. "
        f"Your balance is now <b>{balance} points</b> - use them at checkout on your next order.",
        '', _button(f"{site_url()}/refer", 'See my points'))
    return subject, text, html
