"""Authentication primitives and the versioned multi-user schema foundation.

Existing website data remains unowned until the explicitly authorized owner
setup endpoint is used. This module does not make legacy application routes
private; that is a required follow-up before the app can be exposed.
"""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import smtplib
import ssl
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import Blueprint, current_app, g, jsonify, make_response, request
from itsdangerous import BadSignature, URLSafeTimedSerializer


auth_blueprint = Blueprint("auth", __name__)
password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=2$3+MIg9PctcIoKgfspfjpGw$kYEdDUkUjeIWpcAep3+p/qtqVrjjzzZV4QlscTmgfLU"
SESSION_COOKIE = "__Host-webwatch_session"
CSRF_COOKIE = "webwatch_csrf"
SESSION_DAYS = 30
DEFAULT_GUEST_TTL_SECONDS = 6 * 60 * 60
MAX_GUEST_TTL_SECONDS = 24 * 60 * 60
MAX_ACTIVE_GUESTS = 100
INVITATION_DAYS = 7
LOGIN_WINDOW_SECONDS = 900
LOGIN_MAX_ATTEMPTS = 5
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def _database():
    connection = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _secret():
    secret = current_app.config.get("SECRET_KEY")
    if not isinstance(secret, str) or len(secret) < 32:
        return None
    return secret


def _csrf_serializer():
    secret = _secret()
    return URLSafeTimedSerializer(secret, salt="webwatch-csrf-v1") if secret else None


def _cookie_headers(response, session_token=None, csrf_token=None, clear=False, session_max_age=None):
    if clear:
        response.set_cookie(SESSION_COOKIE, "", max_age=0, expires=0, path="/", secure=True,
                            httponly=True, samesite="Lax")
        response.set_cookie(CSRF_COOKIE, "", max_age=0, expires=0, path="/", secure=True,
                            httponly=False, samesite="Strict")
    if session_token is not None:
        response.set_cookie(SESSION_COOKIE, session_token,
                            max_age=session_max_age or SESSION_DAYS * 86400,
                            path="/", secure=True, httponly=True, samesite="Lax")
    if csrf_token is not None:
        response.set_cookie(CSRF_COOKIE, csrf_token, max_age=3600, path="/", secure=True,
                            httponly=False, samesite="Strict")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _json(payload, status=200, *, session_token=None, csrf_token=None, clear=False, session_max_age=None):
    response = make_response(jsonify(payload), status)
    return _cookie_headers(response, session_token, csrf_token, clear, session_max_age)


def _request_token_hash(token):
    return hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()


def _normalize_email(value):
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if len(normalized) > 254 or not EMAIL_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _password_ok(password):
    return isinstance(password, str) and 12 <= len(password) <= 1024 and len(password.encode("utf-8")) <= 1024


def guest_ttl_seconds():
    try:
        value = int(os.environ.get("WEBWATCH_GUEST_TTL_SECONDS", DEFAULT_GUEST_TTL_SECONDS))
    except (TypeError, ValueError):
        value = DEFAULT_GUEST_TTL_SECONDS
    return min(MAX_GUEST_TTL_SECONDS, max(900, value))


