"""Ko‘prik istoriya domeni va media yordamchilari."""

import math
import os
import subprocess
import time


STORY_TTL_SECONDS = 24 * 60 * 60
MAX_ACTIVE_STORIES = 10
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_VIDEO_SECONDS = 60.0
MAX_CAPTION_LENGTH = 200

IMAGE_MIMES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/webm"}


class StoryValidationError(ValueError):
    """Foydalanuvchiga ko‘rsatiladigan istoriya validatsiya xatosi."""


def ensure_story_tables(conn):
    """Istoriya jadvallarini eski bazaga ma’lumot yo‘qotmasdan qo‘shadi."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS stories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_type TEXT NOT NULL CHECK(owner_type IN ('user','business')),
            owner_id INTEGER NOT NULL,
            created_by_user_id INTEGER NOT NULL,
            media_type TEXT NOT NULL CHECK(media_type IN ('image','video')),
            media_filename TEXT NOT NULL,
            thumbnail_filename TEXT DEFAULT '',
            mime_type TEXT NOT NULL,
            caption TEXT DEFAULT '',
            duration_seconds REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'processing',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            deleted_at INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS story_views(
            story_id INTEGER NOT NULL,
            viewer_user_id INTEGER NOT NULL,
            viewed_at INTEGER NOT NULL,
            PRIMARY KEY(story_id,viewer_user_id),
            FOREIGN KEY(story_id) REFERENCES stories(id) ON DELETE CASCADE,
            FOREIGN KEY(viewer_user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS story_reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL,
            reporter_user_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at INTEGER NOT NULL,
            UNIQUE(story_id,reporter_user_id),
            FOREIGN KEY(story_id) REFERENCES stories(id) ON DELETE CASCADE,
            FOREIGN KEY(reporter_user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_stories_active
            ON stories(status,expires_at,owner_type,owner_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_story_views_story
            ON story_views(story_id,viewed_at);
        CREATE INDEX IF NOT EXISTS idx_story_reports_status
            ON story_reports(status,created_at);
        """
    )


def sniff_media_type(data):
    """Fayl boshidagi signaturadan qo‘llab-quvvatlanadigan media turini topadi."""
    head = bytes(data or b"")[:32]
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video/mp4"
    return ""


def validate_story_upload(content_type, size_bytes, duration_seconds, caption):
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    clean_caption = (caption or "").strip()
    if len(clean_caption) > MAX_CAPTION_LENGTH:
        raise StoryValidationError("Istoriya matni 200 belgidan oshmasin.")
    if mime in IMAGE_MIMES:
        if size_bytes <= 0 or size_bytes > MAX_IMAGE_BYTES:
            raise StoryValidationError("Rasm hajmi 10 MB dan oshmasin.")
        return {
            "media_type": "image",
            "mime_type": "image/jpeg" if mime == "image/jpg" else mime,
            "caption": clean_caption,
            "duration_seconds": 0.0,
        }
    if mime in VIDEO_MIMES:
        if size_bytes <= 0 or size_bytes > MAX_VIDEO_BYTES:
            raise StoryValidationError("Video hajmi 100 MB dan oshmasin.")
        try:
            seconds = float(duration_seconds)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds <= 0 or seconds > MAX_VIDEO_SECONDS:
            raise StoryValidationError("Video 60 soniyadan oshmasin.")
        return {
            "media_type": "video",
            "mime_type": mime,
            "caption": clean_caption,
            "duration_seconds": seconds,
        }
    raise StoryValidationError(
        "Istoriya uchun JPG, PNG, WEBP, MP4, MOV yoki WEBM fayl tanlang."
    )


