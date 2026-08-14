"""Admin panelining oddiy foydalanuvchi authidan ajratilgan kirish API'si."""

from __future__ import annotations

import os
import csv
import io
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from admin_audit import (
    append_admin_audit,
    audit_request_meta,
)
from admin_auth import (
    SESSION_TTL_SECONDS,
    admin_session,
    is_admin_tg_id,
    revoke_admin_session,
    start_admin_challenge,
    verify_admin_challenge,
)
from admin_queries import (
    audit_projection,
    business_detail,
    dashboard_snapshot,
    list_audit,
    list_businesses,
    list_content,
    list_reports,
    list_users,
    user_detail,
)
from database import db
from moderation import (
    ACTOR_TYPES,
    CONTENT_KINDS,
    RESTRICTIONS,
    account_restrictions,
    add_account_note,
    clear_account_restriction,
    content_moderation_status,
    set_account_restriction,
    set_content_visibility,
)
from runtime_config import env_flag


router = APIRouter(prefix="/api/admin")
ADMIN_COOKIE = "koprik_admin_session"


def _secret():
    return os.environ.get("WEBHOOK_SECRET", "platforma-webhook-secret")


def _cookie_token(request):
    return str(request.cookies.get(ADMIN_COOKIE, "") or "").strip()


def _current_admin(request, *, required=True):
    raw_token = _cookie_token(request)
    conn = db()
    try:
        session = admin_session(conn, raw_token)
        if session and not is_admin_tg_id(session["tg_id"]):
            revoke_admin_session(conn, raw_token)
            session = None
        if not session and required:
            raise HTTPException(401, "Admin sessiyasi topilmadi yoki tugagan.")
        return session
    finally:
        conn.close()


def require_admin(request: Request):
    """Reusable dependency for every separate admin router."""
    return _current_admin(request)


@router.post("/auth/start")
async def admin_auth_start(request: Request):
    try:
        body = await request.json()
        tg_id = int(body.get("tg_id"))
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(400, "Telegram ID raqam ko'rinishida bo'lishi kerak.")
    if not is_admin_tg_id(tg_id):
        raise HTTPException(403, "Bu Telegram ID adminlar ro'yxatida yo'q.")

    fixed_code = ""
    if env_flag("TEST_MODE", False):
        candidate = str(os.environ.get("TEST_OTP_CODE", "") or "").strip()
        if len(candidate) == 6 and candidate.isdigit():
            fixed_code = candidate
    conn = db()
    try:
        challenge = start_admin_challenge(
            conn,
            tg_id,
            _secret(),
            fixed_code=fixed_code,
        )
    finally:
        conn.close()

    from main import tg_call

    delivered = await tg_call(
        "sendMessage",
        {
            "chat_id": tg_id,
            "text": (
                "Ko'prik admin paneliga kirish kodi: "
                + challenge["code"]
                + "\nKod 5 daqiqa amal qiladi."
            ),
        },
    )
    if not delivered or not delivered.get("ok"):
        raise HTTPException(502, "Tasdiqlash kodini Telegramga yuborib bo'lmadi.")
    return {
        "ok": True,
        "challenge_id": challenge["id"],
        "expires_at": challenge["expires_at"],
    }


