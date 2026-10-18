"""Batch lab-test certificates: the small, pure helpers (no database, no Flask).

A customer scans the pouch QR, types the batch code printed on it and learns whether that batch is
valid and whether its certificate is ready. To open the certificate they sign in with Google AND type a
one-time code we e-mail to that Google address -- the code is only ever stored as a keyed hash.
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

CODE_MAX_LEN = 40
OTP_DIGITS = 6
OTP_TTL = timedelta(minutes=10)
OTP_MAX_WRONG_TRIES = 5
OTP_RESEND_COOLDOWN = timedelta(seconds=60)
OTP_MAX_SENDS_PER_HOUR = 5
GRANT_TTL_SECONDS = 30 * 60              # how long a verified visitor can keep opening that batch's files

MAX_FILE_BYTES = 6 * 1024 * 1024         # the app caps a whole request at 8 MB
ALLOWED_TYPES = {                        # extension -> (mimetype, magic bytes it must start with)
    'pdf': ('application/pdf', (b'%PDF-',)),
    'jpg': ('image/jpeg', (b'\xff\xd8\xff',)),
    'jpeg': ('image/jpeg', (b'\xff\xd8\xff',)),
    'png': ('image/png', (b'\x89PNG\r\n\x1a\n',)),
}

STATUS_AVAILABLE = 'available'
STATUS_COMING_SOON = 'coming_soon'
STATUS_NOT_FOUND = 'not_found'


def normalize_code(raw):
    """'  hs-0425/a ' -> 'HS-0425/A'. Letters, digits and - _ / only; '' when nothing usable is left."""
    cleaned = re.sub(r'[^A-Za-z0-9\-_/]', '', (raw or '').strip())
    return cleaned.upper()[:CODE_MAX_LEN]


def status_for(batch, certificate_count):
    if batch is None:
        return STATUS_NOT_FOUND
    return STATUS_AVAILABLE if certificate_count > 0 else STATUS_COMING_SOON


def new_otp():
    return ''.join(secrets.choice('0123456789') for _ in range(OTP_DIGITS))


def hash_otp(secret_key, email, batch_code, otp):
    """Keyed hash, bound to the e-mail and the batch so a code can't be replayed elsewhere."""
    msg = f"{(email or '').strip().lower()}|{batch_code}|{(otp or '').strip()}".encode()
    return hmac.new(str(secret_key).encode(), msg, hashlib.sha256).hexdigest()


def otp_matches(secret_key, email, batch_code, otp, stored_hash):
    if not re.fullmatch(r'\d{%d}' % OTP_DIGITS, (otp or '').strip()) or not stored_hash:
        return False
    return hmac.compare_digest(hash_otp(secret_key, email, batch_code, otp), stored_hash)


def check_upload(filename, data):
    """Return (extension, mimetype) for an acceptable certificate file, or raise ValueError(reason).
    The content is checked, not just the name, so a renamed .exe can't be stored."""
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("The file is too large (limit %d MB)." % (MAX_FILE_BYTES // (1024 * 1024)))
    ext = (filename or '').rsplit('.', 1)[-1].lower() if '.' in (filename or '') else ''
    if ext not in ALLOWED_TYPES:
        raise ValueError("Upload a PDF, JPG or PNG file.")
    mimetype, magics = ALLOWED_TYPES[ext]
    if not any(data.startswith(m) for m in magics):
        raise ValueError("That file doesn't look like a real %s." % ext.upper())
    return ext, mimetype


def safe_filename(title, ext):
    """A tidy download name from the certificate title: 'Lab Report (Oct)' -> 'Lab-Report-Oct.pdf'."""
    base = re.sub(r'[^A-Za-z0-9]+', '-', title or '').strip('-')[:60] or 'certificate'
    return f"{base}.{ext}"


def grant_valid(grants, code, email, now_ts):
    """grants is the session dict {batch_code: {'email': ..., 'until': ts}}."""
    g = (grants or {}).get(code)
    return bool(g) and g.get('email') == (email or '').strip().lower() and g.get('until', 0) > now_ts


def can_send(last_sent_at, sends_last_hour, now=None):
    """(allowed, wait_seconds) for a new e-mail code request."""
    now = now or datetime.utcnow()
    if sends_last_hour >= OTP_MAX_SENDS_PER_HOUR:
        return False, 3600
    if last_sent_at and now - last_sent_at < OTP_RESEND_COOLDOWN:
        return False, int((OTP_RESEND_COOLDOWN - (now - last_sent_at)).total_seconds()) + 1
    return True, 0