def cleanup_expired_guest_accounts(database_path):
    """Delete expired guest users and their complete owned data atomically."""
    db = sqlite3.connect(database_path, timeout=30)
    try:
        db.row_factory = sqlite3.Row
        now = _iso(_now())
        if not db.execute(
            "SELECT 1 FROM guest_accounts WHERE expires_at <= ? LIMIT 1", (now,)
        ).fetchone():
            return 0
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        guest_ids = [row[0] for row in db.execute(
            "SELECT user_id FROM guest_accounts WHERE expires_at <= ?", (now,)
        )]
        for user_id in guest_ids:
            db.execute("DELETE FROM alert_jobs WHERE owner_id = ?", (user_id,))
            db.execute("DELETE FROM notifications WHERE owner_id = ?", (user_id,))
            db.execute("DELETE FROM invitations WHERE created_by = ?", (user_id,))
            db.execute("UPDATE invitations SET used_by = NULL WHERE used_by = ?", (user_id,))
            db.execute("DELETE FROM connectivity_incidents WHERE owner_id = ?", (user_id,))
            db.execute("DELETE FROM websites WHERE owner_id = ?", (user_id,))
            db.execute("DELETE FROM guest_accounts WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        return len(guest_ids)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def provision_demo_account(database_path, email, password):
    """Create the configured demo member once, without changing existing accounts."""
    normalized = _normalize_email(email)
    if normalized is None or not isinstance(password, str) or not password or len(password.encode("utf-8")) > 1024:
        raise ValueError("Demo account email or password configuration is invalid.")
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    try:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT id FROM users WHERE email_normalized = ?", (normalized,)
        ).fetchone()
        if existing:
            db.commit()
            return existing[0]
        cursor = db.execute(
            "INSERT INTO users(email, email_normalized, password_hash, role, created_at) "
            "VALUES (?, ?, ?, 'member', ?)",
            (email.strip(), normalized, password_hasher.hash(password), _iso(_now())),
        )
        db.commit()
        return cursor.lastrowid
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _csrf_valid():
    cookie = request.cookies.get(CSRF_COOKIE, "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not cookie or not supplied or not hmac.compare_digest(cookie, supplied):
        return False
    serializer = _csrf_serializer()
    if serializer is None:
        return False
    try:
        serializer.loads(cookie, max_age=3600)
    except BadSignature:
        return False
    origin = request.headers.get("Origin")
    return not origin or origin.rstrip("/") == request.host_url.rstrip("/")


def csrf_request_valid():
    return _csrf_valid()


def _client_key(email):
    """Hash email and direct peer address so throttle storage contains no PII."""
    secret = _secret()
    if secret is None:
        return None
    address = request.remote_addr or "unknown"
    message = f"{email}\x1f{address}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _session_user(db):
    token = request.cookies.get(SESSION_COOKIE, "")
    if len(token) < 32 or len(token) > 256:
        return None
    row = db.execute(
        "SELECT u.id, u.email, u.role, "
        "CASE WHEN g.user_id IS NULL THEN 0 ELSE 1 END AS is_guest "
        "FROM auth_sessions s JOIN users u ON u.id = s.user_id "
        "LEFT JOIN guest_accounts g ON g.user_id = u.id "
        "WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1 "
        "AND (g.user_id IS NULL OR g.expires_at > ?)",
        (_request_token_hash(token), _iso(_now()), _iso(_now())),
    ).fetchone()
    return dict(row) if row else None


def authenticated_user(database_path=None):
    """Resolve the request identity from the server-side session cookie."""
    path = database_path or current_app.config["DATABASE"]
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        return _session_user(db)
    finally:
        db.close()


def _require_user(db, role=None):
    user = _session_user(db)
    if user is None or (role is not None and user["role"] != role):
        return None
    g.webwatch_user = user
    return user


@auth_blueprint.before_request
def protect_auth_mutations():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not _csrf_valid():
            return _json({"error": "This request could not be verified. Reload the page and try again."}, 403)


@auth_blueprint.before_request
def restrict_guest_auth_routes():
    db = _database()
    try:
        user = _session_user(db)
    finally:
        db.close()
    if user and user["is_guest"] and request.path not in {
        "/auth/status", "/auth/csrf", "/auth/logout", "/auth/guest",
    }:
        return _json({"error": "This action is not available in guest mode."}, 403)


@auth_blueprint.get("/auth/status")
def auth_status():
    db = _database()
    try:
        count = db.execute(
            "SELECT COUNT(*) FROM users WHERE id NOT IN (SELECT user_id FROM guest_accounts)"
        ).fetchone()[0]
        owner_count = db.execute("SELECT COUNT(*) FROM users WHERE role = 'owner'").fetchone()[0]
        return _json({
            "account_exists": count > 0,
            "initial_setup_available": owner_count == 0 and _secret() is not None
            and len(os.environ.get("WEBWATCH_OWNER_SETUP_TOKEN", "")) >= 32,
            "authentication_available": _secret() is not None,
            "password_recovery_available": False,
        })
    finally:
        db.close()


@auth_blueprint.get("/auth/csrf")
def csrf_token():
    serializer = _csrf_serializer()
    if serializer is None:
        return _json({"error": "Authentication is not configured."}, 503)
    token = serializer.dumps(secrets.token_urlsafe(32))
    return _json({"ok": True}, csrf_token=token)


@auth_blueprint.post("/auth/guest")
def start_guest_session():
    """Create a private, temporary member identity for this browser session."""
    database_path = current_app.config["DATABASE"]
    cleanup_expired_guest_accounts(database_path)
    ttl = guest_ttl_seconds()
    now = _now()
    expires_at = _iso(now + timedelta(seconds=ttl))
    email = f"guest-{secrets.token_hex(24)}@guest.invalid"
    session_token = secrets.token_urlsafe(48)
    csrf_token = _csrf_serializer().dumps(secrets.token_urlsafe(32))
    db = _database()
    try:
        if _session_user(db) is not None:
            return _json({"error": "Sign out before starting a guest session."}, 409)
        db.execute("BEGIN IMMEDIATE")
        active = db.execute("SELECT COUNT(*) FROM guest_accounts WHERE expires_at > ?", (_iso(now),)).fetchone()[0]
        if active >= MAX_ACTIVE_GUESTS:
            db.rollback()
            return _json({"error": "Guest sessions are temporarily full. Please try again later."}, 503)
        password_hash = password_hasher.hash(secrets.token_urlsafe(48))
        cursor = db.execute(
            "INSERT INTO users(email,email_normalized,password_hash,role,created_at) VALUES(?,?,?,'member',?)",
            (email, email, password_hash, _iso(now)),
        )
        user_id = cursor.lastrowid
        db.execute("INSERT INTO guest_accounts(user_id,expires_at) VALUES(?,?)", (user_id, expires_at))
        db.execute(
            "INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
            (_request_token_hash(session_token), user_id, _iso(now), expires_at),
        )
        db.commit()
        return _json({"ok": True, "expires_at": expires_at}, session_token=session_token,
                     csrf_token=csrf_token, session_max_age=ttl)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@auth_blueprint.post("/auth/setup")
def setup_first_owner():
    configured_token = os.environ.get("WEBWATCH_OWNER_SETUP_TOKEN", "")
    submitted_token = request.headers.get("X-WebWatch-Owner-Setup", "")
    body = request.get_json(silent=True) or {}
    email = _normalize_email(body.get("email"))
    password = body.get("password")
    if (_secret() is None or len(configured_token) < 32 or not submitted_token
            or not hmac.compare_digest(configured_token, submitted_token)):
        return _json({"error": "Owner setup is unavailable."}, 404)
    if email is None or not _password_ok(password):
        return _json({"error": "Enter a valid email and a password of at least 12 characters."}, 400)

    db = _database()
    token = secrets.token_urlsafe(48)
    csrf = _csrf_serializer().dumps(secrets.token_urlsafe(32))
    now = _now()
    try:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM users WHERE role = 'owner'").fetchone()[0] != 0:
            db.rollback()
            return _json({"error": "Owner setup is unavailable."}, 404)
        cursor = db.execute(
            "INSERT INTO users(email, email_normalized, password_hash, role, created_at) "
            "VALUES (?, ?, ?, 'owner', ?)",
            (body["email"].strip(), email, password_hasher.hash(password), _iso(now)),
        )
        user_id = cursor.lastrowid
        # The one-time owner setup is the only path that claims legacy records.
        db.execute("UPDATE websites SET owner_id = ? WHERE owner_id IS NULL", (user_id,))
        db.execute(
            "UPDATE check_jobs SET owner_id = (SELECT owner_id FROM websites WHERE websites.id = check_jobs.website_id) "
            "WHERE owner_id IS NULL"
        )
        db.execute(
            "UPDATE notifications SET owner_id = (SELECT owner_id FROM websites WHERE websites.id = notifications.website_id) "
            "WHERE owner_id IS NULL"
        )
        db.execute(
            "UPDATE connectivity_incident_sites SET owner_id = (SELECT owner_id FROM websites "
            "WHERE websites.id = connectivity_incident_sites.website_id) WHERE owner_id IS NULL"
        )
        db.execute(
            "UPDATE connectivity_incidents SET owner_id = ? WHERE owner_id IS NULL AND EXISTS ("
            "SELECT 1 FROM connectivity_incident_sites x JOIN websites w ON w.id=x.website_id "
            "WHERE x.incident_id=connectivity_incidents.id AND w.owner_id=?)", (user_id, user_id)
        )
        db.execute(
            "UPDATE alert_jobs SET owner_id = (SELECT owner_id FROM websites WHERE websites.id = CAST(substr(alert_jobs.event_key, 7, instr(substr(alert_jobs.event_key, 7), ':') - 1) AS INTEGER)) "
            "WHERE owner_id IS NULL AND event_key LIKE 'change:%'"
        )
        db.execute(
            "UPDATE alert_jobs SET owner_id = (SELECT owner_id FROM notifications WHERE notifications.id = CAST(substr(alert_jobs.event_key, 14) AS INTEGER)) "
            "WHERE owner_id IS NULL AND event_key LIKE 'availability:%'"
        )
        # Legacy digest jobs have no site key; unknown/orphan jobs stay unassigned.
        db.execute("UPDATE alert_jobs SET owner_id = ? WHERE owner_id IS NULL AND event_key LIKE 'digest:%'", (user_id,))
        db.execute(
            "INSERT INTO user_settings(owner_id, setting_key, setting_value) "
            "SELECT ?, setting_key, setting_value FROM app_settings",
            (user_id,),
        )
        db.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_request_token_hash(token), user_id, _iso(now), _iso(now + timedelta(days=SESSION_DAYS))),
        )
        db.commit()
        return _json({"user": {"id": user_id, "email": email, "role": "owner"}}, 201,
                     session_token=token, csrf_token=csrf)
    except sqlite3.IntegrityError:
        db.rollback()
        return _json({"error": "Owner setup is unavailable."}, 404)
    finally:
        db.close()


