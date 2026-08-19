"""v1656 monolit ilovasining ochilgan bo‘limlar bilan production entrypointi.

Asosiy ``main.py`` etaloniga tegmasdan, faqat build/readiness diagnostikasini
haqiqiy feature flag holatiga moslaydi. Frontend va barcha mavjud API oqimlari
``main.app`` ichida o‘z holicha qoladi.
"""

from __future__ import annotations

import os

import database as database_module
import main as legacy
from fastapi.responses import JSONResponse

from feature_flags import FEATURE_ENV_NAMES, feature_env_snapshot, feature_snapshot


app = legacy.app


def _remove_existing_route(path: str) -> None:
    """Eski qattiq yopiq diagnostika route'ini overlay bilan almashtiradi."""

    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) != path
    ]


def _runtime_feature_snapshot():
    """DB mavjud bo‘lsa override'li, aks holda environment holatini qaytaradi."""

    values = feature_env_snapshot()
    db_path = str(database_module.DB_PATH or "").strip()
    if not db_path or (db_path != ":memory:" and not os.path.isfile(db_path)):
        return values

    conn = None
    try:
        conn = legacy.db()
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='platform_feature_flags'"
        ).fetchone()
        if table_exists:
            values = feature_snapshot(conn)
    except Exception:
        # Diagnostika endpointi vaqtinchalik DB o‘qish xatosida ham serverni
        # yiqitmaydi; environment holati qaytariladi.
        pass
    finally:
        if conn is not None:
            conn.close()
    return values


_remove_existing_route("/api/build")
_remove_existing_route("/readyz")


@app.get("/api/build")
async def unlocked_app_build():
    """Eski build metadatasiga haqiqiy ochiq bo‘limlar holatini qo‘shadi."""

    payload = await legacy.app_build()
    features = _runtime_feature_snapshot()
    payload.update(
        {
            "mvp_sections_unlocked_v1656": True,
            "stories_enabled": bool(features.get("stories")),
            "listings_enabled": bool(features.get("listings")),
            "general_chat_enabled": bool(features.get("chat")),
            "systemization_enabled": bool(features.get("systemization")),
            "taxi_call_enabled": bool(features.get("taxi")),
        }
    )
    return payload


@app.get("/readyz", include_in_schema=False)
async def unlocked_readiness_check():
    """Baza, storage, admin assetlar va feature konfiguratsiyasini tekshiradi."""

    database_ready = False
    database_integrity = False
    features = {}
    conn = None
    try:
        conn = legacy.db()
        conn.execute("SELECT 1").fetchone()
        database_ready = True
        database_integrity = legacy._cached_database_quick_check(conn)
        features = feature_snapshot(conn)
    except Exception:
        database_ready = False
        database_integrity = False
    finally:
        if conn is not None:
            conn.close()

    uploads_ready = os.path.isdir(legacy.UPLOAD_DIR) and os.access(
        legacy.UPLOAD_DIR, os.W_OK
    )
    receipts_path = os.path.abspath(legacy.PAYMENT_RECEIPT_DIR)
    uploads_path = os.path.abspath(legacy.UPLOAD_DIR)
    static_path = os.path.abspath("static")
    payment_receipts_ready = (
        os.path.isdir(legacy.PAYMENT_RECEIPT_DIR)
        and os.access(legacy.PAYMENT_RECEIPT_DIR, os.W_OK)
        and receipts_path != uploads_path
        and not receipts_path.startswith(uploads_path + os.sep)
        and receipts_path != static_path
        and not receipts_path.startswith(static_path + os.sep)
    )
    admin_assets_ready = all(
        os.path.isfile(os.path.join(legacy.ADMIN_DIR, name))
        for name in ("index.html", "styles.css", "app.js")
    )
    expected_codes = set(FEATURE_ENV_NAMES)
    features_ready = (
        set(features) == expected_codes
        and all(type(features[code]) is bool for code in expected_codes)
    )

    payload = {
        "ok": all(
            (
                database_ready,
                database_integrity,
                uploads_ready,
                payment_receipts_ready,
                admin_assets_ready,
                features_ready,
            )
        ),
        "build": legacy.APP_BUILD,
        "database": database_ready,
        "database_integrity": database_integrity,
        "uploads": uploads_ready,
        "payment_receipts": payment_receipts_ready,
        "admin_assets": admin_assets_ready,
        "features": features,
    }
    if not payload["ok"]:
        return JSONResponse(status_code=503, content=payload)
    return payload


def _prioritize_overlay_routes() -> None:
    """Overlay endpointlarini yakuniy ``StaticFiles('/')`` mountidan oldinga qo‘yadi."""

    overlay_paths = {"/api/build", "/readyz"}
    overlay_routes = [
        route for route in app.router.routes
        if getattr(route, "path", None) in overlay_paths
    ]
    remaining = [
        route for route in app.router.routes
        if getattr(route, "path", None) not in overlay_paths
    ]
    static_index = next(
        (
            index
            for index, route in enumerate(remaining)
            if route.__class__.__name__ == "Mount"
            and getattr(route, "path", None) == ""
        ),
        len(remaining),
    )
    app.router.routes[:] = (
        remaining[:static_index] + overlay_routes + remaining[static_index:]
    )


_prioritize_overlay_routes()
