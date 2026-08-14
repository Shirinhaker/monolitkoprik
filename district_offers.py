"""Tuman bo'yicha pullik biznes takliflarini tanlash qoidalari."""

import hashlib
import re
import time

from location_keys import canonical_district_key
from subscriptions import home_nearby_eligible_plan_codes


SLOT_SECONDS = 30 * 60
MAX_DISTRICT_OFFERS = 20

_OPAQUE_MEDIA_REFERENCE = re.compile(r"[A-Za-z0-9_-]{1,512}\Z")
_UPLOAD_MEDIA_REFERENCE = re.compile(
    r"/uploads/(?:[A-Za-z0-9][A-Za-z0-9._-]*/)*"
    r"[A-Za-z0-9][A-Za-z0-9._-]*\Z"
)
_PROFILE_MEDIA_REFERENCE = re.compile(
    r"/profile-media/(?:business|user)/[1-9][0-9]*(?:\?v=[0-9]+)?\Z"
)
_PROXIED_MEDIA_REFERENCE = re.compile(r"/media/[A-Za-z0-9_-]{1,512}\Z")


def normalize_district(value):
    """Compatibility alias for the shared persisted district-key policy."""
    return canonical_district_key(value)


def safe_media_reference(value):
    """Return a server-issued media reference, or an empty string.

    District-offer images are loaded automatically.  Only opaque IDs (which
    the client resolves through ``/media``) and canonical same-origin paths
    issued by this server may cross that serialization boundary.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or text != value:
        return ""
    if _OPAQUE_MEDIA_REFERENCE.fullmatch(text):
        return text
    if _UPLOAD_MEDIA_REFERENCE.fullmatch(text):
        return text
    if _PROFILE_MEDIA_REFERENCE.fullmatch(text):
        return text
    if _PROXIED_MEDIA_REFERENCE.fullmatch(text):
        return text
    return ""


def validate_media_reference(value):
    """Validate a business-controlled write and return its canonical value."""
    if value is None or value == "":
        return ""
    safe = safe_media_reference(value)
    if not safe:
        raise ValueError("Media manzili faqat server bergan fayl identifikatori bo'lishi mumkin.")
    return safe


def offer_time_slot(now=None):
    return int(time.time() if now is None else now) // SLOT_SECONDS


def _stable_offset(district_key, slot, count):
    if count <= 0:
        return 0
    seed = hashlib.sha256(district_key.encode("utf-8")).digest()
    district_offset = int.from_bytes(seed[:8], "big")
    return (district_offset + slot) % count


def _row_value(row, name, default=""):
    return row[name] if name in row.keys() and row[name] is not None else default


def _table_columns(conn, table):
    return {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table)}


def _content_count_sql(kind, has_stock_type):
    stock_clause = ""
    if has_stock_type:
        stock_clause = (
            " AND (COALESCE(b.yon,'')<>'Umumiy ovqatlanish' "
            "OR COALESCE(i.stock_type,'ready_food')='ready_food')"
        )
    return (
        "(SELECT COUNT(*) FROM items i "
        "WHERE i.business_id=b.id AND i.kind='"
        + kind
        + "'"
        + stock_clause
        + ")"
    )


def _candidate_query_parts(plan_codes, has_stock_type, include_listings=True):
    product_count = _content_count_sql("product", has_stock_type)
    service_count = _content_count_sql("service", has_stock_type)
    listing_count = "0"
    if include_listings:
        listing_count = (
            "(SELECT COUNT(*) FROM listings l WHERE l.business_id=b.id "
            "AND l.status='active' AND l.visibility='all')"
        )
    item_stock_clause = ""
    if has_stock_type:
        item_stock_clause = (
            " AND (COALESCE(b.yon,'')<>'Umumiy ovqatlanish' "
            "OR COALESCE(i.stock_type,'ready_food')='ready_food')"
        )
    content_eligible = (
        "EXISTS (SELECT 1 FROM items i WHERE i.business_id=b.id "
        "AND i.kind IN ('product','service')"
        + item_stock_clause
        + ")"
    )
    if include_listings:
        content_eligible = (
            "("
            + content_eligible
            + " OR EXISTS (SELECT 1 FROM listings l WHERE l.business_id=b.id "
            "AND l.status='active' AND l.visibility='all'))"
        )
    placeholders = ",".join("?" for _ in plan_codes)
    scope = (
        "FROM users u JOIN businesses b ON b.user_id=u.id "
        "WHERE u.district_key=? AND b.status='active' "
        "AND NOT EXISTS (SELECT 1 FROM account_restrictions ar "
        "WHERE ar.actor_type='business' AND ar.actor_id=b.id "
        "AND ar.status='active' "
        "AND ar.restriction IN ('content_hidden','account_blocked')) "
        "AND NOT EXISTS (SELECT 1 FROM content_moderation bcm "
        "WHERE bcm.content_kind='business' AND bcm.content_id=b.id "
        "AND bcm.id=(SELECT MAX(bcm2.id) FROM content_moderation bcm2 "
        "WHERE bcm2.content_kind='business' AND bcm2.content_id=b.id) "
        "AND bcm.status IN ('hidden','removed')) "
        "AND EXISTS (SELECT 1 FROM business_subscriptions bs "
        "WHERE bs.business_id=b.id AND bs.status='active' "
        "AND bs.plan_code IN ("
        + placeholders
        + ") AND bs.expires_at>?) AND "
        + content_eligible
    )
    fields = (
        "b.id,b.name,b.yon,b.logo_file,b.logo_x,b.logo_y,b.logo_zoom,"
        + product_count
        + " AS product_count,"
        + service_count
        + " AS service_count,"
        + listing_count
        + " AS listing_count "
    )
    return fields, scope


def _eligible_candidates(
    conn,
    district_key,
    current_time,
    has_stock_type,
    slot,
    max_items,
    include_listings=True,
):
    """Return only the rotated candidate rows while preserving global fairness."""
    plan_codes = home_nearby_eligible_plan_codes()
    if not plan_codes or max_items <= 0:
        return []
    fields, scope = _candidate_query_parts(
        plan_codes,
        has_stock_type,
        include_listings=include_listings,
    )
    base_params = (district_key, *plan_codes, current_time)
    candidate_count = int(
        conn.execute("SELECT COUNT(*) " + scope, base_params).fetchone()[0] or 0
    )
    if candidate_count <= 0:
        return []
    offset = _stable_offset(district_key, slot, candidate_count)

    def fetch_rows(row_limit, row_offset):
        if row_limit <= 0:
            return []
        return conn.execute(
            "SELECT "
            + fields
            + scope
            + " ORDER BY b.id LIMIT ? OFFSET ?",
            (*base_params, int(row_limit), int(row_offset)),
        ).fetchall()

    wanted = min(int(max_items), candidate_count)
    first_count = min(wanted, candidate_count - offset)
    rows = list(fetch_rows(first_count, offset))
    if len(rows) < wanted:
        rows.extend(fetch_rows(wanted - len(rows), 0))
    return rows


def _item_at_offset(conn, business, kind, offset, has_stock_type):
    stock_clause = ""
    if has_stock_type and _row_value(business, "yon") == "Umumiy ovqatlanish":
        stock_clause = " AND COALESCE(stock_type,'ready_food')='ready_food'"
    return conn.execute(
        "SELECT id,name,price,unit,kind,photo_file FROM items "
        "WHERE business_id=? AND kind=?"
        + stock_clause
        + " AND NOT EXISTS (SELECT 1 FROM content_moderation cm "
        "WHERE cm.content_kind=items.kind AND cm.content_id=items.id "
        "AND cm.id=(SELECT MAX(cm2.id) FROM content_moderation cm2 "
        "WHERE cm2.content_kind=items.kind AND cm2.content_id=items.id) "
        "AND cm.status IN ('hidden','removed'))"
        + " ORDER BY id LIMIT 1 OFFSET ?",
        (business["id"], kind, int(offset)),
    ).fetchone()


def _listing_at_offset(conn, business_id, offset):
    return conn.execute(
        "SELECT id,title,price FROM listings "
        "WHERE business_id=? AND status='active' AND visibility='all' "
        "AND NOT EXISTS (SELECT 1 FROM content_moderation cm "
        "WHERE cm.content_kind='listing' AND cm.content_id=listings.id "
        "AND cm.id=(SELECT MAX(cm2.id) FROM content_moderation cm2 "
        "WHERE cm2.content_kind='listing' AND cm2.content_id=listings.id) "
        "AND cm.status IN ('hidden','removed')) "
        "ORDER BY id LIMIT 1 OFFSET ?",
        (business_id, int(offset)),
    ).fetchone()


def _listing_image(conn, listing_id):
    rows = conn.execute(
        "SELECT tg_file_id FROM listing_media "
        "WHERE listing_id=? AND mtype='photo' ORDER BY pos,id LIMIT 10",
        (listing_id,),
    ).fetchall()
    for row in rows:
        safe = safe_media_reference(row["tg_file_id"])
        if safe:
            return safe
    return ""


def _offer_item(conn, business, kind, content):
    if kind == "listing":
        title = content["title"]
        price = content["price"]
        unit = ""
        image = _listing_image(conn, content["id"])
    else:
        title = content["name"]
        price = content["price"]
        unit = _row_value(content, "unit", "dona") or "dona"
        image = safe_media_reference(_row_value(content, "photo_file") or "")
    return {
        "business_id": business["id"],
        "business_name": business["name"],
        "business_logo": safe_media_reference(
            _row_value(business, "logo_file") or ""
        ),
        "logo_x": float(_row_value(business, "logo_x", 50) or 50),
        "logo_y": float(_row_value(business, "logo_y", 50) or 50),
        "logo_zoom": float(_row_value(business, "logo_zoom", 1) or 1),
        "content_id": content["id"],
        "kind": kind,
        "title": title,
        "price": price,
        "unit": unit,
        "image": image,
    }


def district_offers_payload(
    conn,
    user_id,
    now=None,
    limit=MAX_DISTRICT_OFFERS,
    district="",
    include_listings=True,
):
    """Return a stable, rotating set of public paid-business offers for one district."""
    # Domain-level callers (including import/ETL and isolated tests) may provide
    # a minimal connection without running the application-wide migration.
    from moderation import ensure_moderation_schema

    ensure_moderation_schema(conn)
    current_time = int(time.time() if now is None else now)
    slot = offer_time_slot(current_time)
    user = None
    if user_id is not None:
        user = conn.execute(
            "SELECT district_key FROM users WHERE id=?", (int(user_id),)
        ).fetchone()
    requested_district_key = canonical_district_key(district)
    district_key = requested_district_key or (
        _row_value(user, "district_key") if user else ""
    )
    if not district_key:
        return {"needs_district": True, "slot": slot, "items": []}

    if isinstance(limit, bool):
        max_items = 0
    else:
        try:
            max_items = max(0, int(limit))
        except (TypeError, ValueError, OverflowError):
            max_items = MAX_DISTRICT_OFFERS
    max_items = min(max_items, MAX_DISTRICT_OFFERS)
    if max_items == 0:
        return {"needs_district": False, "slot": slot, "items": []}

    has_stock_type = "stock_type" in _table_columns(conn, "items")
    candidates = _eligible_candidates(
        conn,
        district_key,
        current_time,
        has_stock_type,
        slot,
        max_items,
        include_listings=include_listings,
    )
    if not candidates:
        return {"needs_district": False, "slot": slot, "items": []}

    items = []
    for business in candidates:
        available_kinds = [
            (kind, int(_row_value(business, kind + "_count", 0) or 0))
            for kind in (
                ("product", "service", "listing")
                if include_listings
                else ("product", "service")
            )
            if int(_row_value(business, kind + "_count", 0) or 0) > 0
        ]
        kind, content_count = available_kinds[
            (slot + business["id"]) % len(available_kinds)
        ]
        content_offset = (slot // 3 + business["id"]) % content_count
        if kind == "listing":
            content = _listing_at_offset(conn, business["id"], content_offset)
        else:
            content = _item_at_offset(
                conn, business, kind, content_offset, has_stock_type
            )
        if content is not None:
            items.append(_offer_item(conn, business, kind, content))
    return {"needs_district": False, "slot": slot, "items": items}
