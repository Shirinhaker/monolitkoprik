"""Authoritative hourly advertisement pricing and schedule rules."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
import unicodedata

from district_catalog import REGION_DISTRICTS


VALID_AD_DURATIONS = (1, 3, 7, 14, 30)
FULL_HOUR_RE = re.compile(r"^(?:[01]\d|2[0-3]):00$")


class AdPricingError(ValueError):
    """Raised when an advertisement pricing input violates the tariff."""


def normalize_ad_geo(value):
    text = unicodedata.normalize(
        "NFKC", str(value or "")
    ).casefold().replace("ʻ", "'").replace("’", "'")
    text = re.sub(
        r"\b(viloyati|viloyat|shahri|shahar|tumani|tuman)\b",
        "",
        text,
    )
    return re.sub(r"[^a-z0-9'\u0400-\u04ff]+", "", text)


def _catalog_indexes():
    regions = {}
    all_keys = set()
    for region, names in REGION_DISTRICTS.items():
        region_key = normalize_ad_geo(region)
        region_keys = {
            normalize_ad_geo(name)
            for name in names
        }
        regions[region_key] = region_keys
        all_keys.update(
            (region_key, district_key)
            for district_key in region_keys
        )
    return regions, all_keys


REGION_KEYS, ALL_DISTRICT_KEYS = _catalog_indexes()


def clean_ad_targets(raw):
    if not isinstance(raw, list):
        raise AdPricingError("Reklama hududlarini tanlang.")
    result, seen = [], set()
    for item in raw[:30]:
        if not isinstance(item, dict):
            continue
        level = str(item.get("level") or "").strip().lower()
        region = str(item.get("region") or "").strip()
        district = str(item.get("district") or "").strip()
        if level not in ("district", "region", "republic"):
            continue
        if level == "region" and not region:
            continue
        if level == "district" and not (region and district):
            continue
        if level == "republic":
            region = district = ""
        key = (level, normalize_ad_geo(region), normalize_ad_geo(district))
        if key not in seen:
            seen.add(key)
            result.append({
                "level": level,
                "region": region,
                "district": district,
            })
    if not result:
        raise AdPricingError("Kamida bitta hudud tanlang.")
    if any(item["level"] == "republic" for item in result) and len(result) > 1:
        raise AdPricingError(
            "Respublika tanlansa boshqa hudud qo'shilmaydi."
        )
    return result


def expand_targets_to_district_keys(targets):
    expanded = set()
    for target in targets:
        level = target["level"]
        if level == "republic":
            expanded.update(ALL_DISTRICT_KEYS)
            continue
        region_key = normalize_ad_geo(target.get("region"))
        if region_key not in REGION_KEYS:
            raise AdPricingError("Tanlangan viloyat backend katalogida yo'q.")
        if level == "region":
            expanded.update(
                (region_key, district_key)
                for district_key in REGION_KEYS[region_key]
            )
            continue
        district_key = normalize_ad_geo(target.get("district"))
        if district_key not in REGION_KEYS[region_key]:
            raise AdPricingError("Tanlangan tuman backend katalogida yo'q.")
        expanded.add((region_key, district_key))
    if not expanded:
        raise AdPricingError("Hudud katalogga yoyilmadi.")
    return tuple(
        region_key + ":" + district_key
        for region_key, district_key in sorted(expanded)
    )


def full_hour(value):
    text = str(value or "").strip()
    if not FULL_HOUR_RE.fullmatch(text):
        raise AdPricingError("Vaqt faqat to'liq HH:00 soat bo'lishi kerak.")
    return int(text[:2])


def hours_per_day(all_day, start, end):
    if bool(all_day):
        return 24
    start_hour, end_hour = full_hour(start), full_hour(end)
    if start_hour == end_hour:
        raise AdPricingError(
            "Boshlanish va tugash soati bir xil bo'lmasin."
        )
    return (end_hour - start_hour) % 24


def calculate_ad_price(
    *, targets, duration_days, daily_all_day, daily_start, daily_end,
    district_hour_rate,
):
    try:
        days = int(duration_days)
        rate = int(district_hour_rate)
    except (TypeError, ValueError):
        raise AdPricingError("Reklama narxi yoki davomiyligi noto'g'ri.")
    if days not in VALID_AD_DURATIONS:
        raise AdPricingError("Kunlar 1, 3, 7, 14 yoki 30 bo'lishi kerak.")
    if rate < 0:
        raise AdPricingError("Bir tuman/soat narxi noto'g'ri.")
    cleaned = clean_ad_targets(targets)
    districts = expand_targets_to_district_keys(cleaned)
    daily_hours = hours_per_day(
        daily_all_day, daily_start, daily_end
    )
    quantity = len(districts) * daily_hours * days
    return {
        "targets": cleaned,
        "district_count": len(districts),
        "hours_per_day": daily_hours,
        "duration_days": days,
        "district_hour_rate": rate,
        "billable_district_hours": quantity,
        "total": quantity * rate,
        "currency": "UZS",
    }


def _uz_timezone(tz_offset_seconds):
    try:
        return timezone(timedelta(seconds=int(tz_offset_seconds)))
    except (TypeError, ValueError, OverflowError):
        raise AdPricingError("Vaqt mintaqasi noto'g'ri.")


def first_schedule_start(
    *, start_date, daily_all_day, daily_start, tz_offset_seconds=18_000
):
    try:
        selected_date = date.fromisoformat(str(start_date or "").strip())
    except (TypeError, ValueError):
        raise AdPricingError("Boshlanish sanasi noto'g'ri.")
    start_hour = 0 if bool(daily_all_day) else full_hour(daily_start)
    local_start = datetime(
        selected_date.year,
        selected_date.month,
        selected_date.day,
        start_hour,
        tzinfo=_uz_timezone(tz_offset_seconds),
    )
    return int(local_start.timestamp())


def shift_schedule_start(
    *,
    requested_start_at,
    approved_at,
    daily_all_day,
    daily_start,
    tz_offset_seconds=18_000,
):
    try:
        requested = int(requested_start_at)
        approved = int(approved_at)
    except (TypeError, ValueError):
        raise AdPricingError("Reklama jadvali noto'g'ri.")
    if approved <= requested:
        return requested

    tz = _uz_timezone(tz_offset_seconds)
    approved_local = datetime.fromtimestamp(approved, tz)
    start_hour = 0 if bool(daily_all_day) else full_hour(daily_start)
    candidate = approved_local.replace(
        hour=start_hour, minute=0, second=0, microsecond=0
    )
    if int(candidate.timestamp()) < approved:
        candidate += timedelta(days=1)
    return int(candidate.timestamp())


def schedule_end_at(
    *, actual_start_at, duration_days, hours_each_day, daily_all_day
):
    try:
        start = int(actual_start_at)
        days = int(duration_days)
        hours = int(hours_each_day)
    except (TypeError, ValueError):
        raise AdPricingError("Reklama jadvali noto'g'ri.")
    if days not in VALID_AD_DURATIONS:
        raise AdPricingError("Kunlar 1, 3, 7, 14 yoki 30 bo'lishi kerak.")
    if bool(daily_all_day):
        return start + days * 86_400
    if hours < 1 or hours > 23:
        raise AdPricingError("Kunlik soatlar soni noto'g'ri.")
    return start + (days - 1) * 86_400 + hours * 3_600
