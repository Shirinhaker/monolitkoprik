"""Ko'prik biznes tariflari va obuna muddatlari uchun yagona domen qoidalari."""

import calendar
import time
from datetime import datetime, timezone


PAID_DURATIONS = (1, 3, 12)

PLAN_FEATURES = {
    "free": {
        "unlimited_items": True,
        "home_nearby_eligible": False,
        "map_marker_eligible": False,
    },
    "plus": {
        "unlimited_items": True,
        "home_nearby_eligible": True,
        "map_marker_eligible": False,
    },
    "pro": {
        "unlimited_items": True,
        "home_nearby_eligible": True,
        "map_marker_eligible": True,
    },
}

PLAN_CATALOG = (
    {
        "code": "free",
        "name": "Bepul",
        "summary": "Biznesni onlayn ko'rsatish uchun asosiy imkoniyatlar",
        "benefits": (
            "Biznes profilidan foydalanish",
            "Mahsulot va xizmatlarni cheksiz joylash",
        ),
    },
    {
        "code": "plus",
        "name": "Plus",
        "summary": "Mahsulot va xizmatlarni yaqin mijozlarga ko'rsatish",
        "benefits": (
            "Bepul tarifdagi barcha imkoniyatlar",
            "Sizga yaqin bo'limiga chiqish huquqi",
        ),
    },
    {
        "code": "pro",
        "name": "Pro",
        "summary": "Hudud bo'yicha kengroq ko'rinish",
        "benefits": (
            "Plus tarifdagi barcha imkoniyatlar",
            "Biznes metkasini xaritada ko'rsatish huquqi",
        ),
    },
)


class SubscriptionValidationError(ValueError):
    """Tarif yoki muddat qiymati kelishilgan qoidalarga mos emas."""


def init_subscription_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS business_subscriptions("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "business_id INTEGER NOT NULL,"
        "plan_code TEXT NOT NULL CHECK(plan_code IN ('free','plus','pro')),"
        "duration_months INTEGER NOT NULL DEFAULT 0,"
        "starts_at INTEGER NOT NULL,"
        "expires_at INTEGER NOT NULL DEFAULT 0,"
        "status TEXT NOT NULL DEFAULT 'active' "
        "CHECK(status IN ('active','superseded','expired')),"
        "is_demo INTEGER NOT NULL DEFAULT 1,"
        "created_at INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_business_subscriptions_current "
        "ON business_subscriptions(business_id,status,expires_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_business_subscriptions_history "
        "ON business_subscriptions(business_id,id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_business_subscriptions_active "
        "ON business_subscriptions(business_id) WHERE status='active'"
    )
    columns = {
        row["name"] if hasattr(row, "keys") else row[1]
        for row in conn.execute("PRAGMA table_info(business_subscriptions)")
    }
    if "payment_request_id" not in columns:
        conn.execute(
            "ALTER TABLE business_subscriptions "
            "ADD COLUMN payment_request_id INTEGER"
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_business_subscriptions_payment_request "
        "ON business_subscriptions(payment_request_id) "
        "WHERE payment_request_id IS NOT NULL"
    )


def subscription_entitlements(plan_code):
    code = str(plan_code or "").strip().lower()
    if code not in PLAN_FEATURES:
        code = "free"
    return dict(PLAN_FEATURES[code])


def home_nearby_eligible_plan_codes():
    """Read-only policy source for batch home-nearby candidate queries."""
    return tuple(
        code
        for code, features in PLAN_FEATURES.items()
        if features.get("home_nearby_eligible") is True
    )


def _add_calendar_months(timestamp, months):
    current = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    month_index = current.month - 1 + int(months)
    year = current.year + month_index // 12
    month = month_index % 12 + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return int(current.replace(year=year, month=month, day=day).timestamp())


def _subscription_dict(row):
    if not row:
        return None
    data = dict(row)
    data["is_demo"] = bool(data.get("is_demo"))
    data["is_virtual"] = False
    return data


def _virtual_free():
    return {
        "id": None,
        "business_id": None,
        "plan_code": "free",
        "duration_months": 0,
        "starts_at": 0,
        "expires_at": 0,
        "status": "active",
        "is_demo": False,
        "created_at": 0,
        "is_virtual": True,
    }


def _active_subscription_row(conn, business_id, now):
    return conn.execute(
        "SELECT * FROM business_subscriptions "
        "WHERE business_id=? AND status='active' "
        "AND (plan_code='free' OR expires_at=0 OR expires_at>?) "
        "ORDER BY id DESC LIMIT 1",
        (int(business_id), now),
    ).fetchone()


def _has_expired_active_paid_subscription(conn, business_id, now):
    return conn.execute(
        "SELECT 1 FROM business_subscriptions "
        "WHERE business_id=? AND status='active' AND plan_code IN ('plus','pro') "
        "AND expires_at>0 AND expires_at<=? LIMIT 1",
        (int(business_id), now),
    ).fetchone() is not None


