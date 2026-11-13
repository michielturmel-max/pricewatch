"""Price extraction from shop HTML.

Pure functions only: no network, no filesystem. Everything here has to survive
being handed a page from a shop that reorganised its markup last week, so the
strategies are ordered from trustworthy to desperate and each result says where
it came from. Confidence is what the caller uses to decide whether a scraped
number may become the price of record.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable

CONF_JSONLD = "json-ld"
CONF_MICRODATA = "microdata"
CONF_META = "meta"
CONF_STATE = "state"
CONF_REGEX = "regex"

TRUSTED = (CONF_JSONLD, CONF_MICRODATA, CONF_META)

PRICE_RE = re.compile(
    r"(?:€|eur)\s*(\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?|\d{1,3})"
    r"|(\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?|\d+,\d{2})\s*(?:€|eur)",
    re.I,
)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


@dataclass
class Candidate:
    """One price found on a page, plus enough provenance to judge it."""

    price: float
    source: str
    currency: str = "EUR"
    title: str = ""
    url: str = ""
    available: bool | None = None
    #: Hoe vaak dit bedrag op de pagina voorkomt. Een echte productprijs staat
    #: er meerdere keren, een eenheidsprijs of los getal één keer.
    count: int = 1

    @property
    def trusted(self) -> bool:
        return self.source in TRUSTED


@dataclass
class PageScan:
    candidates: list[Candidate] = field(default_factory=list)
    title: str = ""

    def best(self) -> Candidate | None:
        if not self.candidates:
            return None
        trusted = [c for c in self.candidates if c.trusted]
        pool = trusted or self.candidates
        return min(pool, key=lambda c: c.price)


LENIENT_RE = re.compile(
    r"^\s*(?:€|eur)?\s*"
    r"(\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?|\d{1,3})"
    r"(?:,?-)?\s*(?:€|eur)?\s*$",
    re.I,
)


def parse_price(text: Any) -> float | None:
    """Parse a price the way Dutch shops write it: 1.234,56 and €1299,-.

    Structured fields (a JSON-LD price, a meta content, a value the user typed)
    may hold a bare number, so a whole-string match is accepted too. The strict
    pattern stays in use for scanning raw page text, where any loose number
    would turn a version number into a price.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        value = float(text)
        return round(value, 2) if value > 0 else None
    raw = str(text).strip()
    if not raw:
        return None
    match = PRICE_RE.search(raw)
    if match:
        value = _number(match.group(1) or match.group(2) or "")
        if value:
            return value
    whole = LENIENT_RE.match(raw)
    if whole:
        return _number(whole.group(1))
    return None