def create_story_record(
    conn,
    actor,
    media,
    media_filename,
    thumbnail_filename,
    now=None,
):
    now = int(time.time() if now is None else now)
    count = conn.execute(
        "SELECT COUNT(*) FROM stories WHERE owner_type=? AND owner_id=? "
        "AND status IN ('processing','active') AND deleted_at=0 AND expires_at>?",
        (actor["owner_type"], actor["owner_id"], now),
    ).fetchone()[0]
    if count >= MAX_ACTIVE_STORIES:
        raise StoryValidationError(
            "Bir vaqtda ko‘pi bilan 10 ta faol istoriya joylash mumkin."
        )
    cur = conn.execute(
        "INSERT INTO stories(owner_type,owner_id,created_by_user_id,media_type,"
        "media_filename,thumbnail_filename,mime_type,caption,duration_seconds,"
        "status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,'processing',?,?)",
        (
            actor["owner_type"],
            actor["owner_id"],
            actor["created_by_user_id"],
            media["media_type"],
            media_filename,
            thumbnail_filename,
            media["mime_type"],
            media["caption"],
            media["duration_seconds"],
            now,
            now + STORY_TTL_SECONDS,
        ),
    )
    conn.commit()
    return cur.lastrowid


def activate_story(conn, story_id, media_filename=None, thumbnail_filename=None):
    updates = ["status='active'"]
    values = []
    if media_filename is not None:
        updates.append("media_filename=?")
        values.append(media_filename)
    if thumbnail_filename is not None:
        updates.append("thumbnail_filename=?")
        values.append(thumbnail_filename)
    values.append(story_id)
    conn.execute(
        "UPDATE stories SET " + ",".join(updates) + " WHERE id=? AND status='processing'",
        values,
    )
    conn.commit()


def fail_story(conn, story_id):
    conn.execute(
        "UPDATE stories SET status='failed' WHERE id=? AND status='processing'",
        (story_id,),
    )
    conn.commit()


def active_story(conn, story_id, now=None):
    now = int(time.time() if now is None else now)
    return conn.execute(
        "SELECT * FROM stories WHERE id=? AND status='active' "
        "AND deleted_at=0 AND expires_at>?",
        (story_id, now),
    ).fetchone()


def list_owner_stories(conn, owner_type, owner_id, now=None, viewer_user_id=None):
    now = int(time.time() if now is None else now)
    viewer = int(viewer_user_id or 0)
    return conn.execute(
        "SELECT s.*, CASE WHEN sv.viewer_user_id IS NULL THEN 0 ELSE 1 END AS viewed "
        "FROM stories s LEFT JOIN story_views sv ON sv.story_id=s.id AND sv.viewer_user_id=? "
        "WHERE s.owner_type=? AND s.owner_id=? AND s.status='active' "
        "AND s.deleted_at=0 AND s.expires_at>? ORDER BY s.created_at,s.id",
        (viewer, owner_type, owner_id, now),
    ).fetchall()


def record_story_view(conn, story_id, viewer_user_id, now=None):
    now = int(time.time() if now is None else now)
    if not active_story(conn, story_id, now):
        raise StoryValidationError("Istoriya topilmadi yoki muddati tugagan.")
    conn.execute(
        "INSERT INTO story_views(story_id,viewer_user_id,viewed_at) VALUES(?,?,?) "
        "ON CONFLICT(story_id,viewer_user_id) DO NOTHING",
        (story_id, viewer_user_id, now),
    )
    conn.commit()


def list_story_viewers(conn, story_id):
    return [
        dict(row)
        for row in conn.execute(
            "SELECT u.id,u.name,sv.viewed_at FROM story_views sv "
            "JOIN users u ON u.id=sv.viewer_user_id "
            "WHERE sv.story_id=? ORDER BY sv.viewed_at DESC",
            (story_id,),
        ).fetchall()
    ]


def can_manage_story(conn, story_id, owner_type, owner_id):
    row = conn.execute(
        "SELECT 1 FROM stories WHERE id=? AND owner_type=? AND owner_id=?",
        (story_id, owner_type, owner_id),
    ).fetchone()
    return bool(row)


def managed_story(conn, story_id, owner_type, owner_id):
    return conn.execute(
        "SELECT * FROM stories WHERE id=? AND owner_type=? AND owner_id=? "
        "AND status='active' AND deleted_at=0",
        (story_id, owner_type, owner_id),
    ).fetchone()


