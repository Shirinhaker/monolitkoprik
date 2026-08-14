"""Manual payment requests and their strict review state machine."""

import json
import secrets
import time


PRICE_RULES = {
    "subscription_plus_1m": {
        "service_type": "subscription",
        "plan_code": "plus",
        "duration_months": 1,
        "amount_uzs": 99_000,
    },
    "subscription_plus_3m": {
        "service_type": "subscription",
        "plan_code": "plus",
        "duration_months": 3,
        "amount_uzs": 279_000,
    },
    "subscription_plus_12m": {
        "service_type": "subscription",
        "plan_code": "plus",
        "duration_months": 12,
        "amount_uzs": 990_000,
    },
    "subscription_pro_1m": {
        "service_type": "subscription",
        "plan_code": "pro",
        "duration_months": 1,
        "amount_uzs": 149_000,
    },
    "subscription_pro_3m": {
        "service_type": "subscription",
        "plan_code": "pro",
        "duration_months": 3,
        "amount_uzs": 419_000,
    },
    "subscription_pro_12m": {
        "service_type": "subscription",
        "plan_code": "pro",
        "duration_months": 12,
        "amount_uzs": 1_490_000,
    },
    "advertisement_district_day": {
        "service_type": "advertisement",
        "unit": "day",
        "amount_uzs": 50_000,
    },
    "advertisement_district_hour": {
        "service_type": "advertisement",
        "unit": "district_hour",
        "amount_uzs": 20_000,
    },
    "listing_publish": {
        "service_type": "listing",
        "amount_uzs": 10_000,
    },
}


class PaymentError(Exception):
    """Base payment-domain error."""


class PaymentValidationError(PaymentError):
    """The requested payment operation is invalid."""


class PaymentConflict(PaymentError):
    """The payment was already changed or a receipt is already in use."""


def _now(value=None):
    return int(time.time()) if value is None else int(value)


def _dict(row):
    return dict(row) if row is not None else None


