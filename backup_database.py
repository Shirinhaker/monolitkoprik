"""SQLite bazasining xavfsiz, tekshirilgan zaxira nusxasini yaratadi."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


BACKUP_PREFIX = "platforma-"
BACKUP_SUFFIX = ".sqlite3"
MANIFEST_SUFFIX = ".manifest.json"


def backup_manifest_path(backup_path):
    return Path(str(backup_path) + MANIFEST_SUFFIX)


def _backup_files(backup_dir: Path):
    return sorted(
        (
            path
            for path in backup_dir.glob(BACKUP_PREFIX + "*" + BACKUP_SUFFIX)
            if path.is_file()
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )


def prune_database_backups(backup_dir: str, retention: int = 14):
    """Eng yangi ``retention`` nusxani qoldirib, eskilarini o‘chiradi."""

    folder = Path(backup_dir)
    if not folder.exists():
        return []
    keep = max(1, int(retention))
    removed = []
    for path in _backup_files(folder)[keep:]:
        path.unlink()
        manifest = backup_manifest_path(path)
        manifest.unlink(missing_ok=True)
        removed.append(str(path))
    return removed


def create_database_backup(db_path: str, backup_dir: str, retention: int = 14):
    """SQLite online-backup API bilan butun va o‘qiladigan nusxa yaratadi."""

    source_path = Path(db_path).expanduser().resolve(strict=False)
    if not source_path.is_file():
        raise FileNotFoundError("Zaxira uchun baza topilmadi: " + str(source_path))

    target_dir = Path(backup_dir).expanduser().resolve(strict=False)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target_path = target_dir / (BACKUP_PREFIX + stamp + BACKUP_SUFFIX)

    source = sqlite3.connect(str(source_path), timeout=30)
    target = sqlite3.connect(str(target_path), timeout=30)
    try:
        source.execute("PRAGMA busy_timeout = 30000")
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError("Yaratilgan SQLite zaxirasi integrity_check’dan o‘tmadi")
    except Exception:
        target.close()
        source.close()
        target_path.unlink(missing_ok=True)
        raise
    else:
        target.close()
        source.close()

    os.chmod(target_path, 0o600)
    digest = hashlib.sha256()
    with target_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    manifest_path = backup_manifest_path(target_path)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_size": source_path.stat().st_size,
        "backup_file": target_path.name,
        "sha256": digest.hexdigest(),
        "integrity": "ok",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o600)
    prune_database_backups(str(target_dir), retention=retention)
    return str(target_path)


def main():
    parser = argparse.ArgumentParser(description="Ko‘prik SQLite bazasidan zaxira olish")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "platforma.db"))
    parser.add_argument("--dir", default=os.environ.get("BACKUP_DIR", "backups"))
    parser.add_argument(
        "--retention",
        type=int,
        default=int(os.environ.get("BACKUP_RETENTION", "14")),
    )
    args = parser.parse_args()
    created = create_database_backup(args.db, args.dir, args.retention)
    print("Zaxira yaratildi:", created)


if __name__ == "__main__":
    main()
