"""Shops we know how to read.

Each shop is a search-URL template plus, where the generic cascade is not
enough, a listing parser that pairs a title with a price per search result.
The generic path (JSON-LD, meta, microdata) already covers most product pages,
so a shop only needs a parser when its *search results* hide the price.

`blocked` marks shops whose search page answers with a bot wall or a 404 as of
the last check. They stay in the list: the wall is the shop's mood, not a
permanent fact, and a product URL from such a shop usually still works.
"""

from __future__ import annotations

import html as html_lib
import re
import statistics
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlparse

from . import extract, fetch
from .extract import Candidate

MATCH_MIN = 0.5


@dataclass
class Shop:
    id: str
    name: str
    domains: tuple[str, ...] = ()
    search_url: str = ""
    parser: str = ""
    blocked: bool = False
    note: str = ""

    def url_for(self, query: str) -> str:
        return self.search_url.format(q=quote_plus(query)) if self.search_url else ""


SHOPS: dict[str, Shop] = {}


def register(shop: Shop) -> Shop:
    SHOPS[shop.id] = shop
    return shop


register(Shop(id="generic", name="alleen deze pagina", note="geen zoekadres; plak een product-URL"))
register(
    Shop(
        id="bol",
        name="bol.com",
        domains=("bol.com",),
        search_url="https://www.bol.com/nl/nl/s/?searchtext={q}",
        blocked=True,
        note="zoeken geeft 403 zonder browsercookies; product-URL werkt",
    )
)
register(
    Shop(
        id="amazon-nl",
        name="Amazon.nl",
        domains=("amazon.nl", "amazon.com.be", "amazon.de"),
        search_url="https://www.amazon.nl/s?k={q}",
        parser="amazon",
    )
)
register(
    Shop(
        id="coolblue",
        name="Coolblue",
        domains=("coolblue.nl",),
        search_url="https://www.coolblue.nl/zoeken?query={q}",
    )
)
register(
    Shop(
        id="informatique",
        name="Informatique",
        domains=("informatique.nl",),
        search_url="https://www.informatique.nl/zoeken?q={q}",
        parser="informatique",
    )
)
register(
    Shop(
        id="tweakers",
        name="Tweakers Pricewatch",
        domains=("tweakers.net",),
        search_url="https://tweakers.net/pricewatch/zoeken/?keyword={q}",
        parser="tweakers",
        blocked=True,
        note="zoeken vraagt om een captcha; een pricewatch-productpagina werkt wel",
    )
)
register(
    Shop(
        id="mediamarkt",
        name="MediaMarkt",
        domains=("mediamarkt.nl",),
        search_url="https://www.mediamarkt.nl/nl/search.html?query={q}",
    )
)
register(
    Shop(
        id="proshop",
        name="ProShop",
        domains=("proshop.nl",),
        search_url="https://www.proshop.nl/Search?q={q}",
        parser="proshop",
        blocked=True,
        note="zoekpagina gaf andere artikelen en daarna 429; product-URL kan wel",
    )
)
register(
    Shop(
        id="aliexpress",
        name="AliExpress",
        domains=("aliexpress.com",),
        search_url="https://nl.aliexpress.com/w/wholesale-{q}.html",
        parser="aliexpress",
        note="alleen via --zoek: de productpagina haalt zijn prijs via JavaScript",
    )
)
register(
    Shop(
        id="apple",
        name="Apple",
        domains=("apple.com",),
        search_url="https://www.apple.com/nl/search/{q}?src=globalnav",
    )
)
register(
    Shop(
        id="megekko",
        name="Megekko",
        domains=("megekko.nl",),
        search_url="https://www.megekko.nl/search?q={q}",
        blocked=True,
        note="zoekpagina leidt door naar /info/search",
    )
)
register(
    Shop(
        id="azerty",
        name="Azerty",
        domains=("azerty.nl",),
        search_url="https://www.azerty.nl/zoeken/?q={q}",
        blocked=True,
        note="403 zonder browsercookies",
    )
)
register(
    Shop(
        id="alternate",
        name="Alternate",
        domains=("alternate.nl",),
        search_url="https://www.alternate.nl/zoeken?q={q}",
        blocked=True,
        note="404 op het zoekpad",
    )
)
register(
    Shop(
        id="anker",
        name="Anker",
        domains=("anker.com",),
        search_url="https://www.anker.com/nl/search?q={q}",
    )
)
register(
    Shop(
        id="paradiso",
        name="Paradiso",
        domains=("paradiso.nl",),
        search_url="https://www.paradiso.nl/nl/search/?q={q}",
        blocked=True,
        note="404 op het zoekpad",
    )
)
register(
    Shop(
        id="fnac",
        name="Fnac",
        domains=("fnac.nl",),
        search_url="https://www.fnac.nl/SearchResult/ResultList.aspx?Search={q}",
        blocked=True,
        note="niet bereikbaar vanuit deze machine",
    )
)
for _supermarkt in (
    ("jumbo", "Jumbo", "https://www.jumbo.nl/zoeken?q={q}", "429, te veel verzoeken"),
    ("ah", "Albert Heijn", "https://www.ah.nl/zoeken?q={q}", "403 zonder browsercookies"),
    ("kruidvat", "Kruidvat", "https://www.kruidvat.nl/zoeken?q={q}", "403 zonder browsercookies"),
    ("marskramer", "Marskramer", "https://www.marskramer.nl/zoeken?q={q}", "403 zonder browsercookies"),
    ("otto", "Otto", "https://www.otto.nl/zoeken?q={q}", "403 zonder browsercookies"),
    ("zalando", "Zalando", "https://www.zalando.nl/catalog/?q={q}", "403 zonder browsercookies"),
    ("gamma", "Gamma", "https://www.gamma.nl/zoeken?q={q}", "429, te veel verzoeken"),
    ("decathlon", "Decathlon", "https://www.decathlon.nl/zoeken?q={q}", "403 zonder browsercookies"),
):
    register(
        Shop(
            id=_supermarkt[0],
            name=_supermarkt[1],
            search_url=_supermarkt[2],
            blocked=True,
            note=_supermarkt[3],
        )
    )