def list_managed_stories(
    conn,
    owner_type,
    owner_id,
    state="all",
    now=None,
):
    now = int(time.time() if now is None else now)
    state = (state or "all").strip().lower()
    if state not in ("active", "archived", "all"):
        raise StoryValidationError("Holat active, archived yoki all bo‘lishi kerak.")
    lifecycle_filter = ""
    values = [now, owner_type, owner_id]
    if state == "active":
        lifecycle_filter = " AND s.expires_at>?"
        values.append(now)
    elif state == "archived":
        lifecycle_filter = " AND s.expires_at<=?"
        values.append(now)
    return conn.execute(
        "SELECT s.*,COUNT(sv.viewer_user_id) AS view_count,"
        "CASE WHEN s.expires_at>? THEN 'active' ELSE 'archived' END "
        "AS lifecycle_state FROM stories s "
        "LEFT JOIN story_views sv ON sv.story_id=s.id "
        "WHERE s.owner_type=? AND s.owner_id=? AND s.status='active' "
        "AND s.deleted_at=0" + lifecycle_filter +
        " GROUP BY s.id ORDER BY s.created_at DESC,s.id DESC",
        values,
    ).fetchall()


def hard_delete_story(conn, story_id):
    conn.execute("DELETE FROM stories WHERE id=?", (story_id,))
    conn.commit()


def report_story(conn, story_id, reporter_user_id, reason, now=None):
    now = int(time.time() if now is None else now)
    reason = (reason or "").strip()
    if len(reason) < 10 or len(reason) > 300:
        raise StoryValidationError("Shikoyat sababini 10–300 belgi bilan yozing.")
    if not active_story(conn, story_id, now):
        raise StoryValidationError("Istoriya topilmadi yoki muddati tugagan.")
    conn.execute(
        "INSERT INTO story_reports(story_id,reporter_user_id,reason,status,created_at) "
        "VALUES(?,?,?,'new',?) ON CONFLICT(story_id,reporter_user_id) DO UPDATE SET "
        "reason=excluded.reason,status='new',created_at=excluded.created_at",
        (story_id, reporter_user_id, reason, now),
    )
    conn.commit()