@auth_blueprint.post("/auth/login")
def login():
    if _secret() is None:
        return _json({"error": "Authentication is not configured."}, 503)
    body = request.get_json(silent=True) or {}
    email = _normalize_email(body.get("email")) or "invalid"
    password = body.get("password")
    key = _client_key(email)
    db = _database()
    try:
        db.execute("BEGIN IMMEDIATE")
        now = _now()
        attempt = db.execute("SELECT * FROM login_rate_limits WHERE key_hash = ?", (key,)).fetchone()
        if attempt and attempt["window_started_at"] > _iso(now - timedelta(seconds=LOGIN_WINDOW_SECONDS)):
            if attempt["attempts"] >= LOGIN_MAX_ATTEMPTS:
                db.rollback()
                return _json({"error": "Email or password is incorrect."}, 401)
        else:
            db.execute("DELETE FROM login_rate_limits WHERE key_hash = ?", (key,))

        user = db.execute(
            "SELECT id, email, email_normalized, password_hash, role FROM users "
            "WHERE email_normalized = ? AND is_active = 1", (email,),
        ).fetchone()
        valid = False
        if isinstance(password, str) and len(password.encode("utf-8")) <= 1024:
            try:
                hash_to_check = user["password_hash"] if user else DUMMY_PASSWORD_HASH
                password_matches = password_hasher.verify(hash_to_check, password)
                valid = bool(user and password_matches)
            except (VerificationError, InvalidHashError, ValueError):
                valid = False
        if not valid:
            if attempt and attempt["window_started_at"] > _iso(now - timedelta(seconds=LOGIN_WINDOW_SECONDS)):
                attempts = attempt["attempts"] + 1
                started = attempt["window_started_at"]
            else:
                attempts, started = 1, _iso(now)
            db.execute(
                "INSERT INTO login_rate_limits(key_hash, attempts, window_started_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key_hash) DO UPDATE SET attempts=excluded.attempts, "
                "window_started_at=excluded.window_started_at",
                (key, attempts, started),
            )
            db.commit()
            return _json({"error": "Email or password is incorrect."}, 401)

        db.execute("DELETE FROM login_rate_limits WHERE key_hash = ?", (key,))
        old_token = request.cookies.get(SESSION_COOKIE, "")
        if old_token:
            db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_request_token_hash(old_token),))
        token = secrets.token_urlsafe(48)
        csrf = _csrf_serializer().dumps(secrets.token_urlsafe(32))
        db.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_request_token_hash(token), user["id"], _iso(now), _iso(now + timedelta(days=SESSION_DAYS))),
        )
        db.commit()
        return _json({"user": {"id": user["id"], "email": user["email"], "role": user["role"]}},
                     session_token=token, csrf_token=csrf)
    finally:
        db.close()


