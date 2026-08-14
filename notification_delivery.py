"""Durable in-app and Telegram delivery for payment decisions."""

from __future__ import annotations

import asyncio
import json
import time


def ensure_notification_delivery_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS telegram_outbox(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          tg_id INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','sent','failed')),
          attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt_at INTEGER NOT NULL,
          sent_at INTEGER NOT NULL DEFAULT 0,
          last_error TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_telegram_outbox_due
          ON telegram_outbox(status,next_attempt_at,id);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_telegram_outbox_payment_event
          ON telegram_outbox(event_type,user_id);
        """
    )


def _decision_copy(status, reason):
    if status == "approved":
        return (
            "To‘lov tasdiqlandi",
            "Xizmatingiz faollashtirildi.",
        )
    if status == "rejected":
        return (
            "To‘lov rad etildi",
            str(reason or "Kvitansiyani tekshirib, qayta yuboring."),
        )
    return (
        "To‘lov bekor qilindi",
        str(reason or "Batafsil ma’lumot uchun administratorga murojaat qiling."),
    )


def queue_payment_decision(conn, payment, status, reason="", now=None):
    """Queue both messages inside the payment review transaction."""
    stamp = int(time.time() if now is None else now)
    payment_id = int(payment["id"])
    user_id = int(payment["user_id"])
    actor_kind = str(payment["actor_type"])
    actor_id = (
        int(payment["business_id"])
        if actor_kind == "business"
        else user_id
    )
    title, body = _decision_copy(status, reason)
    event_key = f"payment:{payment_id}:{status}"
    conn.execute(
        """
        INSERT OR IGNORE INTO notifications(
          user_id,actor_kind,actor_id,event_key,title,body,
          requires_action,action_type,is_read,created_at
        ) VALUES(?,?,?,?,?,?,0,'payment_status',0,?)
        """,
        (
            user_id,
            actor_kind,
            actor_id,
            event_key,
            title,
            body,
            stamp,
        ),
    )
    user = conn.execute(
        "SELECT tg_id FROM users WHERE id=?", (user_id,)
    ).fetchone()
    tg_id = int(user["tg_id"] or 0) if user else 0
    if tg_id > 0:
        payload = {
            "chat_id": tg_id,
            "text": title + "\n\n" + body,
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO telegram_outbox(
              user_id,tg_id,event_type,payload_json,status,attempts,
              next_attempt_at,created_at
            ) VALUES(?,?,?,?,'pending',0,?,?)
            """,
            (
                user_id,
                tg_id,
                event_key,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                stamp,
                stamp,
            ),
        )


async def send_telegram_now(tg_id, payload):
    del tg_id
    from main import tg_call

    result = await tg_call("sendMessage", payload)
    if not result or not result.get("ok"):
        raise RuntimeError("Telegram xabarni qabul qilmadi.")
    return result


async def deliver_pending_outbox(limit=25, now=None):
    from database import db

    stamp = int(time.time() if now is None else now)
    conn = db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM telegram_outbox
            WHERE status='pending' AND next_attempt_at<=?
            ORDER BY id LIMIT ?
            """,
            (stamp, max(1, min(100, int(limit)))),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
            await send_telegram_now(int(row["tg_id"]), payload)
        except Exception as exc:
            attempts = int(row["attempts"] or 0) + 1
            delays = (60, 300, 900, 3600)
            status = "failed" if attempts >= 5 else "pending"
            delay = delays[min(attempts - 1, len(delays) - 1)]
            conn = db()
            try:
                conn.execute(
                    """
                    UPDATE telegram_outbox
                    SET status=?,attempts=?,next_attempt_at=?,last_error=?
                    WHERE id=? AND status='pending'
                    """,
                    (
                        status,
                        attempts,
                        stamp + delay,
                        str(exc)[:500],
                        int(row["id"]),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            continue
        conn = db()
        try:
            conn.execute(
                """
                UPDATE telegram_outbox
                SET status='sent',attempts=attempts+1,sent_at=?,
                    last_error=''
                WHERE id=? AND status='pending'
                """,
                (stamp, int(row["id"])),
            )
            conn.commit()
        finally:
            conn.close()


async def telegram_outbox_worker():
    while True:
        try:
            await deliver_pending_outbox()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(30)