def ensure_payment_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS platform_prices(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          price_code TEXT NOT NULL UNIQUE,
          amount_uzs INTEGER NOT NULL CHECK(amount_uzs >= 0),
          service_type TEXT NOT NULL DEFAULT '',
          config_json TEXT NOT NULL DEFAULT '{}',
          active INTEGER NOT NULL DEFAULT 1,
          updated_by_tg_id INTEGER,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payment_methods(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          method_type TEXT NOT NULL,
          name TEXT NOT NULL,
          details_json TEXT NOT NULL DEFAULT '{}',
          recipient_name TEXT NOT NULL DEFAULT '',
          instructions TEXT NOT NULL DEFAULT '',
          sort_order INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payment_requests(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          request_code TEXT NOT NULL UNIQUE,
          actor_type TEXT NOT NULL CHECK(actor_type IN ('user','business')),
          user_id INTEGER NOT NULL,
          business_id INTEGER,
          service_type TEXT NOT NULL
            CHECK(service_type IN ('advertisement','subscription','listing')),
          target_id INTEGER,
          plan_code TEXT NOT NULL DEFAULT '',
          duration_months INTEGER NOT NULL DEFAULT 0,
          quantity INTEGER NOT NULL DEFAULT 1,
          unit_price_snapshot INTEGER NOT NULL,
          amount_snapshot INTEGER NOT NULL,
          currency TEXT NOT NULL DEFAULT 'UZS',
          price_code TEXT NOT NULL DEFAULT '',
          target_snapshot_json TEXT NOT NULL DEFAULT '{}',
          payment_method_id INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','approved','rejected','cancelled')),
          approved_by_tg_id INTEGER,
          approved_at INTEGER NOT NULL DEFAULT 0,
          rejected_at INTEGER NOT NULL DEFAULT 0,
          cancelled_at INTEGER NOT NULL DEFAULT 0,
          public_reason TEXT NOT NULL DEFAULT '',
          internal_note TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          FOREIGN KEY(payment_method_id) REFERENCES payment_methods(id)
        );

        CREATE TABLE IF NOT EXISTS payment_attempts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          payment_request_id INTEGER NOT NULL,
          attempt_no INTEGER NOT NULL,
          receipt_filename TEXT NOT NULL,
          receipt_mime TEXT NOT NULL,
          receipt_sha256 TEXT NOT NULL,
          submitted_at INTEGER NOT NULL,
          reviewed_at INTEGER NOT NULL DEFAULT 0,
          review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(review_status IN ('pending','approved','rejected','superseded')),
          review_reason TEXT NOT NULL DEFAULT '',
          UNIQUE(payment_request_id, attempt_no),
          FOREIGN KEY(payment_request_id) REFERENCES payment_requests(id)
        );

        CREATE TABLE IF NOT EXISTS payment_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          payment_request_id INTEGER NOT NULL,
          from_status TEXT NOT NULL,
          to_status TEXT NOT NULL,
          actor_kind TEXT NOT NULL,
          actor_id TEXT NOT NULL,
          reason TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at INTEGER NOT NULL,
          FOREIGN KEY(payment_request_id) REFERENCES payment_requests(id)
        );

        CREATE INDEX IF NOT EXISTS idx_payment_requests_owner
          ON payment_requests(user_id,actor_type,business_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_payment_requests_status
          ON payment_requests(status,created_at,id);
        CREATE INDEX IF NOT EXISTS idx_payment_attempts_request
          ON payment_attempts(payment_request_id,attempt_no DESC);
        CREATE INDEX IF NOT EXISTS idx_payment_attempts_receipt_hash
          ON payment_attempts(receipt_sha256);
        CREATE INDEX IF NOT EXISTS idx_payment_events_request
          ON payment_events(payment_request_id,id);
        """
    )
    price_columns = {
        row["name"] if hasattr(row, "keys") else row[1]
        for row in conn.execute("PRAGMA table_info(platform_prices)")
    }
    if "service_type" not in price_columns:
        conn.execute(
            "ALTER TABLE platform_prices ADD COLUMN service_type TEXT NOT NULL DEFAULT ''"
        )
    if "config_json" not in price_columns:
        conn.execute(
            "ALTER TABLE platform_prices ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'"
        )
    payment_columns = {
        row["name"] if hasattr(row, "keys") else row[1]
        for row in conn.execute("PRAGMA table_info(payment_requests)")
    }
    if "target_snapshot_json" not in payment_columns:
        conn.execute(
            "ALTER TABLE payment_requests "
            "ADD COLUMN target_snapshot_json TEXT NOT NULL DEFAULT '{}'"
        )
    stamp = _now()
    conn.execute(
        """
        INSERT INTO payment_methods(
          id,method_type,name,details_json,recipient_name,instructions,
          sort_order,active,created_at,updated_at
        )
        SELECT 1,'manual_card','Bank kartasi','{}','','',0,1,?,?
        WHERE NOT EXISTS(SELECT 1 FROM payment_methods)
        """,
        (stamp, stamp),
    )
    conn.commit()
    ensure_default_prices(conn)


def ensure_default_prices(conn, now=None):
    stamp = _now(now)
    for price_code, rule in PRICE_RULES.items():
        config = {
            key: value
            for key, value in rule.items()
            if key not in ("amount_uzs", "service_type")
        }
        default_active = (
            0 if price_code == "advertisement_district_day" else 1
        )
        conn.execute(
            """
            INSERT INTO platform_prices(
              price_code,amount_uzs,service_type,config_json,active,
              created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(price_code) DO NOTHING
            """,
            (
                price_code,
                int(rule["amount_uzs"]),
                str(rule["service_type"]),
                json.dumps(config, ensure_ascii=False, sort_keys=True),
                default_active,
                stamp,
                stamp,
            ),
        )
    conn.execute(
        """
        UPDATE platform_prices
        SET active=0
        WHERE price_code='advertisement_district_day'
        """
    )
    conn.commit()


def active_payment_catalog(conn):
    prices = conn.execute(
        """
        SELECT id,price_code,amount_uzs,service_type,config_json
        FROM platform_prices WHERE active=1 ORDER BY id
        """
    ).fetchall()
    services = {
        "subscription": [],
        "advertisement": [],
        "listing": [],
    }
    for row in prices:
        data = _dict(row)
        rule = PRICE_RULES.get(data["price_code"])
        if not rule or rule["service_type"] != data["service_type"]:
            continue
        data["config"] = json.loads(data.pop("config_json") or "{}")
        services[data["service_type"]].append(data)
    methods = [
        {
            "id": int(row["id"]),
            "method_type": row["method_type"],
            "name": row["name"],
            "details": json.loads(row["details_json"] or "{}"),
            "recipient_name": row["recipient_name"],
            "instructions": row["instructions"],
        }
        for row in conn.execute(
            """
            SELECT * FROM payment_methods
            WHERE active=1 ORDER BY sort_order,id
            """
        ).fetchall()
    ]
    return {"services": services, "payment_methods": methods}


def _validate_owner(owner):
    owner = dict(owner or {})
    actor_type = str(owner.get("actor_type") or "")
    user_id = int(owner.get("user_id") or 0)
    business_id = owner.get("business_id")
    business_id = int(business_id) if business_id not in (None, "") else None
    if actor_type not in ("user", "business") or user_id <= 0:
        raise PaymentValidationError("To‘lov egasi noto‘g‘ri.")
    if actor_type == "business" and not business_id:
        raise PaymentValidationError("Biznes to‘lovi uchun biznes ID kerak.")
    return actor_type, user_id, business_id


def _validate_receipt(receipt):
    receipt = dict(receipt or {})
    filename = str(receipt.get("path") or "").strip()
    mime = str(receipt.get("mime") or "").strip().lower()
    digest = str(receipt.get("sha256") or "").strip().lower()
    if not filename or mime not in ("image/jpeg", "image/png", "image/webp"):
        raise PaymentValidationError("Kvitansiya formati noto‘g‘ri.")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise PaymentValidationError("Kvitansiya nazorat summasi noto‘g‘ri.")
    return filename, mime, digest


def _assert_receipt_available(conn, digest, exclude_payment_id=None):
    params = [digest]
    exclusion = ""
    if exclude_payment_id is not None:
        exclusion = " AND pr.id<>?"
        params.append(int(exclude_payment_id))
    row = conn.execute(
        """
        SELECT pa.id
        FROM payment_attempts pa
        JOIN payment_requests pr ON pr.id=pa.payment_request_id
        WHERE pa.receipt_sha256=?
          AND pr.status IN ('pending','approved')
        """
        + exclusion
        + " LIMIT 1",
        tuple(params),
    ).fetchone()
    if row:
        raise PaymentConflict("Bu kvitansiya boshqa to‘lovda ishlatilgan.")


def _event(
    conn,
    payment_id,
    from_status,
    to_status,
    actor_kind,
    actor_id,
    reason,
    now,
    metadata=None,
):
    conn.execute(
        """
        INSERT INTO payment_events(
          payment_request_id,from_status,to_status,actor_kind,actor_id,
          reason,metadata_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            int(payment_id),
            str(from_status),
            str(to_status),
            str(actor_kind),
            str(actor_id),
            str(reason or ""),
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            int(now),
        ),
    )


def _payment(conn, payment_id):
    return conn.execute(
        "SELECT * FROM payment_requests WHERE id=?", (int(payment_id),)
    ).fetchone()


def create_payment_request(
    conn, *, owner, service, target, price, receipt, now,
    receipt_claimer=None, target_snapshot=None,
):
    actor_type, user_id, business_id = _validate_owner(owner)
    service = str(service or "").strip()
    if service not in ("advertisement", "subscription", "listing"):
        raise PaymentValidationError("To‘lov xizmati noto‘g‘ri.")
    target = dict(target or {})
    price = dict(price or {})
    amount = int(price.get("amount") or 0)
    quantity = max(1, int(target.get("quantity") or 1))
    snapshot = target_snapshot if isinstance(target_snapshot, dict) else {}
    snapshot_json = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if amount < 0 or str(price.get("currency") or "UZS").upper() != "UZS":
        raise PaymentValidationError("To‘lov narxi noto‘g‘ri.")
    filename, mime, digest = _validate_receipt(receipt)
    stamp = _now(now)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _assert_receipt_available(conn, digest)
        method_id = int(target.get("payment_method_id") or 1)
        method = conn.execute(
            "SELECT id FROM payment_methods WHERE id=? AND active=1",
            (method_id,),
        ).fetchone()
        if not method:
            raise PaymentValidationError("To‘lov usuli mavjud emas.")
        request_code = "PAY-" + secrets.token_hex(6).upper()
        cur = conn.execute(
            """
            INSERT INTO payment_requests(
              request_code,actor_type,user_id,business_id,service_type,
              target_id,plan_code,duration_months,quantity,
              unit_price_snapshot,amount_snapshot,currency,price_code,
              target_snapshot_json,payment_method_id,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)
            """,
            (
                request_code,
                actor_type,
                user_id,
                business_id,
                service,
                target.get("target_id"),
                str(target.get("plan_code") or ""),
                int(target.get("duration_months") or 0),
                quantity,
                amount,
                amount * quantity,
                "UZS",
                str(price.get("price_code") or ""),
                snapshot_json,
                method_id,
                stamp,
                stamp,
            ),
        )
        payment_id = cur.lastrowid
        if receipt_claimer is not None:
            claimed = receipt_claimer(payment_id, dict(receipt))
            filename, mime, digest = _validate_receipt(claimed)
        conn.execute(
            """
            INSERT INTO payment_attempts(
              payment_request_id,attempt_no,receipt_filename,receipt_mime,
              receipt_sha256,submitted_at
            ) VALUES(?,1,?,?,?,?)
            """,
            (payment_id, filename, mime, digest, stamp),
        )
        _event(
            conn,
            payment_id,
            "",
            "pending",
            actor_type,
            business_id or user_id,
            "",
            stamp,
            {
                "price_code": str(price.get("price_code") or ""),
                "billable_district_hours": snapshot.get(
                    "billable_district_hours"
                ),
                "schedule_start": snapshot.get("schedule_start"),
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _dict(_payment(conn, payment_id))


def _review(
    conn,
    payment_id,
    admin_tg_id,
    to_status,
    reason="",
    now=None,
    activator=None,
    post_event=None,
    audit_hook=None,
):
    reason = str(reason or "").strip()
    if to_status in ("rejected", "cancelled") and not reason:
        raise PaymentValidationError("Sabab kiritilishi shart.")
    expected = "approved" if to_status == "cancelled" else "pending"
    stamp = _now(now)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _payment(conn, payment_id)
        if not row or row["status"] != expected:
            raise PaymentConflict("To‘lov holati allaqachon o‘zgargan.")
        activation_metadata = {}
        if to_status == "approved" and activator is not None:
            activation_metadata = activator(
                conn, _dict(row), stamp
            ) or {}
        updates = {
            "approved": ("approved_at", stamp),
            "rejected": ("rejected_at", stamp),
            "cancelled": ("cancelled_at", stamp),
        }
        time_column, time_value = updates[to_status]
        conn.execute(
            f"""
            UPDATE payment_requests
            SET status=?,approved_by_tg_id=?,{time_column}=?,
                public_reason=?,updated_at=?
            WHERE id=? AND status=?
            """,
            (
                to_status,
                int(admin_tg_id),
                time_value,
                reason if to_status != "approved" else "",
                stamp,
                int(payment_id),
                expected,
            ),
        )
        attempt_status = {
            "approved": "approved",
            "rejected": "rejected",
            "cancelled": "approved",
        }[to_status]
        conn.execute(
            """
            UPDATE payment_attempts
            SET reviewed_at=?,review_status=?,review_reason=?
            WHERE id=(
              SELECT id FROM payment_attempts
              WHERE payment_request_id=?
              ORDER BY attempt_no DESC LIMIT 1
            )
            """,
            (stamp, attempt_status, reason, int(payment_id)),
        )
        _event(
            conn,
            payment_id,
            expected,
            to_status,
            "admin",
            admin_tg_id,
            reason,
            stamp,
            activation_metadata,
        )
        if post_event is not None:
            post_event(conn, _dict(row), to_status, reason, stamp)
        if audit_hook is not None:
            audit_hook(conn, _dict(row), _dict(_payment(conn, payment_id)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _dict(_payment(conn, payment_id))


def approve_payment(
    conn,
    payment_id,
    admin_tg_id,
    reason="",
    now=None,
    activator=None,
    post_event=None,
    audit_hook=None,
):
    return _review(
        conn,
        payment_id,
        admin_tg_id,
        "approved",
        reason=reason,
        now=now,
        activator=activator,
        post_event=post_event,
        audit_hook=audit_hook,
    )


def reject_payment(
    conn, payment_id, admin_tg_id, reason, now=None, post_event=None,
    audit_hook=None,
):
    return _review(
        conn,
        payment_id,
        admin_tg_id,
        "rejected",
        reason=reason,
        now=now,
        post_event=post_event,
        audit_hook=audit_hook,
    )


def cancel_approved_payment(
    conn, payment_id, admin_tg_id, reason, now=None, post_event=None,
    audit_hook=None,
):
    return _review(
        conn,
        payment_id,
        admin_tg_id,
        "cancelled",
        reason=reason,
        now=now,
        post_event=post_event,
        audit_hook=audit_hook,
    )


def resubmit_payment(
    conn, payment_id, owner, receipt, now=None, receipt_claimer=None
):
    actor_type, user_id, business_id = _validate_owner(owner)
    filename, mime, digest = _validate_receipt(receipt)
    stamp = _now(now)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _payment(conn, payment_id)
        if (
            not row
            or row["status"] != "rejected"
            or row["actor_type"] != actor_type
            or int(row["user_id"]) != user_id
            or (row["business_id"] or None) != business_id
        ):
            raise PaymentConflict("To‘lovni qayta topshirib bo‘lmaydi.")
        _assert_receipt_available(
            conn, digest, exclude_payment_id=int(payment_id)
        )
        attempt_no = conn.execute(
            """
            SELECT COALESCE(MAX(attempt_no),0)+1
            FROM payment_attempts WHERE payment_request_id=?
            """,
            (int(payment_id),),
        ).fetchone()[0]
        if receipt_claimer is not None:
            claimed = receipt_claimer(int(payment_id), dict(receipt))
            filename, mime, digest = _validate_receipt(claimed)
        conn.execute(
            """
            UPDATE payment_attempts
            SET review_status='superseded'
            WHERE payment_request_id=? AND review_status='rejected'
            """,
            (int(payment_id),),
        )
        conn.execute(
            """
            INSERT INTO payment_attempts(
              payment_request_id,attempt_no,receipt_filename,receipt_mime,
              receipt_sha256,submitted_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (int(payment_id), attempt_no, filename, mime, digest, stamp),
        )
        conn.execute(
            """
            UPDATE payment_requests
            SET status='pending',approved_by_tg_id=NULL,rejected_at=0,
                public_reason='',updated_at=?
            WHERE id=? AND status='rejected'
            """,
            (stamp, int(payment_id)),
        )
        _event(
            conn,
            payment_id,
            "rejected",
            "pending",
            actor_type,
            business_id or user_id,
            "",
            stamp,
            {"attempt_no": attempt_no},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _dict(_payment(conn, payment_id))