def _number(body: str) -> float | None:
    cleaned = body.strip().replace("\u00a0", " ").replace(" ", "")
    if not cleaned:
        return None
    cleaned = re.sub(r"[^\d.,]", "", cleaned)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".") if cleaned.rfind(",") > cleaned.rfind(".") else cleaned.replace(",", "")
    elif "," in cleaned:
        head, _, tail = cleaned.rpartition(",")
        cleaned = f"{head.replace('.', '')}.{tail}" if len(tail) == 2 else head.replace(".", "")
    elif "." in cleaned:
        head, _, tail = cleaned.rpartition(".")
        cleaned = f"{head}.{tail}" if len(tail) in (1, 2) else cleaned.replace(".", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return round(value, 2) if value > 0 else None


def plain_title(title: str) -> str:
    """A shop title without the advertisement label shops prepend."""
    return SPONSORED_RE.sub("", title or "").strip()


def text_of(html: str) -> str:
    return WS_RE.sub(" ", TAG_RE.sub(" ", html or ""))


ORDINAL_RE = re.compile(r"^(\d+)(?:e|ste|de|en|nd|d|st|rd|r|th|gen|generatie)?$", re.I)
SPONSORED_RE = re.compile(r"^(?:gesponsorde\s+)?(?:advertentie|ad)\s*[-–:]\s*", re.I)


def tokenize(name: str) -> set[str]:
    """Words that identify a product, model numbers included.

    "AirPods Pro 2" has to tell generation 2 apart from generation 1, so a bare
    digit counts as a word, and the ways Dutch and English shops write an
    ordinal ("2e", "2nd", "2e generatie") all fold back to the digit.
    """
    words = set()
    for raw in re.findall(r"[a-z0-9]+", (name or "").lower()):
        word = raw
        if raw.isdigit():
            word = raw
        else:
            match = ORDINAL_RE.match(raw)
            if match:
                word = match.group(1)
            elif len(raw) == 2 and raw[0].isdigit():
                word = raw[0]
        if len(word) > 1 or word.isdigit():
            words.add(word)
    stop = {"de", "het", "en", "van", "voor", "met", "per", "a", "in", "op", "naar", "nieuwe", "new", "met"}
    return {w for w in words if w not in stop}


ACCESSORY_WORDS = (
    "hoes",
    "hoesje",
    "cover",
    "case",
    "sleeve",
    "pouch",
    "beschermhoes",
    "beschermfolie",
    "screenprotector",
    "screen",
    "folie",
    "oordopjes",
    "oordop",
    "earhook",
    "earhooks",
    "haakje",
    "haakjes",
    "hooks",
    "wing",
    "wings",
    "wingtip",
    "earplug",
    "earplug",
    "eartip",
    "tips",
    "siliconen",
    "silicone",
    "lanyard",
    "keychain",
    "sleutelhanger",
    "hanger",
    "wristband",
    "polsbandje",
    "vervangend",
    "vervanger",
    "replacement",
    "reinigingsset",
    "reiniging",
    "cleaning",
    "cleaner",
    "polish",
    "sticker",
    "labels",
    "label",
    "bandje",
    "kabels",
    "mousepad",
    "muismat",
    "muisstof",
    "matje",
    "onderlegger",
    "doekje",
    "laser",
    "accessoire",
    "accessory",
)

#: Tweakers zet refurbished en tweedehands in een aparte lijst, want een gebruikte
#: prijs is niet de prijs van een nieuw product. Dezelfde regel hier: een
#: zoekresultaat met zo'n woord telt niet mee, tenzij de watch er zelf om vraagt.
USED_WORDS = (
    "refurbished",
    "refurb",
    "tweedehands",
    "gebruikt",
    "gerenoveerd",
    "occasie",
    "occasion",
    "b-stock",
    "open box",
    "open-box",
    "retour",
)


def used_score(want: str, title: str) -> list[str]:
    wanted = (want or "").lower()
    looked_for = any(word in wanted for word in ("refurbished", "refurb", "tweedehands", "gebruikt", "occasie"))
    if looked_for:
        return []
    haystack = (title or "").lower()
    return [word for word in USED_WORDS if word in haystack]


#: Woorden die van het gezochte product een ander product maken. Wie naar een
#: "grafische kaart" zoekt wil geen SD-kaartlezer. De zoekterm blijft zelf
#: meetellen, dus wie écht een SD-kaart zoekt vindt er nog steeds een.
CONFLICTING: dict[str, tuple[str, ...]] = {
    "kaart": ("sd", "sdkaart", "sim", "simkaart", "geheugen", "spel", "cadeau", "business"),
    "videokaart": ("videocamera", "camera"),
    "geheugen": ("sd", "sdkaart"),
}


def conflicting(want: str, title: str) -> list[str]:
    """Woorden in de titel die er een ander product van maken dan gevraagd.

    Een woord dat in de zoekterm zelf al staat telt niet mee: wie "sd kaart"
    zoekt wil juist een SD-kaart hebben.
    """
    wanted = tokenize(want)
    woorden = tokenize(title)
    gevonden = []
    for word, tegen in CONFLICTING.items():
        if word not in wanted:
            continue
        for ander in tegen:
            if ander in wanted:
                continue
            if word_hit(ander, woorden):
                gevonden.append(ander)
                break
    return gevonden


#: Woorden die in de praktijk hetzelfde product noemen. Wie "wc papier" typt
#: bedoelt toiletpapier, maar geen enkele winkel schrijft "wc" in de titel.
#: Zonder deze lijst blijft een goede zoekterm op een lege lijst staan.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "wc": ("toilet", "toiletpapier", "toiletrol", "wcpapier", "sanitair"),
    "toilet": ("wc", "toiletpapier", "toiletrol"),
    "tissue": ("servet", "servetten", "keukenrol", "handdoek"),
    "servet": ("tissue", "tissues", "keukenrol", "handdoek"),
    "tv": ("televisie", "televisies"),
    "televisie": ("tv",),
    "muis": ("mouse",),
    "mouse": ("muis",),
    "toetsenbord": ("keyboard",),
    "laptop": ("notebook", "macbook"),
    "monitor": ("beeldscherm",),
    "koelkast": ("vrieskast", "diepvries"),
}


