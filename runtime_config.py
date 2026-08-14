"""Ko‘prik serverining xavfsiz ishga tushish sozlamalari.

Development/test rejimi avvalgidek yengil ishlaydi. ``APP_ENV=production``
bo‘lganda esa server faqat kerakli sirlar, HTTPS manzil va doimiy disk yo‘llari
aniq berilgan bo‘lsa ishga tushadi. Bu noto‘g‘ri deploy sabab ma’lumot yoki media
yo‘qolib qolishini va test sozlamalari ommaga chiqib ketishini oldini oladi.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse


TRUE_VALUES = {"1", "true", "yes", "on"}
INSECURE_SECRET_VALUES = {
    "",
    "platforma-webhook-secret",
    "changeme",
    "change-me",
    "secret",
}


def env_flag(name: str, default: bool = False, environ=None) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in TRUE_VALUES


def is_production(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get("APP_ENV", "development")).strip().lower() == "production"


def _is_strong_secret(value: str) -> bool:
    secret = str(value or "").strip()
    upper = secret.upper()
    return (
        len(secret) >= 32
        and secret.lower() not in INSECURE_SECRET_VALUES
        and not upper.startswith(("YOUR_", "GENERATE_", "CHANGE_"))
    )


def _path_is_inside(path_value: str, root_value: str) -> bool:
    try:
        path = Path(path_value).expanduser().resolve(strict=False)
        root = Path(root_value).expanduser().resolve(strict=False)
        return path != root and root in path.parents
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def validate_runtime_config(
    *,
    db_path: str,
    upload_dir: str,
    backup_dir: str,
    environ=None,
) -> None:
    """Production sozlamalarini tekshiradi va xatoda serverni to‘xtatadi."""

    env = os.environ if environ is None else environ
    if not is_production(env):
        return

    errors = []
    base_url = str(env.get("BASE_URL", "")).strip().rstrip("/")
    parsed_url = urlparse(base_url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.netloc
        or (parsed_url.hostname or "").lower().endswith(".example")
        or "YOUR-" in base_url.upper()
    ):
        errors.append("BASE_URL to‘liq HTTPS manzil bo‘lishi kerak")

    bot_token = str(env.get("BOT_TOKEN", "")).strip()
    if not re.fullmatch(r"[0-9]{5,}:[A-Za-z0-9_-]{20,}", bot_token):
        errors.append("BOT_TOKEN production uchun kiritilishi kerak")

    webhook_secret = str(env.get("WEBHOOK_SECRET", "")).strip()
    mobile_secret = str(env.get("MOBILE_OTP_SECRET", "")).strip()
    payment_secret = str(env.get("PAYMENT_TOKEN_SECRET", "")).strip()
    audit_ip_secret = str(env.get("ADMIN_AUDIT_IP_SECRET", "")).strip()
    if not _is_strong_secret(webhook_secret):
        errors.append("WEBHOOK_SECRET kamida 32 belgili tasodifiy sir bo‘lishi kerak")
    if not _is_strong_secret(mobile_secret):
        errors.append("MOBILE_OTP_SECRET kamida 32 belgili alohida sir bo‘lishi kerak")
    if webhook_secret and webhook_secret == mobile_secret:
        errors.append("WEBHOOK_SECRET va MOBILE_OTP_SECRET bir xil bo‘lmasligi kerak")
    if len(payment_secret) < 48 or not _is_strong_secret(payment_secret):
        errors.append(
            "PAYMENT_TOKEN_SECRET kamida 48 belgili tasodifiy sir bo‘lishi kerak"
        )
    if payment_secret and payment_secret in (webhook_secret, mobile_secret):
        errors.append(
            "PAYMENT_TOKEN_SECRET boshqa server sirlaridan alohida bo‘lishi kerak"
        )
    if not _is_strong_secret(audit_ip_secret):
        errors.append(
            "ADMIN_AUDIT_IP_SECRET kamida 32 belgili alohida sir bo‘lishi kerak"
        )
    if audit_ip_secret and audit_ip_secret in (
        webhook_secret,
        mobile_secret,
        payment_secret,
    ):
        errors.append(
            "ADMIN_AUDIT_IP_SECRET boshqa server sirlaridan alohida bo‘lishi kerak"
        )

    if env_flag("TEST_MODE", environ=env):
        errors.append("production rejimida TEST_MODE yoqilmasligi kerak")
    if str(env.get("TEST_OTP_CODE", "")).strip():
        errors.append("production rejimida TEST_OTP_CODE bo‘lmasligi kerak")
    for flag_name in (
        "MVP_LISTINGS_ENABLED",
        "MVP_STORIES_ENABLED",
        "MVP_CHAT_ENABLED",
        "MVP_SYSTEMIZATION_ENABLED",
    ):
        if str(env.get(flag_name, "")).strip() != "0":
            errors.append(f"{flag_name}=0 bo‘lishi kerak")

    try:
        init_data_age = int(str(env.get("INIT_DATA_MAX_AGE_SEC", "86400")).strip())
    except (TypeError, ValueError):
        init_data_age = -1
    if init_data_age < 60 or init_data_age > 86400:
        errors.append("INIT_DATA_MAX_AGE_SEC 60–86400 soniya oralig‘ida bo‘lishi kerak")

    persistent_root = str(env.get("PERSISTENT_ROOT", "/data")).strip()
    root_path = Path(persistent_root).expanduser()
    if not root_path.is_absolute() or root_path == Path("/"):
        errors.append("PERSISTENT_ROOT mutlaq va alohida volume papkasi bo‘lishi kerak")
    else:
        for label, value in (
            ("DB_PATH", db_path),
            ("UPLOAD_DIR", upload_dir),
            ("BACKUP_DIR", backup_dir),
        ):
            if not Path(str(value or "")).expanduser().is_absolute():
                errors.append(f"{label} mutlaq yo‘l bo‘lishi kerak")
            elif not _path_is_inside(value, persistent_root):
                errors.append(f"{label} PERSISTENT_ROOT ichida bo‘lishi kerak")

    if Path(str(upload_dir)).resolve(strict=False) == Path(str(backup_dir)).resolve(strict=False):
        errors.append("UPLOAD_DIR va BACKUP_DIR alohida papkalar bo‘lishi kerak")

    admin_ids = str(env.get("ADMIN_TG_IDS", "")).strip()
    try:
        parsed_admin_ids = {
            int(part.strip())
            for part in admin_ids.split(",")
            if part.strip()
        }
    except ValueError:
        parsed_admin_ids = set()
    if not parsed_admin_ids or any(value <= 0 for value in parsed_admin_ids):
        errors.append(
            "ADMIN_TG_IDS production uchun musbat Telegram IDlardan "
            "iborat bo‘lishi kerak"
        )

    receipt_dir = str(env.get("PAYMENT_RECEIPT_DIR", "")).strip()
    receipt_path = Path(receipt_dir).expanduser()
    if (
        not receipt_dir
        or not receipt_path.is_absolute()
        or not _path_is_inside(receipt_dir, persistent_root)
    ):
        errors.append(
            "PAYMENT_RECEIPT_DIR PERSISTENT_ROOT ichidagi mutlaq private "
            "papka bo‘lishi kerak"
        )
    else:
        resolved_receipts = receipt_path.resolve(strict=False)
        public_paths = {
            Path(str(upload_dir)).expanduser().resolve(strict=False),
            Path(str(backup_dir)).expanduser().resolve(strict=False),
        }
        if resolved_receipts in public_paths:
            errors.append(
                "PAYMENT_RECEIPT_DIR UPLOAD_DIR va BACKUP_DIRdan alohida "
                "bo‘lishi kerak"
            )
        if "static" in {part.lower() for part in resolved_receipts.parts}:
            errors.append(
                "PAYMENT_RECEIPT_DIR static katalog ichida bo‘lmasligi kerak"
            )

    if env_flag("PROJECT_ACCESS_RESTRICTED", False, env) and not str(
        env.get("PRIVILEGED_TG_IDS", "")
    ).strip():
        errors.append("yopiq rejimda PRIVILEGED_TG_IDS aniq ko‘rsatilishi kerak")

    if errors:
        details = "\n - ".join(errors)
        raise RuntimeError("Production konfiguratsiyasi xavfsiz emas:\n - " + details)


def safe_runtime_summary(*, db_path: str, upload_dir: str, backup_enabled: bool, environ=None):
    """Sirlar va aniq server yo‘llarisiz diagnostik xulosa."""

    env = os.environ if environ is None else environ
    return {
        "environment": str(env.get("APP_ENV", "development")).strip().lower(),
        "production": is_production(env),
        "persistent_database": Path(str(db_path)).expanduser().is_absolute(),
        "persistent_uploads": Path(str(upload_dir)).expanduser().is_absolute(),
        "backup_on_start": bool(backup_enabled),
    }
