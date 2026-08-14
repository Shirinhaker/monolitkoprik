"""Ko'prik admin paneli uchun alohida Telegram challenge va sessiya domeni."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time


CHALLENGE_TTL_SECONDS = 5 * 60
MAX_CHALLENGE_ATTEMPTS = 5
SESSION_TTL_SECONDS = 8 * 60 * 60
SESSION_IDLE_SECONDS = 30 * 60


def admin_ids(environ=None):
    env = os.environ if environ is None else environ
    result = set()
    for raw in str(env.get("ADMIN_TG_IDS", "") or "").split(","):
        try:
            value = int(raw.strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            result.add(value)
    return result


def is_admin_tg_id(tg_id, environ=None):
    try:
        value = int(tg_id)
    except (TypeError, ValueError):
        return False
    return value in admin_ids(environ)


def ensure_admin_auth_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS admin_auth_challenges(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tg_id INTEGER NOT NULL,
          code_hash TEXT NOT NULL,
          expires_at INTEGER NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          consumed_at INTEGER NOT NULL DEFAULT 0,
          created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_auth_challenges_tg
          ON admin_auth_challenges(tg_id,created_at DESC);

        CREATE TABLE IF NOT EXISTS admin_sessions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tg_id INTEGER NOT NULL,
          token_hash TEXT NOT NULL UNIQUE,
          created_at INTEGER NOT NULL,
          last_used_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL,
          revoked_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_admin_sessions_active
          ON admin_sessions(tg_id,revoked_at,expires_at);
        """
    )


def _digest(secret, purpose, value):
    return hmac.new(
        str(secret).encode(),
        (str(purpose) + ":" + str(value)).encode(),
        hashlib.sha256,
    ).hexdigest()


def _challenge_code_hash(secret, tg_id, code):
    return _digest(secret, "admin-code", str(int(tg_id)) + ":" + str(code))


def _session_token_hash(token):
    # Sessiya tokeni 384 bit tasodifiy qiymat; DB sizib chiqqanda raw cookie
    # tiklanmasligi uchun bazada faqat bir tomonlama SHA-256 xeshi turadi.
    return hashlib.sha256(str(token).encode()).hexdigest()


def _now(value):
    return int(time.time() if value is None else value)


def start_admin_challenge(
    conn,
    tg_id,
    secret,
    fixed_code="",
    now=None,
):
    current = _now(now)
    telegram_id = int(tg_id)
    if telegram_id <= 0:
        raise ValueError("Telegram ID noto'g'ri.")
    code = str(fixed_code or "")
    if not code:
        code = "".join(secrets.choice("0123456789") for _ in range(6))
    if len(code) != 6 or not code.isdigit():
        raise ValueError("Admin tasdiqlash kodi 6 xonali bo'lishi kerak.")
    cursor = conn.execute(
        """
        INSERT INTO admin_auth_challenges(
          tg_id,code_hash,expires_at,attempts,consumed_at,created_at
        ) VALUES(?,?,?,0,0,?)
        """,
        (
            telegram_id,
            _challenge_code_hash(secret, telegram_id, code),
            current + CHALLENGE_TTL_SECONDS,
            current,
        ),
    )
    conn.commit()
    return {
        "id": int(cursor.lastrowid),
        "tg_id": telegram_id,
        "code": code,
        "expires_at": current + CHALLENGE_TTL_SECONDS,
    }


def _challenge_error(conn, message):
    conn.rollback()
    raise ValueError(message)


def verify_admin_challenge(
    conn,
    challenge_id,
    code,
    secret,
    now=None,
):
    current = _now(now)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT * FROM admin_auth_challenges WHERE id=?",
        (int(challenge_id),),
    ).fetchone()
    if not row:
        _challenge_error(conn, "Admin tasdiqlash so'rovi topilmadi.")
    if int(row["consumed_at"] or 0):
        _challenge_error(conn, "Admin tasdiqlash kodi allaqachon ishlatilgan.")
    if int(row["expires_at"]) < current:
        _challenge_error(conn, "Admin tasdiqlash kodi muddati tugagan.")
    if int(row["attempts"] or 0) >= MAX_CHALLENGE_ATTEMPTS:
        _challenge_error(conn, "Admin tasdiqlash urinishlari tugagan.")
    candidate = str(code or "")
    expected = _challenge_code_hash(secret, row["tg_id"], candidate)
    if not hmac.compare_digest(expected, row["code_hash"]):
        conn.execute(
            "UPDATE admin_auth_challenges SET attempts=attempts+1 WHERE id=?",
            (row["id"],),
        )
        conn.commit()
        raise ValueError("Admin tasdiqlash kodi noto'g'ri.")

    raw_token = secrets.token_urlsafe(48)
    conn.execute(
        "UPDATE admin_auth_challenges SET consumed_at=? WHERE id=?",
        (current, row["id"]),
    )
    conn.execute(
        """
        INSERT INTO admin_sessions(
          tg_id,token_hash,created_at,last_used_at,expires_at,revoked_at
        ) VALUES(?,?,?,?,?,0)
        """,
        (
            row["tg_id"],
            _session_token_hash(raw_token),
            current,
            current,
            current + SESSION_TTL_SECONDS,
        ),
    )
    conn.commit()
    return raw_token


def admin_session(conn, raw_token, now=None):
    token = str(raw_token or "").strip()
    if not token:
        return None
    current = _now(now)
    row = conn.execute(
        """
        SELECT * FROM admin_sessions
        WHERE token_hash=? AND revoked_at=0
        """,
        (_session_token_hash(token),),
    ).fetchone()
    if not row:
        return None
    if (
        int(row["expires_at"]) < current
        or int(row["last_used_at"]) + SESSION_IDLE_SECONDS < current
    ):
        conn.execute(
            "UPDATE admin_sessions SET revoked_at=? WHERE id=? AND revoked_at=0",
            (current, row["id"]),
        )
        conn.commit()
        return None
    conn.execute(
        "UPDATE admin_sessions SET last_used_at=? WHERE id=?",
        (current, row["id"]),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM admin_sessions WHERE id=?",
        (row["id"],),
    ).fetchone()


def revoke_admin_session(conn, raw_token, now=None):
    token = str(raw_token or "").strip()
    if not token:
        return
    conn.execute(
        """
        UPDATE admin_sessions SET revoked_at=?
        WHERE token_hash=? AND revoked_at=0
        """,
        (_now(now), _session_token_hash(token)),
    )
    conn.commit()