def detect(url: str) -> str:
    host = re.sub(r"^www\.", "", (url or "").split("/")[2].lower()) if "//" in (url or "") else ""
    for shop in SHOPS.values():
        for domain in shop.domains:
            if host == domain or host.endswith("." + domain):
                return shop.id
    return "generic"


def label(shop_id: str) -> str:
    return SHOPS.get(shop_id, Shop(id=shop_id, name=shop_id)).name


def search_url(shop_id: str, query: str) -> str:
    shop = SHOPS.get(shop_id)
    return shop.url_for(query) if shop else ""


def parse_query(text: str) -> tuple[str, str]:
    """`amazon-nl=usb-c hub` -> (shop, query). Without a shop, no split."""
    text = (text or "").strip().replace("+", " ")
    if "=" in text:
        shop, _, query = text.partition("=")
        shop = shop.strip().lower()
        if shop in SHOPS and query.strip():
            return shop, query.strip()
    return "generic", text


#: Winkels waarvan de productpagina de prijs met JavaScript laadt. Hun adres is
#: prima om te *zoeken*, maar een watch erop volgt elke tien minuten een pagina
#: waar geen prijs in staat, en ziet er dan stilletjes leeg uit.
ZOEK_ONLY = ("aliexpress",)


def alleen_zoeken(shop_id: str) -> bool:
    return shop_id in ZOEK_ONLY


