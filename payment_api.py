"""Public-owner and separately authenticated admin payment endpoints."""

from __future__ import annotations

import json
import os
import time

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse

from admin_api import require_admin
from admin_audit import append_admin_audit, audit_request_meta
from api import require_business, require_user
from database import db
from feature_flags import feature_enabled
from payments import (
    PRICE_RULES,
    PaymentConflict,
    PaymentValidationError,
    active_payment_catalog,
    approve_payment,
    cancel_approved_payment,
    create_payment_request,
    ensure_default_prices,
    resubmit_payment,
    reject_payment,
)
from receipt_storage import (
    ReceiptValidationError,
    claim_receipt,
    receipt_absolute_path,
    store_receipt,
    verify_receipt_token,
)
from subscriptions import (
    SubscriptionValidationError,
    activate_paid_subscription,
)
from notification_delivery import (
    deliver_pending_outbox,
    queue_payment_decision,
)
from ad_pricing import (
    AdPricingError,
    calculate_ad_price,
    schedule_end_at,
    shift_schedule_start,
)


router = APIRouter(prefix="/api/payments")
admin_router = APIRouter(prefix="/api/admin")


def _admin(request):
    return require_admin(request)


def _receipt_root():
    return os.environ.get(
        "PAYMENT_RECEIPT_DIR", "private/payment_receipts"
    )


def _receipt_secret():
    value = os.environ.get("PAYMENT_TOKEN_SECRET", "")
    if len(value) >= 48:
        return value
    # Development-only fallback; production config rejects a missing secret.
    import hashlib

    return hashlib.sha256(
        ("payment-receipt:" + os.environ.get("WEBHOOK_SECRET", "development"))
        .encode()
    ).hexdigest()


def _owner_context(conn, init_data, actor_type):
    actor = str(actor_type or "user").strip().lower()
    if actor == "business":
        user, business = require_business(conn, init_data)
        return user, business
    if actor != "user":
        raise HTTPException(400, "Profil turi noto‘g‘ri.")
    return require_user(conn, init_data), None