def variants(word: str) -> set[str]:
    """Een woord plus zijn synoniemen, zodat beide kanten van de match werken."""
    return {word, *SYNONYMS.get(word, ())}


def word_hit(word: str, words: set[str]) -> bool:
    """Komt `word` (of een synoniem) voor in de woorden van een titel?

    Nederlands plakt woorden aan elkaar, dus "papier" staat niet in "toiletpapier".
    Daarom telt een titelwoord ook mee als het met het zoekwoord begint. Het
    einde telt alleen mee als wat erachter zinloos is: "papieren" is toiletpapier,
    maar "fotopapier" is een ander product en dat moet hier niet binnenkomen.
    """
    for vorm in variants(word):
        if vorm in words:
            return True
        if len(vorm) < 3:
            continue
        if any(woord.startswith(vorm) for woord in words):
            return True
        if any(woord.endswith(vorm) and _is_plural(woord[: -len(vorm)]) for woord in words):
            return True
    return False


def _is_plural(stuk: str) -> bool:
    """Is dit stuk een meervouds- of verkleiningsuitgang en dus betekenisloos?"""
    return stuk in {"", "en", "s", "es", "n", "e", "je", "tje", "tjes"}


#: Accessoires die een winkel als samenstelling schrijft: "muishoes" is een
#: hoes, "opbergdoos" een doos. Zonder deze lijst slipt een €5-doosje voor een
#: €75-muis de zoeklijst binnen, want de titel noemt het product wél.
COMPOUND_ACCESSORIES = (
    "hoes",
    "hoesje",
    "doos",
    "doosje",
    "behuizing",
    "etui",
    "tas",
    "doek",
    "vulling",
    "navulling",
    "refill",
    "voetjes",
    "knoopjes",
    "dopjes",
    "knoppen",
    "band",
    "lint",
    "ketting",
    "hanger",
    "onderdelen",
    "onderdeel",
    "reparatie",
    "vervanger",
    "sleutel",
    "ventiel",
    "pomp",
    "lampje",
    "adapter",
    "kabel",
    "lader",
    "stat",
)


def accessory_score(want: str, title: str) -> list[str]:
    """Accessory words in a title the watch itself does not ask for.

    A case for AirPods Pro 2 matches the name word for word and costs a fifth
    of the product. These words are the ones Dutch and English shops use for
    the things that go *with* a product rather than being it. A word the watch
    name itself contains is ignored, so watching for a case keeps working.
    """
    wanted = tokenize(want)
    found = []
    for word in tokenize(title):
        if word in wanted:
            continue
        for accessory in ACCESSORY_WORDS:
            if word == accessory or (len(accessory) > 4 and word.startswith(accessory[:5])):
                found.append(accessory)
                break
            if len(accessory) > 4 and word.endswith(accessory):
                found.append(accessory)
                break
        for accessory in COMPOUND_ACCESSORIES:
            if word == accessory or (len(word) > len(accessory) and word.endswith(accessory)):
                found.append(accessory)
                break
    return found


def match_score(want: str, title: str) -> float:
    """How well a candidate title matches the watched product, 0..1.

    A word counts as a hit when the title uses any of its synonyms, so a search
    for "wc papier" also finds a title written as "toiletpapier".
    """
    wanted = tokenize(want)
    if not wanted:
        return 0.0
    got = tokenize(title)
    if not got:
        return 0.0
    hits = sum(1 for word in wanted if word_hit(word, got))
    return round(hits / len(wanted), 3)


