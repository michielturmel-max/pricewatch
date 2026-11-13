"""When a price deserves an interruption.

Three rules, in order of how much they matter to someone who was waiting to
buy something:

  doelprijs    the price reached the number the watcher wrote down
  laagste      cheaper than any price we ever recorded for this watch
  daling       at least `threshold` percent under the median of recent days

A fourth observation, that the best price moved to another shop, is folded into
the message instead of becoming an alert of its own: it is the same product
getting cheaper and a second notification for it is just noise.

Every rule has a bucket, so the same observation never notifies twice.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

DEFAULT_THRESHOLD = 10.0
MIN_REFERENCE_POINTS = 3
DAY = 86400

#: Distinguishes "not given" from "explicitly None", so a caller that wants the
#: watch's own record can leave it out and a caller that measured it beforehand
#: can pass it.
FROM_WATCH = object()

KIND_TARGET = "doelprijs"
KIND_LOW = "laagste"
KIND_DROP = "daling"


@dataclass
class Deal:
    kind: str
    price: float
    shop: str
    url: str
    title: str
    name: str
    drop_pct: float | None = None
    text: str = ""

    @property
    def rank(self) -> int:
        return {KIND_TARGET: 0, KIND_LOW: 1, KIND_DROP: 2}.get(self.kind, 9)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "price": self.price,
            "shop": self.shop,
            "url": self.url,
            "title": self.title,
            "name": self.name,
            "drop_pct": self.drop_pct,
            "text": self.text,
        }


def threshold_of(watch: dict[str, Any]) -> float:
    value = watch.get("threshold")
    return float(value) if value is not None else DEFAULT_THRESHOLD


def drop_pct(reference: float | None, price: float | None) -> float | None:
    if not reference or price is None:
        return None
    return round((reference - price) / reference * 100, 1)


def evaluate(
    store: Any,
    watch: dict[str, Any],
    price: float,
    shop: str,
    url: str = "",
    title: str = "",
    now: int | None = None,
    reference: dict[str, Any] | None = None,
    low_ever: Any = FROM_WATCH,
    low_known: Any = FROM_WATCH,
) -> Deal | None:
    """Does this price earn a notification? Bookkeeping happens here too.

    reference and low_ever describe the situation *before* today's prices are
    written, so a drop is measured against the past rather than against itself.
    """
    now = now or int(time.time())
    if watch.get("muted"):
        return None

    from .shops import label as shop_label
    from .store import money, pct

    reference = reference if reference is not None else store.reference(watch["id"])
    if low_ever is FROM_WATCH:
        low_ever = watch.get("low_price")
    if low_known is FROM_WATCH:
        low_known = True
    drop = drop_pct(reference.get("value"), price)
    target = watch.get("target")
    price_bucket = f"{price:.2f}"

    deal: Deal | None = None

    if target is not None and price <= target:
        if store.notified_once(watch["id"], KIND_TARGET, time.strftime("%Y-%m-%d", time.localtime(now)), now):
            deal = Deal(
                kind=KIND_TARGET,
                price=price,
                shop=shop,
                url=url,
                title=title,
                name=watch["name"],
                drop_pct=drop,
            )
    elif low_known and low_ever is not None and price < low_ever:
        if store.notified_once(watch["id"], KIND_LOW, price_bucket, now):
            deal = Deal(
                kind=KIND_LOW,
                price=price,
                shop=shop,
                url=url,
                title=title,
                name=watch["name"],
                drop_pct=drop,
            )
    elif (
        drop is not None
        and drop >= threshold_of(watch)
        and reference.get("points", 0) >= MIN_REFERENCE_POINTS
    ):
        if store.notified_once(watch["id"], KIND_DROP, f"{price_bucket}:{int(time.time() // DAY)}", now):
            deal = Deal(
                kind=KIND_DROP,
                price=price,
                shop=shop,
                url=url,
                title=title,
                name=watch["name"],
                drop_pct=drop,
            )

    if not deal:
        return None

    shop_name = shop_label(shop)
    if deal.kind == KIND_TARGET:
        deal.text = f"doelprijs bereikt: {money(price)} bij {shop_name} (was {money(target)})"
    elif deal.kind == KIND_LOW:
        deal.text = f"nieuw laagste: {money(price)} bij {shop_name}, {money(low_ever)} was het record"
    else:
        deal.text = f"{pct(drop)} onder de {reference.get('days')}-daagse prijs: {money(price)} bij {shop_name}"
    if drop is not None and deal.kind != KIND_DROP:
        deal.text += f" ({pct(drop)} onder de {reference.get('days')}-daagse prijs)"
    return deal


def advice(watch: dict[str, Any], analysis: dict[str, Any]) -> str:
    """A short read on the current price, like Tweakers' prijsontwikkeling.

    The verdict says what happened today; this says whether the number is a
    good one compared with the last three months, which is the question someone
    who is about to buy actually has.
    """
    current = analysis.get("current", watch.get("best_price"))
    if current is None:
        return ""
    target = watch.get("target")
    if target is not None and current <= target:
        return "onder je doelprijs"
    six = analysis.get("lowest_6m")
    if six and six.get("price") is not None and current <= six["price"]:
        return "laagste in een half jaar"
    average = analysis.get("avg_90d")
    if not average:
        return ""
    difference = (current - average) / average * 100
    if difference <= -10:
        return f"{abs(difference):.0f}% onder het 90-daagse gemiddelde"
    if difference >= 10:
        return f"{difference:.0f}% boven het 90-daagse gemiddelde"
    return "rond het 90-daagse gemiddelde"


def verdict(watch: dict[str, Any], stats: dict[str, Any]) -> str:
    """One word for the panel: wat de huidige prijs betekent."""
    from .store import money, pct

    price = watch.get("best_price")
    if price is None:
        return "onbekend"
    if watch.get("target") is not None and price <= watch["target"]:
        return "doelprijs"
    if watch.get("low_price") is not None and price <= watch["low_price"] and stats.get("points", 0) >= 2:
        return "laagste"
    drop = stats.get("drop_pct")
    if drop is not None and drop >= threshold_of(watch) and stats.get("reference_days", 0) >= MIN_REFERENCE_POINTS:
        return f"-{pct(drop)}"
    if stats.get("points"):
        return "normaal"
    return "eerste"


def sparkline(history: list[dict[str, Any]], width: int = 24) -> str:
    """Price history as text, for terminals that have no fonts."""
    if not history:
        return ""
    blocks = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    prices = [row["price"] for row in history]
    if len(prices) > width:
        step = len(prices) / width
        prices = [prices[min(len(prices) - 1, int(i * step))] for i in range(width)]
    low, high = min(prices), max(prices)
    span = high - low
    if span <= 0:
        return blocks[0] * len(prices)
    return "".join(blocks[min(len(blocks) - 1, int((p - low) / span * (len(blocks) - 1)))] for p in prices)