def zoek_adres(shop_id: str) -> bool:
    """Zal een watch op deze winkel ooit een prijs vinden?"""
    return not alleen_zoeken(shop_id)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _strip_tags(fragment: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


def _eigen_adres(url: str, base_url: str) -> bool:
    """Wijst deze kandidaat naar een productpagina en niet naar de pagina zelf?"""
    eigen = (url or "").split("#", 1)[0].rstrip("/")
    return bool(eigen) and eigen != base_url.split("#", 1)[0].rstrip("/")


def _split_candidates(html: str, base_url: str, want: str, shop: Shop) -> list[Candidate]:
    """Kandidaten uit een zoekpagina: alleen wat zichzelf als product aanwijst.

    Een zoekpagina toont producten, maar ook banners ("tot € 10,- inruilkorting")
    en de paginatitel is hier de zoekterm. Een prijs uit de paginatek draagt dus de
    hele zoekterm als titel, matcht daardoor perfect, en hoort bij geen enkel
    product; een kandidaat zonder eigen productadres verwijst naar de zoekpagina
    zelf, en zo'n adres wordt een watch die een zoekpagina blijft volgen.
    """
    if shop.parser == "amazon":
        found = _amazon(html, base_url)
    elif shop.parser == "proshop":
        found = _proshop(html, base_url)
    elif shop.parser == "aliexpress":
        found = _aliexpress(html, base_url)
    elif shop.parser == "informatique":
        found = _informatique(html, base_url)
    elif shop.parser == "tweakers":
        found = _tweakers(html, base_url)
    else:
        found = extract.scan(html, base_url, want).candidates
    return [
        c for c in found if c.source != extract.CONF_REGEX and _eigen_adres(c.url, base_url)
    ]


@dataclass
class Listing:
    """What a search page yielded, and why it yielded nothing when it did."""

    candidates: list[Candidate] = field(default_factory=list)
    note: str = ""

    def __bool__(self) -> bool:
        return bool(self.candidates)


def _drop_implausible(
    matched: list[Candidate], everything: list[Candidate], want: str
) -> tuple[list[Candidate], str]:
    """Throw out accessories priced as if they were the product itself.

    A search for "AirPods Pro 2" also returns cases for €4,84 and a pair of
    €5,46 ear tips, and those match the name every bit as well as the real
    thing does. Two things give them away.

    Their title names an accessory the watch never asked for, and their price
    sits far below what the rest of the page asks: the product and its
    accessories live in different orders of magnitude. A candidate costing
    less than a quarter of the page's median is a different product, and a
    wrong price here would quietly rewrite months of history.
    """
    pool = [c for c in matched if not extract.accessory_score(want, c.title)]
    if not pool:
        # Iedereen is een accessoire, dus is er geen product gevonden. Terugvalen
        # op de accessoires zou precies het fout bedrag opleveren dat we hier
        # juist weg willen hebben.
        return [], "alle gevonden producten zijn accessoires"
    if len(everything) < 5:
        return pool, ""
    # The matched products describe the product best, but only once there are
    # enough of them; with a handful, the rest of the page is the better idea
    # of what this product costs.
    own = statistics.median(sorted(c.price for c in pool))
    whole = statistics.median(sorted(c.price for c in everything))
    baseline = own if (len(pool) >= 5 and own >= 10) else (whole if whole >= 10 else 0.0)
    if not baseline:
        return pool, ""
    lowest_plausible = baseline * 0.25
    kept = [c for c in pool if c.price >= lowest_plausible]
    if len(kept) == len(pool):
        return pool, ""
    note = "" if kept else "alle gevonden prijzen liggen ver onder de productprijs; vermoedelijk accessoires"
    return kept, note


def _pick(candidates: list[Candidate], want: str) -> list[Candidate]:
    """Keep only results that really are the watched product.

    Two guards, because a wrong price is worse than no price: the whole point
    of a watch is that the number is about *your* product.

    Words that show up on half the result page carry no information ("usb" on
    a USB page), so they do not count towards the score. What is left has to be
    covered. If everything in the name turns out to be common, the bar is all
    of the name's words, because then nothing distinguishes the products and a
    cheap accessory must not be mistaken for the thing being watched.
    """
    if not want:
        return candidates
    wanted = extract.tokenize(want)
    if not wanted:
        return candidates
    titles = [c.title for c in candidates]
    frequency: dict[str, int] = {}
    for title in titles:
        for word in extract.tokenize(title):
            frequency[word] = frequency.get(word, 0) + 1
    common = max(2, len(titles) * 0.3)
    informative = {
        word
        for word in wanted
        if min((frequency.get(w, 0) for w in extract.variants(word)), default=0) <= common
    }

    scored: list[tuple[float, Candidate]] = []
    for candidate in candidates:
        # Een SD-kaartlezer bevat het woord "kaart" en scoort prima op "grafische
        # kaart", maar het is een ander product. Dat weegt zwaarder dan de score.
        if extract.conflicting(want, candidate.title):
            continue
        if extract.used_score(want, candidate.title):
            continue
        words = extract.tokenize(candidate.title)
        if informative:
            score = sum(1 for word in informative if extract.word_hit(word, words)) / len(informative)
            bar = MATCH_MIN
        else:
            score = sum(1 for word in wanted if extract.word_hit(word, words)) / len(wanted)
            bar = 1.0
        scored.append((score, candidate))
    keep = [(score, c) for score, c in scored if score >= bar]
    if not keep:
        return []
    keep.sort(key=lambda pair: (-pair[0], pair[1].price))
    return [c for _, c in keep]


def _amazon(html: str, base_url: str) -> list[Candidate]:
    out: list[Candidate] = []
    for chunk in html.split('data-component-type="s-search-result"')[1:]:
        # The title anchor and the price block can sit in different subtrees of
        # the same card, so pair them by position: the anchor that opens the
        # h2 is this product's link, and the first price after the h2 is this
        # product's price. Taking any price or any link from the chunk mixes up
        # cards with nested carousels.
        anchor = re.search(
            r'<a[^>]*class="[^"]*a-link-normal[^"]*"[^>]*href="([^"]+)"[^>]*>\s*<h2([^>]*)>(.*?)</h2>',
            chunk,
            re.S,
        )
        if not anchor:
            continue
        head_attrs, inner = anchor.group(2), anchor.group(3)
        label = re.search(r'aria-label="([^"]+)"', head_attrs)
        title = extract.plain_title(_strip_tags(label.group(1) if label else inner))
        price = re.search(r'<span class="a-offscreen">(.*?)</span>', chunk[anchor.end() :], re.S)
        value = extract.parse_price(_strip_tags(price.group(1))) if price else None
        if not value or not title:
            continue
        out.append(
            Candidate(
                price=value,
                source=extract.CONF_STATE,
                title=title[:200],
                url=fetch.absolute(base_url, anchor.group(1)),
            )
        )
    return out


def _proshop(html: str, base_url: str) -> list[Candidate]:
    out: list[Candidate] = []
    for chunk in html.split("site-productlist-item")[1:]:
        price = re.search(r'class="priceContainer".*?class="site-currency-lg">(.*?)</span>', chunk, re.S)
        title = re.search(r'<a[^>]+title="([^"]+)"', chunk)
        link = re.search(r'class="site-product-link" href="([^"]+)"', chunk)
        value = extract.parse_price(_strip_tags(price.group(1))) if price else None
        if not value:
            continue
        out.append(
            Candidate(
                price=value,
                source=extract.CONF_STATE,
                title=extract.plain_title(html_lib.unescape(title.group(1)))[:200] if title else "",
                url=fetch.absolute(base_url, link.group(1)) if link else base_url,
            )
        )
    return out


# AliExpress zet de prijs in de aria-label van het prijsblok en de titel in de
# aria-label van het kopblok, beide binnen dezelfde productkaart. De kaart begint
# bij de anchor met search-card-item, dus alles tot de volgende kaart hoort erbij.
ALIEXPRESS_CARD_RE = re.compile(
    r'<a\b[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"', re.S | re.I
)
ALIEXPRESS_PRICE_RE = re.compile(
    r'class="us--price--[^"]*"[^>]*aria-label="([^"]+)"', re.S | re.I
)
ALIEXPRESS_TITLE_RE = re.compile(r'aria-label="([^"]+)"[^>]*role="heading"', re.S | re.I)


def _aliexpress(html: str, base_url: str) -> list[Candidate]:
    out: list[Candidate] = []
    starts = [match.start() for match in ALIEXPRESS_CARD_RE.finditer(html)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(html)
        card = html[start:end]
        link = ALIEXPRESS_CARD_RE.match(card)
        price = ALIEXPRESS_PRICE_RE.search(card)
        title = ALIEXPRESS_TITLE_RE.search(card)
        value = extract.parse_price(price.group(1)) if price else None
        if not value or not title:
            continue
        out.append(
            Candidate(
                price=value,
                source=extract.CONF_STATE,
                title=extract.plain_title(_strip_tags(title.group(1)))[:200],
                url=fetch.absolute(base_url, html_lib.unescape(link.group(1))) if link else base_url,
            )
        )
    return out


# Informatique zet per product één <figure class="card-product-grid"> neer met
# de titel in een <p class="title"> en de prijs in een <strong class="price">.
INFORMATIQUE_CARD = "card-product-grid"
INFORMATIQUE_TITLE_RE = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*>\s*<p class="title[^"]*">(.*?)</p>', re.S | re.I
)
INFORMATIQUE_PRICE_RE = re.compile(r'<strong class="price"[^>]*>(.*?)</strong>', re.S | re.I)


