"""Account restrictions, reactive content moderation and reports."""

from __future__ import annotations

import time


ACTOR_TYPES = {"user", "business"}
RESTRICTIONS = {"content_hidden", "account_blocked"}
CONTENT_STATUSES = {"hidden", "visible", "removed"}
CONTENT_KINDS = {
    "product", "service", "advertisement", "business", "profile",
    "listing", "story",
}
REPORT_REASONS = {"fraud", "spam", "illegal", "abuse", "other"}


def ensure_moderation_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS account_restrictions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          actor_type TEXT NOT NULL CHECK(actor_type IN ('user','business')),
          actor_id INTEGER NOT NULL,
          restriction TEXT NOT NULL
            CHECK(restriction IN ('content_hidden','account_blocked')),
          status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active','revoked')),
          reason TEXT NOT NULL,
          created_by_tg_id INTEGER NOT NULL,
          created_at INTEGER NOT NULL,
          revoked_by_tg_id INTEGER,
          revoked_reason TEXT NOT NULL DEFAULT '',
          revoked_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_account_restriction_active
          ON account_restrictions(actor_type,actor_id,restriction)
          WHERE status='active';
        CREATE INDEX IF NOT EXISTS idx_account_restrictions_lookup
          ON account_restrictions(actor_type,actor_id,status);

        CREATE TABLE IF NOT EXISTS admin_account_notes(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          actor_type TEXT NOT NULL CHECK(actor_type IN ('user','business')),
          actor_id INTEGER NOT NULL,
          note TEXT NOT NULL,
          admin_tg_id INTEGER NOT NULL,
          created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_notes_actor
          ON admin_account_notes(actor_type,actor_id,id DESC);

        CREATE TABLE IF NOT EXISTS content_moderation(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          content_kind TEXT NOT NULL,
          content_id INTEGER NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('hidden','visible','removed')),
          reason TEXT NOT NULL,
          changed_by_tg_id INTEGER NOT NULL,
          created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_content_moderation_latest
          ON content_moderation(content_kind,content_id,id DESC);

        CREATE TABLE IF NOT EXISTS moderation_reports(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          reporter_user_id INTEGER NOT NULL,
          content_kind TEXT NOT NULL,
          content_id INTEGER NOT NULL,
          reason_code TEXT NOT NULL,
          comment TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'open'
            CHECK(status IN ('open','reviewing','resolved','dismissed')),
          assigned_admin_tg_id INTEGER,
          resolution TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_moderation_reports_queue
          ON moderation_reports(status,created_at,id);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_open_report
          ON moderation_reports(reporter_user_id,content_kind,content_id)
          WHERE status IN ('open','reviewing');
        """
    )


def _now(value=None):
    return int(time.time() if value is None else value)


def _require_choice(value, choices, label):
    text = str(value or "").strip()
    if text not in choices:
        raise ValueError(label + " noto‘g‘ri.")
    return text


def _reason(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("Sabab majburiy.")
    return text[:2000]


def set_account_restriction(
    conn, actor_type, actor_id, restriction, admin_tg_id, reason, now=None
):
    actor_type = _require_choice(actor_type, ACTOR_TYPES, "Profil turi")
    restriction = _require_choice(restriction, RESTRICTIONS, "Cheklov")
    actor_id = int(actor_id)
    active = conn.execute(
        """
        SELECT * FROM account_restrictions
        WHERE actor_type=? AND actor_id=? AND restriction=? AND status='active'
        """,
        (actor_type, actor_id, restriction),
    ).fetchone()
    if active:
        return dict(active)
    cursor = conn.execute(
        """
        INSERT INTO account_restrictions(
          actor_type,actor_id,restriction,status,reason,
          created_by_tg_id,created_at
        ) VALUES(?,?,?,'active',?,?,?)
        """,
        (
            actor_type, actor_id, restriction, _reason(reason),
            int(admin_tg_id), _now(now),
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM account_restrictions WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
    )


def clear_account_restriction(
    conn, actor_type, actor_id, restriction, admin_tg_id, reason, now=None
):
    actor_type = _require_choice(actor_type, ACTOR_TYPES, "Profil turi")
    restriction = _require_choice(restriction, RESTRICTIONS, "Cheklov")
    stamp = _now(now)
    row = conn.execute(
        """
        SELECT * FROM account_restrictions
        WHERE actor_type=? AND actor_id=? AND restriction=? AND status='active'
        """,
        (actor_type, int(actor_id), restriction),
    ).fetchone()
    if not row:
        raise ValueError("Faol cheklov topilmadi.")
    conn.execute(
        """
        UPDATE account_restrictions
        SET status='revoked',revoked_by_tg_id=?,revoked_reason=?,revoked_at=?
        WHERE id=? AND status='active'
        """,
        (int(admin_tg_id), _reason(reason), stamp, int(row["id"])),
    )
    return dict(
        conn.execute(
            "SELECT * FROM account_restrictions WHERE id=?", (int(row["id"]),)
        ).fetchone()
    )


def account_restrictions(conn, actor_type, actor_id):
    return {
        str(row["restriction"])
        for row in conn.execute(
            """
            SELECT restriction FROM account_restrictions
            WHERE actor_type=? AND actor_id=? AND status='active'
            """,
            (str(actor_type), int(actor_id)),
        ).fetchall()
    }


def public_owner_allowed(conn, actor_type, actor_id):
    restrictions = account_restrictions(conn, actor_type, actor_id)
    return not restrictions.intersection({"content_hidden", "account_blocked"})


def set_content_visibility(
    conn, content_kind, content_id, status, admin_tg_id, reason, now=None
):
    content_kind = _require_choice(content_kind, CONTENT_KINDS, "Kontent turi")
    status = _require_choice(status, CONTENT_STATUSES, "Moderatsiya holati")
    cursor = conn.execute(
        """
        INSERT INTO content_moderation(
          content_kind,content_id,status,reason,changed_by_tg_id,created_at
        ) VALUES(?,?,?,?,?,?)
        """,
        (
            content_kind, int(content_id), status, _reason(reason),
            int(admin_tg_id), _now(now),
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM content_moderation WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
    )


def content_moderation_status(conn, content_kind, content_id):
    row = conn.execute(
        """
        SELECT status FROM content_moderation
        WHERE content_kind=? AND content_id=?
        ORDER BY id DESC LIMIT 1
        """,
        (str(content_kind), int(content_id)),
    ).fetchone()
    return str(row["status"]) if row else "visible"


def content_is_public(conn, content_kind, content_id):
    return content_moderation_status(
        conn, content_kind, content_id
    ) == "visible"


def add_account_note(conn, actor_type, actor_id, note, admin_tg_id, now=None):
    actor_type = _require_choice(actor_type, ACTOR_TYPES, "Profil turi")
    text = str(note or "").strip()
    if not text or len(text) > 2000:
        raise ValueError("Izoh 1–2000 belgi bo‘lishi kerak.")
    cursor = conn.execute(
        """
        INSERT INTO admin_account_notes(
          actor_type,actor_id,note,admin_tg_id,created_at
        ) VALUES(?,?,?,?,?)
        """,
        (actor_type, int(actor_id), text, int(admin_tg_id), _now(now)),
    )
    return dict(
        conn.execute(
            "SELECT * FROM admin_account_notes WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
    )
