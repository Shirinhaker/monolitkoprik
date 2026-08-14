"""Tashqi provayderlarni keyin ulash uchun barqaror adapter chegaralari.

Bu modulning o‘zi hech qaysi SMS, to‘lov yoki storage xizmatiga so‘rov
yubormaydi. Keyingi provayder alohida adapter sifatida ro‘yxatdan o‘tkaziladi;
asosiy API oqimlarini qayta yozish talab qilinmaydi.
"""

from __future__ import annotations

import os
import re
from typing import Protocol


PROVIDER_RE = re.compile(r"[a-z][a-z0-9_-]{0,39}")
INTEGRATION_KINDS = ("sms", "payment", "object_storage")


class IntegrationNotConfigured(RuntimeError):
    pass


class IntegrationDeliveryError(RuntimeError):
    pass


class SmsProvider(Protocol):
    async def send_verification_code(
        self, *, phone: str, code: str, purpose: str, expires_in: int
    ) -> None: ...


class PaymentProvider(Protocol):
    async def create_payment(self, *, reference: str, amount: int, currency: str, return_url: str): ...

    async def verify_webhook(self, *, headers, body: bytes): ...


class ObjectStorageProvider(Protocol):
    async def put(self, *, key: str, content: bytes, content_type: str): ...

    async def delete(self, *, key: str) -> None: ...


_PROVIDER_FACTORIES = {kind: {} for kind in INTEGRATION_KINDS}


def _clean_name(value) -> str:
    return str(value or "disabled").strip().lower()


def register_provider(kind: str, name: str, factory) -> None:
    if kind not in _PROVIDER_FACTORIES:
        raise ValueError("Noma’lum integratsiya turi: " + str(kind))
    clean = _clean_name(name)
    if clean == "disabled" or not PROVIDER_RE.fullmatch(clean):
        raise ValueError("Provayder nomi noto‘g‘ri")
    if not callable(factory):
        raise TypeError("Provayder factory callable bo‘lishi kerak")
    _PROVIDER_FACTORIES[kind][clean] = factory


def unregister_provider(kind: str, name: str) -> None:
    if kind in _PROVIDER_FACTORIES:
        _PROVIDER_FACTORIES[kind].pop(_clean_name(name), None)


def selected_provider_name(kind: str, environ=None) -> str:
    if kind not in _PROVIDER_FACTORIES:
        raise ValueError("Noma’lum integratsiya turi: " + str(kind))
    env = os.environ if environ is None else environ
    return _clean_name(env.get(kind.upper() + "_PROVIDER", "disabled"))


def get_provider(kind: str, environ=None):
    name = selected_provider_name(kind, environ)
    if name == "disabled":
        raise IntegrationNotConfigured(kind + " provayderi hali ulanmagan")
    factory = _PROVIDER_FACTORIES[kind].get(name)
    if factory is None:
        raise IntegrationNotConfigured(
            kind + " uchun '" + name + "' adapteri o‘rnatilmagan"
        )
    return factory()


def integration_status(environ=None):
    result = {}
    for kind in INTEGRATION_KINDS:
        selected = selected_provider_name(kind, environ)
        result[kind] = {
            "selected": selected,
            "configured": selected != "disabled"
            and selected in _PROVIDER_FACTORIES[kind],
        }
    return result