def _informatique(html: str, base_url: str) -> list[Candidate]:
    out: list[Candidate] = []
    for chunk in html.split(INFORMATIQUE_CARD)[1:]:
        title = INFORMATIQUE_TITLE_RE.search(chunk)
        price = INFORMATIQUE_PRICE_RE.search(chunk)
        value = extract.parse_price(_strip_tags(price.group(1))) if price else None
        if not title or not value:
            continue
        out.append(
            Candidate(
                price=value,
                source=extract.CONF_STATE,
                title=extract.plain_title(_strip_tags(title.group(2)))[:200],
                url=fetch.absolute(base_url, html_lib.unescape(title.group(1))),
            )
        )
    return out


# Tweakers Pricewatch toont per kaart twee bedragen: het eerste is de
# adviesprijs, het tweede de laagste winkelprijs. Alleen dat tweede telt.
TWEAKERS_CARD = '<div class="card">'
TWEAKERS_TITLE_RE = re.compile(
    r'<p class="title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I
)
TWEAKERS_CARD_PRICE_RE = re.compile(
    r'<p class="price">.*?<a[^>]*>(.*?)</a>', re.S | re.I
)


def _tweakers(html: str, base_url: str) -> list[Candidate]:
    out: list[Candidate] = []
    for chunk in html.split(TWEAKERS_CARD)[1:]:
        title = TWEAKERS_TITLE_RE.search(chunk)
        price = TWEAKERS_CARD_PRICE_RE.search(chunk)
        value = extract.parse_price(_strip_tags(price.group(1))) if price else None
        if not title or not value:
            continue
        url = fetch.absolute(base_url, html_lib.unescape(title.group(1)))
        if "/pricewatch/" not in url:
            continue
        out.append(
            Candidate(
                price=value,
                source=extract.CONF_STATE,
                title=extract.plain_title(_strip_tags(title.group(2)))[:200],
                url=url,
            )
        )
    return out


