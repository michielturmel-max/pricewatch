"""The polling loop: fetch every offer of every watch, keep history, raise deals.

Two rules keep this from turning into noise:

  - a price is measured against the situation *before* today's write, so a new
    low never drags its own reference down with it;
  - an unchanged price is not written again for six hours, otherwise a timer
    every ten minutes fills the history with copies of the same number.

A shop that blocks us, times out or reorganises its markup updates its own error
field and the watch carries on with the other shops.
"""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import fetch, shops
from .deals import Deal, evaluate
from .extract import Candidate
from .store import SOFT_NOTE, money

TIMEOUT = 12.0
RESEAT_HOURS = 6


@dataclass
class OfferResult:
    offer: dict[str, Any]
    price: float | None = None
    candidate: Candidate | None = None
    error: str = ""
    url: str = ""
    written: bool = False
    considered: bool = False


@dataclass
class WatchResult:
    watch_id: str
    name: str
    offers: list[OfferResult] = field(default_factory=list)
    deals: list[Deal] = field(default_factory=list)
    best_price: float | None = None
    best_shop: str = ""
    best_url: str = ""
    previous_best: float | None = None

    @property
    def errors(self) -> list[str]:
        return [o.error for o in self.offers if o.error]

    @property
    def ok(self) -> int:
        return sum(1 for o in self.offers if o.price is not None)


def target_url(offer: dict[str, Any]) -> str:
    if offer["kind"] == "url":
        return offer["value"]
    url = shops.search_url(offer["shop"], offer["value"])
    if not url:
        raise fetch.FetchError(f"winkel {offer['shop']} kent geen zoekadres")
    return url


def read_price(html: str, url: str, offer: dict[str, Any], want: str) -> tuple[Candidate | None, str]:
    if offer["kind"] == "url":
        return shops.product_price(html, url, want), ""
    listing = shops.search_listing(html, url, offer["shop"], want)
    pool = [c for c in listing.candidates if c.trusted] or listing.candidates
    linked = [c for c in pool if c.url]
    best = (linked or pool or [None])[0]
    if best is None:
        return None, listing.note or "geen prijs gevonden op deze pagina"
    return best, ""


def fetch_offer(offer: dict[str, Any], want: str, timeout: float = TIMEOUT) -> tuple[Candidate | None, str, str]:
    try:
        url = target_url(offer)
        response = fetch.get(url, timeout=timeout)
    except fetch.Blocked as exc:
        return None, str(exc), ""
    except fetch.NotFound as exc:
        return None, str(exc), ""
    except fetch.FetchError as exc:
        return None, str(exc), ""
    try:
        candidate, note = read_price(response.body, response.url, offer, want)
    except Exception as exc:  # a shop's markup must never take the run down
        return None, f"parseerfout: {type(exc).__name__}: {exc}"[:200], response.url
    if candidate:
        return candidate, "", response.url

    # Geen match op de zoekpagina. Sommige winkels (Coolblue) sturen een
    # zoekopdracht door naar een categoriepagina; dan volgen we de productlink
    # die het beste bij de naam past en lezen we die pagina.
    followed = follow_category(response, offer, want, timeout)
    if followed is not None:
        return followed
    return None, note or "geen prijs gevonden op deze pagina", response.url


def follow_category(response: Any, offer: dict[str, Any], want: str, timeout: float) -> tuple[Candidate | None, str, str] | None:
    """Een categoriepagina alsnog proberen, via de best passende productlink.

    Geeft None terug als er niets te volgen viel, zodat de gewone foutmelding
    van de zoekpagina blijft staan. De bodem van de watch controleert
    check_watch, dus die is hier niet aan de beurt.
    """
    notes: list[str] = []
    for url in shops.follow_product_links(response.body, response.url, offer["shop"], want):
        try:
            page = fetch.get(url, timeout=timeout)
            candidate = shops.product_price(page.body, page.url, want)
        except (fetch.FetchError, ValueError) as exc:
            notes.append(str(exc)[:120])
            continue
        if candidate:
            return candidate, "", page.url
    return None if not notes else (None, "; ".join(notes)[:200], response.url)


