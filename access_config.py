"""Platformadagi maxsus profil huquqlari."""

import os


def _env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() not in ("0", "false", "no", "off", "open")


def _privileged_ids():
    raw = os.environ.get("PRIVILEGED_TG_IDS", "1423181561,607563067")
    result = set()
    for value in raw.split(","):
        try:
            result.add(int(value.strip()))
        except (TypeError, ValueError):
            pass
    return result


PRIVILEGED_TG_IDS = _privileged_ids()

# Loyiha hamma uchun ochiq. Zarur bo'lsa vaqtincha yopish uchun Railway'da
# PROJECT_ACCESS_RESTRICTED=1 berishning o'zi yetadi; kodni almashtirish shart emas.
PROJECT_ACCESS_RESTRICTED = _env_flag("PROJECT_ACCESS_RESTRICTED", False)


def is_privileged_tg_id(tg_id):
    try:
        return int(tg_id) in PRIVILEGED_TG_IDS
    except (TypeError, ValueError):
        return False


def project_access_is_restricted():
    return bool(PROJECT_ACCESS_RESTRICTED)


def project_access_allowed_tg_id(tg_id):
    return not project_access_is_restricted() or is_privileged_tg_id(tg_id)