def _safe_payment(row):
    return {
        "id": int(row["id"]),
        "request_code": row["request_code"],
        "actor_type": row["actor_type"],
        "service_type": row["service_type"],
        "plan_code": row["plan_code"],
        "duration_months": int(row["duration_months"]),
        "quantity": int(row["quantity"]),
        "amount": int(row["amount_snapshot"]),
        "currency": row["currency"],
        "status": row["status"],
        "reason": row["public_reason"],
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


@router.get("/catalog")
async def payment_catalog(
    x_telegram_init_data: str = Header(default=""),
):
    conn = db()
    try:
        require_user(conn, x_telegram_init_data)
        ensure_default_prices(conn)
        return active_payment_catalog(conn)
    finally:
        conn.close()


@router.post("/receipts")
async def upload_payment_receipt(
    request: Request,
    x_telegram_init_data: str = Header(default=""),
):
    conn = db()
    try:
        user = require_user(conn, x_telegram_init_data)
    finally:
        conn.close()
    raw = await request.body()
    try:
        stored = store_receipt(
            _receipt_root(),
            int(user["id"]),
            raw,
            request.headers.get("content-type", ""),
            _receipt_secret(),
        )
    except ReceiptValidationError as exc:
        raise HTTPException(400, str(exc))
    return {
        "ok": True,
        "receipt_token": stored["token"],
        "mime": stored["mime"],
        "expires_at": stored["expires_at"],
    }


def _resolve_price(conn, service_type, price_code, target):
    rule = PRICE_RULES.get(str(price_code or ""))
    if not rule or rule["service_type"] != service_type:
        raise HTTPException(400, "Tarif kodi xizmatga mos emas.")
    row = conn.execute(
        """
        SELECT * FROM platform_prices
        WHERE price_code=? AND service_type=? AND active=1
        """,
        (str(price_code), service_type),
    ).fetchone()
    if not row:
        raise HTTPException(400, "Tanlangan tarif hozir faol emas.")
    if service_type == "subscription":
        if (
            str(target.get("plan_code") or "") != rule["plan_code"]
            or int(target.get("duration_months") or 0)
            != int(rule["duration_months"])
        ):
            raise HTTPException(400, "Tarif parametrlari mos emas.")
    return row, rule


def _owned_pending_advertisement(
    conn, *, user, business, actor_type, ad_id
):
    try:
        target_id = int(ad_id)
    except (TypeError, ValueError):
        raise HTTPException(404, "To'lov kutilayotgan reklama topilmadi.")
    if actor_type == "business":
        if business is None:
            raise HTTPException(404, "To'lov kutilayotgan reklama topilmadi.")
        row = conn.execute(
            """
            SELECT * FROM advertisements
            WHERE id=? AND user_id=? AND business_id=?
              AND actor_type='business' AND status='payment_pending'
            """,
            (target_id, int(user["id"]), int(business["id"])),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM advertisements
            WHERE id=? AND user_id=? AND business_id IS NULL
              AND actor_type='user' AND status='payment_pending'
            """,
            (target_id, int(user["id"])),
        ).fetchone()
    if not row:
        raise HTTPException(404, "To'lov kutilayotgan reklama topilmadi.")
    return row


def _hourly_ad_payment_target(
    conn, *, user, business, actor_type, target, price_row
):
    ad = _owned_pending_advertisement(
        conn,
        user=user,
        business=business,
        actor_type=actor_type,
        ad_id=target.get("target_id"),
    )
    if str(ad["price_code"] or "") != "advertisement_district_hour":
        raise HTTPException(400, "Reklama soatlik tarifga tegishli emas.")
    try:
        targets = json.loads(ad["targets_json"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(400, "Reklama hududlari buzilgan.")
    try:
        pricing = calculate_ad_price(
            targets=targets,
            duration_days=int(ad["duration_days"]),
            daily_all_day=bool(ad["daily_all_day"]),
            daily_start=str(ad["daily_start"]),
            daily_end=str(ad["daily_end"]),
            district_hour_rate=int(price_row["amount_uzs"]),
        )
    except AdPricingError as exc:
        raise HTTPException(400, str(exc)) from exc
    quantity = int(pricing["billable_district_hours"])
    conn.execute(
        """
        UPDATE advertisements
        SET targets_json=?,district_count=?,hours_per_day=?,
            district_hour_rate=?,billable_district_hours=?,price=?,
            updated_at=?
        WHERE id=? AND status='payment_pending'
        """,
        (
            json.dumps(pricing["targets"], ensure_ascii=False),
            int(pricing["district_count"]),
            int(pricing["hours_per_day"]),
            int(pricing["district_hour_rate"]),
            quantity,
            int(pricing["total"]),
            int(time.time()),
            int(ad["id"]),
        ),
    )
    return (
        {
            "target_id": int(ad["id"]),
            "quantity": quantity,
        },
        {
            "district_count": int(pricing["district_count"]),
            "hours_per_day": int(pricing["hours_per_day"]),
            "duration_days": int(pricing["duration_days"]),
            "district_hour_rate": int(pricing["district_hour_rate"]),
            "billable_district_hours": quantity,
            "schedule_start": int(ad["start_at"]),
            "daily_all_day": bool(ad["daily_all_day"]),
            "daily_start": str(ad["daily_start"]),
            "daily_end": str(ad["daily_end"]),
        },
    )


@router.post("/requests", status_code=201)
async def submit_payment_request(
    request: Request,
    x_telegram_init_data: str = Header(default=""),
):
    body = await request.json()
    actor_type = str(body.get("actor_type") or "user").lower()
    service_type = str(body.get("service_type") or "").lower()
    target = body.get("target") or {}
    if not isinstance(target, dict):
        raise HTTPException(400, "To‘lov maqsadi noto‘g‘ri.")
    conn = db()
    try:
        if service_type == "listing" and not feature_enabled(
            conn, "listings"
        ):
            raise HTTPException(
                404,
                detail={
                    "code": "feature_disabled",
                    "feature": "listings",
                    "detail": "E’lonlar hozircha yopiq.",
                },
            )
        ensure_default_prices(conn)
        user, business = _owner_context(
            conn, x_telegram_init_data, actor_type
        )
        price_row, rule = _resolve_price(
            conn,
            service_type,
            body.get("price_code"),
            target,
        )
        del rule
        payment_target = dict(target)
        target_snapshot = None
        if service_type == "advertisement":
            if str(price_row["price_code"]) != "advertisement_district_hour":
                raise HTTPException(
                    400, "Yangi reklama uchun soatlik tarifni tanlang."
                )
            payment_target, target_snapshot = _hourly_ad_payment_target(
                conn,
                user=user,
                business=business,
                actor_type=actor_type,
                target=target,
                price_row=price_row,
            )
        method_id = int(body.get("payment_method_id") or 0)
        method = conn.execute(
            "SELECT id FROM payment_methods WHERE id=? AND active=1",
            (method_id,),
        ).fetchone()
        if not method:
            raise HTTPException(400, "To‘lov usuli faol emas.")
        try:
            token_data = verify_receipt_token(
                body.get("receipt_token"),
                _receipt_secret(),
                owner_id=int(user["id"]),
            )
        except ReceiptValidationError as exc:
            raise HTTPException(400, str(exc))

        receipt = {
            "path": token_data["relative_path"],
            "mime": token_data["mime"],
            "sha256": token_data["sha256"],
        }

        def claimer(payment_id, unused):
            del unused
            return claim_receipt(
                _receipt_root(), token_data, payment_id=payment_id
            )

        try:
            if service_type == "advertisement":
                conn.commit()
            payment = create_payment_request(
                conn,
                owner={
                    "user_id": int(user["id"]),
                    "actor_type": actor_type,
                    "business_id": (
                        int(business["id"]) if business is not None else None
                    ),
                },
                service=service_type,
                target={
                    **payment_target,
                    "payment_method_id": method_id,
                    "quantity": (
                        int(payment_target.get("quantity") or 1)
                        if service_type == "advertisement"
                        else 1
                    ),
                },
                price={
                    "amount": int(price_row["amount_uzs"]),
                    "currency": "UZS",
                    "price_code": price_row["price_code"],
                },
                receipt=receipt,
                receipt_claimer=claimer,
                target_snapshot=target_snapshot,
                now=int(time.time()),
            )
        except PaymentConflict as exc:
            raise HTTPException(409, str(exc))
        except PaymentValidationError as exc:
            raise HTTPException(400, str(exc))
        return _safe_payment(payment)
    finally:
        conn.close()


@router.get("/my")
async def my_payments(
    actor_type: str = "user",
    x_telegram_init_data: str = Header(default=""),
):
    conn = db()
    try:
        user, business = _owner_context(
            conn, x_telegram_init_data, actor_type
        )
        if business is None:
            rows = conn.execute(
                """
                SELECT * FROM payment_requests
                WHERE user_id=? AND actor_type='user'
                ORDER BY id DESC
                """,
                (int(user["id"]),),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM payment_requests
                WHERE user_id=? AND actor_type='business' AND business_id=?
                ORDER BY id DESC
                """,
                (int(user["id"]), int(business["id"])),
            ).fetchall()
        return [_safe_payment(row) for row in rows]
    finally:
        conn.close()


def _owned_payment(conn, payment_id, init_data):
    user = require_user(conn, init_data)
    row = conn.execute(
        "SELECT * FROM payment_requests WHERE id=?", (int(payment_id),)
    ).fetchone()
    if not row or int(row["user_id"]) != int(user["id"]):
        raise HTTPException(404, "To‘lov topilmadi.")
    if row["actor_type"] == "business":
        business = conn.execute(
            "SELECT id FROM businesses WHERE id=? AND user_id=?",
            (row["business_id"], int(user["id"])),
        ).fetchone()
        if not business:
            raise HTTPException(404, "To‘lov topilmadi.")
    return user, row


@router.get("/{payment_id}/receipt")
async def owner_payment_receipt(
    payment_id: int,
    x_telegram_init_data: str = Header(default=""),
):
    conn = db()
    try:
        _, payment = _owned_payment(
            conn, payment_id, x_telegram_init_data
        )
        attempt = conn.execute(
            """
            SELECT * FROM payment_attempts
            WHERE payment_request_id=? ORDER BY attempt_no DESC LIMIT 1
            """,
            (int(payment["id"]),),
        ).fetchone()
        if not attempt:
            raise HTTPException(404, "Kvitansiya topilmadi.")
        try:
            absolute = receipt_absolute_path(
                _receipt_root(), attempt["receipt_filename"]
            )
        except ReceiptValidationError:
            raise HTTPException(404, "Kvitansiya topilmadi.")
        extension = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }.get(attempt["receipt_mime"], "bin")
        return FileResponse(
            absolute,
            media_type=attempt["receipt_mime"],
            filename=f"receipt-{payment_id}.{extension}",
            headers={"Cache-Control": "no-store, private"},
            content_disposition_type="inline",
        )
    finally:
        conn.close()