def check_watch(
    store: Any,
    watch: dict[str, Any],
    dry_run: bool = False,
    timeout: float = TIMEOUT,
    on_progress: Callable[[str], None] | None = None,
) -> WatchResult:
    result = WatchResult(watch_id=watch["id"], name=watch["name"])
    result.previous_best = watch.get("best_price")
    if not dry_run:
        store.begin_check(watch["id"])
    offers = [o for o in watch["offers"] if o.get("enabled")]
    if not offers:
        result.offers.append(OfferResult(offer={"id": "-", "shop": "", "kind": "", "value": ""}, error="geen winkels"))
        result.best_price = result.previous_best
        return result
    reference = store.reference(watch["id"])
    low_before = watch.get("low_price")
    now = int(time.time())

    floor = watch.get("floor")
    for offer in offers:
        if on_progress:
            on_progress(f"{watch['name']} \u2192 {shops.label(offer['shop'])}")
        candidate, error, page_url = fetch_offer(offer, watch["name"], timeout)
        if candidate and floor is not None and candidate.price < floor:
            error = (
                f"{SOFT_NOTE} {money(candidate.price)} ligt onder je bodem van {money(floor)}"
                " (vermoedelijk een accessoire of een andere variant)"
            )
            candidate = None
        entry = OfferResult(offer=offer, candidate=candidate, error=error, url=page_url)
        if not candidate:
            result.offers.append(entry)
            if not dry_run:
                store.record_failure(watch["id"], offer["id"], error)
            continue

        price = candidate.price
        entry.price = price
        unchanged = offer.get("last_price") == price
        fresh = not (unchanged and (now - int(offer.get("last_checked") or 0)) < RESEAT_HOURS * 3600)
        entry.considered = True
        if result.best_price is None or price < result.best_price:
            result.best_price = price
            result.best_shop = offer["shop"]
            result.best_url = candidate.url or page_url
        result.offers.append(entry)
        if dry_run:
            continue

        if fresh:
            store.record_price(
                watch["id"], offer["id"], price, offer["shop"], candidate.url or page_url, candidate.title, candidate.source
            )
            entry.written = True

        deal = evaluate(
            store,
            watch,
            price,
            offer["shop"],
            candidate.url or page_url,
            candidate.title,
            now=now,
            reference=reference,
            low_ever=low_before,
            low_known=low_before is not None,
        )
        if deal:
            result.deals.append(deal)

    # Er moet altijd een beste prijs staan. De check schrijft een prijs alleen
    # als die veranderd is, dus de beste van déze run is hier de enige die weet
    # wat de watch nu kost. Geeft niemand antwoord, dan blijft de vorige staan:
    # een winkel die zwijgt is geen daling.
    if not dry_run:
        beste = next((o for o in result.offers if o.price == result.best_price), None)
        kandidaat = beste.candidate if beste else None
        store.end_check(
            watch["id"],
            result.best_price,
            beste.offer["shop"] if beste else "",
            beste.url if beste else "",
            (kandidaat.title if kandidaat else "") or "",
            (kandidaat.source if kandidaat else "") or "",
        )
    if result.best_price is None:
        result.best_price = result.previous_best

    result.deals.sort(key=lambda d: d.rank)
    if not dry_run and result.deals:
        store.forget_notices(watch["id"])
    return result


@contextmanager
def run_lock(store: Any):
    """Eén check tegelijk.

    De timer en een tik in het paneel kunnen elkaar op hetzelfde moment raken.
    Twee runs tegelijk verdubbelen het aantal verzoeken per winkel, en juist
    daarom wil je geen extra verzoeken: Amazon sluit de deur bij een paar
    snelle bezoeken. Degene die te laat komt zegt het en stopt.
    """
    path = Path(str(store.path)).parent / "check.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        yield True
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def check_all(
    store: Any,
    only: str | None = None,
    dry_run: bool = False,
    notify: bool = False,
    timeout: float = TIMEOUT,
    on_progress: Callable[[str], None] | None = None,
) -> list[WatchResult]:
    if only:
        watches = [store.watch(store.resolve(only))]
    else:
        watches = store.watches()
    results = []
    for watch in watches:
        result = check_watch(store, watch, dry_run=dry_run, timeout=timeout, on_progress=on_progress)
        results.append(result)
        if notify and not dry_run:
            for deal in result.deals:
                send_notification(deal)
    return results


def send_notification(deal: Deal) -> bool:
    """notify-send, so the deal shows up next to Omarchy's own meldingen."""
    import shutil
    import subprocess

    if not shutil.which("notify-send"):
        return False
    body = deal.text if not deal.url else f"{deal.text}\n{deal.url}"
    try:
        subprocess.run(
            ["notify-send", "-t", "15000", "-a", "pricewatch", "-i", "dialog-information", deal.name, body],
            timeout=10,
            check=False,
            capture_output=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return True


def stale_seconds(store: Any) -> float | None:
    checked = [w["last_checked"] for w in store.watches() if w["last_checked"]]
    if not checked:
        return None
    return time.time() - max(checked)