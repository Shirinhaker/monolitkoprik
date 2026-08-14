"""
Platforma — ma'lumotlar bazasi (SQLite).

Jadval tuzilishi kelishilgan dizaynga mos:
  users         - barcha foydalanuvchilar (oddiy + biznes egalari), login/parol, Telegram bog'lanishi
  businesses    - biznes profillari (nomi, yo'nalishi, joyi, aloqa)
  specialists   - mutaxasislik ma'lumotlari (davlat ishchisi rejimi bilan)
  items         - biznes mahsulot/xizmatlari
  listings      - e'lonlar (ko'rinish turi: butun platforma / faqat sahifa mehmonlari)
  listing_media - e'lon rasm/videolari (Telegram file_id sifatida — serverga yuk tushmaydi)
  follows       - obunalar (odamga ham, biznesga ham)
  saved         - saqlanganlar
  orders        - buyurtma va navbat yozuvlari (user/business aktyorlar bo'yicha)
  debtors/qarz_tx - qarz daftari (biznes kabineti bo'limi)
  pending_regs  - ro'yxatdan o'tish kutilmoqda (kod tasdiqlangunicha)
  auth_codes    - kirishdagi tasdiqlash kodlari
"""

import os
import sqlite3

from location_keys import canonical_district_key
from payments import ensure_payment_schema
from admin_audit import ensure_admin_audit_schema
from moderation import ensure_moderation_schema

DB_PATH = os.environ.get("DB_PATH", "platforma.db")
DISTRICT_KEY_BACKFILL_MARKER = "users_district_key_backfill_v1"


