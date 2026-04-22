"""
Authentication helpers for personal_ai.

Password hash is stored in .auth_hash (written by set_password.py).
The LOGIN_PASSWORD_HASH env var overrides the file.
"""
import os
import time
import secrets
from urllib.parse import urlsplit
from functools import wraps
from flask import session, redirect, url_for, request
from werkzeug.security import generate_password_hash, check_password_hash

AUTH_HASH_FILE = ".auth_hash"

# ── Rate limiter ──────────────────────────────────────────────────────────────

_failed: dict[str, list[float]] = {}  # ip -> [timestamps]
MAX_ATTEMPTS = 5
WINDOW = 900  # 15 minutes


def get_client_ip() -> str:
    remote = request.remote_addr or "unknown"
    try:
        from config import config
        trust_xff = config.TRUST_X_FORWARDED_FOR
    except Exception:
        trust_xff = False

    if not trust_xff:
        return remote

    forwarded = request.headers.get("X-Forwarded-For", "")
    if not forwarded:
        return remote
    return forwarded.split(",")[0].strip() or remote


def is_rate_limited(ip: str) -> bool:
    cutoff = time.time() - WINDOW
    attempts = [t for t in _failed.get(ip, []) if t > cutoff]
    _failed[ip] = attempts
    return len(attempts) >= MAX_ATTEMPTS


def record_failed(ip: str) -> None:
    cutoff = time.time() - WINDOW
    attempts = [t for t in _failed.get(ip, []) if t > cutoff]
    attempts.append(time.time())
    _failed[ip] = attempts


def clear_failed(ip: str) -> None:
    _failed.pop(ip, None)


# ── CSRF ──────────────────────────────────────────────────────────────────────

def make_csrf() -> str:
    token = secrets.token_hex(32)
    session["_csrf"] = token
    return token


def valid_csrf(token: str) -> bool:
    expected = session.get("_csrf", "")
    return bool(expected and token and secrets.compare_digest(expected, token))


def _normalized_origin(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def request_has_same_origin() -> bool:
    """Validate browser Origin/Referer against current or forwarded host."""
    allowed_origins: set[str] = set()

    host_origin = _normalized_origin(request.host_url)
    if host_origin:
        allowed_origins.add(host_origin)

    xf_proto = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
    xf_host = request.headers.get("X-Forwarded-Host", "").split(",", 1)[0].strip()
    if xf_proto and xf_host:
        forwarded_origin = _normalized_origin(f"{xf_proto}://{xf_host}")
        if forwarded_origin:
            allowed_origins.add(forwarded_origin)

    origin = _normalized_origin(request.headers.get("Origin", ""))
    if origin:
        return origin in allowed_origins

    referer = _normalized_origin(request.headers.get("Referer", ""))
    if referer:
        return referer in allowed_origins

    return False


# ── Password hash storage ─────────────────────────────────────────────────────

def load_hash() -> str:
    """Return stored password hash, or empty string if not configured."""
    env_hash = os.getenv("LOGIN_PASSWORD_HASH", "")
    if env_hash:
        return env_hash
    try:
        with open(AUTH_HASH_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def save_hash(password: str) -> None:
    """Hash `password` and persist to AUTH_HASH_FILE."""
    h = generate_password_hash(password)
    fd = os.open(AUTH_HASH_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(h)
    os.chmod(AUTH_HASH_FILE, 0o600)


def verify_password(password: str) -> bool:
    h = load_hash()
    return bool(h and check_password_hash(h, password))


# ── Session guard ─────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return inner
