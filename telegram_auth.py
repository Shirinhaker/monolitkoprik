"""Telegram orqali sayt autentifikatsiyasi uchun bir martalik challenge'lar."""

import hashlib
import hmac
import secrets
import time


TELEGRAM_LINK_TTL = 10 * 60
TELEGRAM_CODE_TTL = 5 * 60
TELEGRAM_MAX_ATTEMPTS = 5
TELEGRAM_RESEND_AFTER = 60


class TelegramAuthError(ValueError):
    """Telegram autentifikatsiya qoidasi buzilganda qaytariladigan xato."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _now(value):
    return int(time.time()) if value is None else int(value)


def hash_start_token(token):
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def hash_telegram_code(challenge_id, tg_id, code, otp_secret):
    raw = (
        str(int(challenge_id))
        + ":"
        + str(int(tg_id))
        + ":"
        + str(code or "")
        + ":"
        + str(otp_secret or "")
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def create_start_challenge(
    conn,
    purpose,
    user_id=0,
    pending_registration_id=0,
    now=None,
):
    purpose = str(purpose or "").strip().lower()
    if purpose not in ("register", "login"):
        raise TelegramAuthError("purpose_invalid", "Tasdiqlash maqsadi noto‘g‘ri.")
    user_id = int(user_id or 0)
    pending_registration_id = int(pending_registration_id or 0)
    if purpose == "register" and pending_registration_id <= 0:
        raise TelegramAuthError(
            "pending_registration_required",
            "Ro‘yxatdan o‘tish yozuvi topilmadi.",
        )
    if purpose == "login" and user_id <= 0:
        raise TelegramAuthError("user_required", "Foydalanuvchi topilmadi.")

    created_at = _now(now)
    if purpose == "register":
        conn.execute(
            """UPDATE telegram_auth_challenges SET invalidated_at=?
               WHERE purpose='register' AND pending_registration_id=?
                 AND verified_at=0 AND invalidated_at=0""",
            (created_at, pending_registration_id),
        )
    else:
        conn.execute(
            """UPDATE telegram_auth_challenges SET invalidated_at=?
               WHERE purpose='login' AND user_id=?
                 AND verified_at=0 AND invalidated_at=0""",
            (created_at, user_id),
        )

    start_token = secrets.token_urlsafe(24)
    cur = conn.execute(
        """INSERT INTO telegram_auth_challenges(
               purpose,user_id,pending_registration_id,start_token_hash,
               tg_id,code_hash,attempts,max_attempts,created_at,start_expires_at,
               code_sent_at,code_expires_at,verified_at,invalidated_at
           ) VALUES(?,?,?,?,0,'',0,?,?,?,0,0,0,0)""",
        (
            purpose,
            user_id,
            pending_registration_id,
            hash_start_token(start_token),
            TELEGRAM_MAX_ATTEMPTS,
            created_at,
            created_at + TELEGRAM_LINK_TTL,
        ),
    )
    conn.commit()
    return {
        "id": cur.lastrowid,
        "purpose": purpose,
        "start_token": start_token,
        "expires_at": created_at + TELEGRAM_LINK_TTL,
    }


def activate_start_challenge(
    conn,
    start_token,
    tg_id,
    otp_secret,
    fixed_code="",
    now=None,
):
    current = _now(now)
    token_hash = hash_start_token(start_token)
    row = conn.execute(
        "SELECT * FROM telegram_auth_challenges WHERE start_token_hash=?",
        (token_hash,),
    ).fetchone()
    if not row:
        raise TelegramAuthError("start_token_invalid", "Havola noto‘g‘ri.")
    if int(row["invalidated_at"] or 0):
        raise TelegramAuthError("start_token_invalid", "Havola bekor qilingan.")
    if int(row["verified_at"] or 0):
        raise TelegramAuthError("start_token_used", "Havola ishlatilgan.")
    if int(row["code_sent_at"] or 0):
        raise TelegramAuthError("start_token_used", "Havola ishlatilgan.")
    if int(row["start_expires_at"] or 0) <= current:
        raise TelegramAuthError("start_token_expired", "Havola muddati tugagan.")

    tg_id = int(tg_id or 0)
    if tg_id <= 0:
        raise TelegramAuthError(
            "telegram_user_invalid",
            "Telegram foydalanuvchisi aniqlanmadi.",
        )
    code = str(fixed_code or "")
    if len(code) != 6 or not code.isdigit():
        code = "".join(secrets.choice("0123456789") for _ in range(6))
    code_hash = hash_telegram_code(row["id"], tg_id, code, otp_secret)
    conn.execute(
        """UPDATE telegram_auth_challenges
           SET tg_id=?,code_hash=?,attempts=0,code_sent_at=?,code_expires_at=?
           WHERE id=?""",
        (tg_id, code_hash, current, current + TELEGRAM_CODE_TTL, row["id"]),
    )
    conn.commit()
    return {
        "id": row["id"],
        "purpose": row["purpose"],
        "user_id": int(row["user_id"] or 0),
        "pending_registration_id": int(row["pending_registration_id"] or 0),
        "tg_id": tg_id,
        "code": code,
        "expires_at": current + TELEGRAM_CODE_TTL,
    }


def verify_telegram_code(conn, challenge_id, code, otp_secret, now=None):
    current = _now(now)
    try:
        challenge_id = int(challenge_id)
    except (TypeError, ValueError):
        challenge_id = 0
    row = conn.execute(
        "SELECT * FROM telegram_auth_challenges WHERE id=?",
        (challenge_id,),
    ).fetchone()
    if not row or int(row["invalidated_at"] or 0):
        raise TelegramAuthError(
            "challenge_invalid",
            "Tasdiqlash so‘rovi topilmadi.",
        )
    if int(row["verified_at"] or 0):
        raise TelegramAuthError("code_used", "Tasdiqlash kodi ishlatilgan.")
    if not int(row["code_sent_at"] or 0):
        raise TelegramAuthError(
            "code_not_sent",
            "Kod hali Telegram bot orqali olinmagan.",
        )
    if int(row["code_expires_at"] or 0) <= current:
        raise TelegramAuthError("code_expired", "Tasdiqlash kodi muddati tugagan.")
    if int(row["attempts"] or 0) >= int(
        row["max_attempts"] or TELEGRAM_MAX_ATTEMPTS
    ):
        raise TelegramAuthError(
            "attempts_exhausted",
            "Kod kiritish urinishlari tugagan.",
        )

    supplied_hash = hash_telegram_code(
        row["id"],
        row["tg_id"],
        "".join(ch for ch in str(code or "") if ch.isdigit()),
        otp_secret,
    )
    if not hmac.compare_digest(str(row["code_hash"]), supplied_hash):
        conn.execute(
            "UPDATE telegram_auth_challenges SET attempts=attempts+1 WHERE id=?",
            (row["id"],),
        )
        conn.commit()
        raise TelegramAuthError("code_invalid", "Tasdiqlash kodi noto‘g‘ri.")

    conn.execute(
        "UPDATE telegram_auth_challenges SET verified_at=? WHERE id=?",
        (current, row["id"]),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM telegram_auth_challenges WHERE id=?",
        (row["id"],),
    ).fetchone()
