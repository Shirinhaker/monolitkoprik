"""Private storage for manual-payment receipt images."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import time
from pathlib import Path


MAX_RECEIPT_BYTES = 5 * 1024 * 1024
TOKEN_TTL_SECONDS = 60 * 60


class ReceiptValidationError(ValueError):
    """Receipt bytes, token, ownership or lifetime is invalid."""


def _stamp(value=None):
    return int(time.time()) if value is None else int(value)


def _b64encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value):
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except Exception as exc:
        raise ReceiptValidationError("Kvitansiya tokeni noto‘g‘ri.") from exc


def _strong_secret(secret):
    value = str(secret or "")
    if len(value) < 48:
        raise ReceiptValidationError(
            "Kvitansiya token siri kamida 48 belgili bo‘lishi kerak."
        )
    return value.encode("utf-8")


def _sniff(raw):
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ReceiptValidationError("Faqat JPG, PNG yoki WEBP rasm qabul qilinadi.")


def _safe_path(root, relative_path):
    root_path = Path(root).expanduser().resolve(strict=False)
    relative = Path(str(relative_path or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise ReceiptValidationError("Kvitansiya yo‘li noto‘g‘ri.")
    target = (root_path / relative).resolve(strict=False)
    if target == root_path or root_path not in target.parents:
        raise ReceiptValidationError("Kvitansiya yo‘li private katalogdan tashqarida.")
    return root_path, target


def store_receipt(root, owner_id, raw, content_type, secret, now=None):
    secret_bytes = _strong_secret(secret)
    owner_id = int(owner_id or 0)
    if owner_id <= 0:
        raise ReceiptValidationError("Kvitansiya egasi noto‘g‘ri.")
    raw = bytes(raw or b"")
    if not raw or len(raw) > MAX_RECEIPT_BYTES:
        raise ReceiptValidationError("Kvitansiya hajmi 5 MBdan oshmasligi kerak.")
    mime, extension = _sniff(raw)
    declared = str(content_type or "").split(";", 1)[0].strip().lower()
    if declared and declared != mime:
        raise ReceiptValidationError("Kvitansiya turi fayl tarkibiga mos emas.")
    stamp = _stamp(now)
    relative = Path(
        "unclaimed",
        time.strftime("%Y", time.gmtime(stamp)),
        time.strftime("%m", time.gmtime(stamp)),
        secrets.token_hex(24) + extension,
    )
    _, absolute = _safe_path(root, relative)
    absolute.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(absolute, "xb") as output:
            output.write(raw)
        os.chmod(absolute, 0o600)
    except FileExistsError as exc:
        raise ReceiptValidationError("Kvitansiya fayli yaratilmadi.") from exc
    digest = hashlib.sha256(raw).hexdigest()
    payload = {
        "owner_id": owner_id,
        "relative_path": relative.as_posix(),
        "mime": mime,
        "sha256": digest,
        "expires_at": stamp + TOKEN_TTL_SECONDS,
        "nonce": secrets.token_hex(16),
    }
    encoded = _b64encode(
        json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    )
    signature = hmac.new(
        secret_bytes, encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return {
        "relative_path": payload["relative_path"],
        "mime": mime,
        "sha256": digest,
        "token": encoded + "." + signature,
        "expires_at": payload["expires_at"],
    }


def verify_receipt_token(token, secret, owner_id, now=None):
    secret_bytes = _strong_secret(secret)
    try:
        encoded, supplied_signature = str(token or "").split(".", 1)
    except ValueError as exc:
        raise ReceiptValidationError("Kvitansiya tokeni noto‘g‘ri.") from exc
    expected = hmac.new(
        secret_bytes, encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied_signature):
        raise ReceiptValidationError("Kvitansiya tokeni imzosi noto‘g‘ri.")
    try:
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptValidationError("Kvitansiya tokeni o‘qilmadi.") from exc
    if int(payload.get("owner_id") or 0) != int(owner_id or 0):
        raise ReceiptValidationError("Kvitansiya boshqa foydalanuvchiga tegishli.")
    if int(payload.get("expires_at") or 0) <= _stamp(now):
        raise ReceiptValidationError("Kvitansiya tokenining muddati tugagan.")
    relative = str(payload.get("relative_path") or "")
    if not relative.startswith("unclaimed/"):
        raise ReceiptValidationError("Kvitansiya tokeni allaqachon ishlatilgan.")
    digest = str(payload.get("sha256") or "")
    if len(digest) != 64:
        raise ReceiptValidationError("Kvitansiya tokeni to‘liq emas.")
    return payload


def claim_receipt(root, token_data, payment_id):
    payload = dict(token_data or {})
    root_path, source = _safe_path(root, payload.get("relative_path"))
    if not str(payload.get("relative_path") or "").startswith("unclaimed/"):
        raise ReceiptValidationError("Kvitansiya tokeni ishlatilgan.")
    if not source.is_file():
        raise ReceiptValidationError("Kvitansiya topilmadi yoki ishlatilgan.")
    raw = source.read_bytes()
    if not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), str(payload.get("sha256") or "")
    ):
        raise ReceiptValidationError("Kvitansiya fayli o‘zgartirilgan.")
    payment_id = int(payment_id or 0)
    if payment_id <= 0:
        raise ReceiptValidationError("To‘lov raqami noto‘g‘ri.")
    destination_relative = Path(
        "claimed", str(payment_id), source.name
    ).as_posix()
    _, destination = _safe_path(root_path, destination_relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
    except OSError as exc:
        raise ReceiptValidationError("Kvitansiyani biriktirib bo‘lmadi.") from exc
    return {
        "path": destination_relative,
        "mime": str(payload.get("mime") or ""),
        "sha256": str(payload.get("sha256") or ""),
    }


def receipt_absolute_path(root, relative_path):
    """Resolve a claimed receipt without exposing or accepting traversal."""
    relative = str(relative_path or "")
    if not relative.startswith("claimed/"):
        raise ReceiptValidationError("Kvitansiya yo‘li noto‘g‘ri.")
    _, target = _safe_path(root, relative)
    if not target.is_file():
        raise ReceiptValidationError("Kvitansiya topilmadi.")
    return str(target)


def delete_expired_unclaimed_receipts(root, older_than):
    root_path, unclaimed = _safe_path(root, "unclaimed")
    del root_path
    if not unclaimed.exists():
        return 0
    cutoff = int(older_than)
    removed = 0
    for path in unclaimed.rglob("*"):
        if not path.is_file():
            continue
        try:
            if int(path.stat().st_mtime) < cutoff:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    for directory in sorted(
        (path for path in unclaimed.rglob("*") if path.is_dir()),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed
