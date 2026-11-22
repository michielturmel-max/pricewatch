"""Producten zoeken in de winkels.

Een watch begint met een vraag: "MX Master 3S". Deze module stelt die vraag aan
alle winkels die het aankunnen en levert de producten terug die er echt staan,
met prijs, winkel en link. Het verschil met `check` is dat hier niets wordt
opgeslagen: zoeken is rondkijken, pas een gevonden product wordt een watch.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import extract, fetch, shops

TIMEOUT = 12.0
#: Per winkel houden we tegen dat we er een stortvloed van maken.
MAX_PER_SHOP = 6
#: Standaard proberen we de winkels die aantoonbaar antwoorden.
DEFAULT_SHOPS = ("amazon-nl", "coolblue", "mediamarkt", "apple", "informatique", "aliexpress")


@dataclass
class Hit:
    shop: str
    shop_name: str
    title: str
    price: float
    url: str
    source: str = ""
    note: str = ""
    #: Kan hier een watch van worden? Een winkel die zijn prijs met JavaScript
    #: laadt kan dat niet: zo'n adres blijft leeg bij elke check.
    watchable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "shop": self.shop,
            "shop_name": self.shop_name,
            "title": self.title,
            "price": self.price,
            "url": self.url,
            "source": self.source,
            "note": self.note,
            "watchable": self.watchable,
        }


@dataclass
class ShopResult:
    shop: str
    shop_name: str
    hits: list[Hit] = field(default_factory=list)
    error: str = ""
    #: De winkel weigerde ons (botmuur). Dat is iets anders dan een storing: de
    #: winkel antwoordt, alleen niet aan ons. Daar hoort een andere route bij dan
    #: "probeer het zo nog eens".
    blocked: bool = False
    watchable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "shop": self.shop,
            "shop_name": self.shop_name,
            "hits": [h.as_dict() for h in self.hits],
            "error": self.error,
            "blocked": self.blocked,
            "watchable": self.watchable,
        }


#: Winkels die we niet in de standaardvragen zetten omdat ze ons wegzetten of
#: geen producten teruggeven. Ze blijven zichtbaar in het zoekresultaat, anders
#: lijkt het alsof die winkel het product niet heeft.
SKIPPED_SHOPS = (
    "bol",
    "tweakers",
    "proshop",
    "megekko",
    "azerty",
    "alternate",
    "paradiso",
    "fnac",
    "jumbo",
    "ah",
    "kruidvat",
    "marskramer",
    "zalando",
    "otto",
    "gamma",
    "decathlon",
)


def shop_ids(requested: str = "") -> list[str]:
    if not requested:
        return list(DEFAULT_SHOPS)
    ids = [s.strip() for s in requested.split(",") if s.strip()]
    unknown = [s for s in ids if s not in shops.SHOPS]
    if unknown:
        raise ValueError(f"onbekende winkel {unknown[0]!r}; zie: pricewatch shops")
    return ids


def skipped(requested: str = "") -> list[tuple[str, str]]:
    """Winkels die we bewust niet vroegen, met de reden, voor onder de uitvoer."""
    gevraagd = set(shop_ids(requested))
    return [
        (shops.label(shop_id), shops.SHOPS[shop_id].note)
        for shop_id in SKIPPED_SHOPS
        if shop_id not in gevraagd
    ]


def search_shop(
    shop_id: str,
    term: str,
    timeout: float = TIMEOUT,
    per_shop: int = MAX_PER_SHOP,
) -> ShopResult:
    """Eén winkel doorzoeken op één term."""
    shop = shops.SHOPS.get(shop_id)
    name = shop.name if shop else shop_id
    result = ShopResult(shop=shop_id, shop_name=name)
    result.watchable = shops.zoek_adres(shop_id)
    if not shop:
        result.error = "onbekende winkel"
        return result
    url = shop.url_for(term)
    if not url:
        result.error = "winkel kent geen zoekadres"
        return result
    try:
        response = fetch.get(url, timeout=timeout)
    except fetch.Blocked as exc:
        result.error = str(exc)[:200]
        result.blocked = True
        return result
    except fetch.FetchError as exc:
        result.error = str(exc)[:200]
        return result

    try:
        listing = shops.search_listing(response.body, response.url, shop_id, term)
    except Exception as exc:  # winkelmarkup mag de zoekactie niet sloepen
        result.error = f"parseerfout: {type(exc).__name__}: {exc}"[:200]
        return result

    pool = [c for c in listing.candidates if c.trusted] or listing.candidates
    # Zoekpagina's herhalen hetzelfde product ( banners, varianten, carousels ).
    # Zonder adres of titel te dubbelen, ziet de lijst er raar uit.
    gezien: set[tuple[str, str]] = set()
    for candidate in pool:
        if len(result.hits) >= per_shop:
            break
        if not candidate.price:
            continue
        sleutel = (candidate.url, candidate.title.strip().lower())
        if sleutel in gezien:
            continue
        gezien.add(sleutel)
        result.hits.append(
            Hit(
                shop=shop_id,
                shop_name=name,
                title=candidate.title,
                price=candidate.price,
                url=candidate.url,
                source=candidate.source,
                watchable=result.watchable,
            )
        )
    if not result.hits:
        # Zoekpagina zonder bruikbare prijzen: het kan een categoriepagina zijn.
        result.hits.extend(_follow(response, shop_id, term, timeout, per_shop))
        result.note = listing.note
    return result


def _follow(
    response: Any, shop_id: str, term: str, timeout: float, per_shop: int
) -> list[Hit]:
    """De categoriepagina alsnog aflopen op productpagina's met een echte prijs."""
    shop = shops.SHOPS.get(shop_id)
    name = shop.name if shop else shop_id
    out: list[Hit] = []
    for url in shops.follow_product_links(response.body, response.url, shop_id, term, limit=per_shop):
        try:
            page = fetch.get(url, timeout=timeout)
            candidate = shops.product_price(page.body, page.url, term)
        except Exception:  # één kapotte productlink mag de zoekactie niet stoppen
            continue
        if not candidate or not candidate.price:
            continue
        # Ook hier geldt: een SD-kaartlezer is geen grafische kaart, ook al
        # deelt hij het woord en ook al kwam hij via een productlink.
        if extract.conflicting(term, candidate.title or term):
            continue
        if extract.used_score(term, candidate.title or term):
            continue
        out.append(
            Hit(
                shop=shop_id,
                shop_name=name,
                title=candidate.title or term,
                price=candidate.price,
                url=candidate.url or page.url,
                source=candidate.source,
                note="via productpagina",
                watchable=shops.zoek_adres(shop_id),
            )
        )
    return out