TWEAKERS_PRODUCT_RE = re.compile(
    r'<a[^>]*class="price"[^>]*title="([^"]*)"[^>]*>(.*?)</a>', re.S | re.I
)
TWEAKERS_LOWEST_RE = re.compile(r'class="lowest-price"[^>]*>(.*?)</span>', re.S | re.I)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


def _tweakers_product(html: str, url: str) -> Candidate | None:
    """De laagste nieuwe prijs op een Tweakers Pricewatch-productpagina.

    Tweakers vergelijkt zelf tientallen winkels. Het bedrag in de eerste
    prijsregel is de laagste nieuwe prijs; de refurbishedlijst eronder is een
    ander product en telt niet mee.
    """
    price = TWEAKERS_PRODUCT_RE.search(html)
    if not price:
        return None
    lowest = TWEAKERS_LOWEST_RE.search(price.group(2))
    value = extract.parse_price(_strip_tags(lowest.group(1) if lowest else price.group(1)))
    if not value:
        return None
    title = H1_RE.search(html)
    name = extract.plain_title(_strip_tags(title.group(1))) if title else ""
    shop_name = ""
    label = price.group(1)
    if " bij " in label:
        shop_name = _strip_tags(html_lib.unescape(label.split(" bij ", 1)[1])).strip()
    if shop_name:
        name = f"{name} (via {shop_name})" if name else f"via {shop_name}"
    return Candidate(
        price=value,
        source=extract.CONF_STATE,
        title=name[:200],
        url=url,
        available=True,
    )