class _Collector(HTMLParser):
    """Pulls out the parts of a page we care about in one pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.metas: list[tuple[str, str]] = []
        self.ld_blocks: list[str] = []
        self.itemprop_prices: list[tuple[str, str]] = []
        self.availability: list[str] = []
        self._sink: str | None = None
        self._buf: list[str] = []
        self._itemprop: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._sink = "title"
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key:
                self.metas.append((key, a.get("content", "")))
        elif tag == "script":
            if (a.get("type") or "").lower() == "application/ld+json":
                self._sink = "ld"
                self._buf = []
            elif a.get("id") in {"__NEXT_DATA__", "__NUXT_DATA__"} or a.get("type") == "application/json":
                self._sink = "ld"
                self._buf = []
        if "itemprop" in a:
            prop = a["itemprop"].lower()
            self._itemprop = prop
            if "price" in prop or "lowprice" in prop:
                content = a.get("content") or a.get("value") or ""
                if content:
                    self.itemprop_prices.append((prop, content))
            if "availability" in prop:
                self.availability.append(a.get("content") or a.get("href") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"title", "script"} and self._sink:
            blob = "".join(self._buf).strip()
            if self._sink == "title" and not self.title:
                self.title = blob
            elif self._sink == "ld" and blob:
                self.ld_blocks.append(blob)
            self._sink = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._sink:
            self._buf.append(data)
        if self._itemprop and ("price" in self._itemprop or "lowprice" in self._itemprop):
            stripped = data.strip()
            if stripped:
                self.itemprop_prices.append((self._itemprop, stripped))
                self._itemprop = None


def _walk(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _offers_from_ld(node: dict) -> list[Candidate]:
    out: list[Candidate] = []
    name = str(node.get("name") or "")
    url = str(node.get("url") or "")
    for offer in _walk(node.get("offers")):
        if not isinstance(offer, dict):
            continue
        price = None
        for key in ("price", "lowPrice", "priceSpecification", "priceAmount"):
            if key in offer:
                price = parse_price(offer[key])
                if price:
                    break
        if not price:
            for spec in _walk(offer.get("priceSpecification")):
                if isinstance(spec, dict) and spec.get("price") is not None:
                    price = parse_price(spec.get("price"))
                    if price:
                        break
        if not price:
            continue
        currency = str(offer.get("priceCurrency") or node.get("priceCurrency") or "EUR")
        avail = offer.get("availability")
        out.append(
            Candidate(
                price=price,
                source=CONF_JSONLD,
                currency=currency,
                title=str(offer.get("name") or name)[:200],
                url=str(offer.get("url") or url)[:500],
                available=_available(avail),
            )
        )
    return out


def _available(value: Any) -> bool | None:
    text = str(value or "").lower()
    if not text:
        return None
    if "outofstock" in text or "soldout" in text or "nietbeschikbaar" in text:
        return False
    if "instock" in text or "limitedavailability" in text or "preorder" in text:
        return True
    return None


STATE_PRICE_KEYS = (
    "price",
    "prijs",
    "sellingPrice",
    "priceAmount",
    "priceCurrent",
    "finalPrice",
    "lowPrice",
    "currentPrice",
)
TITLE_KEYS = ("title", "productName", "name", "displayName", "productTitle", "label")
LINK_KEYS = ("url", "link", "productUrl", "href", "canonicalUrl")


def _first_text(node: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
        if isinstance(value, dict):
            inner = value.get("text") or value.get("name")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()[:200]
    return ""


def _first_link(node: dict) -> str:
    for key in LINK_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            inner = value.get("url") or value.get("href")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


def _walk_with_parent(node: Any, parent: dict | None = None):
    if isinstance(node, dict):
        yield node, parent
        for value in node.values():
            yield from _walk_with_parent(value, node)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_with_parent(item, parent)


ASSIGN_RE = re.compile(r"(?:window\.|var\s+|let\s+|const\s+)[A-Za-z_$][\w$]*(?:\.[\w$]+)*\s*=\s*(?=\{)")


def _balanced_object(text: str, start: int) -> str:
    """Read one brace-balanced JSON object out of inline JavaScript."""
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in "\"'":
            in_string = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _inline_state(html: str) -> list[Candidate]:
    """State assigned in inline scripts, e.g. window.pageLevelData = {...}.

    Several storefronts serve no structured markup at all on their search page
    and hand the prices to their own JavaScript as a JSON island instead.
    """
    out: list[Candidate] = []
    for match in ASSIGN_RE.finditer(html or ""):
        blob = _balanced_object(html, match.end())
        if not blob or len(blob) > 4_000_000:
            continue
        try:
            data = json.loads(blob)
        except (ValueError, TypeError):
            continue
        out.extend(_candidates_from_data(data, ""))
        if len(out) >= 80:
            break
    return out


def _candidates_from_data(data: Any, base_url: str) -> list[Candidate]:
    out: list[Candidate] = []
    for node, parent in _walk_with_parent(data):
        price = None
        for key in STATE_PRICE_KEYS:
            if key in node and isinstance(node[key], (int, float, str)):
                price = parse_price(node[key])
                if price:
                    break
        if not price:
            continue
        currency = str(node.get("currency") or node.get("currencyCode") or node.get("priceCurrency") or "").upper()
        if currency and currency not in {"EUR", "EURO"}:
            continue
        # Shops that park prices in their own little object (productPrice, or a
        # nested price node) keep the name and the link one level up.
        title = _first_text(node, TITLE_KEYS) or _first_text(parent or {}, TITLE_KEYS)
        if not currency and not title:
            continue
        link = _first_link(node) or _first_link(parent or {})
        out.append(
            Candidate(
                price=price,
                source=CONF_STATE,
                title=title,
                url=str(link or base_url)[:500],
                available=_available(node.get("availability") or node.get("available")),
            )
        )
    return out


def _from_state_json(html: str, base_url: str) -> list[Candidate]:
    """Embedded app state (Next/Nuxt blobs, JSON islands)."""
    out: list[Candidate] = []
    for block in re.findall(r'<script[^>]+type="application/(?:ld\+)?json"[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        out.extend(_candidates_from_data(data, base_url))
        if len(out) >= 60:
            return out
    out.extend(_inline_state(html))
    return out[:80]


#: Een prijs achter deze eenheid is geen totaalprijs: "€ 1,15/eenheid" is de rol,
#: niet de verpakking. Zulke bedragen zijn altijd veel lager dan het product en
#: zouden een €11,48-verpakking als €1,15 noteren.
UNIT_AFTER_RE = re.compile(
    r"^\s*(?:/|per\s)(?:eenheid|stuk|ks|kg|kilo|gram|g\b|liter|liter\b|l\b|"
    r"meter|m\b|rol|zak|doos|tray|100\s?g|100\s?ml|1000\s?g)",
    re.I,
)


def _from_regex(html: str, page_title: str) -> list[Candidate]:
    # Winkels schrijven het euroteken ook als &euro; of &#8364;, dus eerst de
    # entities oplossen, anders blijft zo'n bedrag onvindbaar.
    body = WS_RE.sub(" ", html_lib.unescape(TAG_RE.sub(" ", html)))
    # De echte prijs staat op een productpagina vaker dan eenmaal: in de kop, in
    # de aria-label en in de prijsbadge. Een eenheidsprijs of een los getal
    # staat er een keer. Die herhaling is het enige hulpmiddel om ze te schcheiden.
    telling: dict[float, int] = {}
    gevonden: dict[float, Candidate] = {}
    for match in PRICE_RE.finditer(body):
        price = _number(match.group(1) or match.group(2) or "")
        if not price or price < 1:
            continue
        if UNIT_AFTER_RE.match(body[match.end() : match.end() + 20]):
            continue
        telling[price] = telling.get(price, 0) + 1
        if price not in gevonden:
            gevonden[price] = Candidate(price=price, source=CONF_REGEX, title=page_title)
    for price, keer in telling.items():
        gevonden[price].count = keer
    return sorted(gevonden.values(), key=lambda c: (-c.count, c.price))


def scan(html: str, url: str = "", want: str = "") -> PageScan:
    """Every price we can defend on this page, most trustworthy first.

    When want is given, only candidates whose title matches the watched
    product survive; a search page otherwise drags in accessories and
    subscriptions at a fifth of the product price.
    """
    scan_result = PageScan()
    parser = _Collector()
    try:
        parser.feed(html or "")
    except Exception:
        pass
    scan_result.title = parser.title.strip()[:200]

    found: list[Candidate] = []

    for block in parser.ld_blocks:
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        for node in _walk(data):
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            names = {str(t).lower() for t in types}
            if names & {"product", "productgroup", "vehicle", "softwareapplication", "book"} or "offers" in node:
                found.extend(_offers_from_ld(node))

    for prop, raw in parser.itemprop_prices:
        price = parse_price(raw)
        if price:
            found.append(Candidate(price=price, source=CONF_MICRODATA, title=scan_result.title, url=url))

    for key, raw in parser.metas:
        if not any(token in key for token in ("price", "prijs")):
            continue
        if any(token in key for token in ("availability", "condition", "shipping")):
            continue
        price = parse_price(raw)
        if price:
            found.append(Candidate(price=price, source=CONF_META, title=scan_result.title, url=url))

    found.extend(_from_state_json(html or "", url))

    seen: set[tuple[float, str]] = set()
    unique: list[Candidate] = []
    for candidate in found:
        key = (candidate.price, candidate.source)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    if want:
        scored = [(match_score(want, c.title), c) for c in unique]
        strong = [(s, c) for s, c in scored if s >= 0.5]
        if strong:
            unique = [c for _, c in sorted(strong, key=lambda pair: -pair[0])]

    unique.extend(_from_regex(html or "", scan_result.title))
    scan_result.candidates = unique
    return scan_result