def db():
    # Baza papkasi mavjudligini ta'minlaymiz (Railway volume uchun)
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id         INTEGER UNIQUE,
            username      TEXT DEFAULT '',                  -- Telegram username (@siz)
            login         TEXT UNIQUE NOT NULL,
            pass_hash     TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',     -- 'user' | 'business'
            name          TEXT NOT NULL,
            phone         TEXT DEFAULT '',
            region        TEXT DEFAULT '',                  -- viloyat/shahar
            district      TEXT DEFAULT '',                  -- tuman
            district_key  TEXT DEFAULT '',                  -- tuman uchun kanonik qidiruv kaliti
            mahalla       TEXT DEFAULT '',
            lat           REAL,                             -- foydalanuvchining bosh sahifa manzil koordinatasi
            lng           REAL,                             -- foydalanuvchining bosh sahifa manzil koordinatasi
            location_exact INTEGER DEFAULT 0,               -- xaritada/GPS bilan aniq belgilanganmi
            avatar_file   TEXT DEFAULT '',                  -- Telegram file_id
            avatar_x      REAL NOT NULL DEFAULT 50,
            avatar_y      REAL NOT NULL DEFAULT 50,
            avatar_zoom   REAL NOT NULL DEFAULT 1,
            created_at    INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS businesses(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL UNIQUE,
            name          TEXT NOT NULL,
            yon           TEXT DEFAULT '',                  -- faoliyat yo'nalishi (20 tadan biri)
            tur           TEXT DEFAULT '',                  -- faoliyat turi
            descr         TEXT DEFAULT '',
            phone         TEXT DEFAULT '',
            telegram      TEXT DEFAULT '',
            work_hours    TEXT DEFAULT '',
            address       TEXT DEFAULT '',
            lat           REAL,
            lng           REAL,
            logo_file     TEXT DEFAULT '',
            logo_x        REAL NOT NULL DEFAULT 50,
            logo_y        REAL NOT NULL DEFAULT 50,
            logo_zoom     REAL NOT NULL DEFAULT 1,
            biz_login     TEXT,                             -- biznes uchun alohida login
            biz_pass_hash TEXT,                             -- biznes uchun alohida parol
            status        TEXT DEFAULT 'active',
            map_visible   INTEGER DEFAULT 0,                -- bosh xaritada platforma ko'rsatadigan biznesmi
            rating_sum    INTEGER DEFAULT 0,                -- baholar yig'indisi
            rating_cnt    INTEGER DEFAULT 0,                -- baholar soni
            created_at    INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS specialists(
            user_id       INTEGER PRIMARY KEY,
            kasb          TEXT DEFAULT '',
            descr         TEXT DEFAULT '',
            narx          TEXT DEFAULT '',
            hudud         TEXT DEFAULT '',
            is_gov        INTEGER DEFAULT 0,                -- davlat ishchisimi
            org           TEXT DEFAULT '',                  -- tashkilot
            dept          TEXT DEFAULT '',                  -- bo'lim
            lavozim       TEXT DEFAULT '',
            work_hours    TEXT DEFAULT '',                  -- ish vaqtidagi qabul (davlat ishchisi)
            after_hours   TEXT DEFAULT '',                  -- ishdan tashqari qabul
            visible       INTEGER DEFAULT 0,                -- ko'rinaman/ko'rinmayman
            available     INTEGER DEFAULT 1,                -- bo'shman/bandman
            lat           REAL,
            lng           REAL,
            rating_sum    INTEGER DEFAULT 0,
            rating_cnt    INTEGER DEFAULT 0,
            created_at    INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS item_groups(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id   INTEGER NOT NULL,
            name          TEXT NOT NULL,                    -- guruh nomi
            kind          TEXT DEFAULT 'product',           -- 'product' | 'service'
            created_at    INTEGER NOT NULL,
            FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS items(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id   INTEGER NOT NULL,
            group_id      INTEGER,                          -- NULL bo'lsa: Guruhsiz
            name          TEXT NOT NULL,
            price         TEXT DEFAULT '',
            note          TEXT DEFAULT '',
            kind          TEXT DEFAULT 'product',           -- 'product' | 'service'
            queue_enabled INTEGER NOT NULL DEFAULT 0,       -- xizmatda onlayn/oflayn navbat ishlaydimi
            photo_file    TEXT DEFAULT '',
            created_at    INTEGER NOT NULL,
            FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS listings(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,                 -- kim joylagan
            business_id   INTEGER,                          -- biznes nomidan bo'lsa
            cat           TEXT NOT NULL,                    -- toifa (uy, moshina, ish...)
            title         TEXT NOT NULL,
            price         TEXT DEFAULT '',
            descr         TEXT DEFAULT '',
            address       TEXT DEFAULT '',
            lat           REAL,
            lng           REAL,
            visibility    TEXT DEFAULT 'all',               -- 'all' | 'own' (faqat sahifa mehmonlari)
            status        TEXT DEFAULT 'active',
            created_at    INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS listing_media(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id    INTEGER NOT NULL,
            tg_file_id    TEXT NOT NULL,                    -- Telegramda saqlanadi (bepul)
            mtype         TEXT DEFAULT 'photo',             -- 'photo' | 'video'
            pos           INTEGER DEFAULT 0,
            FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS follows(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            follower_id   INTEGER NOT NULL,                 -- kim obuna bo'ldi (user id)
            target_kind   TEXT NOT NULL,                    -- 'user' | 'business'
            target_id     INTEGER NOT NULL,
            created_at    INTEGER NOT NULL,
            UNIQUE(follower_id, target_kind, target_id),
            FOREIGN KEY(follower_id) REFERENCES users(id) ON DELETE CASCADE
        );

        -- Biznes kabinet nomidan obunalar. Oddiy foydalanuvchi obunalaridan alohida yuritiladi.
        CREATE TABLE IF NOT EXISTS business_follows(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id   INTEGER NOT NULL,
            target_kind   TEXT NOT NULL,                    -- 'user' | 'business'
            target_id     INTEGER NOT NULL,
            created_at    INTEGER NOT NULL,
            UNIQUE(business_id, target_kind, target_id),
            FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS saved(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            target_kind   TEXT NOT NULL,                    -- 'listing' | 'business'
            target_id     INTEGER NOT NULL,
            created_at    INTEGER NOT NULL,
            UNIQUE(user_id, target_kind, target_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS orders(
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_kind      TEXT NOT NULL DEFAULT 'user',      -- 'user' | 'business'
            customer_actor_id  INTEGER NOT NULL,                 -- user.id yoki businesses.id
            customer_user_id   INTEGER NOT NULL,                 -- Telegram egasi user.id
            provider_kind      TEXT NOT NULL DEFAULT 'business',  -- 'user' | 'business'
            provider_actor_id  INTEGER NOT NULL,                 -- user.id yoki businesses.id
            provider_user_id   INTEGER NOT NULL,                 -- Telegram egasi user.id
            item_id            INTEGER,
            listing_id         INTEGER,
            title              TEXT DEFAULT '',                  -- buyurtma nomi
            note               TEXT DEFAULT '',                  -- mijoz izohi
            phone              TEXT DEFAULT '',
            order_type         TEXT DEFAULT 'delivery',          -- delivery/pickup/booking
            address            TEXT DEFAULT '',                  -- yetkazib berish manzili yoki joy
            desired_time       TEXT DEFAULT '',                  -- mijoz xohlagan vaqt
            delivery_lat       REAL,                             -- yetkazib berish metkasi latitude
            delivery_lng       REAL,                             -- yetkazib berish metkasi longitude
            qty                INTEGER DEFAULT 1,
            status             TEXT DEFAULT 'new',               -- new/accepted/rejected/done/cancelled
            created_at         INTEGER NOT NULL,
            updated_at         INTEGER NOT NULL,
            provider_seen_at   INTEGER DEFAULT 0,                  -- biznes egasi ko'rgan vaqt
            customer_seen_at   INTEGER DEFAULT 0,                  -- mijoz status yangilanishini ko'rgan vaqt
            FOREIGN KEY(customer_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(provider_user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS order_items(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id    INTEGER NOT NULL,
            item_id     INTEGER,
            item_name   TEXT NOT NULL,
            price_text  TEXT DEFAULT '',
            qty         INTEGER DEFAULT 1,
            line_total  INTEGER DEFAULT 0,
            note        TEXT DEFAULT '',
            created_at  INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS order_messages(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id        INTEGER NOT NULL,                  -- qaysi buyurtmaga tegishli
            sender_kind     TEXT NOT NULL DEFAULT 'user',      -- 'user' | 'business'
            sender_actor_id INTEGER NOT NULL,                  -- user.id yoki businesses.id
            sender_user_id  INTEGER NOT NULL,                  -- Telegram egasi user.id
            text            TEXT DEFAULT '',
            media_type      TEXT DEFAULT 'text',             -- 'text' | 'photo'
            media_url       TEXT DEFAULT '',                 -- serverdagi rasm manzili
            file_name       TEXT DEFAULT '',                 -- asl fayl nomi
            reply_to_id     INTEGER,                         -- qaysi xabarga javob berilgan
            edited_at       INTEGER DEFAULT 0,                -- tahrirlangan vaqt
            deleted_at      INTEGER DEFAULT 0,                -- o'chirilgan vaqt
            is_deleted      INTEGER DEFAULT 0,                -- 1 bo'lsa xabar o'chirilgan
            created_at      INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY(sender_user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS debtors(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id   INTEGER NOT NULL,
            name          TEXT NOT NULL,
            phone         TEXT DEFAULT '',
            note          TEXT DEFAULT '',
            due           TEXT DEFAULT '',
            created_at    INTEGER NOT NULL,
            FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS qarz_tx(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            debtor_id     INTEGER NOT NULL,
            type          TEXT NOT NULL,                    -- 'debt' | 'payment'
            amount        INTEGER NOT NULL,
            date          TEXT NOT NULL,
            note          TEXT DEFAULT '',
            created_at    INTEGER NOT NULL,
            FOREIGN KEY(debtor_id) REFERENCES debtors(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS pending_regs(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id         INTEGER NOT NULL,
            role          TEXT NOT NULL,
            login         TEXT NOT NULL,
            pass_hash     TEXT NOT NULL,
            payload       TEXT NOT NULL,                    -- forma ma'lumotlari (JSON)
            code          TEXT NOT NULL,
            expires_at    INTEGER NOT NULL,
            created_at    INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS login_requests(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,                 -- kimning akkauntiga kirilmoqchi
            device_tg     INTEGER NOT NULL,                 -- kirayotgan qurilma Telegram ID
            device_name   TEXT DEFAULT '',                  -- kirayotgan qurilma nomi (ko'rsatish uchun)
            status        TEXT DEFAULT 'pending',           -- 'pending' | 'approved' | 'rejected'
            expires_at    INTEGER NOT NULL,
            created_at    INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS media_inbox(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id         INTEGER NOT NULL,                 -- kim yuborgan
            file_id       TEXT NOT NULL,                    -- Telegram file_id
            mtype         TEXT DEFAULT 'photo',
            created_at    INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages(
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id         INTEGER NOT NULL,                 -- yuboruvchining egasi user id (moslik uchun)
            receiver_id       INTEGER NOT NULL,                 -- qabul qiluvchining egasi user id (moslik uchun)
            sender_kind       TEXT DEFAULT 'user',              -- 'user' | 'business'
            sender_actor_id   INTEGER,                          -- user.id yoki businesses.id
            receiver_kind     TEXT DEFAULT 'user',              -- 'user' | 'business'
            receiver_actor_id INTEGER,                          -- user.id yoki businesses.id
            text              TEXT DEFAULT '',
            media_type        TEXT DEFAULT 'text',               -- 'text' | 'photo'
            media_url         TEXT DEFAULT '',                   -- rasm server manzili
            file_name         TEXT DEFAULT '',                   -- serverdagi fayl nomi
            reply_to_id       INTEGER,                           -- qaysi xabarga javob
            edited_at         INTEGER DEFAULT 0,                 -- tahrirlangan vaqt
            deleted_at        INTEGER DEFAULT 0,                 -- o'chirilgan vaqt
            is_deleted        INTEGER DEFAULT 0,                 -- xavfsiz o'chirish belgisi
            is_read           INTEGER DEFAULT 0,
            created_at        INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notify_filters(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            cat           TEXT NOT NULL,
            region        TEXT DEFAULT '',
            district      TEXT DEFAULT '',
            price_min     INTEGER DEFAULT 0,
            price_max     INTEGER DEFAULT 0,
            keyword       TEXT DEFAULT '',
            created_at    INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            actor_kind      TEXT NOT NULL DEFAULT 'user',
            actor_id        INTEGER NOT NULL,
            event_key       TEXT NOT NULL,
            title           TEXT NOT NULL,
            body            TEXT DEFAULT '',
            order_id        INTEGER,
            dining_order_id INTEGER,
            medical_queue_id INTEGER,
            target_staff_id INTEGER,
            target_perm     TEXT DEFAULT '',
            ride_id         INTEGER,
            requires_action INTEGER DEFAULT 0,
            action_type     TEXT DEFAULT '',
            resolved_at     INTEGER DEFAULT 0,
            is_read         INTEGER DEFAULT 0,
            created_at      INTEGER NOT NULL,
            read_at         INTEGER DEFAULT 0,
            UNIQUE(user_id, actor_kind, actor_id, event_key)
        );

        CREATE TABLE IF NOT EXISTS push_devices(
            id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,platform TEXT NOT NULL DEFAULT 'android',
            device_name TEXT DEFAULT '',app_version TEXT DEFAULT '',enabled INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,last_seen_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS push_preferences(
            user_id INTEGER NOT NULL,actor_kind TEXT NOT NULL,actor_id INTEGER NOT NULL,
            enabled INTEGER DEFAULT 1,orders_enabled INTEGER DEFAULT 1,
            updated_at INTEGER NOT NULL,PRIMARY KEY(user_id,actor_kind,actor_id)
        );
        CREATE TABLE IF NOT EXISTS push_outbox(
            id INTEGER PRIMARY KEY AUTOINCREMENT,notification_id INTEGER NOT NULL,
            device_id INTEGER NOT NULL,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,
            provider_message_id TEXT DEFAULT '',last_error TEXT DEFAULT '',
            created_at INTEGER NOT NULL,sent_at INTEGER DEFAULT 0,
            UNIQUE(notification_id,device_id)
        );

        CREATE TABLE IF NOT EXISTS drivers(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL UNIQUE,           -- haydovchi (foydalanuvchi)
            phone         TEXT DEFAULT '',
            car_model     TEXT DEFAULT '',                   -- mashina rusumi
            car_color     TEXT DEFAULT '',                   -- rangi
            car_plate     TEXT DEFAULT '',                   -- davlat raqami
            service       TEXT DEFAULT 'taxi',               -- 'taxi' | 'dostavka' | 'both'
            available     INTEGER DEFAULT 1,                 -- 1 = bo'shman, 0 = bandman
            rating_sum    INTEGER DEFAULT 0,                 -- reyting yig'indisi (keyin)
            rating_cnt    INTEGER DEFAULT 0,                 -- baholar soni (keyin)
            balance       INTEGER DEFAULT 0,                 -- hisob (keyin, to'lov uchun)
            status        TEXT DEFAULT 'active',
            created_at    INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rides(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id     INTEGER NOT NULL,                -- mijoz (foydalanuvchi)
            kind            TEXT NOT NULL DEFAULT 'taxi',    -- 'taxi' | 'dostavka'
            from_addr       TEXT DEFAULT '',
            to_addr         TEXT DEFAULT '',
            from_lat        REAL,                            -- xaritadan: boshlanish koordinatasi
            from_lng        REAL,
            to_lat          REAL,                            -- xaritadan: manzil koordinatasi
            to_lng          REAL,
            dist_km         REAL,                            -- masofa (km)
            dur_min         INTEGER,                         -- taxminiy vaqt (daqiqa)
            meter_km        REAL,                            -- jonli GPS hisoblagich: bosib o'tilgan masofa (km)
            ozim            INTEGER DEFAULT 0,               -- 1 = manzilni og'zaki aytadi
            cargo           TEXT DEFAULT '',                 -- dostavka: yuk turi
            car_type        TEXT DEFAULT '',                 -- dostavka: yengil/katta yuk
            note            TEXT DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'pending', -- pending|accepted|completed|canceled
            driver_id       INTEGER,                         -- qabul qilgan haydovchi
            src_order_id    INTEGER,                         -- #4: shu do'kon buyurtmasidan avtomatik yaratilgan bo'lsa
            created_at      INTEGER NOT NULL,
            accepted_at     INTEGER,
            FOREIGN KEY(customer_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_inbox_tg       ON media_inbox(tg_id);
        CREATE INDEX IF NOT EXISTS idx_users_tg       ON users(tg_id);
        CREATE INDEX IF NOT EXISTS idx_biz_user       ON businesses(user_id);
        CREATE INDEX IF NOT EXISTS idx_item_groups_biz ON item_groups(business_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_items_biz      ON items(business_id);
        CREATE INDEX IF NOT EXISTS idx_list_user      ON listings(user_id);
        CREATE INDEX IF NOT EXISTS idx_list_cat       ON listings(cat, status);
        CREATE INDEX IF NOT EXISTS idx_media_list     ON listing_media(listing_id);
        CREATE INDEX IF NOT EXISTS idx_follows_t      ON follows(target_kind, target_id);
        CREATE INDEX IF NOT EXISTS idx_saved_u        ON saved(user_id);
        CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_kind, customer_actor_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_orders_provider ON orders(provider_kind, provider_actor_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_orders_status   ON orders(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_notifications_actor ON notifications(user_id,actor_kind,actor_id,is_read,created_at);
        CREATE INDEX IF NOT EXISTS idx_push_outbox_status ON push_outbox(status,created_at);
        CREATE INDEX IF NOT EXISTS idx_debtors_biz    ON debtors(business_id);
        CREATE INDEX IF NOT EXISTS idx_qtx_debtor     ON qarz_tx(debtor_id);
        CREATE INDEX IF NOT EXISTS idx_drivers_user   ON drivers(user_id);
        """
    )
    _migrate(conn)
    conn.commit()
    conn.close()


def ensure_user_district_keys(conn):
    """Add, backfill once, and index the canonical key without altering labels."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    column_added = "district_key" not in columns
    if column_added:
        conn.execute("ALTER TABLE users ADD COLUMN district_key TEXT DEFAULT ''")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_meta("
        "key TEXT PRIMARY KEY,value TEXT NOT NULL)"
    )
    backfill_marker = conn.execute(
        "SELECT value FROM app_meta WHERE key=?",
        (DISTRICT_KEY_BACKFILL_MARKER,),
    ).fetchone()
    if column_added or backfill_marker is None:
        rows = conn.execute("SELECT id,district FROM users").fetchall()
        for row in rows:
            conn.execute(
                "UPDATE users SET district_key=? WHERE id=?",
                (canonical_district_key(row[1]), row[0]),
            )
        conn.execute(
            "INSERT INTO app_meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (DISTRICT_KEY_BACKFILL_MARKER, "1"),
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_district_key ON users(district_key)")


def _migrate(conn):
    """Eski bazaga yetishmayotgan ustun va jadvallarni xavfsiz qo'shadi (ma'lumot yo'qolmaydi)."""
    from feature_flags import ensure_feature_flag_schema
    from admin_auth import ensure_admin_auth_schema
    from stories import ensure_story_tables
    from subscriptions import init_subscription_schema
    from notification_delivery import ensure_notification_delivery_schema

    ensure_feature_flag_schema(conn)
    ensure_payment_schema(conn)
    ensure_admin_auth_schema(conn)
    ensure_admin_audit_schema(conn)
    ensure_moderation_schema(conn)
    ensure_story_tables(conn)
    init_subscription_schema(conn)
    ensure_notification_delivery_schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS profile_images(
        owner_kind TEXT NOT NULL, owner_id INTEGER NOT NULL, mime_type TEXT NOT NULL,
        content BLOB NOT NULL, updated_at INTEGER NOT NULL,
        PRIMARY KEY(owner_kind, owner_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,
        actor_kind TEXT NOT NULL DEFAULT 'user',actor_id INTEGER NOT NULL,
        event_key TEXT NOT NULL,title TEXT NOT NULL,body TEXT DEFAULT '',
        order_id INTEGER,ride_id INTEGER,is_read INTEGER DEFAULT 0,
        created_at INTEGER NOT NULL,read_at INTEGER DEFAULT 0,
        UNIQUE(user_id,actor_kind,actor_id,event_key))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_actor ON notifications(user_id,actor_kind,actor_id,is_read,created_at)")
    ncols = [r["name"] for r in conn.execute("PRAGMA table_info(notifications)").fetchall()]
    if "requires_action" not in ncols:
        conn.execute("ALTER TABLE notifications ADD COLUMN requires_action INTEGER DEFAULT 0")
    if "action_type" not in ncols:
        conn.execute("ALTER TABLE notifications ADD COLUMN action_type TEXT DEFAULT ''")
    if "resolved_at" not in ncols:
        conn.execute("ALTER TABLE notifications ADD COLUMN resolved_at INTEGER DEFAULT 0")
    if "dining_order_id" not in ncols:
        conn.execute("ALTER TABLE notifications ADD COLUMN dining_order_id INTEGER")
    if "medical_queue_id" not in ncols:
        conn.execute("ALTER TABLE notifications ADD COLUMN medical_queue_id INTEGER")
    if "target_staff_id" not in ncols:
        conn.execute("ALTER TABLE notifications ADD COLUMN target_staff_id INTEGER")
    if "target_perm" not in ncols:
        conn.execute("ALTER TABLE notifications ADD COLUMN target_perm TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_medical_queue ON notifications(medical_queue_id,created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS push_devices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,token TEXT NOT NULL UNIQUE,
        platform TEXT NOT NULL DEFAULT 'android',device_name TEXT DEFAULT '',app_version TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,last_seen_at INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS push_preferences(
        user_id INTEGER NOT NULL,actor_kind TEXT NOT NULL,actor_id INTEGER NOT NULL,
        enabled INTEGER DEFAULT 1,orders_enabled INTEGER DEFAULT 1,updated_at INTEGER NOT NULL,
        PRIMARY KEY(user_id,actor_kind,actor_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS push_outbox(
        id INTEGER PRIMARY KEY AUTOINCREMENT,notification_id INTEGER NOT NULL,device_id INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,provider_message_id TEXT DEFAULT '',
        last_error TEXT DEFAULT '',created_at INTEGER NOT NULL,sent_at INTEGER DEFAULT 0,
        UNIQUE(notification_id,device_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_push_outbox_status ON push_outbox(status,created_at)")
    # Mahsulot/xizmat guruhlari — v1379. CASCADE qo'ymaymiz: guruh o'chsa, tovarlar Guruhsizga o'tadi.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS item_groups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            kind TEXT DEFAULT 'product',
            created_at INTEGER NOT NULL
        )"""
    )
    _igcols = [r["name"] for r in conn.execute("PRAGMA table_info(item_groups)").fetchall()]
    if "storage_type" not in _igcols:
        conn.execute("ALTER TABLE item_groups ADD COLUMN storage_type TEXT DEFAULT 'ready_food'")
    icols = [r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
    if "group_id" not in icols:
        # Eski mahsulotlar avtomatik Guruhsiz bo'lib qolishi uchun NULL ustun qo'shamiz.
        conn.execute("ALTER TABLE items ADD COLUMN group_id INTEGER")
    if "queue_enabled" not in icols:
        conn.execute("ALTER TABLE items ADD COLUMN queue_enabled INTEGER NOT NULL DEFAULT 0")
    _icols_edu = [r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
    for _name, _sql in (
        ("course_mode", "ALTER TABLE items ADD COLUMN course_mode TEXT DEFAULT ''"),
        ("course_duration", "ALTER TABLE items ADD COLUMN course_duration TEXT DEFAULT ''"),
        ("lesson_duration", "ALTER TABLE items ADD COLUMN lesson_duration INTEGER DEFAULT 0"),
        ("age_from", "ALTER TABLE items ADD COLUMN age_from INTEGER DEFAULT 0"),
        ("age_to", "ALTER TABLE items ADD COLUMN age_to INTEGER DEFAULT 0"),
        ("course_level", "ALTER TABLE items ADD COLUMN course_level TEXT DEFAULT ''"),
        ("enrollment_status", "ALTER TABLE items ADD COLUMN enrollment_status TEXT DEFAULT 'open'"),
    ):
        if _name not in _icols_edu:
            conn.execute(_sql)
    # Ta'lim yo'nalishida mahsulot bo'lmaydi: eski yozuv va guruhlarni xizmat/kursga o'tkazamiz.
    conn.execute("UPDATE items SET kind='service' WHERE business_id IN (SELECT id FROM businesses WHERE yon='Ta''lim faoliyati')")
    conn.execute("UPDATE item_groups SET kind='service' WHERE business_id IN (SELECT id FROM businesses WHERE yon='Ta''lim faoliyati')")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_item_groups_biz ON item_groups(business_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_group ON items(group_id)")

    # Taxi haydovchilari — v1383
    conn.execute(
        """CREATE TABLE IF NOT EXISTS drivers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            phone TEXT DEFAULT '',
            car_model TEXT DEFAULT '',
            car_color TEXT DEFAULT '',
            car_plate TEXT DEFAULT '',
            service TEXT DEFAULT 'taxi',
            available INTEGER DEFAULT 1,
            rating_sum INTEGER DEFAULT 0,
            rating_cnt INTEGER DEFAULT 0,
            balance INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at INTEGER NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drivers_user ON drivers(user_id)")

    # Taxi zakazlari — v1384
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rides(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'taxi',
            from_addr TEXT DEFAULT '',
            to_addr TEXT DEFAULT '',
            from_lat REAL,
            from_lng REAL,
            to_lat REAL,
            to_lng REAL,
            dist_km REAL,
            dur_min INTEGER,
            meter_km REAL,
            ozim INTEGER DEFAULT 0,
            cargo TEXT DEFAULT '',
            car_type TEXT DEFAULT '',
            note TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            driver_id INTEGER,
            created_at INTEGER NOT NULL,
            accepted_at INTEGER
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rides_status ON rides(status, kind, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rides_customer ON rides(customer_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rides_driver ON rides(driver_id, status)")
    # rides: xaritadan koordinata ustunlari — v1386 (eski jadvalga xavfsiz qo'shamiz)
    rcols = [r["name"] for r in conn.execute("PRAGMA table_info(rides)").fetchall()]
    for _c, _t in (("from_lat", "REAL"), ("from_lng", "REAL"), ("to_lat", "REAL"),
                   ("to_lng", "REAL"), ("dist_km", "REAL"), ("dur_min", "INTEGER"),
                   ("meter_km", "REAL")):
        if _c not in rcols:
            conn.execute("ALTER TABLE rides ADD COLUMN %s %s" % (_c, _t))

    # users.username ustuni bormi?
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "username" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN username TEXT DEFAULT ''")
    if "lat" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN lat REAL")
    if "lng" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN lng REAL")
    if "avatar_x" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_x REAL NOT NULL DEFAULT 50")
    if "avatar_y" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_y REAL NOT NULL DEFAULT 50")
    if "avatar_zoom" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_zoom REAL NOT NULL DEFAULT 1")
    ensure_user_district_keys(conn)
    # v1613: district-offer hot path counts by kind/public visibility and
    # fetches one deterministic row by id without materializing full catalogs.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_business_kind_id "
        "ON items(business_id,kind,id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_listings_business_public_id "
        "ON listings(business_id,status,visibility,id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_listing_media_offer_photo "
        "ON listing_media(listing_id,mtype,pos,id)"
    )
    # v1479: kelajakdagi Android/iOS ilovasi uchun Telegramdan mustaqil sessiyalar.
    # Bazada tokenning o'zi emas, SHA-256 xeshi saqlanadi.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mobile_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            device_name TEXT DEFAULT '',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            last_used_at INTEGER DEFAULT 0,
            revoked_at INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_sessions_user ON mobile_sessions(user_id, revoked_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_sessions_expiry ON mobile_sessions(expires_at, revoked_at)")
    # v1480: telefon orqali kirish uchun bir martalik tasdiqlash kodlari.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mobile_verification_codes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            phone TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            purpose TEXT NOT NULL DEFAULT 'login',
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 5,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            verified_at INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_codes_phone ON mobile_verification_codes(phone, purpose, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_codes_expiry ON mobile_verification_codes(expires_at, verified_at)")
    # v1481: yangi foydalanuvchini telefon kodi tasdiqlanguncha vaqtincha saqlash.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mobile_pending_registrations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            yon TEXT DEFAULT '',
            address TEXT DEFAULT '',
            code_hash TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 5,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            verified_at INTEGER DEFAULT 0
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_pending_phone ON mobile_pending_registrations(phone, created_at)")
    # v1639: sayt uchun Telegram deep-link orqali bir martalik tasdiqlash.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS telegram_pending_registrations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload_json TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            verified_at INTEGER DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS telegram_auth_challenges(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purpose TEXT NOT NULL,
            user_id INTEGER DEFAULT 0,
            pending_registration_id INTEGER DEFAULT 0,
            start_token_hash TEXT NOT NULL UNIQUE,
            tg_id INTEGER DEFAULT 0,
            code_hash TEXT DEFAULT '',
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 5,
            created_at INTEGER NOT NULL,
            start_expires_at INTEGER NOT NULL,
            code_sent_at INTEGER DEFAULT 0,
            code_expires_at INTEGER DEFAULT 0,
            verified_at INTEGER DEFAULT 0,
            invalidated_at INTEGER DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_tg_auth_start
           ON telegram_auth_challenges(start_token_hash, start_expires_at)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_tg_auth_user
           ON telegram_auth_challenges(user_id, purpose, created_at)"""
    )
    # login_requests jadvali bormi? (CREATE TABLE IF NOT EXISTS yuqorida bor, lekin ishonch uchun)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS login_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            device_tg INTEGER NOT NULL,
            device_name TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )"""
    )
    # Chat xabarlari jadvali — user va biznes aktyorlari ajratilgan holda
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id         INTEGER NOT NULL,      -- yuboruvchi egasi user id (moslik uchun)
            receiver_id       INTEGER NOT NULL,      -- qabul qiluvchi egasi user id (moslik uchun)
            sender_kind       TEXT DEFAULT 'user',   -- 'user' | 'business'
            sender_actor_id   INTEGER,               -- user.id yoki businesses.id
            receiver_kind     TEXT DEFAULT 'user',   -- 'user' | 'business'
            receiver_actor_id INTEGER,               -- user.id yoki businesses.id
            text              TEXT DEFAULT '',
            media_type        TEXT DEFAULT 'text',   -- 'text' | 'photo'
            media_url         TEXT DEFAULT '',       -- rasm server manzili
            file_name         TEXT DEFAULT '',       -- serverdagi fayl nomi
            reply_to_id       INTEGER,               -- qaysi xabarga javob
            edited_at         INTEGER DEFAULT 0,     -- tahrirlangan vaqt
            deleted_at        INTEGER DEFAULT 0,     -- o'chirilgan vaqt
            is_deleted        INTEGER DEFAULT 0,     -- xavfsiz o'chirish belgisi
            is_read           INTEGER DEFAULT 0,     -- qabul qiluvchi aktyor o'qiganmi
            created_at        INTEGER NOT NULL
        )"""
    )
    mcols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
    if "sender_kind" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN sender_kind TEXT DEFAULT 'user'")
    if "sender_actor_id" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN sender_actor_id INTEGER")
    if "receiver_kind" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN receiver_kind TEXT DEFAULT 'user'")
    if "receiver_actor_id" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN receiver_actor_id INTEGER")
    if "media_type" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN media_type TEXT DEFAULT 'text'")
    if "media_url" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN media_url TEXT DEFAULT ''")
    if "file_name" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN file_name TEXT DEFAULT ''")
    if "reply_to_id" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN reply_to_id INTEGER")
    if "edited_at" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN edited_at INTEGER DEFAULT 0")
    if "deleted_at" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN deleted_at INTEGER DEFAULT 0")
    if "is_deleted" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN is_deleted INTEGER DEFAULT 0")
    # Eski xabarlar user -> user deb belgilab qo'yiladi, ma'lumot yo'qolmaydi.
    conn.execute("UPDATE messages SET sender_kind='user' WHERE sender_kind IS NULL OR sender_kind=''")
    conn.execute("UPDATE messages SET receiver_kind='user' WHERE receiver_kind IS NULL OR receiver_kind=''")
    conn.execute("UPDATE messages SET sender_actor_id=sender_id WHERE sender_actor_id IS NULL")
    conn.execute("UPDATE messages SET receiver_actor_id=receiver_id WHERE receiver_actor_id IS NULL")
    conn.execute("UPDATE messages SET media_type='text' WHERE media_type IS NULL OR media_type=''")
    conn.execute("UPDATE messages SET media_url='' WHERE media_url IS NULL")
    conn.execute("UPDATE messages SET file_name='' WHERE file_name IS NULL")
    conn.execute("UPDATE messages SET edited_at=0 WHERE edited_at IS NULL")
    conn.execute("UPDATE messages SET deleted_at=0 WHERE deleted_at IS NULL")
    conn.execute("UPDATE messages SET is_deleted=0 WHERE is_deleted IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_pair ON messages(sender_id, receiver_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_actor_pair ON messages(sender_kind, sender_actor_id, receiver_kind, receiver_actor_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_receiver_actor ON messages(receiver_kind, receiver_actor_id, is_read)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_reply ON messages(reply_to_id)")
    # Buyurtmalar / navbatlar — user va biznes aktyorlari ajratilgan holda
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_kind      TEXT NOT NULL DEFAULT 'user',
            customer_actor_id  INTEGER NOT NULL,
            customer_user_id   INTEGER NOT NULL,
            provider_kind      TEXT NOT NULL DEFAULT 'business',
            provider_actor_id  INTEGER NOT NULL,
            provider_user_id   INTEGER NOT NULL,
            item_id            INTEGER,
            listing_id         INTEGER,
            title              TEXT DEFAULT '',
            note               TEXT DEFAULT '',
            phone              TEXT DEFAULT '',
            order_type         TEXT DEFAULT 'delivery',
            address            TEXT DEFAULT '',
            desired_time       TEXT DEFAULT '',
            delivery_lat       REAL,
            delivery_lng       REAL,
            qty                INTEGER DEFAULT 1,
            status             TEXT DEFAULT 'new',
            created_at         INTEGER NOT NULL,
            updated_at         INTEGER NOT NULL,
            provider_seen_at   INTEGER DEFAULT 0,
            customer_seen_at   INTEGER DEFAULT 0
        )"""
    )
    ocols = [r["name"] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
    if "order_type" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN order_type TEXT DEFAULT 'delivery'")
    if "address" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN address TEXT DEFAULT ''")
    if "desired_time" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN desired_time TEXT DEFAULT ''")
    if "delivery_lat" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN delivery_lat REAL")
    if "delivery_lng" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN delivery_lng REAL")
    if "provider_seen_at" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN provider_seen_at INTEGER DEFAULT 0")
    if "customer_seen_at" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN customer_seen_at INTEGER DEFAULT 0")
    if "order_category" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN order_category TEXT DEFAULT ''")
    if "pay_type" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN pay_type TEXT DEFAULT ''")
    if "debtor_id" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN debtor_id INTEGER")
    if "qarz_tx_id" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN qarz_tx_id INTEGER")
    # v1483: to'lov bo'yicha muammoli buyurtmalar alohida yuritiladi.
    for _c, _t in (
        ("problem_open", "INTEGER DEFAULT 0"),
        ("problem_reason", "TEXT DEFAULT ''"),
        ("problem_note", "TEXT DEFAULT ''"),
        ("problem_solution", "TEXT DEFAULT ''"),
        ("problem_opened_at", "INTEGER DEFAULT 0"),
        ("problem_resolved_at", "INTEGER DEFAULT 0"),
        ("seller_completed_at", "INTEGER DEFAULT 0"),
        ("customer_received_at", "INTEGER DEFAULT 0"),
    ):
        if _c not in ocols:
            conn.execute("ALTER TABLE orders ADD COLUMN %s %s" % (_c, _t))
    conn.execute("UPDATE orders SET problem_open=0 WHERE problem_open IS NULL")
    conn.execute("UPDATE orders SET order_type='delivery' WHERE order_type IS NULL OR order_type=''")
    # v1544: eski buyurtmalarni mahsulot va xizmat bo'limlariga bir marta ajratamiz.
    conn.execute("UPDATE orders SET order_category='service' WHERE COALESCE(order_category,'')='' AND order_type='booking'")
    conn.execute("""UPDATE orders SET order_category='service'
                    WHERE COALESCE(order_category,'')='' AND provider_kind='user'
                      AND NOT EXISTS(SELECT 1 FROM order_items oi WHERE oi.order_id=orders.id)""")
    conn.execute("""UPDATE orders SET order_category='service'
                    WHERE COALESCE(order_category,'')=''
                      AND EXISTS(SELECT 1 FROM order_items oi JOIN items i ON i.id=oi.item_id
                                 WHERE oi.order_id=orders.id AND LOWER(COALESCE(i.kind,''))='service')
                      AND NOT EXISTS(SELECT 1 FROM order_items oi JOIN items i ON i.id=oi.item_id
                                     WHERE oi.order_id=orders.id AND LOWER(COALESCE(i.kind,'product'))<>'service')""")
    conn.execute("UPDATE orders SET order_category='product' WHERE COALESCE(order_category,'')='' ")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_kind, customer_actor_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_provider ON orders(provider_kind, provider_actor_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_provider_seen ON orders(provider_kind, provider_actor_id, provider_seen_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_customer_seen ON orders(customer_kind, customer_actor_id, customer_seen_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_problem ON orders(problem_open, updated_at)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS order_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id    INTEGER NOT NULL,
            item_id     INTEGER,
            item_name   TEXT NOT NULL,
            price_text  TEXT DEFAULT '',
            qty         INTEGER DEFAULT 1,
            line_total  INTEGER DEFAULT 0,
            note        TEXT DEFAULT '',
            created_at  INTEGER NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_order_items_item ON order_items(item_id)")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS order_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id        INTEGER NOT NULL,
            sender_kind     TEXT NOT NULL DEFAULT 'user',
            sender_actor_id INTEGER NOT NULL,
            sender_user_id  INTEGER NOT NULL,
            text            TEXT DEFAULT '',
            media_type      TEXT DEFAULT 'text',
            media_url       TEXT DEFAULT '',
            file_name       TEXT DEFAULT '',
            reply_to_id     INTEGER,
            edited_at       INTEGER DEFAULT 0,
            deleted_at      INTEGER DEFAULT 0,
            is_deleted      INTEGER DEFAULT 0,
            created_at      INTEGER NOT NULL
        )"""
    )
    omcols = [r["name"] for r in conn.execute("PRAGMA table_info(order_messages)").fetchall()]
    if "media_type" not in omcols:
        conn.execute("ALTER TABLE order_messages ADD COLUMN media_type TEXT DEFAULT 'text'")
    if "media_url" not in omcols:
        conn.execute("ALTER TABLE order_messages ADD COLUMN media_url TEXT DEFAULT ''")
    if "file_name" not in omcols:
        conn.execute("ALTER TABLE order_messages ADD COLUMN file_name TEXT DEFAULT ''")
    if "reply_to_id" not in omcols:
        conn.execute("ALTER TABLE order_messages ADD COLUMN reply_to_id INTEGER")
    if "edited_at" not in omcols:
        conn.execute("ALTER TABLE order_messages ADD COLUMN edited_at INTEGER DEFAULT 0")
    if "deleted_at" not in omcols:
        conn.execute("ALTER TABLE order_messages ADD COLUMN deleted_at INTEGER DEFAULT 0")
    if "is_deleted" not in omcols:
        conn.execute("ALTER TABLE order_messages ADD COLUMN is_deleted INTEGER DEFAULT 0")
    conn.execute("UPDATE order_messages SET media_type='text' WHERE media_type IS NULL OR media_type=''")
    conn.execute("UPDATE order_messages SET edited_at=0 WHERE edited_at IS NULL")
    conn.execute("UPDATE order_messages SET deleted_at=0 WHERE deleted_at IS NULL")
    conn.execute("UPDATE order_messages SET is_deleted=0 WHERE is_deleted IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_order_messages_order ON order_messages(order_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_order_messages_sender ON order_messages(sender_kind, sender_actor_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_order_messages_reply ON order_messages(reply_to_id)")
    # Bildirishnoma filtrlari — foydalanuvchi qiziqishlari (tur+hudud+narx+kalit so'z)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS notify_filters(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            cat       TEXT NOT NULL,            -- e'lon turi (uy/ish/moshina/hayvon/texnika/boshqa)
            region    TEXT DEFAULT '',          -- viloyat ('' = istalgan)
            district  TEXT DEFAULT '',          -- tuman ('' = istalgan)
            price_min INTEGER DEFAULT 0,        -- 0 = chegara yo'q
            price_max INTEGER DEFAULT 0,        -- 0 = chegara yo'q
            keyword   TEXT DEFAULT '',          -- kalit so'z ('' = istalgan)
            created_at INTEGER NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nf_cat ON notify_filters(cat)")
    # businesses uchun alohida login/parol ustunlari
    bcols = [r["name"] for r in conn.execute("PRAGMA table_info(businesses)").fetchall()]
    if "biz_login" not in bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN biz_login TEXT")
    if "biz_pass_hash" not in bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN biz_pass_hash TEXT")
    if "map_visible" not in bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN map_visible INTEGER DEFAULT 0")
    if "logo_x" not in bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN logo_x REAL NOT NULL DEFAULT 50")
    if "logo_y" not in bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN logo_y REAL NOT NULL DEFAULT 50")
    if "logo_zoom" not in bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN logo_zoom REAL NOT NULL DEFAULT 1")
    if "username" not in bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN username TEXT DEFAULT ''")

    # --- v1395: To'liq matnli qidiruv (FTS5) — nom/sarlavha alohida ustun ---
    # Har tur uchun: 'name' ustuni (asosiy nom/sarlavha) + 'body' ustuni (qolgan matn).
    # bm25 tartiblashda nomga ko'proq og'irlik beriladi (api.py). Indeks avtomatik sinxron.
    def _canon_expr(inner):
        expr = "LOWER(" + inner + ")"
        for a in ("'", "\u2019", "\u2018", "`", "\u02bb", "\u02bc"):
            expr = "REPLACE(" + expr + ", '" + a.replace("'", "''") + "', '')"
        return expr

    def _concat(prefix, fields):
        return " || ' ' || ".join("COALESCE(" + prefix + f + ",'')" for f in fields)

    def _setup_fts(table, title_field, body_fields, tag, id_col="id"):
        fts = table + "_fts"
        # Eski sxema (bitta 'txt' ustun) bo'lsa qayta quramiz (indeks manbadan tiklanadi)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(" + fts + ")").fetchall()]
        if cols and cols != ["name", "body"]:
            conn.execute("DROP TABLE IF EXISTS " + fts)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS " + fts + " USING fts5(name, body)")
        for suf in ("ai", "ad", "au"):
            conn.execute("DROP TRIGGER IF EXISTS " + tag + "_fts_" + suf)
        tval = lambda pfx: _canon_expr("COALESCE(" + pfx + title_field + ",'')")
        bval = lambda pfx: _canon_expr(_concat(pfx, body_fields))
        conn.execute("CREATE TRIGGER " + tag + "_fts_ai AFTER INSERT ON " + table + " BEGIN "
                     "INSERT INTO " + fts + "(rowid, name, body) VALUES(new." + id_col + ", "
                     + tval("new.") + ", " + bval("new.") + "); END")
        conn.execute("CREATE TRIGGER " + tag + "_fts_ad AFTER DELETE ON " + table + " BEGIN "
                     "DELETE FROM " + fts + " WHERE rowid = old." + id_col + "; END")
        conn.execute("CREATE TRIGGER " + tag + "_fts_au AFTER UPDATE ON " + table + " BEGIN "
                     "DELETE FROM " + fts + " WHERE rowid = old." + id_col + "; "
                     "INSERT INTO " + fts + "(rowid, name, body) VALUES(new." + id_col + ", "
                     + tval("new.") + ", " + bval("new.") + "); END")
        if conn.execute("SELECT COUNT(*) FROM " + fts).fetchone()[0] == 0:
            conn.execute("INSERT INTO " + fts + "(rowid, name, body) SELECT " + id_col + ", "
                         + tval("") + ", " + bval("") + " FROM " + table)

    _setup_fts("businesses", "name", ["yon", "tur", "descr", "address", "phone", "telegram", "work_hours", "username"], "biz")
    _setup_fts("listings", "title", ["cat", "price", "descr", "address"], "lst")

    # --- v1397: Mutaxassislar (specialists) uchun FTS — mutaxassis + foydalanuvchi maydonlari ---
    # 'name' = kasb + foydalanuvchi ismi (10x og'irlik); 'body' = tavsif/narx/hudud/tashkilot/
    # lavozim + foydalanuvchi viloyat/tuman/mahalla. PK = user_id. Foydalanuvchi maydonlari
    # mutaxassis yozilganda qo'shiladi (triggerlar faqat mutaxassis yozuviga ta'sir qiladi).
    scols = [r["name"] for r in conn.execute("PRAGMA table_info(specialists_fts)").fetchall()]
    if scols and scols != ["name", "body"]:
        conn.execute("DROP TABLE IF EXISTS specialists_fts")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS specialists_fts USING fts5(name, body)")
    for suf in ("ai", "ad", "au"):
        conn.execute("DROP TRIGGER IF EXISTS spec_fts_" + suf)
    conn.execute("DROP TRIGGER IF EXISTS spec_fts_user_au")

    _sp_name_new = _canon_expr("COALESCE(new.kasb,'') || ' ' || COALESCE(u.name,'')")
    _sp_body_new = _canon_expr(
        "COALESCE(new.descr,'') || ' ' || COALESCE(new.narx,'') || ' ' || COALESCE(new.hudud,'') || ' ' || "
        "COALESCE(new.org,'') || ' ' || COALESCE(new.dept,'') || ' ' || COALESCE(new.lavozim,'') || ' ' || "
        "COALESCE(u.region,'') || ' ' || COALESCE(u.district,'') || ' ' || COALESCE(u.mahalla,'')")
    conn.execute(
        "CREATE TRIGGER spec_fts_ai AFTER INSERT ON specialists BEGIN "
        "INSERT INTO specialists_fts(rowid, name, body) "
        "SELECT new.user_id, " + _sp_name_new + ", " + _sp_body_new + " "
        "FROM users u WHERE u.id = new.user_id; END")
    conn.execute(
        "CREATE TRIGGER spec_fts_ad AFTER DELETE ON specialists BEGIN "
        "DELETE FROM specialists_fts WHERE rowid = old.user_id; END")
    conn.execute(
        "CREATE TRIGGER spec_fts_au AFTER UPDATE ON specialists BEGIN "
        "DELETE FROM specialists_fts WHERE rowid = old.user_id; "
        "INSERT INTO specialists_fts(rowid, name, body) "
        "SELECT new.user_id, " + _sp_name_new + ", " + _sp_body_new + " "
        "FROM users u WHERE u.id = new.user_id; END")
    # Foydalanuvchi ismi yoki hududi o'zgarsa mutaxassis indeksini ham darhol yangilaymiz.
    _sp_name_user = _canon_expr("COALESCE(s.kasb,'') || ' ' || COALESCE(new.name,'')")
    _sp_body_user = _canon_expr(
        "COALESCE(s.descr,'') || ' ' || COALESCE(s.narx,'') || ' ' || COALESCE(s.hudud,'') || ' ' || "
        "COALESCE(s.org,'') || ' ' || COALESCE(s.dept,'') || ' ' || COALESCE(s.lavozim,'') || ' ' || "
        "COALESCE(new.region,'') || ' ' || COALESCE(new.district,'') || ' ' || COALESCE(new.mahalla,'')")
    conn.execute(
        "CREATE TRIGGER spec_fts_user_au AFTER UPDATE OF name, region, district, mahalla ON users BEGIN "
        "DELETE FROM specialists_fts WHERE rowid = new.id; "
        "INSERT INTO specialists_fts(rowid, name, body) "
        "SELECT s.user_id, " + _sp_name_user + ", " + _sp_body_user + " "
        "FROM specialists s WHERE s.user_id = new.id; END")
    # --- v1401: O'lchov birliklari — items.unit va order_items.unit (eski bazaga xavfsiz) ---
    _icols = [r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
    if "unit" not in _icols:
        conn.execute("ALTER TABLE items ADD COLUMN unit TEXT DEFAULT 'dona'")
    _oicols = [r["name"] for r in conn.execute("PRAGMA table_info(order_items)").fetchall()]
    if "unit" not in _oicols:
        conn.execute("ALTER TABLE order_items ADD COLUMN unit TEXT DEFAULT ''")

    # --- v1406: Ombor — mahsulotda qoldiq + kirim-chiqim tarixi ---
    _icols2 = [r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
    if "track_stock" not in _icols2:
        conn.execute("ALTER TABLE items ADD COLUMN track_stock INTEGER DEFAULT 0")
    if "stock_type" not in _icols2:
        conn.execute("ALTER TABLE items ADD COLUMN stock_type TEXT DEFAULT 'ready_food'")
    if "stock_qty" not in _icols2:
        conn.execute("ALTER TABLE items ADD COLUMN stock_qty REAL DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stock_moves("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "item_id INTEGER NOT NULL, "
        "delta REAL NOT NULL, "            # + kirim, - chiqim
        "reason TEXT DEFAULT '', "         # kirim | chiqim | sotuv | tuzatish
        "note TEXT DEFAULT '', "
        "order_id INTEGER, "               # sotuv bo'lsa — buyurtma raqami
        "user_id INTEGER, "                # kim qildi
        "created_at INTEGER NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_moves_item ON stock_moves(business_id, item_id, created_at)")

    # --- v1410: Tannarx — mahsulotda oxirgi tannarx, kirimda tannarx tarixi ---
    _icols3 = [r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
    if "cost_price" not in _icols3:
        conn.execute("ALTER TABLE items ADD COLUMN cost_price INTEGER DEFAULT 0")
    if "min_qty" not in _icols3:
        conn.execute("ALTER TABLE items ADD COLUMN min_qty REAL DEFAULT 0")
    _smcols = [r["name"] for r in conn.execute("PRAGMA table_info(stock_moves)").fetchall()]
    if "cost" not in _smcols:
        conn.execute("ALTER TABLE stock_moves ADD COLUMN cost INTEGER DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS production_batches("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,ready_item_id INTEGER NOT NULL,"
        "qty REAL NOT NULL,total_cost INTEGER DEFAULT 0,unit_cost INTEGER DEFAULT 0,note TEXT DEFAULT '',user_id INTEGER,created_at INTEGER NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS production_inputs("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,batch_id INTEGER NOT NULL,item_id INTEGER NOT NULL,qty REAL NOT NULL,"
        "unit_cost INTEGER DEFAULT 0,total_cost INTEGER DEFAULT 0)")
    _pbcols = [r["name"] for r in conn.execute("PRAGMA table_info(production_batches)").fetchall()]
    if "total_cost" not in _pbcols:
        conn.execute("ALTER TABLE production_batches ADD COLUMN total_cost INTEGER DEFAULT 0")
    if "unit_cost" not in _pbcols:
        conn.execute("ALTER TABLE production_batches ADD COLUMN unit_cost INTEGER DEFAULT 0")
    _picols = [r["name"] for r in conn.execute("PRAGMA table_info(production_inputs)").fetchall()]
    if "unit_cost" not in _picols:
        conn.execute("ALTER TABLE production_inputs ADD COLUMN unit_cost INTEGER DEFAULT 0")
    if "total_cost" not in _picols:
        conn.execute("ALTER TABLE production_inputs ADD COLUMN total_cost INTEGER DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stock_batches("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,item_id INTEGER NOT NULL,"
        "qty_in REAL NOT NULL,qty_remaining REAL NOT NULL,unit_cost INTEGER NOT NULL DEFAULT 0,"
        "source_move_id INTEGER,created_at INTEGER NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stock_batch_consumptions("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,batch_id INTEGER NOT NULL,item_id INTEGER NOT NULL,"
        "qty REAL NOT NULL,unit_cost INTEGER NOT NULL DEFAULT 0,total_cost INTEGER NOT NULL DEFAULT 0,"
        "source_type TEXT DEFAULT '',source_id INTEGER,created_at INTEGER NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_batches_fifo ON stock_batches(business_id,item_id,created_at,id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_consumption_source ON stock_batch_consumptions(source_type,source_id,id)")
    if "fifo_initialized" not in _icols3:
        conn.execute("ALTER TABLE items ADD COLUMN fifo_initialized INTEGER DEFAULT 0")
    import time as _time
    _fifo_now = int(_time.time())
    for _fi in conn.execute("SELECT id,business_id,stock_qty,cost_price FROM items WHERE COALESCE(fifo_initialized,0)=0").fetchall():
        _fq = float(_fi["stock_qty"] or 0)
        if _fq > 0:
            conn.execute("INSERT INTO stock_batches(business_id,item_id,qty_in,qty_remaining,unit_cost,source_move_id,created_at) VALUES(?,?,?,?,?,NULL,?)",
                         (_fi["business_id"], _fi["id"], _fq, _fq, int(_fi["cost_price"] or 0), _fifo_now))
        conn.execute("UPDATE items SET fifo_initialized=1 WHERE id=?", (_fi["id"],))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_production_batches_biz ON production_batches(business_id,created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_production_inputs_batch ON production_inputs(batch_id,id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS item_recipes("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,ready_item_id INTEGER NOT NULL,"
        "ingredient_item_id INTEGER NOT NULL,qty_per_unit REAL NOT NULL,updated_at INTEGER NOT NULL,"
        "UNIQUE(business_id,ready_item_id,ingredient_item_id))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_item_recipes_ready ON item_recipes(business_id,ready_item_id,id)")

    # --- v1408: KASSA — savdo daftari ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sales("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "source TEXT DEFAULT 'manual', "   # manual (qo'lda) | order (buyurtmadan)
        "order_id INTEGER, "
        "item_id INTEGER, "
        "item_name TEXT DEFAULT '', "
        "qty REAL DEFAULT 1, "
        "unit TEXT DEFAULT '', "
        "price INTEGER DEFAULT 0, "        # birlik narxi (so'm)
        "total INTEGER DEFAULT 0, "        # jami (so'm)
        "pay_type TEXT DEFAULT '', "       # naqd | karta | qarz | '' (buyurtma)
        "debtor_id INTEGER, "
        "qarz_tx_id INTEGER, "             # qarz bo'lsa — qarz daftaridagi yozuv
        "note TEXT DEFAULT '', "
        "user_id INTEGER, "
        "created_at INTEGER NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_biz_time ON sales(business_id, created_at)")

    # --- v1412: Chek raqami (bitta chekdagi savdolarni birlashtiradi) ---
    _scols = [r["name"] for r in conn.execute("PRAGMA table_info(sales)").fetchall()]
    if "chek_no" not in _scols:
        conn.execute("ALTER TABLE sales ADD COLUMN chek_no INTEGER")
    if "cost_total" not in _scols:
        conn.execute("ALTER TABLE sales ADD COLUMN cost_total INTEGER DEFAULT 0")

    # --- v1414: XARAJATLAR ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS expenses("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "category TEXT DEFAULT 'Boshqa', "
        "amount INTEGER DEFAULT 0, "
        "note TEXT DEFAULT '', "
        "source TEXT DEFAULT 'manual', "   # manual | stock (ombor kirimi)
        "stock_move_id INTEGER, "          # ombordan bog'liq harakat
        "user_id INTEGER, "
        "created_at INTEGER NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_biz_time ON expenses(business_id, created_at)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS expense_cats("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "name TEXT NOT NULL, "
        "created_at INTEGER NOT NULL)")

    # --- v1420: Biznes to'lov ma'lumotlari (onlayn buyurtma uchun) ---
    _bcols = [r["name"] for r in conn.execute("PRAGMA table_info(businesses)").fetchall()]
    if "pay_card" not in _bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN pay_card TEXT DEFAULT ''")
    if "pay_holder" not in _bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN pay_holder TEXT DEFAULT ''")
    if "pay_qr" not in _bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN pay_qr TEXT DEFAULT ''")
    if "username" not in _bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN username TEXT DEFAULT ''")
    if "director" not in _bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN director TEXT DEFAULT ''")
    if "inn" not in _bcols:
        conn.execute("ALTER TABLE businesses ADD COLUMN inn TEXT DEFAULT ''")
    _ucols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "location_exact" not in _ucols:
        conn.execute("ALTER TABLE users ADD COLUMN location_exact INTEGER DEFAULT 0")
    if "pub_username" not in _ucols:
        conn.execute("ALTER TABLE users ADD COLUMN pub_username TEXT DEFAULT ''")
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_pub_username ON users(lower(pub_username)) WHERE COALESCE(pub_username,'')<>''")
    except Exception:
        pass
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_businesses_username ON businesses(lower(username)) WHERE COALESCE(username,'')<>''")
    except Exception:
        pass

    # --- v1423: Buyurtma to'lov holati (onlayn to'lov) ---
    _ocols = [r["name"] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
    if "payment_status" not in _ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN payment_status TEXT DEFAULT ''")

    # --- v1424: XODIMLAR (kadr) ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS staff("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "name TEXT NOT NULL, "
        "profession TEXT DEFAULT '', "
        "phone TEXT DEFAULT '', "
        "salary INTEGER DEFAULT 0, "
        "hire_date TEXT DEFAULT '', "
        "status TEXT DEFAULT 'active', "     # active | fired
        "note TEXT DEFAULT '', "
        "user_id INTEGER, "
        "created_at INTEGER NOT NULL, "
        "schedule_json TEXT DEFAULT '', "
        "login TEXT DEFAULT '', "
        "pass_hash TEXT DEFAULT '', "
        "pass_plain TEXT DEFAULT '', "
        "perms TEXT DEFAULT '', "
        "can_login INTEGER DEFAULT 0, "
        "fired_at INTEGER)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staff_biz ON staff(business_id, status)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS staff_attendance("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "staff_id INTEGER NOT NULL, "
        "date TEXT NOT NULL, "
        "status TEXT DEFAULT '', "
        "time_in TEXT DEFAULT '', "
        "time_out TEXT DEFAULT '', "
        "created_at INTEGER NOT NULL, "
        "UNIQUE(staff_id, date))")
    _stc2 = [r["name"] for r in conn.execute("PRAGMA table_info(staff)").fetchall()]
    for _c, _d in (("login","TEXT DEFAULT ''"),("pass_hash","TEXT DEFAULT ''"),("pass_plain","TEXT DEFAULT ''"),("perms","TEXT DEFAULT ''"),("can_login","INTEGER DEFAULT 0")):
        if _c not in _stc2:
            conn.execute("ALTER TABLE staff ADD COLUMN %s %s" % (_c, _d))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS staff_sessions("
        "token TEXT PRIMARY KEY, staff_id INTEGER NOT NULL, business_id INTEGER NOT NULL, "
        "created_at INTEGER NOT NULL)")
    conn.execute("DROP INDEX IF EXISTS uq_staff_login")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_login_biz ON staff(business_id, lower(login)) WHERE COALESCE(login,'')<>''")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS staff_professions("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "name TEXT NOT NULL, "
        "created_at INTEGER NOT NULL)")
    # --- M2a: Kontragentlar (hamkorlar bazasi) ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS contractors("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL, "
        "name TEXT NOT NULL, ctype TEXT DEFAULT '', director TEXT DEFAULT '', "
        "phone TEXT DEFAULT '', address TEXT DEFAULT '', inn TEXT DEFAULT '', "
        "account TEXT DEFAULT '', bank TEXT DEFAULT '', mfo TEXT DEFAULT '', "
        "note TEXT DEFAULT '', created_at INTEGER NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contractors_biz ON contractors(business_id)")
    # --- M2: Hujjatlar ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS documents("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL, "
        "direction TEXT DEFAULT '', doc_type TEXT DEFAULT '', title TEXT DEFAULT '', "
        "number TEXT DEFAULT '', doc_date TEXT DEFAULT '', contractor_id INTEGER, "
        "body TEXT DEFAULT '', created_at INTEGER NOT NULL, "
        "sender_business_id INTEGER, sender_name TEXT DEFAULT '', "
        "receiver_inn TEXT DEFAULT '', status TEXT DEFAULT '')")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_biz ON documents(business_id, direction)")
    # --- #1: Baholash va fikrlar ---
    _rdc = [r["name"] for r in conn.execute("PRAGMA table_info(rides)").fetchall()]
    if "src_order_id" not in _rdc:
        conn.execute("ALTER TABLE rides ADD COLUMN src_order_id INTEGER")
    _ordc = [r["name"] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
    if "last_event" not in _ordc:
        conn.execute("ALTER TABLE orders ADD COLUMN last_event TEXT DEFAULT ''")
    _bzc = [r["name"] for r in conn.execute("PRAGMA table_info(businesses)").fetchall()]
    if "rating_sum" not in _bzc:
        conn.execute("ALTER TABLE businesses ADD COLUMN rating_sum INTEGER DEFAULT 0")
    if "rating_cnt" not in _bzc:
        conn.execute("ALTER TABLE businesses ADD COLUMN rating_cnt INTEGER DEFAULT 0")
    _spc = [r["name"] for r in conn.execute("PRAGMA table_info(specialists)").fetchall()]
    if "rating_sum" not in _spc:
        conn.execute("ALTER TABLE specialists ADD COLUMN rating_sum INTEGER DEFAULT 0")
    if "rating_cnt" not in _spc:
        conn.execute("ALTER TABLE specialists ADD COLUMN rating_cnt INTEGER DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS reviews("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, target_kind TEXT NOT NULL, target_id INTEGER NOT NULL, "
        "reviewer_user_id INTEGER NOT NULL, order_id INTEGER, stars INTEGER NOT NULL, "
        "comment TEXT DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_review_one ON reviews(target_kind, target_id, reviewer_user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_target ON reviews(target_kind, target_id)")
    # Mutaxassis/biznes egasining mijoz fikriga javobi. Fikrning o'zi egasi tomonidan o'chirilmaydi.
    _rvc = [r["name"] for r in conn.execute("PRAGMA table_info(reviews)").fetchall()]
    if "owner_reply" not in _rvc:
        conn.execute("ALTER TABLE reviews ADD COLUMN owner_reply TEXT DEFAULT ''")
    if "owner_replied_at" not in _rvc:
        conn.execute("ALTER TABLE reviews ADD COLUMN owner_replied_at INTEGER DEFAULT 0")

    # --- v1473: Mutaxassis hujjatlari, xizmat/mahsulotlari va portfolio ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS specialist_credentials("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
        "file_url TEXT NOT NULL, pos INTEGER DEFAULT 0, created_at INTEGER NOT NULL, "
        "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_credentials_user ON specialist_credentials(user_id, pos, id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS specialist_offers("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
        "kind TEXT NOT NULL DEFAULT 'service', name TEXT NOT NULL, price TEXT DEFAULT '', "
        "note TEXT DEFAULT '', photo_file TEXT DEFAULT '', created_at INTEGER NOT NULL, "
        "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_offers_user ON specialist_offers(user_id, created_at, id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS specialist_portfolio("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
        "media_type TEXT NOT NULL DEFAULT 'photo', file_url TEXT NOT NULL, "
        "created_at INTEGER NOT NULL, "
        "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_portfolio_user ON specialist_portfolio(user_id, created_at, id)")

    # --- v1468: Biznes kabinet nomidan obunalar ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS business_follows("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "target_kind TEXT NOT NULL, "
        "target_id INTEGER NOT NULL, "
        "created_at INTEGER NOT NULL, "
        "UNIQUE(business_id, target_kind, target_id), "
        "FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_business_follows_biz ON business_follows(business_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_business_follows_target ON business_follows(target_kind, target_id)")
    # Avval biznes kabinetida bosilgan obunalar user obunasi sifatida saqlangan.
    # Birinchi yangilanishda ularni biznes obunasiga ham ko'chiramiz, shunda xaritada yo'qolib qolmaydi.
    conn.execute(
        "INSERT OR IGNORE INTO business_follows(business_id, target_kind, target_id, created_at) "
        "SELECT b.id, f.target_kind, f.target_id, f.created_at "
        "FROM follows f JOIN businesses b ON b.user_id=f.follower_id "
        "WHERE NOT (f.target_kind='business' AND f.target_id=b.id)"
    )


    # --- v1472: Bosh sahifa reklamalari ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS advertisements("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER NOT NULL, "
        "business_id INTEGER, "
        "actor_type TEXT NOT NULL DEFAULT 'user', "
        "title TEXT DEFAULT '', "
        "caption TEXT DEFAULT '', "
        "image_file TEXT NOT NULL, "
        "mobile_image_file TEXT NOT NULL DEFAULT '', "
        "crop_x REAL NOT NULL DEFAULT 50, "
        "crop_y REAL NOT NULL DEFAULT 50, "
        "crop_zoom REAL NOT NULL DEFAULT 1, "
        "daily_all_day INTEGER NOT NULL DEFAULT 1, "
        "daily_start TEXT NOT NULL DEFAULT '00:00', "
        "daily_end TEXT NOT NULL DEFAULT '23:59', "
        "targets_json TEXT NOT NULL DEFAULT '[]', "
        "start_at INTEGER NOT NULL, "
        "end_at INTEGER NOT NULL, "
        "duration_days INTEGER NOT NULL DEFAULT 1, "
        "price INTEGER NOT NULL DEFAULT 0, "
        "district_count INTEGER NOT NULL DEFAULT 0, "
        "hours_per_day INTEGER NOT NULL DEFAULT 0, "
        "district_hour_rate INTEGER NOT NULL DEFAULT 0, "
        "billable_district_hours INTEGER NOT NULL DEFAULT 0, "
        "price_code TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'active', "
        "views INTEGER NOT NULL DEFAULT 0, "
        "clicks INTEGER NOT NULL DEFAULT 0, "
        "created_at INTEGER NOT NULL, "
        "updated_at INTEGER NOT NULL, "
        "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, "
        "FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ads_schedule ON advertisements(status, start_at, end_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ads_owner ON advertisements(user_id, business_id, created_at)")
    adcols = [r["name"] for r in conn.execute("PRAGMA table_info(advertisements)").fetchall()]
    if "mobile_image_file" not in adcols:
        conn.execute("ALTER TABLE advertisements ADD COLUMN mobile_image_file TEXT NOT NULL DEFAULT ''")
    if "crop_x" not in adcols:
        conn.execute("ALTER TABLE advertisements ADD COLUMN crop_x REAL NOT NULL DEFAULT 50")
    if "crop_y" not in adcols:
        conn.execute("ALTER TABLE advertisements ADD COLUMN crop_y REAL NOT NULL DEFAULT 50")
    if "crop_zoom" not in adcols:
        conn.execute("ALTER TABLE advertisements ADD COLUMN crop_zoom REAL NOT NULL DEFAULT 1")
    if "daily_all_day" not in adcols:
        conn.execute("ALTER TABLE advertisements ADD COLUMN daily_all_day INTEGER NOT NULL DEFAULT 1")
    if "daily_start" not in adcols:
        conn.execute("ALTER TABLE advertisements ADD COLUMN daily_start TEXT NOT NULL DEFAULT '00:00'")
    if "daily_end" not in adcols:
        conn.execute("ALTER TABLE advertisements ADD COLUMN daily_end TEXT NOT NULL DEFAULT '23:59'")
    ad_snapshot_columns = {
        "district_count": "INTEGER NOT NULL DEFAULT 0",
        "hours_per_day": "INTEGER NOT NULL DEFAULT 0",
        "district_hour_rate": "INTEGER NOT NULL DEFAULT 0",
        "billable_district_hours": "INTEGER NOT NULL DEFAULT 0",
        "price_code": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in ad_snapshot_columns.items():
        if name not in adcols:
            conn.execute(
                f"ALTER TABLE advertisements ADD COLUMN {name} {definition}"
            )

    # --- v1396: Mahsulotlar (items) FTS — biznes maydonlari bilan (denormalizatsiya) ---
    # name = mahsulot nomi; body = mahsulot izohi/turi + tegishli biznes (nom, yo'nalish, tur, tavsif, manzil).
    # Mahsulotni o'z nomi bo'yicha ham, tegishli biznes ma'lumoti bo'yicha ham topsa bo'ladi.
    def _item_name(pfx):
        return _canon_expr("COALESCE(" + pfx + "name,'')")

    def _item_body(ip, bp):   # ip = item prefiksi, bp = biznes prefiksi
        inner = ("COALESCE(" + ip + "note,'') || ' ' || COALESCE(" + ip + "kind,'') || ' ' || "
                 "COALESCE(" + bp + "name,'') || ' ' || COALESCE(" + bp + "yon,'') || ' ' || "
                 "COALESCE(" + bp + "tur,'') || ' ' || COALESCE(" + bp + "descr,'') || ' ' || "
                 "COALESCE(" + bp + "address,'')")
        return _canon_expr(inner)

    icols = [r["name"] for r in conn.execute("PRAGMA table_info(items_fts)").fetchall()]
    if icols and icols != ["name", "body"]:
        conn.execute("DROP TABLE IF EXISTS items_fts")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(name, body)")
    for tg in ("item_fts_ai", "item_fts_au", "item_fts_ad", "item_fts_biz_au"):
        conn.execute("DROP TRIGGER IF EXISTS " + tg)
    # Mahsulot qo'shilganda: tegishli biznesni topib, biznes matni bilan birga indekslaymiz
    conn.execute(
        "CREATE TRIGGER item_fts_ai AFTER INSERT ON items BEGIN "
        "INSERT INTO items_fts(rowid, name, body) "
        "SELECT new.id, " + _item_name("new.") + ", " + _item_body("new.", "b.") + " "
        "FROM businesses b WHERE b.id = new.business_id; END")
    conn.execute(
        "CREATE TRIGGER item_fts_au AFTER UPDATE ON items BEGIN "
        "DELETE FROM items_fts WHERE rowid = old.id; "
        "INSERT INTO items_fts(rowid, name, body) "
        "SELECT new.id, " + _item_name("new.") + ", " + _item_body("new.", "b.") + " "
        "FROM businesses b WHERE b.id = new.business_id; END")
    conn.execute(
        "CREATE TRIGGER item_fts_ad AFTER DELETE ON items BEGIN "
        "DELETE FROM items_fts WHERE rowid = old.id; END")
    # Biznes o'zgarganda: uning barcha mahsulotlari indeksini ham yangilaymiz (biznes matni o'zgargani uchun)
    conn.execute(
        "CREATE TRIGGER item_fts_biz_au AFTER UPDATE ON businesses BEGIN "
        "DELETE FROM items_fts WHERE rowid IN (SELECT id FROM items WHERE business_id = new.id); "
        "INSERT INTO items_fts(rowid, name, body) "
        "SELECT i.id, " + _item_name("i.") + ", " + _item_body("i.", "new.") + " "
        "FROM items i WHERE i.business_id = new.id; END")
    if conn.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO items_fts(rowid, name, body) "
            "SELECT i.id, " + _item_name("i.") + ", " + _item_body("i.", "b.") + " "
            "FROM items i JOIN businesses b ON b.id = i.business_id")

    # --- v1535: FTS kontent migratsiyasi versiyasi ---
    # Ustunlar o'zgarmasa ham kanonizatsiya qoidasi yangilanishi mumkin. Versiya
    # oshganda to'rtta indeks bir marta manba jadvallardan qayta quriladi.
    conn.execute("CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    fts_version = "2"
    old_fts_version = conn.execute(
        "SELECT value FROM app_meta WHERE key='search_fts_version'"
    ).fetchone()
    version_changed = (not old_fts_version) or old_fts_version[0] != fts_version

    # --- v1536: indeks bo'sh qolib ketgan bo'lsa ham qayta quramiz ---
    # Qidiruv FTS bo'sh qaytarganda LIKE zaxirasiga o'tmaydi (bu pagination uchun
    # ataylab shunday). Demak bo'sh indeks = jimgina "hech narsa topilmadi".
    # Backup FTSsiz tiklansa yoki trigger jimgina yiqilsa shu holat yuzaga keladi.
    def _index_broken():
        for fts, src in (("businesses_fts", "businesses"), ("listings_fts", "listings"),
                         ("items_fts", "items"), ("specialists_fts", "specialists")):
            try:
                n_fts = conn.execute("SELECT COUNT(*) FROM " + fts).fetchone()[0]
                n_src = conn.execute("SELECT COUNT(*) FROM " + src).fetchone()[0]
            except Exception:
                return True
            if n_src > 0 and n_fts == 0:
                return True
        return False

    if version_changed or _index_broken():
        conn.execute("DELETE FROM businesses_fts")
        conn.execute(
            "INSERT INTO businesses_fts(rowid,name,body) SELECT id," +
            _canon_expr("COALESCE(name,'')") + "," +
            _canon_expr(_concat("", ["yon", "tur", "descr", "address", "phone", "telegram", "work_hours", "username"])) +
            " FROM businesses")

        conn.execute("DELETE FROM listings_fts")
        conn.execute(
            "INSERT INTO listings_fts(rowid,name,body) SELECT id," +
            _canon_expr("COALESCE(title,'')") + "," +
            _canon_expr(_concat("", ["cat", "price", "descr", "address"])) +
            " FROM listings")

        conn.execute("DELETE FROM items_fts")
        conn.execute(
            "INSERT INTO items_fts(rowid,name,body) SELECT i.id," + _item_name("i.") + "," +
            _item_body("i.", "b.") + " FROM items i JOIN businesses b ON b.id=i.business_id")

        sp_name = _canon_expr("COALESCE(s.kasb,'') || ' ' || COALESCE(u.name,'')")
        sp_body = _canon_expr(
            "COALESCE(s.descr,'') || ' ' || COALESCE(s.narx,'') || ' ' || COALESCE(s.hudud,'') || ' ' || "
            "COALESCE(s.org,'') || ' ' || COALESCE(s.dept,'') || ' ' || COALESCE(s.lavozim,'') || ' ' || "
            "COALESCE(u.region,'') || ' ' || COALESCE(u.district,'') || ' ' || COALESCE(u.mahalla,'')")
        conn.execute("DELETE FROM specialists_fts")
        conn.execute(
            "INSERT INTO specialists_fts(rowid,name,body) SELECT s.user_id," + sp_name + "," + sp_body +
            " FROM specialists s JOIN users u ON u.id=s.user_id")
        conn.execute(
            "INSERT INTO app_meta(key,value) VALUES('search_fts_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (fts_version,))

    # --- v1540: Umumiy ovqatlanish — zal rejasidagi stol va xonalar ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dining_places("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "kind TEXT NOT NULL CHECK(kind IN ('table','room')), "
        "name TEXT NOT NULL, "
        "seats INTEGER DEFAULT 0, "
        "x REAL NOT NULL DEFAULT 4, "
        "y REAL NOT NULL DEFAULT 4, "
        "locked INTEGER NOT NULL DEFAULT 1, "
        "created_at INTEGER NOT NULL, "
        "updated_at INTEGER NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dining_places_biz ON dining_places(business_id, id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dining_bookings("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL, place_id INTEGER NOT NULL, "
        "kind TEXT NOT NULL CHECK(kind IN ('order','booking')), customer_name TEXT DEFAULT '', "
        "phone TEXT DEFAULT '', booking_date TEXT DEFAULT '', booking_time TEXT DEFAULT '', "
        "guests INTEGER DEFAULT 0, note TEXT DEFAULT '', total INTEGER DEFAULT 0, "
        "waiter_staff_id INTEGER, waiter_name TEXT DEFAULT '', problem_open INTEGER DEFAULT 0, "
        "problem_reason TEXT DEFAULT '', problem_note TEXT DEFAULT '', problem_opened_at INTEGER DEFAULT 0, "
        "kitchen_status TEXT DEFAULT 'new', payment_status TEXT DEFAULT 'open', "
        "status TEXT NOT NULL DEFAULT 'active', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
    _dbc = [r["name"] for r in conn.execute("PRAGMA table_info(dining_bookings)").fetchall()]
    for _name, _sql in (
        ("waiter_staff_id", "ALTER TABLE dining_bookings ADD COLUMN waiter_staff_id INTEGER"),
        ("waiter_name", "ALTER TABLE dining_bookings ADD COLUMN waiter_name TEXT DEFAULT ''"),
        ("problem_open", "ALTER TABLE dining_bookings ADD COLUMN problem_open INTEGER DEFAULT 0"),
        ("problem_reason", "ALTER TABLE dining_bookings ADD COLUMN problem_reason TEXT DEFAULT ''"),
        ("problem_note", "ALTER TABLE dining_bookings ADD COLUMN problem_note TEXT DEFAULT ''"),
        ("problem_opened_at", "ALTER TABLE dining_bookings ADD COLUMN problem_opened_at INTEGER DEFAULT 0"),
        ("pay_type", "ALTER TABLE dining_bookings ADD COLUMN pay_type TEXT DEFAULT ''"),
        ("debtor_id", "ALTER TABLE dining_bookings ADD COLUMN debtor_id INTEGER"),
        ("qarz_tx_id", "ALTER TABLE dining_bookings ADD COLUMN qarz_tx_id INTEGER"),
        ("kitchen_status", "ALTER TABLE dining_bookings ADD COLUMN kitchen_status TEXT DEFAULT 'new'"),
        ("payment_status", "ALTER TABLE dining_bookings ADD COLUMN payment_status TEXT DEFAULT 'open'"),
    ):
        if _name not in _dbc:
            conn.execute(_sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dining_bookings_place ON dining_bookings(business_id,place_id,status,id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dining_booking_items("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL, item_id INTEGER, "
        "name TEXT NOT NULL, qty REAL NOT NULL DEFAULT 1, unit TEXT DEFAULT '', price INTEGER DEFAULT 0, total INTEGER DEFAULT 0)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dining_booking_items ON dining_booking_items(booking_id,id)")

    # --- v1573: Ta'lim faoliyati — kurs guruhlari ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS education_groups("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL, "
        "name TEXT NOT NULL, course_item_id INTEGER, teacher_name TEXT DEFAULT '', "
        "room_name TEXT DEFAULT '', capacity INTEGER NOT NULL DEFAULT 0, "
        "weekdays TEXT DEFAULT '', lesson_from TEXT DEFAULT '', lesson_to TEXT DEFAULT '', "
        "start_date TEXT DEFAULT '', end_date TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'active', "
        "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_education_groups_biz ON education_groups(business_id,status,id)")
    _egcols = [r["name"] for r in conn.execute("PRAGMA table_info(education_groups)").fetchall()]
    for _name, _sql in (
        ("billing_type", "ALTER TABLE education_groups ADD COLUMN billing_type TEXT NOT NULL DEFAULT 'monthly'"),
        ("package_lessons", "ALTER TABLE education_groups ADD COLUMN package_lessons INTEGER NOT NULL DEFAULT 0"),
        ("package_price", "ALTER TABLE education_groups ADD COLUMN package_price INTEGER NOT NULL DEFAULT 0"),
    ):
        if _name not in _egcols:
            conn.execute(_sql)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS education_students("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL, group_id INTEGER, "
        "full_name TEXT NOT NULL, phone TEXT DEFAULT '', parent_name TEXT DEFAULT '', "
        "parent_phone TEXT DEFAULT '', birth_date TEXT DEFAULT '', joined_date TEXT DEFAULT '', "
        "note TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'active', "
        "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_education_students_biz ON education_students(business_id,status,group_id,id)")
    _escols = [r["name"] for r in conn.execute("PRAGMA table_info(education_students)").fetchall()]
    if "monthly_fee" not in _escols:
        conn.execute("ALTER TABLE education_students ADD COLUMN monthly_fee INTEGER NOT NULL DEFAULT 0")
    if "user_id" not in _escols:
        conn.execute("ALTER TABLE education_students ADD COLUMN user_id INTEGER")
    if "payment_start_date" not in _escols:
        conn.execute("ALTER TABLE education_students ADD COLUMN payment_start_date TEXT DEFAULT ''")
    if "lesson_package_override" not in _escols:
        conn.execute("ALTER TABLE education_students ADD COLUMN lesson_package_override INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS education_student_group_history("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,student_id INTEGER NOT NULL,"
        "group_id INTEGER NOT NULL,started_date TEXT NOT NULL,ended_date TEXT DEFAULT '',"
        "note TEXT DEFAULT '',created_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_education_student_group_history ON education_student_group_history(business_id,student_id,started_date,id)")
    conn.execute(
        """INSERT INTO education_student_group_history(business_id,student_id,group_id,started_date,ended_date,note,created_at)
           SELECT s.business_id,s.id,s.group_id,
                  CASE WHEN length(COALESCE(s.joined_date,''))=10 THEN s.joined_date ELSE date('now','+5 hours') END,
                  '','Boshlang''ich guruh',CAST(strftime('%s','now') AS INTEGER)
           FROM education_students s WHERE s.group_id IS NOT NULL
             AND NOT EXISTS(SELECT 1 FROM education_student_group_history h WHERE h.business_id=s.business_id AND h.student_id=s.id)"""
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS education_attendance("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL, group_id INTEGER NOT NULL, "
        "student_id INTEGER NOT NULL, lesson_date TEXT NOT NULL, "
        "attendance_status TEXT NOT NULL DEFAULT 'present', note TEXT DEFAULT '', "
        "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
        "UNIQUE(business_id,group_id,student_id,lesson_date))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_education_attendance_day ON education_attendance(business_id,lesson_date,group_id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS education_payments("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL, student_id INTEGER NOT NULL, "
        "payment_month TEXT NOT NULL, amount INTEGER NOT NULL, pay_type TEXT NOT NULL DEFAULT 'naqd', "
        "note TEXT DEFAULT '', sale_id INTEGER, created_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_education_payments_student ON education_payments(business_id,student_id,payment_month,id)")
    _epcols = [r["name"] for r in conn.execute("PRAGMA table_info(education_payments)").fetchall()]
    for _name, _definition in (("voided_at", "INTEGER NOT NULL DEFAULT 0"), ("voided_by", "INTEGER"), ("void_reason", "TEXT DEFAULT ''")):
        if _name not in _epcols:
            conn.execute("ALTER TABLE education_payments ADD COLUMN %s %s" % (_name, _definition))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS education_teachers("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL, full_name TEXT NOT NULL, "
        "phone TEXT DEFAULT '', specialty TEXT DEFAULT '', hired_date TEXT DEFAULT '', "
        "salary_type TEXT NOT NULL DEFAULT 'monthly', salary_amount INTEGER NOT NULL DEFAULT 0, "
        "note TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'active', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_education_teachers_biz ON education_teachers(business_id,status,id)")
    _egcols2 = [r["name"] for r in conn.execute("PRAGMA table_info(education_groups)").fetchall()]
    if "teacher_id" not in _egcols2:
        conn.execute("ALTER TABLE education_groups ADD COLUMN teacher_id INTEGER")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS education_exams("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,group_id INTEGER NOT NULL,"
        "title TEXT NOT NULL,exam_date TEXT NOT NULL,max_score REAL NOT NULL DEFAULT 100,note TEXT DEFAULT '',"
        "status TEXT NOT NULL DEFAULT 'active',created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_education_exams_biz ON education_exams(business_id,status,exam_date,id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS education_exam_results("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,exam_id INTEGER NOT NULL,student_id INTEGER NOT NULL,"
        "score REAL NOT NULL DEFAULT 0,note TEXT DEFAULT '',created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,"
        "UNIQUE(business_id,exam_id,student_id))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_education_exam_results ON education_exam_results(business_id,exam_id,student_id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS education_enrollments("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,course_item_id INTEGER NOT NULL,"
        "user_id INTEGER NOT NULL,customer_name TEXT NOT NULL,phone TEXT DEFAULT '',note TEXT DEFAULT '',"
        "status TEXT NOT NULL DEFAULT 'new',group_id INTEGER,student_id INTEGER,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_education_enrollments_biz ON education_enrollments(business_id,status,id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS education_teacher_payments("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,teacher_id INTEGER NOT NULL,"
        "payment_month TEXT NOT NULL,amount INTEGER NOT NULL,pay_type TEXT NOT NULL DEFAULT 'naqd',"
        "note TEXT DEFAULT '',expense_id INTEGER,created_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_education_teacher_payments ON education_teacher_payments(business_id,teacher_id,payment_month,id)")
    # --- Tibbiyot: xizmat-shifokor va yagona onlayn/oflayn navbat ---
    conn.execute("CREATE TABLE IF NOT EXISTS medical_doctor_services(id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,staff_id INTEGER NOT NULL,item_id INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,UNIQUE(business_id,staff_id,item_id))")
    _mdscols=[r["name"] for r in conn.execute("PRAGMA table_info(medical_doctor_services)").fetchall()]
    if "duration_minutes" not in _mdscols:
        conn.execute("ALTER TABLE medical_doctor_services ADD COLUMN duration_minutes INTEGER NOT NULL DEFAULT 20")
    conn.execute("CREATE TABLE IF NOT EXISTS medical_doctors(id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,staff_id INTEGER NOT NULL,specialty TEXT DEFAULT '',experience_years INTEGER NOT NULL DEFAULT 0,qualification TEXT DEFAULT '',work_days TEXT DEFAULT '1,2,3,4,5,6',work_start TEXT DEFAULT '08:00',work_end TEXT DEFAULT '17:00',avg_minutes INTEGER NOT NULL DEFAULT 20,room TEXT DEFAULT '',bio TEXT DEFAULT '',status TEXT NOT NULL DEFAULT 'active',created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(business_id,staff_id))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_medical_doctors ON medical_doctors(business_id,status,staff_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_medical_doctor_services ON medical_doctor_services(business_id,item_id,staff_id)")
    conn.execute("CREATE TABLE IF NOT EXISTS medical_queue(id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,item_id INTEGER NOT NULL,staff_id INTEGER NOT NULL,user_id INTEGER,patient_name TEXT NOT NULL,phone TEXT DEFAULT '',queue_date TEXT NOT NULL,queue_no INTEGER NOT NULL,queue_code TEXT NOT NULL,source TEXT NOT NULL DEFAULT 'online',status TEXT NOT NULL DEFAULT 'waiting',note TEXT DEFAULT '',created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(business_id,item_id,staff_id,queue_date,queue_no))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_medical_queue_day ON medical_queue(business_id,queue_date,staff_id,item_id,queue_no)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_medical_queue_service_day ON medical_queue(business_id,item_id,queue_date,status)")
    # Vaqtli qabul (slot) rejimi: xodimда rejim, navbatда qabul vaqti. Default 'live' — eski xatti-harakat o'zgarmaydi.
    _mdcols=[r["name"] for r in conn.execute("PRAGMA table_info(medical_doctors)").fetchall()]
    if "mode" not in _mdcols:
        conn.execute("ALTER TABLE medical_doctors ADD COLUMN mode TEXT NOT NULL DEFAULT 'live'")
    _mqcols=[r["name"] for r in conn.execute("PRAGMA table_info(medical_queue)").fetchall()]
    if "slot_time" not in _mqcols:
        conn.execute("ALTER TABLE medical_queue ADD COLUMN slot_time TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_medical_queue_slot ON medical_queue(business_id,item_id,staff_id,queue_date,slot_time) WHERE slot_time<>''")
    conn.execute("CREATE TABLE IF NOT EXISTS medical_queue_history(id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,queue_id INTEGER NOT NULL,action TEXT NOT NULL,old_value TEXT DEFAULT '',new_value TEXT DEFAULT '',actor_user_id INTEGER,actor_staff_id INTEGER,note TEXT DEFAULT '',created_at INTEGER NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_medical_queue_history ON medical_queue_history(business_id,queue_id,id)")
    # Avvaldan shifokor biriktirilgan tibbiy xizmatlarning ishlashi uzilmasin.
    conn.execute("""UPDATE items SET queue_enabled=1
                    WHERE kind='service'
                      AND business_id IN (SELECT id FROM businesses WHERE yon='Tibbiy xizmatlar')
                      AND id IN (SELECT item_id FROM medical_doctor_services WHERE active=1)""")
