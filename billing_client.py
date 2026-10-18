"""Read-only link to the shop's Heritage Billing database (its own Supabase project).

The server signs in as a read-only "Accountant" user and calls the dashboard_summary function. Nothing is
ever written, and the credentials live only in Render's environment variables -- never in a page.
Error messages are written to be shown to the owner, so they never contain a key or a password.

Environment variables:
  BILLING_SUPABASE_URL, BILLING_SUPABASE_ANON_KEY, BILLING_DASHBOARD_EMAIL, BILLING_DASHBOARD_PASSWORD
"""
import os
import threading
import time

import requests

TIMEOUT = 15
CACHE_SECONDS = 300

_lock = threading.Lock()
_token = {'value': None, 'expires': 0.0}
_cache = {}


class BillingUnavailable(Exception):
    """The shop data could not be loaded. str(self) is a safe, plain-English reason."""


def _cfg():
    return (os.getenv('BILLING_SUPABASE_URL', '').strip().rstrip('/'),
            os.getenv('BILLING_SUPABASE_ANON_KEY', '').strip(),
            os.getenv('BILLING_DASHBOARD_EMAIL', '').strip(),
            os.getenv('BILLING_DASHBOARD_PASSWORD', ''))


def configured():
    return all(_cfg())


def reset():
    """Forget the cached login and results (used by tests, and if the password is changed)."""
    with _lock:
        _token.update(value=None, expires=0.0)
        _cache.clear()


def _login(url, key, email, password):
    try:
        r = requests.post(f'{url}/auth/v1/token', params={'grant_type': 'password'},
                          headers={'apikey': key, 'Content-Type': 'application/json'},
                          json={'email': email, 'password': password}, timeout=TIMEOUT)
    except requests.RequestException:
        raise BillingUnavailable('The shop database could not be reached. Try again in a few minutes.')
    if r.status_code in (400, 401, 403, 422):
        raise BillingUnavailable('The shop database refused the dashboard login. '
                                 'Check the email and password saved in Render.')
    if r.status_code != 200:
        raise BillingUnavailable(f'The shop database login failed (code {r.status_code}).')
    try:
        data = r.json()
        token = data['access_token']
    except (ValueError, KeyError, TypeError):
        raise BillingUnavailable('The shop database gave an unexpected login reply.')
    _token['value'] = token
    _token['expires'] = time.time() + max(60, int(data.get('expires_in') or 3600)) - 60
    return token


def _get_token(url, key, email, password, force=False):
    if not force and _token['value'] and time.time() < _token['expires']:
        return _token['value']
    return _login(url, key, email, password)


def _rpc(url, key, token, start, end, fn='dashboard_summary'):
    try:
        return requests.post(f'{url}/rest/v1/rpc/{fn}',
                             headers={'apikey': key, 'Authorization': f'Bearer {token}',
                                      'Content-Type': 'application/json'},
                             json={'p_from': start.isoformat(), 'p_to': end.isoformat()}, timeout=TIMEOUT)
    except requests.RequestException:
        raise BillingUnavailable('The shop database could not be reached. Try again in a few minutes.')


def _fetch(fn, required_key, start, end):
    """Call one of the billing database's read-only functions for start..end (dates, inclusive); cached for
    5 minutes. Raises BillingUnavailable with a safe message when anything goes wrong."""
    url, key, email, password = _cfg()
    if not all((url, key, email, password)):
        raise BillingUnavailable('The shop database is not connected yet.')
    ck = (fn, start, end)
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]

    with _lock:
        token = _get_token(url, key, email, password)
        r = _rpc(url, key, token, start, end, fn)
        if r.status_code == 401:                       # token expired or revoked: sign in again, once
            token = _get_token(url, key, email, password, force=True)
            r = _rpc(url, key, token, start, end, fn)
    if r.status_code == 404:
        sql_file = {'dashboard_summary': 'dashboard_api.sql', 'gst_summary': 'gst_api.sql'}.get(fn, 'the matching SQL file')
        raise BillingUnavailable(f'The shop database is missing its {fn} function. '
                                 f'Run {sql_file} in the billing Supabase SQL Editor.')
    if r.status_code in (401, 403):
        raise BillingUnavailable('The shop database did not allow the dashboard user to read. '
                                 'Check that the user is Active with the Accountant role in the billing app.')
    if r.status_code != 200:
        raise BillingUnavailable(f'The shop database returned an error (code {r.status_code}).')
    try:
        data = r.json()
    except ValueError:
        data = None
    if not isinstance(data, dict) or required_key not in data:
        raise BillingUnavailable('The shop database gave an unexpected reply.')
    _cache[ck] = (time.time(), data)
    return data


def get_summary(start, end):
    """The shop's sales dashboard totals (dashboard_summary)."""
    return _fetch('dashboard_summary', 'salesPaise', start, end)


def get_gst_summary(start, end):
    """The shop's GST figures: B2B/B2C, HSN, credit notes, documents issued and purchase GST (gst_summary)."""
    return _fetch('gst_summary', 'totals', start, end)