@auth_blueprint.post("/auth/logout")
def logout():
    token = request.cookies.get(SESSION_COOKIE, "")
    db = _database()
    try:
        if token:
            token_hash = _request_token_hash(token)
            guest = db.execute(
                "SELECT g.user_id FROM guest_accounts g JOIN auth_sessions s ON s.user_id=g.user_id "
                "WHERE s.token_hash=?", (token_hash,),
            ).fetchone()
            if guest:
                db.execute("UPDATE guest_accounts SET expires_at=? WHERE user_id=?",
                           (_iso(_now()), guest["user_id"]))
            db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
            db.commit()
            if guest:
                cleanup_expired_guest_accounts(current_app.config["DATABASE"])
        return _json({"ok": True}, clear=True)
    finally:
        db.close()


@auth_blueprint.get("/auth/me")
def current_user():
    db = _database()
    try:
        user = _session_user(db)
        if user is None:
            return _json({"error": "Authentication required."}, 401)
        return _json({"user": {"id": user["id"], "email": user["email"], "role": user["role"]}})
    finally:
        db.close()


@auth_blueprint.post("/auth/invitations")
def create_invitation():
    db = _database()
    try:
        user = _require_user(db, role="owner")
        if user is None:
            return _json({"error": "Authentication required."}, 401)
        body = request.get_json(silent=True) or {}
        email = _normalize_email(body.get("email"))
        if email is None:
            return _json({"error": "Enter a valid email address."}, 400)
        if db.execute("SELECT 1 FROM users WHERE email_normalized = ?", (email,)).fetchone():
            return _json({"error": "An account already exists for that email."}, 409)
        smtp_host = os.environ.get("WEBWATCH_SMTP_HOST", "").strip()
        sender = os.environ.get("WEBWATCH_SMTP_FROM", "").strip() or os.environ.get("WEBWATCH_SMTP_USER", "").strip()
        if not smtp_host or not sender:
            return _json({"error": "Invitation email is unavailable until SMTP delivery is configured."}, 503)
        token = secrets.token_urlsafe(40)
        now = _now()
        cursor = db.execute(
            "INSERT INTO invitations(token_hash, email_normalized, created_by, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_request_token_hash(token), email, user["id"], _iso(now), _iso(now + timedelta(days=INVITATION_DAYS))),
        )
        # Deliver a single-use code only to the email address bound to the invitation.
        from email.message import EmailMessage
        message = EmailMessage()
        message["Subject"] = "Your WebWatch invitation"
        message["From"] = sender
        message["To"] = email
        message.set_content(f"Use this invitation code in WebWatch within {INVITATION_DAYS} days:\n\n{token}\n")
        try:
            with smtplib.SMTP(smtp_host, int(os.environ.get("WEBWATCH_SMTP_PORT", "587")), timeout=10) as server:
                server.starttls(context=ssl.create_default_context())
                username = os.environ.get("WEBWATCH_SMTP_USER", "")
                if username:
                    server.login(username, os.environ.get("WEBWATCH_SMTP_PASSWORD", ""))
                server.send_message(message)
        except Exception:
            db.execute("DELETE FROM invitations WHERE id = ? AND used_at IS NULL", (cursor.lastrowid,))
            db.commit()
            return _json({"error": "Invitation email could not be delivered. Check server mail configuration."}, 502)
        db.commit()
        return _json({"email": email, "expires_at": _iso(now + timedelta(days=INVITATION_DAYS))}, 201)
    finally:
        db.close()


