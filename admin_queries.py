"""Bounded, secret-free projections for the separate admin site."""

from __future__ import annotations

import json
import time

from moderation import account_restrictions, content_moderation_status


PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


def _page(value):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _paging(page, page_size=PAGE_SIZE):
    page = _page(page)
    size = max(1, min(MAX_PAGE_SIZE, int(page_size or PAGE_SIZE)))
    return page, size, (page - 1) * size


def _scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _safe_json(value):
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def audit_projection(row, include_payload=False):
    result = {
        "id": int(row["id"]),
        "admin_tg_id": int(row["admin_tg_id"]),
        "action": row["action"],
        "target_kind": row["target_kind"],
        "target_id": row["target_id"],
        "reason": row["reason"],
        "created_at": int(row["created_at"]),
    }
    if include_payload:
        result.update(
            {
                "before": _safe_json(row["before_json"]),
                "after": _safe_json(row["after_json"]),
                "ip_hash": row["ip_hash"],
                "user_agent": row["user_agent"],
            }
        )
    return result


def dashboard_snapshot(conn, now=None):
    stamp = int(time.time() if now is None else now)
    payment_rows = conn.execute(
        """
        SELECT status,COUNT(*) AS count,COALESCE(SUM(amount_snapshot),0) AS amount
        FROM payment_requests GROUP BY status
        """
    ).fetchall()
    payments = {
        status: {"count": 0, "amount": 0}
        for status in ("pending", "approved", "rejected", "cancelled")
    }
    for row in payment_rows:
        if row["status"] in payments:
            payments[row["status"]] = {
                "count": int(row["count"] or 0),
                "amount": int(row["amount"] or 0),
            }
    activity = [
        audit_projection(row)
        for row in conn.execute(
            "SELECT * FROM admin_audit_log ORDER BY id DESC LIMIT 10"
        ).fetchall()
    ]
    return {
        "payments": payments,
        "users": {
            "total": _scalar(conn, "SELECT COUNT(*) FROM users"),
            "new_24h": _scalar(
                conn, "SELECT COUNT(*) FROM users WHERE created_at>=?",
                (stamp - 86400,),
            ),
            "new_30d": _scalar(
                conn, "SELECT COUNT(*) FROM users WHERE created_at>=?",
                (stamp - 30 * 86400,),
            ),
        },
        "businesses": {
            "total": _scalar(conn, "SELECT COUNT(*) FROM businesses"),
            "new_24h": _scalar(
                conn, "SELECT COUNT(*) FROM businesses WHERE created_at>=?",
                (stamp - 86400,),
            ),
            "new_30d": _scalar(
                conn, "SELECT COUNT(*) FROM businesses WHERE created_at>=?",
                (stamp - 30 * 86400,),
            ),
        },
        "content": {
            "products": _scalar(
                conn, "SELECT COUNT(*) FROM items WHERE kind='product'"
            ),
            "services": _scalar(
                conn, "SELECT COUNT(*) FROM items WHERE kind='service'"
            ),
            "advertisements": _scalar(
                conn,
                "SELECT COUNT(*) FROM advertisements WHERE status='active'",
            ),
        },
        "reports": {
            "open": _scalar(
                conn,
                "SELECT COUNT(*) FROM moderation_reports "
                "WHERE status IN ('open','reviewing')",
            )
        },
        "activity": activity,
        "generated_at": stamp,
        "timezone": "Asia/Samarkand",
    }


def list_users(conn, q="", status="", page=1):
    page, size, offset = _paging(page)
    q = " ".join(str(q or "").strip().split())[:120]
    where, params = [], []
    if q:
        if len(q) < 2:
            raise ValueError("Qidiruv kamida 2 belgi bo‘lsin.")
        like = "%" + q.lower() + "%"
        where.append(
            "(LOWER(COALESCE(u.name,'')) LIKE ? OR "
            "LOWER(COALESCE(u.login,'')) LIKE ? OR "
            "LOWER(COALESCE(u.username,'')) LIKE ? OR "
            "CAST(COALESCE(u.tg_id,0) AS TEXT)=?)"
        )
        params.extend((like, like, like, q))
    if status in ("restricted", "blocked", "hidden"):
        restriction = {
            "restricted": None,
            "blocked": "account_blocked",
            "hidden": "content_hidden",
        }[status]
        clause = (
            "EXISTS(SELECT 1 FROM account_restrictions ar "
            "WHERE ar.actor_type='user' AND ar.actor_id=u.id "
            "AND ar.status='active'"
        )
        if restriction:
            clause += " AND ar.restriction=?"
            params.append(restriction)
        where.append(clause + ")")
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    total = _scalar(
        conn, "SELECT COUNT(*) FROM users u" + where_sql, tuple(params)
    )
    rows = conn.execute(
        """
        SELECT u.id,u.tg_id,u.username,u.login,u.role,u.name,u.phone,u.created_at,
               (SELECT COUNT(*) FROM businesses b WHERE b.user_id=u.id) AS businesses_count
        FROM users u
        """
        + where_sql
        + " ORDER BY u.id DESC LIMIT ? OFFSET ?",
        tuple(params + [size, offset]),
    ).fetchall()
    items = []
    for row in rows:
        data = dict(row)
        data["active_restrictions"] = sorted(
            account_restrictions(conn, "user", row["id"])
        )
        items.append(data)
    return {"items": items, "page": page, "page_size": size, "total": total}