def search(
    term: str,
    shop_list: str = "",
    timeout: float = TIMEOUT,
    per_shop: int = MAX_PER_SHOP,
    on_progress: Callable[[str], None] | None = None,
) -> list[ShopResult]:
    """Alle winkels langs. Een winkel die weigert komt in het resultaat met een fout."""
    results: list[ShopResult] = []
    for shop_id in shop_ids(shop_list):
        if on_progress:
            on_progress(shops.label(shop_id))
        results.append(search_shop(shop_id, term, timeout=timeout, per_shop=per_shop))
        time.sleep(0.4)  # even rustig tussen winkels
    return results


def hits(results: list[ShopResult]) -> list[Hit]:
    """Alle treffers, goedkoopste eerst."""
    flat = [hit for r in results for hit in r.hits]
    flat.sort(key=lambda h: h.price)
    return flat


def tweakers_url(term: str) -> str:
    """De Tweakers-zoekpagina voor een *mens*, niet voor ons.

    Een winkel die ons weigert, laat aan een browser wel zijn prijs zien, en
    Tweakers Pricewatch vergelijkt die winkels zelf. Wij halen deze pagina niet
    op (Tweakers vraagt daar om een captcha) en omzeilen niets: we geven de
    gebruiker het adres waar hij zelf verder kan, en de productpagina die hij
    daarvan overneemt leest de plugin daarna gewoon.
    """
    return shops.SHOPS["tweakers"].url_for(term) if term else ""