@auth_blueprint.post("/auth/redeem")
def redeem_invitation():
    body = request.get_json(silent=True) or {}
    email = _normalize_email(body.get("email"))
    password = body.get("password")
    invitation = body.get("invitation_token")
    if email is None or not _password_ok(password) or not isinstance(invitation, str) or len(invitation) > 256:
        return _json({"error": "Invitation is invalid or expired."}, 400)
    db = _database()
    try:
        now = _now()
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT id FROM invitations WHERE token_hash = ? AND email_normalized = ? "
            "AND used_at IS NULL AND expires_at > ?",
            (_request_token_hash(invitation), email, _iso(now)),
        ).fetchone()
        if row is None:
            db.rollback()
            return _json({"error": "Invitation is invalid or expired."}, 400)
        cursor = db.execute(
            "INSERT INTO users(email, email_normalized, password_hash, role, created_at) "
            "VALUES (?, ?, ?, 'member', ?)",
            (body["email"].strip(), email, password_hasher.hash(password), _iso(now)),
        )
        consumed = db.execute("UPDATE invitations SET used_at = ?, used_by = ? WHERE id = ? AND used_at IS NULL",
                              (_iso(now), cursor.lastrowid, row["id"]))
        if consumed.rowcount != 1:
            db.rollback()
            return _json({"error": "Invitation is invalid or expired."}, 400)
        db.commit()
        return _json({"user": {"id": cursor.lastrowid, "email": email, "role": "member"}}, 201)
    except sqlite3.IntegrityError:
        db.rollback()
        return _json({"error": "Invitation is invalid or expired."}, 400)
    finally:
        db.close()