def user_detail(conn, user_id):
    row = conn.execute(
        """
        SELECT id,tg_id,username,login,role,name,phone,created_at,
               CASE WHEN name<>'' AND phone<>'' THEN 100
                    WHEN name<>'' OR phone<>'' THEN 50 ELSE 0 END
                 AS profile_completion
        FROM users WHERE id=?
        """,
        (int(user_id),),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["counts"] = {
        "businesses": _scalar(
            conn, "SELECT COUNT(*) FROM businesses WHERE user_id=?",
            (int(user_id),),
        ),
        "listings": _scalar(
            conn, "SELECT COUNT(*) FROM listings WHERE user_id=?",
            (int(user_id),),
        ),
        "orders": _scalar(
            conn,
            "SELECT COUNT(*) FROM orders WHERE customer_user_id=? "
            "OR provider_user_id=?",
            (int(user_id), int(user_id)),
        ),
    }
    result["active_restrictions"] = sorted(
        account_restrictions(conn, "user", user_id)
    )
    result["notes"] = [
        dict(note)
        for note in conn.execute(
            """
            SELECT id,note,admin_tg_id,created_at FROM admin_account_notes
            WHERE actor_type='user' AND actor_id=? ORDER BY id DESC LIMIT 100
            """,
            (int(user_id),),
        ).fetchall()
    ]
    return result


def list_businesses(conn, q="", status="", page=1):
    page, size, offset = _paging(page)
    q = " ".join(str(q or "").strip().split())[:120]
    where, params = [], []
    if q:
        if len(q) < 2:
            raise ValueError("Qidiruv kamida 2 belgi bo‘lsin.")
        like = "%" + q.lower() + "%"
        where.append(
            "(LOWER(COALESCE(b.name,'')) LIKE ? OR "
            "LOWER(COALESCE(b.yon,'')) LIKE ? OR "
            "LOWER(COALESCE(b.tur,'')) LIKE ? OR "
            "CAST(b.id AS TEXT)=?)"
        )
        params.extend((like, like, like, q))
    if status in ("active", "inactive"):
        where.append("b.status=?")
        params.append(status)
    elif status in ("restricted", "blocked", "hidden"):
        restriction = {
            "restricted": None,
            "blocked": "account_blocked",
            "hidden": "content_hidden",
        }[status]
        clause = (
            "EXISTS(SELECT 1 FROM account_restrictions ar "
            "WHERE ar.actor_type='business' AND ar.actor_id=b.id "
            "AND ar.status='active'"
        )
        if restriction:
            clause += " AND ar.restriction=?"
            params.append(restriction)
        where.append(clause + ")")
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    total = _scalar(
        conn, "SELECT COUNT(*) FROM businesses b" + where_sql, tuple(params)
    )
    rows = conn.execute(
        """
        SELECT b.id,b.user_id,b.name,b.yon,b.tur,b.phone,b.address,b.status,
               b.map_visible,b.created_at,u.name AS owner_name,u.tg_id AS owner_tg_id
        FROM businesses b JOIN users u ON u.id=b.user_id
        """
        + where_sql
        + " ORDER BY b.id DESC LIMIT ? OFFSET ?",
        tuple(params + [size, offset]),
    ).fetchall()
    items = []
    for row in rows:
        data = dict(row)
        data["active_restrictions"] = sorted(
            account_restrictions(conn, "business", row["id"])
        )
        items.append(data)
    return {"items": items, "page": page, "page_size": size, "total": total}


def business_detail(conn, business_id):
    row = conn.execute(
        """
        SELECT b.id,b.user_id,b.name,b.yon,b.tur,b.descr,b.phone,b.telegram,
               b.work_hours,b.address,b.lat,b.lng,b.status,b.map_visible,
               b.created_at,u.name AS owner_name,u.tg_id AS owner_tg_id,
               u.login AS owner_login
        FROM businesses b JOIN users u ON u.id=b.user_id WHERE b.id=?
        """,
        (int(business_id),),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["counts"] = {
        "products": _scalar(
            conn,
            "SELECT COUNT(*) FROM items WHERE business_id=? AND kind='product'",
            (int(business_id),),
        ),
        "services": _scalar(
            conn,
            "SELECT COUNT(*) FROM items WHERE business_id=? AND kind='service'",
            (int(business_id),),
        ),
        "advertisements": _scalar(
            conn, "SELECT COUNT(*) FROM advertisements WHERE business_id=?",
            (int(business_id),),
        ),
    }
    result["payments"] = {
        "pending": _scalar(
            conn,
            "SELECT COUNT(*) FROM payment_requests "
            "WHERE business_id=? AND status='pending'",
            (int(business_id),),
        ),
        "approved": _scalar(
            conn,
            "SELECT COUNT(*) FROM payment_requests "
            "WHERE business_id=? AND status='approved'",
            (int(business_id),),
        ),
    }
    subscription = conn.execute(
        """
        SELECT plan_code,duration_months,status,starts_at,expires_at
        FROM business_subscriptions
        WHERE business_id=? ORDER BY id DESC LIMIT 1
        """,
        (int(business_id),),
    ).fetchone()
    result["subscription"] = dict(subscription) if subscription else None
    result["active_restrictions"] = sorted(
        account_restrictions(conn, "business", business_id)
    )
    result["notes"] = [
        dict(note)
        for note in conn.execute(
            """
            SELECT id,note,admin_tg_id,created_at FROM admin_account_notes
            WHERE actor_type='business' AND actor_id=? ORDER BY id DESC LIMIT 100
            """,
            (int(business_id),),
        ).fetchall()
    ]
    return result


def _content_source(kind):
    if kind in ("product", "service"):
        return (
            "items i JOIN businesses b ON b.id=i.business_id",
            "i",
            "i.name",
            "i.business_id",
            "b.name",
            "i.kind=?",
            [kind],
        )
    if kind == "advertisement":
        return (
            "advertisements i LEFT JOIN businesses b ON b.id=i.business_id",
            "i",
            "i.title",
            "i.business_id",
            "COALESCE(b.name,'')",
            "1=1",
            [],
        )
    if kind == "business":
        return (
            "businesses i",
            "i",
            "i.name",
            "i.id",
            "i.name",
            "1=1",
            [],
        )
    if kind == "profile":
        return (
            "users i",
            "i",
            "i.name",
            "NULL",
            "''",
            "1=1",
            [],
        )
    raise ValueError("Kontent turi noto‘g‘ri.")


def list_content(conn, kind="product", status="", q="", page=1):
    source, alias, title, business_id, owner_name, base, params = (
        _content_source(kind)
    )
    page, size, offset = _paging(page)
    clauses = [base]
    q = " ".join(str(q or "").strip().split())[:120]
    if q:
        clauses.append("LOWER(COALESCE(" + title + ",'')) LIKE ?")
        params.append("%" + q.lower() + "%")
    rows = conn.execute(
        "SELECT "
        + alias
        + ".id AS id,"
        + title
        + " AS title,"
        + business_id
        + " AS business_id,"
        + owner_name
        + " AS owner_name,"
        + alias
        + ".created_at AS created_at FROM "
        + source
        + " WHERE "
        + " AND ".join(clauses)
        + " ORDER BY "
        + alias
        + ".id DESC LIMIT ? OFFSET ?",
        tuple(params + [size * 3, offset]),
    ).fetchall()
    items = []
    for row in rows:
        state = content_moderation_status(conn, kind, row["id"])
        if status and state != status:
            continue
        data = dict(row)
        data["kind"] = kind
        data["moderation_status"] = state
        items.append(data)
        if len(items) >= size:
            break
    return {"items": items, "page": page, "page_size": size}


def list_reports(conn, status="open", page=1):
    page, size, offset = _paging(page)
    statuses = {"open", "reviewing", "resolved", "dismissed", "all"}
    if status not in statuses:
        raise ValueError("Shikoyat holati noto‘g‘ri.")
    where, params = "", []
    if status != "all":
        where, params = " WHERE status=?", [status]
    total = _scalar(
        conn, "SELECT COUNT(*) FROM moderation_reports" + where, tuple(params)
    )
    rows = conn.execute(
        "SELECT * FROM moderation_reports"
        + where
        + " ORDER BY created_at,id LIMIT ? OFFSET ?",
        tuple(params + [size, offset]),
    ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "page": page,
        "page_size": size,
        "total": total,
    }


def list_audit(
    conn, action="", admin_tg_id=None, date_from=None, date_to=None,
    page=1, page_size=PAGE_SIZE,
):
    page, size, offset = _paging(page, page_size)
    clauses, params = [], []
    if action:
        clauses.append("action=?")
        params.append(str(action)[:120])
    if admin_tg_id:
        clauses.append("admin_tg_id=?")
        params.append(int(admin_tg_id))
    if date_from:
        clauses.append("created_at>=?")
        params.append(int(date_from))
    if date_to:
        clauses.append("created_at<=?")
        params.append(int(date_to))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    total = _scalar(
        conn, "SELECT COUNT(*) FROM admin_audit_log" + where, tuple(params)
    )
    rows = conn.execute(
        "SELECT * FROM admin_audit_log"
        + where
        + " ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params + [size, offset]),
    ).fetchall()
    return {
        "items": [audit_projection(row) for row in rows],
        "page": page,
        "page_size": size,
        "total": total,
    }
