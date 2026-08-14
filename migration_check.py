"""Ko‘prik release bazasini zaxiralab, tekshirib va idempotent migratsiya qiladi."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import database
from backup_database import (
    backup_manifest_path,
    create_database_backup,
)


REQUIRED_OBJECTS = {
    "tables": {
        "platform_feature_flags",
        "admin_sessions",
        "payment_requests",
        "admin_audit_log",
        "account_restrictions",
        "schema_migrations",
    },
    "triggers": {
        "admin_audit_no_update",
        "admin_audit_no_delete",
    },
}


def _integrity(path):
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0] if row else "")
    finally:
        conn.close()


def _ensure_legacy_users_shape(db_path):
    """Juda eski users jadvalini hozirgi CREATE INDEX bosqichiga tayyorlaydi."""

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if not exists:
            return
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        additions = {
            "tg_id": "INTEGER",
            "username": "TEXT DEFAULT ''",
            "pass_hash": "TEXT NOT NULL DEFAULT ''",
            "role": "TEXT NOT NULL DEFAULT 'user'",
            "name": "TEXT NOT NULL DEFAULT ''",
            "phone": "TEXT DEFAULT ''",
            "region": "TEXT DEFAULT ''",
            "district": "TEXT DEFAULT ''",
            "district_key": "TEXT DEFAULT ''",
            "mahalla": "TEXT DEFAULT ''",
            "lat": "REAL",
            "lng": "REAL",
            "location_exact": "INTEGER DEFAULT 0",
            "avatar_file": "TEXT DEFAULT ''",
            "avatar_x": "REAL NOT NULL DEFAULT 50",
            "avatar_y": "REAL NOT NULL DEFAULT 50",
            "avatar_zoom": "REAL NOT NULL DEFAULT 1",
            "created_at": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(
                    f'ALTER TABLE users ADD COLUMN "{name}" {definition}'
                )
        conn.commit()
    finally:
        conn.close()


def _schema_summary(conn):
    rows = conn.execute(
        """
        SELECT type, name
        FROM sqlite_master
        WHERE type IN ('table','index','trigger')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    names = [f"{row[0]}:{row[1]}" for row in rows]
    encoded = "\n".join(names).encode("utf-8")
    return {
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "table_count": sum(item.startswith("table:") for item in names),
        "index_count": sum(item.startswith("index:") for item in names),
        "trigger_count": sum(item.startswith("trigger:") for item in names),
    }


def prepare_release_database(
    db_path,
    backup_dir,
    expected_schema,
    retention=14,
):
    """Backup muvaffaqiyatli bo‘lgandan keyingina target bazani migratsiya qiladi."""

    target = Path(db_path).expanduser().resolve(strict=False)
    if not target.is_file():
        raise FileNotFoundError("Migratsiya uchun baza topilmadi.")
    backup_path = create_database_backup(
        str(target),
        backup_dir,
        retention=retention,
    )
    if _integrity(backup_path) != "ok":
        raise RuntimeError("Backup integrity_check’dan o‘tmadi.")

    original_db_path = database.DB_PATH
    try:
        _ensure_legacy_users_shape(target)
        database.DB_PATH = str(target)
        database.init_db()
    finally:
        database.DB_PATH = original_db_path

    conn = sqlite3.connect(str(target), timeout=30)
    try:
        conn.execute(
            """
            UPDATE platform_feature_flags
            SET enabled=0, updated_by_tg_id=0, updated_at=strftime('%s','now')
            WHERE feature_code IN ('listings','stories','chat','systemization')
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations(
              version TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)",
            (str(expected_schema),),
        )
        for object_type, names in REQUIRED_OBJECTS.items():
            sqlite_type = object_type[:-1]
            for name in names:
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
                    (sqlite_type, name),
                ).fetchone():
                    raise RuntimeError(
                        f"Majburiy schema obyekti topilmadi: {sqlite_type}:{name}"
                    )
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("Migratsiyadan keyingi integrity_check xato.")
        schema = _schema_summary(conn)
    finally:
        conn.close()

    return {
        "ok": True,
        "expected_schema": str(expected_schema),
        "backup_path": str(backup_path),
        "manifest_path": str(backup_manifest_path(backup_path)),
        "integrity": "ok",
        "schema": schema,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Ko‘prik bazasini backup + migration + integrity tekshiruvi"
    )
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "platforma.db"))
    parser.add_argument(
        "--backup-dir",
        default=os.environ.get("BACKUP_DIR", "backups"),
    )
    parser.add_argument("--schema", default="v1654")
    parser.add_argument(
        "--retention",
        type=int,
        default=int(os.environ.get("BACKUP_RETENTION", "14")),
    )
    args = parser.parse_args()
    result = prepare_release_database(
        args.db,
        args.backup_dir,
        expected_schema=args.schema,
        retention=args.retention,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