def _distance_km(lat1, lng1, lat2, lng2):
    if None in (lat1, lng1, lat2, lng2):
        return None
    try:
        lat1, lng1, lat2, lng2 = map(float, (lat1, lng1, lat2, lng2))
    except (TypeError, ValueError):
        return None
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _owner_profile(conn, owner_type, owner_id):
    if owner_type == "business":
        row = conn.execute(
            "SELECT id,name,lat,lng,logo_file FROM businesses "
            "WHERE id=? AND status='active'",
            (owner_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "name": row["name"],
            "lat": row["lat"],
            "lng": row["lng"],
            "avatar_url": row["logo_file"] or "",
        }
    row = conn.execute(
        "SELECT id,name,lat,lng,avatar_file FROM users WHERE id=?", (owner_id,)
    ).fetchone()
    if not row:
        return None
    return {
        "name": row["name"],
        "lat": row["lat"],
        "lng": row["lng"],
        "avatar_url": row["avatar_file"] or "",
    }


def _is_followed(conn, actor_type, actor_id, owner_type, owner_id):
    if actor_type == "business":
        row = conn.execute(
            "SELECT 1 FROM business_follows WHERE business_id=? "
            "AND target_kind=? AND target_id=?",
            (actor_id, owner_type, owner_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM follows WHERE follower_id=? "
            "AND target_kind=? AND target_id=?",
            (actor_id, owner_type, owner_id),
        ).fetchone()
    return bool(row)


def _story_dict(row):
    story_id = int(row["id"])
    thumb = (
        "/story-thumbnail/" + str(story_id)
        if row["thumbnail_filename"]
        else "/story-media/" + str(story_id)
    )
    return {
        "id": story_id,
        "media_type": row["media_type"],
        "media_url": "/story-media/" + str(story_id),
        "thumbnail_url": thumb,
        "caption": row["caption"] or "",
        "duration_seconds": float(row["duration_seconds"] or 0),
        "created_at": int(row["created_at"]),
        "expires_at": int(row["expires_at"]),
        "viewed": bool(row["viewed"]),
    }


def list_story_feed(
    conn,
    viewer_user_id,
    actor_type,
    actor_id,
    lat=None,
    lng=None,
    now=None,
):
    now = int(time.time() if now is None else now)
    rows = conn.execute(
        "SELECT s.*,CASE WHEN sv.viewer_user_id IS NULL THEN 0 ELSE 1 END AS viewed "
        "FROM stories s LEFT JOIN story_views sv ON sv.story_id=s.id "
        "AND sv.viewer_user_id=? WHERE s.status='active' AND s.deleted_at=0 "
        "AND s.expires_at>? ORDER BY s.created_at,s.id",
        (viewer_user_id, now),
    ).fetchall()
    groups = {}
    for row in rows:
        key = (row["owner_type"], int(row["owner_id"]))
        groups.setdefault(key, []).append(row)

    result = []
    for (owner_type, owner_id), owner_rows in groups.items():
        profile = _owner_profile(conn, owner_type, owner_id)
        if not profile:
            continue
        is_own = owner_type == actor_type and owner_id == int(actor_id)
        followed = _is_followed(conn, actor_type, actor_id, owner_type, owner_id)
        stories = [_story_dict(row) for row in owner_rows]
        unseen = any(not item["viewed"] for item in stories)
        distance = _distance_km(lat, lng, profile["lat"], profile["lng"])
        result.append(
            {
                "owner_type": owner_type,
                "owner_id": owner_id,
                "name": profile["name"],
                "avatar_url": profile["avatar_url"],
                "is_own": is_own,
                "is_followed": followed,
                "has_unseen": unseen,
                "distance_km": distance,
                "stories": stories,
                "latest_story_at": max(item["created_at"] for item in stories),
            }
        )
    result.sort(
        key=lambda item: (
            0 if item["is_own"] else 1,
            0 if item["has_unseen"] else 1,
            0 if item["is_followed"] else 1,
            item["distance_km"] if item["distance_km"] is not None else 1_000_000,
            -item["latest_story_at"],
        )
    )
    for item in result:
        item.pop("latest_story_at", None)
    return result


def story_storage_dir(upload_dir):
    base = (os.environ.get("STORY_UPLOAD_DIR") or "").strip()
    if not base:
        base = os.path.join(
            os.path.dirname(os.path.abspath(upload_dir)), "stories"
        )
    os.makedirs(base, exist_ok=True)
    return base


def write_story_bytes(folder, filename, data):
    """Bitta media faylini xavfsiz nom va atomik almashtirish bilan yozadi."""
    if not filename or os.path.basename(filename) != filename:
        raise StoryValidationError("Media fayl nomi noto‘g‘ri.")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, filename)
    temp = target + ".part"
    try:
        with open(temp, "wb") as file_obj:
            file_obj.write(data)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass
    return target


def delete_story_files(folder, media_filename, thumbnail_filename):
    """Faqat story papkasidagi aniq bazaviy fayl nomlarini o‘chiradi."""
    for filename in (media_filename, thumbnail_filename):
        if not filename or os.path.basename(filename) != filename:
            continue
        path = os.path.join(folder, filename)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def probe_video_seconds(path):
    """FFprobe orqali videoning haqiqiy davomiyligini qaytaradi."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        seconds = float(result.stdout.strip())
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as exc:
        raise StoryValidationError(
            "Video tekshirilmadi. Serverda FFmpeg sozlamasini tekshiring."
        ) from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise StoryValidationError("Video davomiyligi aniqlanmadi.")
    return seconds


def transcode_video(source_path, output_path, thumbnail_path):
    """Videoni brauzerlar uchun MP4/H.264 ga keltirib, muqova yaratadi."""
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                source_path,
                "-t",
                "60",
                "-vf",
                "scale=720:-2:force_original_aspect_ratio=decrease",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "27",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                output_path,
            ],
            capture_output=True,
            timeout=180,
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                "0",
                "-i",
                output_path,
                "-frames:v",
                "1",
                "-vf",
                "scale=480:-2",
                thumbnail_path,
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        for path in (output_path, thumbnail_path):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        raise StoryValidationError(
            "Video qayta ishlanmadi. Serverda FFmpeg sozlamasini tekshiring."
        ) from exc
