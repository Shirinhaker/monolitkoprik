"""Append-only audit log for privileged Ko‘prik admin actions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time


def ensure_admin_audit_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS admin_audit_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          admin_tg_id INTEGER NOT NULL,
          action TEXT NOT NULL,
          target_kind TEXT NOT NULL,
          target_id TEXT NOT NULL,
          before_json TEXT NOT NULL DEFAULT '{}',
          after_json TEXT NOT NULL DEFAULT '{}',
          reason TEXT NOT NULL DEFAULT '',
          ip_hash TEXT NOT NULL DEFAULT '',
          user_agent TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_audit_action_created
          ON admin_audit_log(action,created_at DESC,id DESC);
        CREATE INDEX IF NOT EXISTS idx_admin_audit_admin_created
          ON admin_audit_log(admin_tg_id,created_at DESC,id DESC);
        CREATE TRIGGER IF NOT EXISTS admin_audit_no_update
        BEFORE UPDATE ON admin_audit_log
        BEGIN SELECT RAISE(ABORT, 'admin audit is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS admin_audit_no_delete
        BEFORE DELETE ON admin_audit_log
        BEGIN SELECT RAISE(ABORT, 'admin audit is append-only'); END;
        """
    )


def _json(value):
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def append_admin_audit(
    conn,
    *,
    admin_tg_id,
    action,
    target,
    before,
    after,
    reason,
    request_meta,
    now=None,
):
    """Insert one immutable audit event without committing the caller transaction."""
    target = target or {}
    request_meta = request_meta or {}
    cursor = conn.execute(
        """
        INSERT INTO admin_audit_log(
          admin_tg_id,action,target_kind,target_id,before_json,after_json,
          reason,ip_hash,user_agent,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(admin_tg_id),
            str(action or "").strip(),
            str(target.get("kind") or "unknown").strip(),
            str(target.get("id") if target.get("id") is not None else ""),
            _json(before),
            _json(after),
            str(reason or "").strip()[:2000],
            str(request_meta.get("ip_hash") or "")[:128],
            str(request_meta.get("user_agent") or "")[:500],
            int(time.time() if now is None else now),
        ),
    )
    return int(cursor.lastrowid)


def audit_request_meta(request):
    """Return bounded metadata; the raw client IP is never persisted."""
    raw_ip = str(request.client.host if request.client else "")
    secret = os.environ.get("ADMIN_AUDIT_IP_SECRET", "")
    if len(secret) < 32:
        secret = os.environ.get("WEBHOOK_SECRET", "koprik-admin-audit")
    digest = ""
    if raw_ip:
        digest = hmac.new(
            secret.encode(), raw_ip.encode(), hashlib.sha256
        ).hexdigest()
    return {
        "ip_hash": digest,
        "user_agent": str(request.headers.get("user-agent", ""))[:500],
    }

