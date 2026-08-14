"""Ko‘prik MVP funksiyalarini server tomonda boshqarish qoidalari."""

from __future__ import annotations

import os
import time

from runtime_config import env_flag


FEATURE_ENV_NAMES = {
    "listings": "MVP_LISTINGS_ENABLED",
    "stories": "MVP_STORIES_ENABLED",
    "chat": "MVP_CHAT_ENABLED",
    "systemization": "MVP_SYSTEMIZATION_ENABLED",
}

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


def feature_snapshot(conn, environ=None):
    env = os.environ if environ is None else environ
    values = {
        code: env_flag(env_name, False, env)
        for code, env_name in FEATURE_ENV_NAMES.items()
    }
    rows = conn.execute(
        "SELECT feature_code, enabled FROM platform_feature_flags"
    ).fetchall()
    for row in rows:
        if row["feature_code"] in values:
            values[row["feature_code"]] = bool(row["enabled"])
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
    return None
