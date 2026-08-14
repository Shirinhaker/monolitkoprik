"""Firebase Cloud Messaging navbatini yuboruvchi worker.

Maxfiy service-account faqat FIREBASE_SERVICE_ACCOUNT_JSON yoki
FIREBASE_SERVICE_ACCOUNT_PATH environment variable orqali olinadi.
"""
import asyncio
import json
import os
import time

from database import db


def _firebase_app():
    raw = (os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip()
    path = (os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH") or "").strip()
    if not raw and not path:
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials
        try:
            return firebase_admin.get_app()
        except ValueError:
            info = json.loads(raw) if raw else None
            cred = credentials.Certificate(info if info else path)
            return firebase_admin.initialize_app(cred)
    except Exception as exc:
        print("Firebase sozlanmadi:", type(exc).__name__)
        return None


def send_pending_pushes(limit=50):
    app = _firebase_app()
    if not app:
        return 0
    from firebase_admin import messaging
    conn = db()
    rows = conn.execute(
        """SELECT po.id AS outbox_id,po.attempts,n.title,n.body,n.order_id,n.ride_id,
                  pd.id AS device_id,pd.token
           FROM push_outbox po JOIN notifications n ON n.id=po.notification_id
           JOIN push_devices pd ON pd.id=po.device_id
           WHERE po.status IN ('pending','retry') AND po.attempts<5 AND pd.enabled=1
           ORDER BY po.created_at ASC,po.id ASC LIMIT ?""", (int(limit),)
    ).fetchall()
    sent = 0
    for row in rows:
        data = {"type": "order_notification"}
        if row["order_id"]: data["order_id"] = str(row["order_id"])
        if row["ride_id"]: data["ride_id"] = str(row["ride_id"])
        try:
            message = messaging.Message(
                notification=messaging.Notification(title=row["title"], body=row["body"] or ""),
                data=data, token=row["token"],
                android=messaging.AndroidConfig(priority="high"),
                apns=messaging.APNSConfig(headers={"apns-priority": "10"}),
            )
            message_id = messaging.send(message, app=app)
            conn.execute("UPDATE push_outbox SET status='sent',attempts=attempts+1,provider_message_id=?,sent_at=?,last_error='' WHERE id=?",
                         (message_id, int(time.time()), row["outbox_id"]))
            sent += 1
        except Exception as exc:
            name = type(exc).__name__
            invalid = name in ("UnregisteredError", "SenderIdMismatchError", "InvalidArgumentError")
            conn.execute("UPDATE push_outbox SET status=?,attempts=attempts+1,last_error=? WHERE id=?",
                         ("failed" if invalid else "retry", name[:160], row["outbox_id"]))
            if invalid:
                conn.execute("UPDATE push_devices SET enabled=0,updated_at=? WHERE id=?",
                             (int(time.time()), row["device_id"]))
        conn.commit()
    conn.close()
    return sent


async def push_worker_loop():
    while True:
        try:
            await asyncio.to_thread(send_pending_pushes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print("Push worker xatosi:", type(exc).__name__)
        await asyncio.sleep(10)