def _rebuild_websites_for_owner(path, db):
    original = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='websites'").fetchone()
    if original is None:
        return
    sql = original[0]
    if "owner_id" in {row[1] for row in db.execute("PRAGMA table_info(websites)")}:
        return
    global_url_constraint = re.compile(r"\burl\s+TEXT\s+NOT\s+NULL\s+UNIQUE\b", re.IGNORECASE)
    if global_url_constraint.search(sql) is None:
        raise RuntimeError("Could not identify the existing global URL constraint safely.")
    sql, replacements = global_url_constraint.subn("url TEXT NOT NULL", sql, count=1)
    if replacements != 1 or global_url_constraint.search(sql) or re.search(r"\bUNIQUE\b", sql, re.I):
        raise RuntimeError("Could not safely remove the global URL constraint.")
    sql = sql[:sql.rfind(")")] + ", owner_id INTEGER REFERENCES users(id) ON DELETE RESTRICT" + sql[sql.rfind(")"):]
    create_sql = re.sub(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:\"?websites\"?)", "CREATE TABLE websites_auth_new", sql, count=1, flags=re.I)
    columns = [row[1] for row in db.execute("PRAGMA table_info(websites)")]
    column_list = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
    indexes = [row[0] for row in db.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='websites' AND sql IS NOT NULL"
    )]
    db.execute(create_sql)
    db.execute(f"INSERT INTO websites_auth_new ({column_list}, owner_id) SELECT {column_list}, NULL FROM websites")
    db.execute("DROP TABLE websites")
    db.execute("ALTER TABLE websites_auth_new RENAME TO websites")
    for index_sql in indexes:
        db.execute(index_sql)
    db.execute("CREATE UNIQUE INDEX websites_owner_url_unique ON websites(owner_id, url)")


def migrate_auth_schema(database_path):
    """Apply additive auth schema versions atomically while retaining legacy IDs."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        version_one = db.execute("SELECT 1 FROM schema_migrations WHERE version = 1").fetchone()
        statements = (
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL,
                email_normalized TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('owner', 'member')),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS auth_sessions_expiry ON auth_sessions(expires_at)",
            """CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                email_normalized TEXT NOT NULL,
                created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                used_by INTEGER REFERENCES users(id) ON DELETE SET NULL
            )""",
            """CREATE TABLE IF NOT EXISTS login_rate_limits (
                key_hash TEXT PRIMARY KEY,
                attempts INTEGER NOT NULL,
                window_started_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS user_settings (
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                PRIMARY KEY(owner_id, setting_key)
            )""",
        )
        if not version_one:
            for statement in statements:
                db.execute(statement)
            _rebuild_websites_for_owner(path, db)
            for table in ("notifications", "check_jobs", "alert_jobs", "connectivity_incident_sites"):
                columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
                if "owner_id" not in columns:
                    db.execute(f'ALTER TABLE "{table}" ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL')
            db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)", (_iso(_now()),))
        if not db.execute("SELECT 1 FROM schema_migrations WHERE version = 2").fetchone():
            columns = {row[1] for row in db.execute('PRAGMA table_info("connectivity_incidents")')}
            if "owner_id" not in columns:
                db.execute("ALTER TABLE connectivity_incidents ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
            index = db.execute("SELECT tbl_name, sql FROM sqlite_master WHERE type='index' AND name='connectivity_incidents_owner_recent'").fetchone()
            if index and (index["tbl_name"] != "connectivity_incidents" or
                          "owner_id" not in (index["sql"] or "").lower()):
                raise RuntimeError("The connectivity incident owner index name is already used by an unexpected index.")
            db.execute("CREATE INDEX IF NOT EXISTS connectivity_incidents_owner_recent ON connectivity_incidents(owner_id, id DESC)")
            db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (2, ?)", (_iso(_now()),))
        if not db.execute("SELECT 1 FROM schema_migrations WHERE version = 3").fetchone():
            db.execute(
                "CREATE TABLE IF NOT EXISTS guest_accounts ("
                "user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, "
                "expires_at TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS guest_accounts_expiry ON guest_accounts(expires_at)")
            db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (3, ?)", (_iso(_now()),))
        violations = db.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("Authentication migration produced foreign-key violations.")
        db.commit()
        db.execute("PRAGMA foreign_keys = ON")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