@router.post("/{payment_id}/resubmit")
async def resubmit_owner_payment(
    payment_id: int,
    request: Request,
    x_telegram_init_data: str = Header(default=""),
):
    body = await request.json()
    conn = db()
    try:
        user, payment = _owned_payment(
            conn, payment_id, x_telegram_init_data
        )
        try:
            token_data = verify_receipt_token(
                body.get("receipt_token"),
                _receipt_secret(),
                owner_id=int(user["id"]),
            )
        except ReceiptValidationError as exc:
            raise HTTPException(400, str(exc))
        receipt = {
            "path": token_data["relative_path"],
            "mime": token_data["mime"],
            "sha256": token_data["sha256"],
        }

        def claimer(target_payment_id, unused):
            del unused
            return claim_receipt(
                _receipt_root(), token_data, target_payment_id
            )

        try:
            updated = resubmit_payment(
                conn,
                payment_id,
                {
                    "user_id": int(user["id"]),
                    "actor_type": payment["actor_type"],
                    "business_id": payment["business_id"],
                },
                receipt,
                receipt_claimer=claimer,
            )
        except PaymentConflict as exc:
            raise HTTPException(409, str(exc))
        except PaymentValidationError as exc:
            raise HTTPException(400, str(exc))
        return _safe_payment(updated)
    finally:
        conn.close()