def search_listing(html: str, base_url: str, shop_id: str, want: str) -> Listing:
    shop = SHOPS.get(shop_id, SHOPS["generic"])
    candidates = _split_candidates(html, base_url, want, shop)
    matched = _pick(candidates, want)
    if not matched:
        if candidates:
            return Listing([], "geen van de gevonden producten past op deze naam")
        return Listing([], "geen prijzen op deze zoekpagina")
    kept, note = _drop_implausible(matched, candidates, want)
    return Listing(kept, note)


# Coolblue (en sommige andere winkels) sturen een zoekopdracht door naar een
# categoriepagina. De prijzen staan daar wel, maar de titels matchen niet
# streng genoeg. Daarom: zoek de productlink waarvan de tekst het best past en
# lees daarna die productpagina.
PRODUCT_URL_HINTS = ("/product/", "/product-detail", "/productpage", "/nl/p/", "/nl/product/")
ANCHOR_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)


def follow_product_links(html: str, base_url: str, shop_id: str, want: str, limit: int = 3) -> list[str]:
    """Productadressen uit een zoek- of categoriepagina, beste match eerst."""
    del shop_id  # elke winkel gebruikt dezelfde hints; per domein filteren we al
    host = _host(base_url)
    seen: dict[str, int] = {}
    for href, inner in ANCHOR_RE.findall(html):
        url = fetch.absolute(base_url, html_lib.unescape(href))
        if _host(url) != host or not url.startswith(("http://", "https://")):
            continue
        if not any(hint in url for hint in PRODUCT_URL_HINTS):
            continue
        text = extract.plain_title(_strip_tags(inner))
        if not text or len(text) < 4:
            continue
        score = extract.match_score(want, text)
        if score <= 0:
            continue
        url = url.split("#", 1)[0]
        if url in seen:
            seen[url] = max(seen[url], score)
        else:
            seen[url] = score
    ranked = sorted(seen.items(), key=lambda pair: (-pair[1], len(pair[0])))
    return [url for url, _ in ranked[:limit]]


def is_search_url(url: str) -> bool:
    """Is dit adres een zoekpagina van een van de winkels die we kennen?"""
    clean = (url or "").split("#", 1)[0]
    for shop in SHOPS.values():
        prefix = shop.search_url.partition("{q}")[0]
        if prefix and clean.startswith(prefix):
            return True
    return False


def product_price(html: str, url: str, want: str = "") -> Candidate | None:
    if detect(url) == "tweakers":
        return _tweakers_product(html, url)
    scan = extract.scan(html, url, want)
    # Op een echte productpagina is de paginatitel het product, en is een prijs
    # uit de paginatek een laatste hulpmiddel. Op een zoekpagina is die titel de
    # zoekterm, en is zo'n prijs een banner ("tot € 10,- inruilkorting") die bij
    # geen enkel product hoort. Een watch die zo'n pagina volgt wil dat liever
    # niets weten dan een verzonnen bedrag.
    if is_search_url(url):
        scan.candidates = [c for c in scan.candidates if c.source != extract.CONF_REGEX]
    usable = [c for c in scan.candidates if c.trusted and (c.available is not False)]
    if not usable:
        usable = [c for c in scan.candidates if c.available is not False]
    if not usable:
        return None
    # Bij gestructureerde prijzen wint de laagste: varianten en bundels. Zijn die
    # er niet, dan wint de prijs die de winkel het vaakst opschrijft. De goedkoopste
    # nemen zou een eenheidsprijs of een los getal uit een aanbevelingsrij kiezen.
    usable.sort(key=lambda c: (not c.trusted, c.price if c.trusted else -c.count))
    best = usable[0]
    if best.url:
        best.url = fetch.absolute(url, best.url)
    elif url:
        best.url = url
    return best