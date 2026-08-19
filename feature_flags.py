"""Ko‘prik funksiyalarini server tomonda xavfsiz boshqarish qoidalari."""

from __future__ import annotations

import os
import time

from runtime_config import env_flag


FEATURE_ENV_NAMES = {
    "listings": "MVP_LISTINGS_ENABLED",
    "stories": "MVP_STORIES_ENABLED",
    "chat": "MVP_CHAT_ENABLED",
    "systemization": "MVP_SYSTEMIZATION_ENABLED",
    "taxi": "MVP_TAXI_ENABLED",
}

# Ushbu v1656 paketida tayyor bo‘limlar odatda ochiq. Railway Variables orqali
# istalgan bo‘limni 0 qilib vaqtincha qayta yopish mumkin.
UNLOCK_COMPLETED_SECTIONS_ENV = "MVP_UNLOCK_COMPLETED_SECTIONS"


FEATURE_DEFAULTS = {
    "listings": True,
    "stories": True,
    "chat": True,
    "systemization": True,
    "taxi": True,
}

# Eski MVP migratsiyasi aynan shu to‘rtta bo‘limni updated_by_tg_id=0 bilan
# majburan yopgan. Yangi paket faqat o‘sha texnik qulf yozuvlarini tozalaydi;
# haqiqiy admin qo‘ygan override saqlanib qoladi.
LEGACY_LOCK_FEATURE_CODES = (
    "listings",
    "stories",
    "chat",
    "systemization",
)

SYSTEMIZATION_PREFIXES = (
    "/api/stock",
    "/api/stats",
    "/api/expense",
    "/api/kassa",
    "/api/sales",
    "/api/qarz",
    "/api/staff",
    "/api/tabel",
    "/api/business/credentials",
    "/api/contractors",
    "/api/documents",
    "/api/education",
    "/api/ai",
)


def ensure_feature_flag_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_feature_flags(
          feature_code TEXT PRIMARY KEY,
          enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
          updated_by_tg_id INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        )
        """
    )


def feature_env_snapshot(environ=None):
    """Railway/environment bo‘yicha boshlang‘ich feature holatini qaytaradi.

    Eski v1656 deployida to‘rtta ``MVP_*`` qiymatni ``0`` qilish production
    validator talabi edi. Yangilanishdan keyin o‘sha legacy qiymatlar bo‘limlarni
    qayta yopib qo‘ymasligi uchun master unlock default holatda yoqilgan.
    Per-section flaglarni qayta boshqarish kerak bo‘lsa
    ``MVP_UNLOCK_COMPLETED_SECTIONS=0`` qilinadi.
    """

    env = os.environ if environ is None else environ
    if env_flag(UNLOCK_COMPLETED_SECTIONS_ENV, True, env):
        return dict(FEATURE_DEFAULTS)
    return {
        code: env_flag(env_name, FEATURE_DEFAULTS[code], env)
        for code, env_name in FEATURE_ENV_NAMES.items()
    }


def feature_snapshot(conn, environ=None):
    """Environment qiymatlari ustiga haqiqiy DB override'larini qo‘llaydi."""

    values = feature_env_snapshot(environ)
    rows = conn.execute(
        "SELECT feature_code, enabled, updated_by_tg_id "
        "FROM platform_feature_flags"
    ).fetchall()
    for row in rows:
        code = row["feature_code"]
        if code not in values:
            continue
        # Eski MVP migratsiyasining texnik ``0`` yozuvi environmentdagi yangi
        # ochiq holatni bosib ketmasin. Admin qo‘ygan haqiqiy override saqlanadi.
        if (
            code in LEGACY_LOCK_FEATURE_CODES
            and not bool(row["enabled"])
            and int(row["updated_by_tg_id"] or 0) == 0
        ):
            continue
        values[code] = bool(row["enabled"])
    return values


def feature_enabled(conn, code, environ=None):
    return bool(feature_snapshot(conn, environ).get(code, False))


def set_feature_override(
    conn,
    code,
    enabled,
    admin_tg_id,
    now=None,
):
    if code not in FEATURE_ENV_NAMES:
        raise ValueError("Noma’lum feature flag.")
    conn.execute(
        """
        INSERT INTO platform_feature_flags(
          feature_code, enabled, updated_by_tg_id, updated_at
        ) VALUES(?,?,?,?)
        ON CONFLICT(feature_code) DO UPDATE SET
          enabled=excluded.enabled,
          updated_by_tg_id=excluded.updated_by_tg_id,
          updated_at=excluded.updated_at
        """,
        (
            code,
            1 if enabled else 0,
            int(admin_tg_id),
            int(now or time.time()),
        ),
    )


def clear_legacy_mvp_lock_overrides(conn):
    """Eski release migratsiyasi yaratgan texnik ``0`` override'larini o‘chiradi.

    ``updated_by_tg_id=0`` va ``enabled=0`` sharti foydalanuvchi/admin qo‘ygan
    haqiqiy override'larni tegmasdan qoldiradi. Funksiya idempotent.
    """

    placeholders = ",".join("?" for _ in LEGACY_LOCK_FEATURE_CODES)
    cursor = conn.execute(
        f"""
        DELETE FROM platform_feature_flags
        WHERE feature_code IN ({placeholders})
          AND enabled=0
          AND updated_by_tg_id=0
        """,
        LEGACY_LOCK_FEATURE_CODES,
    )
    return max(0, int(cursor.rowcount or 0))


def _matches_prefix(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


def guarded_feature_for_path(path):
    value = str(path or "")
    if (
        _matches_prefix(value, "/api/stories")
        or _matches_prefix(value, "/story-media")
        or _matches_prefix(value, "/story-thumbnail")
    ):
        return "stories"
    if _matches_prefix(value, "/api/listings"):
        return "listings"
    if _matches_prefix(value, "/api/messages"):
        return "chat"
    if _matches_prefix(value, "/api/staff-auth"):
        return "systemization"
    if any(_matches_prefix(value, prefix) for prefix in SYSTEMIZATION_PREFIXES):
        return "systemization"

    # Taxi va dostavka bir xil /api/driver hamda /api/rides oqimlaridan
    # foydalanadi. Shu sabab bu umumiy yo‘llarni taxi flagi bilan to‘sish
    # ishlayotgan dostavka zanjirini ham buzadi. Taxi kirish nuqtalari frontend
    # data-feature="taxi" orqali boshqariladi; mavjud dostavka API'lari saqlanadi.
    return None