@admin_router.get("/prices")
async def admin_prices(request: Request):
    _admin(request)
    conn = db()
    try:
        ensure_default_prices(conn)
        return [
            {
                **dict(row),
                "config": json.loads(row["config_json"] or "{}"),
            }
            for row in conn.execute(
                """
                SELECT * FROM platform_prices
                WHERE price_code!='advertisement_district_day'
                ORDER BY id
                """
            ).fetchall()
        ]
    finally:
        conn.close()


@admin_router.put("/prices/{price_id}")
async def admin_update_price(price_id: int, request: Request):
    admin = _admin(request)
    body = await request.json()
    try:
        amount = int(body.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Narx butun son bo‘lishi kerak.")
    if amount < 0 or amount > 10_000_000_000:
        raise HTTPException(400, "Narx ruxsat etilgan chegaradan tashqarida.")
    active = 1 if body.get("active", True) else 0
    conn = db()
    try:
        before = conn.execute(
            "SELECT * FROM platform_prices WHERE id=?", (int(price_id),)
        ).fetchone()
        if not before:
            raise HTTPException(404, "Narx topilmadi.")
        cur = conn.execute(
            """
            UPDATE platform_prices
            SET amount_uzs=?,active=?,updated_by_tg_id=?,updated_at=?
            WHERE id=?
            """,
            (
                amount,
                active,
                int(admin["tg_id"]),
                int(time.time()),
                int(price_id),
            ),
        )
        if cur.rowcount != 1:
            raise HTTPException(404, "Narx topilmadi.")
        after = conn.execute(
            "SELECT * FROM platform_prices WHERE id=?", (int(price_id),)
        ).fetchone()
        append_admin_audit(
            conn,
            admin_tg_id=int(admin["tg_id"]),
            action="price.update",
            target={"kind": "price", "id": price_id},
            before=dict(before),
            after=dict(after),
            reason=str(body.get("reason") or "Narx sozlamasi yangilandi"),
            request_meta=audit_request_meta(request),
        )
        conn.commit()
        return dict(after)
    finally:
        conn.close()


@admin_router.get("/payment-methods")
async def admin_payment_methods(request: Request):
    _admin(request)
    conn = db()
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM payment_methods ORDER BY sort_order,id"
            ).fetchall()
        ]
    finally:
        conn.close()


def _payment_method_payload(body):
    method_type = str(body.get("method_type") or "").strip()[:40]
    name = str(body.get("name") or "").strip()[:120]
    if not method_type or not name:
        raise HTTPException(400, "To‘lov usuli turi va nomi kerak.")
    details = body.get("details") or {}
    if not isinstance(details, dict):
        raise HTTPException(400, "To‘lov rekvizitlari noto‘g‘ri.")
    return {
        "method_type": method_type,
        "name": name,
        "details_json": json.dumps(
            details, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ),
        "recipient_name": str(body.get("recipient_name") or "").strip()[:160],
        "instructions": str(body.get("instructions") or "").strip()[:1000],
        "sort_order": max(-1000, min(1000, int(body.get("sort_order") or 0))),
        "active": 1 if body.get("active", True) else 0,
    }