def _expire_active_paid_subscriptions(conn, business_id, now):
    """Commit expired-history cleanup without turning normal reads into writes."""
    if conn.in_transaction:
        conn.execute("SAVEPOINT subscription_expiry_cleanup")
        try:
            conn.execute(
                "UPDATE business_subscriptions SET status='expired' "
                "WHERE business_id=? AND status='active' AND plan_code IN ('plus','pro') "
                "AND expires_at>0 AND expires_at<=?",
                (int(business_id), now),
            )
            conn.execute("RELEASE SAVEPOINT subscription_expiry_cleanup")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT subscription_expiry_cleanup")
            conn.execute("RELEASE SAVEPOINT subscription_expiry_cleanup")
            raise
        return

    conn.execute("BEGIN")
    try:
        conn.execute(
            "UPDATE business_subscriptions SET status='expired' "
            "WHERE business_id=? AND status='active' AND plan_code IN ('plus','pro') "
            "AND expires_at>0 AND expires_at<=?",
            (int(business_id), now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def current_business_subscription(conn, business_id, now=None):
    now = int(time.time() if now is None else now)
    if _has_expired_active_paid_subscription(conn, business_id, now):
        _expire_active_paid_subscriptions(conn, business_id, now)
    row = _active_subscription_row(conn, business_id, now)
    return _subscription_dict(row) or _virtual_free()


def business_has_entitlement(conn, business_id, feature, now=None):
    """Keyingi bosh sahifa/xarita/istoriya ishlarida ishlatiladigan yagona tekshiruv."""
    current = current_business_subscription(conn, business_id, now=now)
    return bool(subscription_entitlements(current["plan_code"]).get(str(feature or ""), False))


def _validate_activation(plan_code, duration_months):
    code = str(plan_code or "").strip().lower()
    if code not in PLAN_FEATURES:
        raise SubscriptionValidationError("Tarif noto'g'ri tanlangan.")
    if isinstance(duration_months, bool) or not isinstance(duration_months, int):
        raise SubscriptionValidationError("Obuna muddati noto'g'ri.")
    duration = duration_months
    if code == "free" and duration != 0:
        raise SubscriptionValidationError("Bepul tarif muddatsiz tanlanadi.")
    if code != "free" and duration not in PAID_DURATIONS:
        raise SubscriptionValidationError("Plus va Pro uchun 1, 3 yoki 12 oy tanlang.")
    return code, duration


def activate_demo_subscription(conn, business_id, plan_code, duration_months, now=None):
    code, duration = _validate_activation(plan_code, duration_months)
    business_id = int(business_id)
    now = int(time.time() if now is None else now)
    if conn.in_transaction:
        raise RuntimeError("Subscription activation requires a connection outside a transaction.")

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE business_subscriptions SET status='expired' "
            "WHERE business_id=? AND status='active' AND plan_code IN ('plus','pro') "
            "AND expires_at>0 AND expires_at<=?",
            (business_id, now),
        )
        current = _subscription_dict(_active_subscription_row(conn, business_id, now))
        current = current or _virtual_free()

        if current["plan_code"] == code and not current["is_virtual"]:
            if code != "free":
                base = max(int(current["expires_at"]), now)
                conn.execute(
                    "UPDATE business_subscriptions SET duration_months=?,expires_at=? WHERE id=?",
                    (
                        int(current["duration_months"]) + duration,
                        _add_calendar_months(base, duration),
                        int(current["id"]),
                    ),
                )
        else:
            conn.execute(
                "UPDATE business_subscriptions SET status='superseded' "
                "WHERE business_id=? AND status='active'",
                (business_id,),
            )
            expires_at = 0 if code == "free" else _add_calendar_months(now, duration)
            conn.execute(
                "INSERT INTO business_subscriptions("
                "business_id,plan_code,duration_months,starts_at,expires_at,status,is_demo,created_at"
                ") VALUES(?,?,?,?,?,'active',1,?)",
                (business_id, code, duration, now, expires_at, now),
            )
        payload = subscription_payload(conn, business_id, now=now)
        conn.commit()
        return payload
    except Exception:
        conn.rollback()
        raise


def activate_paid_subscription(
    conn,
    business_id,
    plan_code,
    duration_months,
    payment_request_id,
    now=None,
):
    """Activate one paid plan inside the caller's review transaction."""
    code, duration = _validate_activation(plan_code, duration_months)
    if code == "free":
        raise SubscriptionValidationError("Bepul tarif to‘lov talab qilmaydi.")
    business_id = int(business_id)
    payment_request_id = int(payment_request_id)
    now = int(time.time() if now is None else now)
    if not conn.in_transaction:
        raise RuntimeError("Paid activation requires an active transaction.")
    existing = conn.execute(
        """
        SELECT * FROM business_subscriptions
        WHERE payment_request_id=?
        """,
        (payment_request_id,),
    ).fetchone()
    if existing:
        return _subscription_dict(existing)
    current = _subscription_dict(
        _active_subscription_row(conn, business_id, now)
    )
    base = now
    if current and current["plan_code"] == code:
        base = max(now, int(current["expires_at"] or 0))
    conn.execute(
        """
        UPDATE business_subscriptions SET status='superseded'
        WHERE business_id=? AND status='active'
        """,
        (business_id,),
    )
    expires_at = _add_calendar_months(base, duration)
    cursor = conn.execute(
        """
        INSERT INTO business_subscriptions(
          business_id,plan_code,duration_months,starts_at,expires_at,
          status,is_demo,created_at,payment_request_id
        ) VALUES(?,?,?,?,?,'active',0,?,?)
        """,
        (
            business_id,
            code,
            duration,
            now,
            expires_at,
            now,
            payment_request_id,
        ),
    )
    return _subscription_dict(
        conn.execute(
            "SELECT * FROM business_subscriptions WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
    )


def subscription_payload(conn, business_id, now=None):
    current = current_business_subscription(conn, business_id, now=now)
    rows = conn.execute(
        "SELECT * FROM business_subscriptions "
        "WHERE business_id=? AND status<>'active' ORDER BY id DESC",
        (int(business_id),),
    ).fetchall()
    plans = []
    for item in PLAN_CATALOG:
        plan = dict(item)
        plan["benefits"] = list(plan["benefits"])
        plan["features"] = subscription_entitlements(plan["code"])
        plans.append(plan)
    return {
        "current": current,
        "features": subscription_entitlements(current["plan_code"]),
        "history": [_subscription_dict(row) for row in rows],
        "plans": plans,
        "durations": list(PAID_DURATIONS),
        "demo_mode": True,
    }