@router.post("/auth/verify")
async def admin_auth_verify(request: Request, response: Response):
    try:
        body = await request.json()
        challenge_id = int(body.get("challenge_id"))
        code = str(body.get("code", "") or "").strip()
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(400, "Tasdiqlash ma'lumoti noto'g'ri.")
    conn = db()
    try:
        try:
            raw_token = verify_admin_challenge(
                conn,
                challenge_id,
                code,
                _secret(),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        session = admin_session(conn, raw_token)
        if not session or not is_admin_tg_id(session["tg_id"]):
            revoke_admin_session(conn, raw_token)
            raise HTTPException(401, "Admin ruxsati tasdiqlanmadi.")
    finally:
        conn.close()
    response.set_cookie(
        ADMIN_COOKIE,
        raw_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/api/admin",
    )
    return {"ok": True, "tg_id": int(session["tg_id"])}


@router.get("/auth/me")
async def admin_auth_me(request: Request):
    session = _current_admin(request)
    return {"ok": True, "tg_id": int(session["tg_id"])}


@router.post("/auth/logout")
async def admin_auth_logout(request: Request, response: Response):
    raw_token = _cookie_token(request)
    if raw_token:
        conn = db()
        try:
            revoke_admin_session(conn, raw_token)
        finally:
            conn.close()
    response.delete_cookie(
        ADMIN_COOKIE,
        path="/api/admin",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return {"ok": True}


def _body_reason(body, *, field="reason", maximum=2000):
    text = str((body or {}).get(field) or "").strip()
    if not text:
        raise HTTPException(400, "Sabab kiritilishi shart.")
    return text[:maximum]


def _audit(
    conn, request, admin, action, target, before, after, reason=""
):
    return append_admin_audit(
        conn,
        admin_tg_id=int(admin["tg_id"]),
        action=action,
        target=target,
        before=before or {},
        after=after or {},
        reason=reason,
        request_meta=audit_request_meta(request),
    )


@router.get("/dashboard")
async def admin_dashboard(request: Request):
    _current_admin(request)
    conn = db()
    try:
        return dashboard_snapshot(conn)
    finally:
        conn.close()


@router.get("/users")
async def admin_users(
    request: Request, q: str = "", status: str = "", page: int = 1
):
    _current_admin(request)
    conn = db()
    try:
        try:
            return list_users(conn, q=q, status=status, page=page)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.get("/users/{user_id}")
async def admin_user_detail(user_id: int, request: Request):
    _current_admin(request)
    conn = db()
    try:
        result = user_detail(conn, user_id)
        if result is None:
            raise HTTPException(404, "Foydalanuvchi topilmadi.")
        return result
    finally:
        conn.close()


@router.get("/businesses")
async def admin_businesses(
    request: Request, q: str = "", status: str = "", page: int = 1
):
    _current_admin(request)
    conn = db()
    try:
        try:
            return list_businesses(conn, q=q, status=status, page=page)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.get("/businesses/{business_id}")
async def admin_business_detail(business_id: int, request: Request):
    _current_admin(request)
    conn = db()
    try:
        result = business_detail(conn, business_id)
        if result is None:
            raise HTTPException(404, "Biznes topilmadi.")
        return result
    finally:
        conn.close()


def _account_exists(conn, actor_type, actor_id):
    table = "users" if actor_type == "user" else "businesses"
    return bool(
        conn.execute(
            "SELECT 1 FROM " + table + " WHERE id=?", (int(actor_id),)
        ).fetchone()
    )


@router.post("/accounts/{actor_type}/{actor_id}/restrict")
async def admin_restrict_account(
    actor_type: str, actor_id: int, request: Request
):
    admin = _current_admin(request)
    body = await request.json()
    restriction = str(body.get("restriction") or "").strip()
    reason = _body_reason(body)
    if actor_type not in ACTOR_TYPES or restriction not in RESTRICTIONS:
        raise HTTPException(400, "Profil turi yoki cheklov noto‘g‘ri.")
    conn = db()
    try:
        if not _account_exists(conn, actor_type, actor_id):
            raise HTTPException(404, "Profil topilmadi.")
        conn.execute("BEGIN IMMEDIATE")
        try:
            before = sorted(
                account_restrictions(conn, actor_type, actor_id)
            )
            row = set_account_restriction(
                conn, actor_type, actor_id, restriction,
                int(admin["tg_id"]), reason,
            )
            after = sorted(account_restrictions(conn, actor_type, actor_id))
            _audit(
                conn, request, admin, "account.restrict",
                {"kind": actor_type, "id": actor_id},
                {"active_restrictions": before},
                {"active_restrictions": after},
                reason,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "ok": True,
            "restriction": row,
            "active_restrictions": after,
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.post("/accounts/{actor_type}/{actor_id}/unrestrict")
async def admin_unrestrict_account(
    actor_type: str, actor_id: int, request: Request
):
    admin = _current_admin(request)
    body = await request.json()
    restriction = str(body.get("restriction") or "").strip()
    reason = _body_reason(body)
    if actor_type not in ACTOR_TYPES or restriction not in RESTRICTIONS:
        raise HTTPException(400, "Profil turi yoki cheklov noto‘g‘ri.")
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            before = sorted(
                account_restrictions(conn, actor_type, actor_id)
            )
            row = clear_account_restriction(
                conn, actor_type, actor_id, restriction,
                int(admin["tg_id"]), reason,
            )
            after = sorted(account_restrictions(conn, actor_type, actor_id))
            _audit(
                conn, request, admin, "account.unrestrict",
                {"kind": actor_type, "id": actor_id},
                {"active_restrictions": before},
                {"active_restrictions": after},
                reason,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "ok": True,
            "restriction": row,
            "active_restrictions": after,
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.post("/accounts/{actor_type}/{actor_id}/notes", status_code=201)
async def admin_add_account_note(
    actor_type: str, actor_id: int, request: Request
):
    admin = _current_admin(request)
    body = await request.json()
    conn = db()
    try:
        if actor_type not in ACTOR_TYPES or not _account_exists(
            conn, actor_type, actor_id
        ):
            raise HTTPException(404, "Profil topilmadi.")
        conn.execute("BEGIN IMMEDIATE")
        try:
            note = add_account_note(
                conn, actor_type, actor_id, body.get("note"),
                int(admin["tg_id"]),
            )
            _audit(
                conn, request, admin, "account.note",
                {"kind": actor_type, "id": actor_id}, {}, {"note_id": note["id"]},
                "Ichki admin izohi",
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return note
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


def _content_table(kind):
    return {
        "product": ("items", "kind='product'"),
        "service": ("items", "kind='service'"),
        "advertisement": ("advertisements", "1=1"),
        "business": ("businesses", "1=1"),
        "profile": ("users", "1=1"),
        "listing": ("listings", "1=1"),
    }.get(kind)


def _content_row(conn, kind, content_id):
    source = _content_table(kind)
    if not source:
        return None
    table, clause = source
    return conn.execute(
        "SELECT * FROM " + table + " WHERE id=? AND " + clause,
        (int(content_id),),
    ).fetchone()


@router.get("/content")
async def admin_content(
    request: Request, kind: str = "product", status: str = "",
    q: str = "", page: int = 1,
):
    _current_admin(request)
    conn = db()
    try:
        try:
            return list_content(
                conn, kind=kind, status=status, q=q, page=page
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.get("/content/{kind}/{content_id}")
async def admin_content_detail(
    kind: str, content_id: int, request: Request
):
    _current_admin(request)
    conn = db()
    try:
        row = _content_row(conn, kind, content_id)
        if not row:
            raise HTTPException(404, "Kontent topilmadi.")
        safe = {
            key: row[key]
            for key in row.keys()
            if key not in {
                "biz_pass_hash", "pass_hash", "token_hash", "image_file",
                "mobile_image_file",
            }
        }
        safe["kind"] = kind
        safe["moderation_status"] = content_moderation_status(
            conn, kind, content_id
        )
        return safe
    finally:
        conn.close()


async def _change_content(kind, content_id, status, request):
    admin = _current_admin(request)
    body = await request.json()
    reason = _body_reason(body)
    if kind not in CONTENT_KINDS:
        raise HTTPException(400, "Kontent turi noto‘g‘ri.")
    conn = db()
    try:
        if not _content_row(conn, kind, content_id):
            raise HTTPException(404, "Kontent topilmadi.")
        conn.execute("BEGIN IMMEDIATE")
        try:
            before = content_moderation_status(conn, kind, content_id)
            event = set_content_visibility(
                conn, kind, content_id, status, int(admin["tg_id"]), reason
            )
            _audit(
                conn, request, admin, "content." + status,
                {"kind": kind, "id": content_id},
                {"moderation_status": before},
                {"moderation_status": status},
                reason,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "ok": True,
            "moderation_status": status,
            "event_id": int(event["id"]),
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.post("/content/{kind}/{content_id}/hide")
async def admin_hide_content(kind: str, content_id: int, request: Request):
    return await _change_content(kind, content_id, "hidden", request)


@router.post("/content/{kind}/{content_id}/restore")
async def admin_restore_content(kind: str, content_id: int, request: Request):
    return await _change_content(kind, content_id, "visible", request)


@router.post("/content/{kind}/{content_id}/remove")
async def admin_remove_content(kind: str, content_id: int, request: Request):
    return await _change_content(kind, content_id, "removed", request)


@router.get("/reports")
async def admin_reports(
    request: Request, status: str = "open", page: int = 1
):
    _current_admin(request)
    conn = db()
    try:
        try:
            return list_reports(conn, status=status, page=page)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.get("/reports/{report_id}")
async def admin_report_detail(report_id: int, request: Request):
    _current_admin(request)
    conn = db()
    try:
        row = conn.execute(
            "SELECT * FROM moderation_reports WHERE id=?", (int(report_id),)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Shikoyat topilmadi.")
        return dict(row)
    finally:
        conn.close()


@router.post("/reports/{report_id}/assign")
async def admin_assign_report(report_id: int, request: Request):
    admin = _current_admin(request)
    conn = db()
    try:
        row = conn.execute(
            "SELECT * FROM moderation_reports WHERE id=?", (int(report_id),)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Shikoyat topilmadi.")
        if row["status"] not in ("open", "reviewing"):
            raise HTTPException(409, "Shikoyat allaqachon yopilgan.")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE moderation_reports SET status='reviewing',
              assigned_admin_tg_id=?,updated_at=? WHERE id=?
            """,
            (int(admin["tg_id"]), int(time.time()), int(report_id)),
        )
        after = dict(
            conn.execute(
                "SELECT * FROM moderation_reports WHERE id=?",
                (int(report_id),),
            ).fetchone()
        )
        _audit(
            conn, request, admin, "report.assign",
            {"kind": "report", "id": report_id}, dict(row), after, "",
        )
        conn.commit()
        return after
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _reported_owner(conn, kind, content_id):
    if kind in ("product", "service"):
        row = conn.execute(
            "SELECT business_id FROM items WHERE id=?", (int(content_id),)
        ).fetchone()
        return ("business", int(row["business_id"])) if row else None
    if kind == "advertisement":
        row = conn.execute(
            "SELECT user_id,business_id,actor_type FROM advertisements WHERE id=?",
            (int(content_id),),
        ).fetchone()
        if not row:
            return None
        return (
            ("business", int(row["business_id"]))
            if row["actor_type"] == "business" and row["business_id"]
            else ("user", int(row["user_id"]))
        )
    if kind == "business":
        return ("business", int(content_id))
    if kind == "profile":
        return ("user", int(content_id))
    if kind == "listing":
        row = conn.execute(
            "SELECT user_id,business_id FROM listings WHERE id=?",
            (int(content_id),),
        ).fetchone()
        if not row:
            return None
        return (
            ("business", int(row["business_id"]))
            if row["business_id"]
            else ("user", int(row["user_id"]))
        )
    return None


async def _close_report(report_id, status, request):
    admin = _current_admin(request)
    body = await request.json()
    resolution = _body_reason(body, field="resolution")
    action = str(body.get("moderation_action") or "none").strip()
    if action not in (
        "none", "hide_content", "content_hidden", "account_blocked"
    ):
        raise HTTPException(400, "Moderatsiya qarori noto‘g‘ri.")
    conn = db()
    try:
        row = conn.execute(
            "SELECT * FROM moderation_reports WHERE id=?", (int(report_id),)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Shikoyat topilmadi.")
        if row["status"] not in ("open", "reviewing"):
            raise HTTPException(409, "Shikoyat allaqachon yopilgan.")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if status == "resolved" and action == "hide_content":
                set_content_visibility(
                    conn, row["content_kind"], row["content_id"], "hidden",
                    int(admin["tg_id"]), resolution,
                )
            elif status == "resolved" and action in RESTRICTIONS:
                owner = _reported_owner(
                    conn, row["content_kind"], row["content_id"]
                )
                if not owner:
                    raise HTTPException(400, "Kontent egasi topilmadi.")
                set_account_restriction(
                    conn, owner[0], owner[1], action,
                    int(admin["tg_id"]), resolution,
                )
            conn.execute(
                """
                UPDATE moderation_reports SET status=?,resolution=?,
                  assigned_admin_tg_id=?,updated_at=? WHERE id=?
                """,
                (
                    status, resolution, int(admin["tg_id"]),
                    int(time.time()), int(report_id),
                ),
            )
            after = dict(
                conn.execute(
                    "SELECT * FROM moderation_reports WHERE id=?",
                    (int(report_id),),
                ).fetchone()
            )
            _audit(
                conn, request, admin, "report." + status,
                {"kind": "report", "id": report_id}, dict(row), after,
                resolution,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return after
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.post("/reports/{report_id}/resolve")
async def admin_resolve_report(report_id: int, request: Request):
    return await _close_report(report_id, "resolved", request)


@router.post("/reports/{report_id}/dismiss")
async def admin_dismiss_report(report_id: int, request: Request):
    return await _close_report(report_id, "dismissed", request)


@router.get("/audit")
async def admin_audit_list(
    request: Request, action: str = "", admin_tg_id: int | None = None,
    from_: int | None = Query(default=None, alias="from"),
    to: int | None = None, page: int = 1,
):
    _current_admin(request)
    conn = db()
    try:
        return list_audit(
            conn, action=action, admin_tg_id=admin_tg_id,
            date_from=from_, date_to=to, page=page,
        )
    finally:
        conn.close()


@router.get("/audit/export.csv")
async def admin_audit_export(
    request: Request, action: str = "", admin_tg_id: int | None = None,
    from_: int | None = Query(default=None, alias="from"),
    to: int | None = None,
):
    _current_admin(request)
    conn = db()
    try:
        payload = list_audit(
            conn, action=action, admin_tg_id=admin_tg_id,
            date_from=from_, date_to=to, page=1, page_size=10_000,
        )
        # list_audit deliberately caps normal pages; export is still bounded.
        clauses, params = [], []
        if action:
            clauses.append("action=?")
            params.append(action)
        if admin_tg_id:
            clauses.append("admin_tg_id=?")
            params.append(int(admin_tg_id))
        if from_:
            clauses.append("created_at>=?")
            params.append(int(from_))
        if to:
            clauses.append("created_at<=?")
            params.append(int(to))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            "SELECT * FROM admin_audit_log" + where
            + " ORDER BY id DESC LIMIT 10000",
            tuple(params),
        ).fetchall()
        del payload
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id", "admin_tg_id", "action", "target_kind", "target_id",
                "before_json", "after_json", "reason", "created_at",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["id"], row["admin_tg_id"], row["action"],
                    row["target_kind"], row["target_id"], row["before_json"],
                    row["after_json"], row["reason"], row["created_at"],
                ]
            )
        response = StreamingResponse(
            iter([output.getvalue()]), media_type="text/csv; charset=utf-8"
        )
        response.headers["Content-Disposition"] = (
            'attachment; filename="koprik-admin-audit.csv"'
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        conn.close()


@router.get("/audit/{audit_id}")
async def admin_audit_detail(audit_id: int, request: Request):
    _current_admin(request)
    conn = db()
    try:
        row = conn.execute(
            "SELECT * FROM admin_audit_log WHERE id=?", (int(audit_id),)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Audit hodisasi topilmadi.")
        return audit_projection(row, include_payload=True)
    finally:
        conn.close()