@admin_router.post("/payment-methods", status_code=201)
async def admin_create_payment_method(request: Request):
    admin = _admin(request)
    payload = _payment_method_payload(await request.json())
    stamp = int(time.time())
    conn = db()
    try:
        cur = conn.execute(
            """
            INSERT INTO payment_methods(
              method_type,name,details_json,recipient_name,instructions,
              sort_order,active,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                payload["method_type"],
                payload["name"],
                payload["details_json"],
                payload["recipient_name"],
                payload["instructions"],
                payload["sort_order"],
                payload["active"],
                stamp,
                stamp,
            ),
        )
        after = conn.execute(
            "SELECT * FROM payment_methods WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        append_admin_audit(
            conn,
            admin_tg_id=int(admin["tg_id"]),
            action="payment_method.create",
            target={"kind": "payment_method", "id": cur.lastrowid},
            before={},
            after=dict(after),
            reason="To‘lov usuli yaratildi",
            request_meta=audit_request_meta(request),
        )
        conn.commit()
        return dict(after)
    finally:
        conn.close()


@admin_router.put("/payment-methods/{method_id}")
async def admin_update_payment_method(method_id: int, request: Request):
    admin = _admin(request)
    body = await request.json()
    payload = _payment_method_payload(body)
    conn = db()
    try:
        before = conn.execute(
            "SELECT * FROM payment_methods WHERE id=?", (int(method_id),)
        ).fetchone()
        if not before:
            raise HTTPException(404, "To‘lov usuli topilmadi.")
        cur = conn.execute(
            """
            UPDATE payment_methods SET
              method_type=?,name=?,details_json=?,recipient_name=?,
              instructions=?,sort_order=?,active=?,updated_at=?
            WHERE id=?
            """,
            (
                payload["method_type"],
                payload["name"],
                payload["details_json"],
                payload["recipient_name"],
                payload["instructions"],
                payload["sort_order"],
                payload["active"],
                int(time.time()),
                int(method_id),
            ),
        )
        if cur.rowcount != 1:
            raise HTTPException(404, "To‘lov usuli topilmadi.")
        after = conn.execute(
            "SELECT * FROM payment_methods WHERE id=?", (int(method_id),)
        ).fetchone()
        append_admin_audit(
            conn,
            admin_tg_id=int(admin["tg_id"]),
            action="payment_method.update",
            target={"kind": "payment_method", "id": method_id},
            before=dict(before),
            after=dict(after),
            reason=str(body.get("reason") or "To‘lov usuli yangilandi"),
            request_meta=audit_request_meta(request),
        )
        conn.commit()
        return dict(after)
    finally:
        conn.close()


def _admin_payment_row(conn, payment_id):
    row = conn.execute(
        "SELECT * FROM payment_requests WHERE id=?", (int(payment_id),)
    ).fetchone()
    if not row:
        raise HTTPException(404, "To‘lov topilmadi.")
    return row


@admin_router.get("/payments")
async def admin_payments(
    request: Request,
    status: str = "",
    service_type: str = "",
):
    _admin(request)
    filters = []
    params = []
    if status:
        if status not in ("pending", "approved", "rejected", "cancelled"):
            raise HTTPException(400, "To‘lov holati noto‘g‘ri.")
        filters.append("status=?")
        params.append(status)
    if service_type:
        if service_type not in ("subscription", "advertisement", "listing"):
            raise HTTPException(400, "Xizmat turi noto‘g‘ri.")
        filters.append("service_type=?")
        params.append(service_type)
    where = (" WHERE " + " AND ".join(filters)) if filters else ""
    conn = db()
    try:
        rows = conn.execute(
            "SELECT * FROM payment_requests"
            + where
            + " ORDER BY created_at,id",
            tuple(params),
        ).fetchall()
        return [_safe_payment(row) for row in rows]
    finally:
        conn.close()


@admin_router.get("/payments/{payment_id}")
async def admin_payment_detail(payment_id: int, request: Request):
    _admin(request)
    conn = db()
    try:
        row = _admin_payment_row(conn, payment_id)
        result = _safe_payment(row)
        result.update(
            {
                "user_id": int(row["user_id"]),
                "business_id": row["business_id"],
                "target_id": row["target_id"],
                "price_code": row["price_code"],
                "payment_method_id": row["payment_method_id"],
            }
        )
        return result
    finally:
        conn.close()


@admin_router.get("/payments/{payment_id}/receipt")
async def admin_payment_receipt(payment_id: int, request: Request):
    _admin(request)
    conn = db()
    try:
        _admin_payment_row(conn, payment_id)
        attempt = conn.execute(
            """
            SELECT * FROM payment_attempts
            WHERE payment_request_id=? ORDER BY attempt_no DESC LIMIT 1
            """,
            (int(payment_id),),
        ).fetchone()
        if not attempt:
            raise HTTPException(404, "Kvitansiya topilmadi.")
        try:
            absolute = receipt_absolute_path(
                _receipt_root(), attempt["receipt_filename"]
            )
        except ReceiptValidationError:
            raise HTTPException(404, "Kvitansiya topilmadi.")
        extension = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }.get(attempt["receipt_mime"], "bin")
        return FileResponse(
            absolute,
            media_type=attempt["receipt_mime"],
            filename=f"receipt-{payment_id}.{extension}",
            headers={"Cache-Control": "no-store, private"},
            content_disposition_type="inline",
        )
    finally:
        conn.close()


def _activate_legacy_advertisement(conn, payment, now):
    days = max(1, int(payment["quantity"] or 1))
    start_at = int(now)
    end_at = start_at + days * 86_400
    cursor = conn.execute(
        """
        UPDATE advertisements
        SET status='active',start_at=?,end_at=?,updated_at=?
        WHERE id=? AND status='payment_pending'
        """,
        (
            start_at,
            end_at,
            int(now),
            int(payment["target_id"] or 0),
        ),
    )
    if cursor.rowcount != 1:
        raise PaymentValidationError("Kutilayotgan reklama topilmadi.")
    return {"schedule_start": start_at, "schedule_end": end_at}


def _activate_hourly_advertisement(conn, payment, now):
    try:
        snapshot = json.loads(payment["target_snapshot_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PaymentValidationError(
            "Reklama to'lov jadvali buzilgan."
        ) from exc
    if not isinstance(snapshot, dict):
        raise PaymentValidationError("Reklama to'lov jadvali buzilgan.")
    ad = conn.execute(
        "SELECT * FROM advertisements WHERE id=?",
        (int(payment["target_id"] or 0),),
    ).fetchone()
    if not ad or ad["status"] != "payment_pending":
        raise PaymentValidationError("Kutilayotgan reklama topilmadi.")
    if (
        int(ad["user_id"]) != int(payment["user_id"])
        or str(ad["actor_type"]) != str(payment["actor_type"])
        or (ad["business_id"] or None) != (payment["business_id"] or None)
    ):
        raise PaymentValidationError("Reklama to'lov egasiga tegishli emas.")
    try:
        duration_days = int(snapshot.get("duration_days") or 0)
        hours_each_day = int(snapshot.get("hours_per_day") or 0)
        requested_start = int(snapshot.get("schedule_start") or 0)
        daily_all_day = bool(snapshot.get("daily_all_day"))
        daily_start = str(snapshot.get("daily_start") or "00:00")
        daily_end = str(snapshot.get("daily_end") or "00:00")
        if duration_days <= 0 or hours_each_day <= 0 or requested_start <= 0:
            raise ValueError
        actual_start = shift_schedule_start(
            requested_start_at=requested_start,
            approved_at=int(now),
            daily_all_day=daily_all_day,
            daily_start=daily_start,
        )
        actual_end = schedule_end_at(
            actual_start_at=actual_start,
            duration_days=duration_days,
            hours_each_day=hours_each_day,
            daily_all_day=daily_all_day,
        )
    except (AdPricingError, TypeError, ValueError) as exc:
        raise PaymentValidationError(
            "Reklama to'lov jadvali noto'g'ri."
        ) from exc
    cursor = conn.execute(
        """
        UPDATE advertisements
        SET status='active',start_at=?,end_at=?,daily_all_day=?,
            daily_start=?,daily_end=?,updated_at=?
        WHERE id=? AND status='payment_pending'
        """,
        (
            actual_start,
            actual_end,
            1 if daily_all_day else 0,
            daily_start,
            daily_end,
            int(now),
            int(ad["id"]),
        ),
    )
    if cursor.rowcount != 1:
        raise PaymentValidationError("Kutilayotgan reklama topilmadi.")
    return {"schedule_start": actual_start, "schedule_end": actual_end}


def _activate_approved_service(conn, payment, now):
    service_type = payment["service_type"]
    if service_type == "subscription":
        try:
            activate_paid_subscription(
                conn,
                int(payment["business_id"] or 0),
                payment["plan_code"],
                int(payment["duration_months"]),
                int(payment["id"]),
                now=now,
            )
        except SubscriptionValidationError as exc:
            raise PaymentValidationError(str(exc)) from exc
        return
    if service_type == "advertisement":
        if payment["price_code"] == "advertisement_district_hour":
            return _activate_hourly_advertisement(conn, payment, now)
        return _activate_legacy_advertisement(conn, payment, now)
    if service_type == "listing":
        cursor = conn.execute(
            """
            UPDATE listings SET status='active'
            WHERE id=? AND status='payment_pending'
            """,
            (int(payment["target_id"] or 0),),
        )
        if cursor.rowcount != 1:
            raise PaymentValidationError("Kutilayotgan e’lon topilmadi.")
        return
    raise PaymentValidationError("To‘lov xizmati qo‘llab-quvvatlanmaydi.")


async def _decision_body(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return str((body or {}).get("reason") or "").strip()


@admin_router.post("/payments/{payment_id}/approve")
async def admin_approve_payment(payment_id: int, request: Request):
    admin = _admin(request)
    reason = await _decision_body(request)
    conn = db()
    try:
        try:
            payment = approve_payment(
                conn,
                payment_id,
                int(admin["tg_id"]),
                reason,
                activator=_activate_approved_service,
                post_event=queue_payment_decision,
                audit_hook=lambda audit_conn, before, after: append_admin_audit(
                    audit_conn,
                    admin_tg_id=int(admin["tg_id"]),
                    action="payment.approve",
                    target={"kind": "payment", "id": payment_id},
                    before=before,
                    after=after,
                    reason=reason,
                    request_meta=audit_request_meta(request),
                ),
            )
        except PaymentConflict as exc:
            raise HTTPException(409, str(exc))
        except PaymentValidationError as exc:
            raise HTTPException(400, str(exc))
        result = _safe_payment(payment)
    finally:
        conn.close()
    await deliver_pending_outbox()
    return result


@admin_router.post("/payments/{payment_id}/reject")
async def admin_reject_payment(payment_id: int, request: Request):
    admin = _admin(request)
    reason = await _decision_body(request)
    conn = db()
    try:
        try:
            payment = reject_payment(
                conn,
                payment_id,
                int(admin["tg_id"]),
                reason,
                post_event=queue_payment_decision,
                audit_hook=lambda audit_conn, before, after: append_admin_audit(
                    audit_conn,
                    admin_tg_id=int(admin["tg_id"]),
                    action="payment.reject",
                    target={"kind": "payment", "id": payment_id},
                    before=before,
                    after=after,
                    reason=reason,
                    request_meta=audit_request_meta(request),
                ),
            )
        except PaymentConflict as exc:
            raise HTTPException(409, str(exc))
        except PaymentValidationError as exc:
            raise HTTPException(400, str(exc))
        result = _safe_payment(payment)
    finally:
        conn.close()
    await deliver_pending_outbox()
    return result


@admin_router.post("/payments/{payment_id}/cancel")
async def admin_cancel_payment(payment_id: int, request: Request):
    admin = _admin(request)
    reason = await _decision_body(request)
    conn = db()
    try:
        try:
            payment = cancel_approved_payment(
                conn,
                payment_id,
                int(admin["tg_id"]),
                reason,
                post_event=queue_payment_decision,
                audit_hook=lambda audit_conn, before, after: append_admin_audit(
                    audit_conn,
                    admin_tg_id=int(admin["tg_id"]),
                    action="payment.cancel",
                    target={"kind": "payment", "id": payment_id},
                    before=before,
                    after=after,
                    reason=reason,
                    request_meta=audit_request_meta(request),
                ),
            )
        except PaymentConflict as exc:
            raise HTTPException(409, str(exc))
        except PaymentValidationError as exc:
            raise HTTPException(400, str(exc))
        result = _safe_payment(payment)
    finally:
        conn.close()
    await deliver_pending_outbox()
    return result
