"""HTTP fetching with urllib only.

A price watch runs unattended for weeks, so this module is deliberately
careful: one polite delay per host, retries with backoff, and a clear
Blocked error when a shop puts a bot wall in front of us. A blocked shop is a
normal outcome, not a crash; the caller records it and carries on.
"""

from __future__ import annotations

import gzip
import http.cookiejar
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}
MIN_HOST_INTERVAL = 1.2
BACKOFF_AFTER_BLOCK = 8.0
_last_hit: dict[str, float] = {}
_cooldown: dict[str, float] = {}
_loaded = False
_ctx = ssl.create_default_context()
#: Cookies overleven binnen één run. Sommige winkels zetten eerst een
#: toestemmingspagina voor hun deur; die zet een cookie en pas de tweede vraag
#: komt bij het product uit.
_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_jar),
    urllib.request.HTTPSHandler(context=_ctx),
)
CONSENT_HOSTS = ("myprivacy.dpgmedia.nl",)


def _cooldown_file() -> Path:
    from .store import default_home

    return Path(os.environ.get("PRICEWATCH_HOME") or default_home()) / "cooldown.json"


_COOLDOWN_FILE = _cooldown_file()


class FetchError(Exception):
    """Network or protocol trouble; retrying later is reasonable."""


class Blocked(FetchError):
    """The shop answered, but with a bot wall or a challenge."""


class NotFound(FetchError):
    """The page is gone (404/410)."""

    def __init__(self, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Response:
    url: str
    status: int
    body: str
    elapsed: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def host(self) -> str:
        return urllib.parse.urlparse(self.url).netloc


WALL_MARKERS = (
    "captcha",
    "robot check",
    "are you a human",
    "unusual traffic",
    "access denied",
    "blocked by",
    "cloudflare",
    "ddos protection",
)


def absolute(base: str, href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    return urllib.parse.urljoin(base, href)


def _decode(raw: bytes, encoding: str) -> str:
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    match = re.search(rb'charset=["\']?([\w-]+)', raw[:4096], re.I)
    charset = match.group(1).decode("ascii", "ignore") if match else "utf-8"
    for candidate in (charset, "utf-8", "latin-1"):
        try:
            return raw.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _throttle(host: str) -> None:
    if not _loaded:
        _load_cooldown()
    now = time.monotonic()
    wait = max(
        _cooldown.get(host, 0.0) - now,
        _cooldown.get(f"{host}:interval", MIN_HOST_INTERVAL) - (now - _last_hit.get(host, 0.0)),
    )
    if wait > 0:
        time.sleep(min(wait, 120.0))
    _last_hit[host] = time.monotonic()


def _cool_down(host: str) -> None:
    """A shop that pushed us away gets a longer pause before the next offer.

    Each CLI run is its own process, so the pause is written next to the
    database: a timer that runs every ten minutes has to remember that the shop
    pushed it away, instead of starting over at the polite default every time.
    """
    interval = min(_cooldown.get(f"{host}:interval", MIN_HOST_INTERVAL) * 4, 300.0)
    pause = BACKOFF_AFTER_BLOCK * (interval / MIN_HOST_INTERVAL)
    _cooldown[f"{host}:interval"] = interval
    _cooldown[host] = time.monotonic() + pause
    _save_cooldown(pause)


def _load_cooldown() -> None:
    global _loaded
    _loaded = True
    if not _COOLDOWN_FILE.exists():
        return
    try:
        state = json.loads(_COOLDOWN_FILE.read_text())
    except (ValueError, OSError):
        return
    now = time.time()
    for host, entry in state.items():
        try:
            interval = float(entry.get("interval") or MIN_HOST_INTERVAL)
            until = float(entry.get("until") or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        _cooldown[f"{host}:interval"] = interval
        if until > now:
            _cooldown[host] = time.monotonic() + (until - now)


def _save_cooldown(pause: float) -> None:
    try:
        state = json.loads(_COOLDOWN_FILE.read_text()) if _COOLDOWN_FILE.exists() else {}
    except (ValueError, OSError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state.update(
        {
            key[: -len(":interval")]: {"until": time.time() + pause, "interval": interval}
            for key, interval in _cooldown.items()
            if key.endswith(":interval")
        }
    )
    try:
        _COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COOLDOWN_FILE.write_text(json.dumps(state, indent=1, sort_keys=True))
    except OSError:
        pass


def get(url: str, timeout: float = 12.0, retries: int = 2, data: bytes | None = None) -> Response:
    if not url.startswith(("http://", "https://")):
        raise FetchError(f"geen http-url: {url}")
    host = urllib.parse.urlparse(url).netloc
    last: Exception | None = None
    for attempt in range(retries + 1):
        _throttle(host)
        request = urllib.request.Request(url, data=data, headers=HEADERS, method="POST" if data else "GET")
        started = time.monotonic()
        try:
            with _opener.open(request, timeout=timeout) as handle:
                raw = handle.read()
                body = _decode(raw, (handle.headers.get("Content-Encoding") or "").lower())
                final_url = handle.geturl()
                # Een toestemmingspoort zet zijn cookie en stuurt ons nog niet
                # naar het product. De volgende poging heeft dat cookie wel.
                if _host_of(final_url) in CONSENT_HOSTS and attempt < retries:
                    time.sleep(0.3)
                    continue
                return Response(
                    url=final_url,
                    status=handle.status,
                    body=body,
                    elapsed=time.monotonic() - started,
                    headers={k.lower(): v for k, v in handle.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            body = _decode(raw, (exc.headers.get("Content-Encoding") or "").lower()) if raw else ""
            if exc.code in (404, 410):
                raise NotFound(f"pagina weg ({exc.code}): {url}", exc.code) from exc
            if exc.code in (401, 403, 429) or _is_wall(body):
                _cool_down(host)
                raise Blocked(f"shop houdt ons tegen ({exc.code}): {url}") from exc
            if exc.code >= 500 and attempt < retries:
                last = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            raise FetchError(f"http {exc.code}: {url}") from exc
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise FetchError(f"netwerkfout: {exc}") from exc
    raise FetchError(f"geen antwoord: {last}")


def _host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _is_wall(body: str) -> bool:
    head = body[:20000].lower()
    return any(marker in head for marker in WALL_MARKERS)


def probe(url: str, timeout: float = 10.0) -> tuple[str, str]:
    """Reachability check used by `pricewatch shops`: (status word, note)."""
    try:
        response = get(url, timeout=timeout, retries=1)
    except NotFound as exc:
        return "404", str(exc)
    except Blocked as exc:
        return "blocked", str(exc)
    except FetchError as exc:
        return "netwerk", str(exc)
    scan = _quick_prices(response.body)
    return "ok", f"{len(response.body)} bytes, {scan} prijzen"


def _quick_prices(body: str) -> int:
    from .extract import scan as scan_page

    return len(scan_page(body).candidates)