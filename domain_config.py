"""Ko‘prikning asosiy domeni va HTTP xavfsizlik siyosati."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from runtime_config import env_flag, is_production


DOMAIN_RE = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def primary_domain(environ=None) -> str:
    env = os.environ if environ is None else environ
    return str(env.get("PRIMARY_DOMAIN", "koprik.uz")).strip().lower().rstrip(".")


def configured_allowed_hosts(environ=None):
    env = os.environ if environ is None else environ
    raw = str(env.get("ALLOWED_HOSTS", "")).strip()
    if raw:
        values = [item.strip().lower().rstrip(".") for item in raw.split(",")]
        return list(dict.fromkeys(item for item in values if item))
    if is_production(env):
        domain = primary_domain(env)
        return [
            domain,
            "www." + domain,
            "admin." + domain,
            "*.up.railway.app",
            "*.railway.internal",
            "localhost",
            "127.0.0.1",
        ]
    return ["*"]


def validate_domain_config(environ=None) -> None:
    env = os.environ if environ is None else environ
    if not is_production(env):
        return
    errors = []
    domain = primary_domain(env)
    if not DOMAIN_RE.fullmatch(domain):
        errors.append("PRIMARY_DOMAIN haqiqiy domen bo‘lishi kerak")

    base_url = str(env.get("BASE_URL", "")).strip().rstrip("/")
    base_host = (urlparse(base_url).hostname or "").lower().rstrip(".")
    if base_host != domain:
        errors.append("BASE_URL hosti PRIMARY_DOMAIN bilan bir xil bo‘lishi kerak")

    hosts = configured_allowed_hosts(env)
    if "*" in hosts:
        errors.append("productionda ALLOWED_HOSTS=* ishlatib bo‘lmaydi")
    if domain not in hosts:
        errors.append("ALLOWED_HOSTS ichida PRIMARY_DOMAIN bo‘lishi kerak")
    if env_flag("CANONICAL_WWW_REDIRECT", True, env) and "www." + domain not in hosts:
        errors.append("www yo‘naltirish uchun ALLOWED_HOSTS ichida www domeni bo‘lishi kerak")
    for host in hosts:
        if "://" in host or "/" in host or " " in host:
            errors.append("ALLOWED_HOSTS faqat host nomlaridan iborat bo‘lishi kerak")
            break

    if errors:
        raise RuntimeError("Production domen sozlamasi noto‘g‘ri:\n - " + "\n - ".join(errors))


def _request_host(request) -> str:
    return (request.headers.get("host") or "").split(":", 1)[0].strip().lower().rstrip(".")


def _request_is_https(request) -> bool:
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    return forwarded == "https" or request.url.scheme == "https"


def apply_security_headers(response, *, production: bool, https: bool):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(self), camera=(self), microphone=(self), payment=(self)",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "base-uri 'self'; object-src 'none'; form-action 'self'",
    )
    if production and https:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


class DomainPolicyMiddleware(BaseHTTPMiddleware):
    """www → asosiy domen yo‘naltirishi va buzmaydigan xavfsizlik sarlavhalari."""

    def __init__(self, app, *, domain: str, production: bool, redirect_www: bool):
        super().__init__(app)
        self.domain = domain
        self.production = bool(production)
        self.redirect_www = bool(redirect_www)

    async def dispatch(self, request, call_next):
        is_https = _request_is_https(request)
        if (
            self.production
            and self.redirect_www
            and _request_host(request) == "www." + self.domain
        ):
            query = ("?" + request.url.query) if request.url.query else ""
            response = RedirectResponse(
                "https://" + self.domain + request.url.path + query,
                status_code=308,
            )
            return apply_security_headers(response, production=True, https=True)

        response = await call_next(request)
        return apply_security_headers(
            response,
            production=self.production,
            https=is_https,
        )
