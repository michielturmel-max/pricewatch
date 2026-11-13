"""Storage: watches, their offers, price history and notification bookkeeping.

One SQLite file, no dependencies, WAL so the panel can read while the timer
writes. Money is stored as REAL in euros; that is fine at consumer price
magnitudes and keeps the arithmetic readable.

The schema version lives in PRAGMA user_version. Upgrades are additive and
run once; nothing here ever drops a table a user still needs.
"""

from __future__ import annotations

import os
import re
import sqlite3
import statistics
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 2
HISTORY_DAYS = 30
HISTORY_KEEP = 4000

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  note          TEXT NOT NULL DEFAULT '',
  target        REAL,
  threshold     REAL,
  floor         REAL,
  muted         INTEGER NOT NULL DEFAULT 0,
  created       INTEGER NOT NULL,
  last_checked  INTEGER,
  best_price    REAL,
  best_shop     TEXT,
  best_url      TEXT,
  best_title    TEXT,
  best_source   TEXT,
  low_price     REAL,
  low_at        INTEGER
);

CREATE TABLE IF NOT EXISTS offers (
  id            TEXT PRIMARY KEY,
  watch_id      TEXT NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
  shop          TEXT NOT NULL,
  kind          TEXT NOT NULL,
  value         TEXT NOT NULL,
  label         TEXT NOT NULL DEFAULT '',
  enabled       INTEGER NOT NULL DEFAULT 1,
  last_price    REAL,
  last_checked  INTEGER,
  last_error    TEXT NOT NULL DEFAULT '',
  ok_count      INTEGER NOT NULL DEFAULT 0,
  fail_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS prices (
  watch_id  TEXT NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
  offer_id  TEXT NOT NULL,
  ts        INTEGER NOT NULL,
  price     REAL NOT NULL,
  shop      TEXT NOT NULL DEFAULT '',
  url       TEXT NOT NULL DEFAULT '',
  title     TEXT NOT NULL DEFAULT '',
  source    TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (watch_id, offer_id, ts, price)
);

CREATE INDEX IF NOT EXISTS prices_watch_ts ON prices(watch_id, ts);

CREATE TABLE IF NOT EXISTS notified (
  watch_id  TEXT NOT NULL,
  kind      TEXT NOT NULL,
  bucket    TEXT NOT NULL,
  ts        INTEGER NOT NULL,
  PRIMARY KEY (watch_id, kind, bucket)
);
"""


def default_home() -> Path:
    override = os.environ.get("PRICEWATCH_HOME")
    if override:
        return Path(override)
    data = os.environ.get("XDG_DATA_HOME")
    base = Path(data) if data else Path.home() / ".local" / "share"
    return base / "pricewatch"


def slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return base[:40] or "watch"


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_home() / "pricewatch.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=15.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self._upgrade()

    def _upgrade(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        # Additive only: a column that older databases lack, never a dropped
        # table, because the price history is the one thing that cannot be
        # fetched again.
        if version < 2:
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(watches)")}
            if "floor" not in columns:
                self.db.execute("ALTER TABLE watches ADD COLUMN floor REAL")
        self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ watches --

    def add_watch(
        self,
        name: str,
        note: str = "",
        target: float | None = None,
        threshold: float | None = None,
        muted: bool = False,
    ) -> dict[str, Any]:
        watch_id = self._free_id(slugify(name))
        now = int(time.time())
        self.db.execute(
            "INSERT INTO watches (id, name, note, target, threshold, muted, created)"
            " VALUES (?,?,?,?,?,?,?)",
            (watch_id, name.strip(), note.strip(), target, threshold, int(muted), now),
        )
        self.db.commit()
        return self.watch(watch_id)

    def _free_id(self, base: str) -> str:
        row = self.db.execute("SELECT 1 FROM watches WHERE id=?", (base,)).fetchone()
        if not row:
            return base
        return f"{base}-{uuid.uuid4().hex[:4]}"

    def watch(self, watch_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM watches WHERE id=? OR name=?", (watch_id, watch_id)).fetchone()
        if not row:
            raise KeyError(watch_id)
        watch = dict(row)
        watch["offers"] = [dict(o) for o in self.offers(watch_id)]
        return watch

    def resolve(self, ref: str) -> str:
        row = self.db.execute("SELECT id FROM watches WHERE id=? OR name=? OR id LIKE ?", (ref, ref, f"{ref}-%")).fetchone()
        if not row:
            raise KeyError(ref)
        return row[0]

    def watches(self, include_muted: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM watches"
        if not include_muted:
            sql += " WHERE muted=0"
        sql += " ORDER BY created"
        out = []
        for row in self.db.execute(sql):
            watch = dict(row)
            watch["offers"] = [dict(o) for o in self.offers(watch["id"])]
            out.append(watch)
        return out

    def update_watch(self, watch_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"name", "note", "target", "threshold", "floor", "muted"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot set {sorted(unknown)}")
        watch_id = self.resolve(watch_id)
        if not fields:
            return self.watch(watch_id)
        if "muted" in fields:
            fields["muted"] = int(bool(fields["muted"]))
        sets = ", ".join(f"{key}=?" for key in fields)
        self.db.execute(f"UPDATE watches SET {sets} WHERE id=?", [*fields.values(), watch_id])
        self.db.commit()
        return self.watch(watch_id)

    def remove_watch(self, watch_id: str) -> str:
        watch_id = self.resolve(watch_id)
        self.db.execute("DELETE FROM prices WHERE watch_id=?", (watch_id,))
        self.db.execute("DELETE FROM notified WHERE watch_id=?", (watch_id,))
        self.db.execute("DELETE FROM offers WHERE watch_id=?", (watch_id,))
        self.db.execute("DELETE FROM watches WHERE id=?", (watch_id,))
        self.db.commit()
        return watch_id

    # ------------------------------------------------------------- offers --

    def add_offer(self, watch_id: str, shop: str, kind: str, value: str, label: str = "") -> dict[str, Any]:
        if kind not in {"url", "search"}:
            raise ValueError("offer-soort moet 'url' of 'search' zijn")
        watch_id = self.resolve(watch_id)
        for existing in self.offers(watch_id):
            if existing["shop"] == shop and existing["value"] == value:
                self.db.execute("UPDATE offers SET enabled=1 WHERE id=?", (existing["id"],))
                self.db.commit()
                return existing
        offer_id = uuid.uuid4().hex[:8]
        self.db.execute(
            "INSERT INTO offers (id, watch_id, shop, kind, value, label) VALUES (?,?,?,?,?,?)",
            (offer_id, watch_id, shop, kind, value, label),
        )
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone())

    def offers(self, watch_id: str, only_enabled: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM offers WHERE watch_id=?"
        if only_enabled:
            sql += " AND enabled=1"
        return list(self.db.execute(sql + " ORDER BY rowid", (watch_id,)))

    def offer(self, offer_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()
        if not row:
            raise KeyError(offer_id)
        return dict(row)

    def set_offer(self, offer_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"shop", "kind", "value", "label", "enabled", "last_error"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot set {sorted(unknown)}")
        if "enabled" in fields:
            fields["enabled"] = int(bool(fields["enabled"]))
        sets = ", ".join(f"{key}=?" for key in fields)
        self.db.execute(f"UPDATE offers SET {sets} WHERE id=?", [*fields.values(), offer_id])
        self.db.commit()
        return self.offer(offer_id)

    def remove_offer(self, offer_id: str) -> None:
        self.db.execute("DELETE FROM offers WHERE id=?", (offer_id,))
        self.db.commit()

    # ------------------------------------------------------------- prices --

    def record_price(
        self,
        watch_id: str,
        offer_id: str,
        price: float,
        shop: str,
        url: str,
        title: str = "",
        source: str = "",
        when: int | None = None,
    ) -> dict[str, Any]:
        # The price is part of the key: two checks in the same second that see
        # different prices are two facts, and history that drops one of them
        # cannot be trusted later.
        now = int(when if when is not None else time.time())
        self.db.execute(
            "INSERT OR REPLACE INTO prices (watch_id, offer_id, ts, price, shop, url, title, source)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (watch_id, offer_id, now, price, shop, url, title[:200], source),
        )
        self.db.execute(
            "UPDATE offers SET last_price=?, last_checked=?, last_error='',"
            " ok_count=ok_count+1 WHERE id=?",
            (price, now, offer_id),
        )
        self.db.execute("UPDATE watches SET last_checked=? WHERE id=?", (now, watch_id))
        previous = self.db.execute(
            "SELECT best_price, low_price, low_at FROM watches WHERE id=?",
            (watch_id,),
        ).fetchone()
        # De laagste van de winkels uit déze check wint. Binnen een check daalt
        # best_price dus alleen, precies als toen, maar begin_check heeft de waarde
        # van de vorige check weggehaald zodat die niet blijft hangen.
        if previous and (previous["best_price"] is None or price < previous["best_price"]):
            self.db.execute(
                "UPDATE watches SET best_price=?, best_shop=?, best_url=?, best_title=?, best_source=?"
                " WHERE id=?",
                (price, shop, url, title[:200], source, watch_id),
            )
        # low_price is het record over alle tijd en daalt daarom voorgoed.
        if previous and (previous["low_price"] is None or price < previous["low_price"]):
            self.db.execute("UPDATE watches SET low_price=?, low_at=? WHERE id=?", (price, now, watch_id))
        self._trim(watch_id)
        self.db.commit()
        return {"previous_best": previous["best_price"] if previous else None, "price": price, "shop": shop, "url": url, "title": title}

    def begin_check(self, watch_id: str) -> None:
        """Maak de beste prijs leeg voor een nieuwe check.

        De laagste prijs vasthouden werkte, maar een meting die verkeerd uitviel
        bleef daarmee voorgoed de beste prijs: het product leek permanent in
        aanbieding. Binnen een check daalt de beste prijs alleen nog, en de
        laagste ooit (low_price) blijft wél overeind.
        """
        self.db.execute(
            "UPDATE watches SET best_price=NULL, best_shop='', best_url='', best_title='', best_source=''"
            " WHERE id=?",
            (watch_id,),
        )
        self.db.commit()

    def end_check(
        self,
        watch_id: str,
        price: float | None = None,
        shop: str = "",
        url: str = "",
        title: str = "",
        source: str = "",
    ) -> None:
        """Zet de beste prijs van déze check, of haal de laatste bekende terug.

        Er moet altijd iets staan. Een check schrijft alleen een prijs als die
        veranderd is, dus deze waarde is de enige die zegt wat de watch nu kost.
        """
        if price is not None:
            self.db.execute(
                "UPDATE watches SET best_price=?, best_shop=?, best_url=?, best_title=?, best_source=?"
                " WHERE id=?",
                (price, shop, url, title[:200], source, watch_id),
            )
            self.db.commit()
            return
        row = self.db.execute(
            "SELECT price, shop, url, title, source FROM prices WHERE watch_id=? ORDER BY ts DESC LIMIT 1",
            (watch_id,),
        ).fetchone()
        if row is None:
            return
        self.db.execute(
            "UPDATE watches SET best_price=?, best_shop=?, best_url=?, best_title=?, best_source=? WHERE id=?",
            (row["price"], row["shop"], row["url"], row["title"], row["source"], watch_id),
        )
        self.db.commit()

    def record_failure(self, watch_id: str, offer_id: str, error: str) -> None:
        now = int(time.time())
        self.db.execute(
            "UPDATE offers SET last_checked=?, last_error=?, fail_count=fail_count+1 WHERE id=?",
            (now, error[:300], offer_id),
        )
        self.db.execute("UPDATE watches SET last_checked=? WHERE id=?", (now, watch_id))
        self.db.commit()

    def _trim(self, watch_id: str) -> None:
        count = self.db.execute("SELECT COUNT(*) FROM prices WHERE watch_id=?", (watch_id,)).fetchone()[0]
        if count <= HISTORY_KEEP:
            return
        cutoff = self.db.execute(
            "SELECT ts FROM prices WHERE watch_id=? ORDER BY ts DESC LIMIT 1 OFFSET ?", (watch_id, HISTORY_KEEP - 1)
        ).fetchone()[0]
        self.db.execute("DELETE FROM prices WHERE watch_id=? AND ts < ?", (watch_id, cutoff))

    def history(self, watch_id: str, days: int | None = HISTORY_DAYS) -> list[dict[str, Any]]:
        watch_id = self.resolve(watch_id)
        if days and days > 0:
            since = int(time.time()) - days * 86400
            rows = self.db.execute(
                "SELECT ts, price, shop, url, title, source, offer_id FROM prices"
                " WHERE watch_id=? AND ts>=? ORDER BY ts",
                (watch_id, since),
            )
        else:
            rows = self.db.execute(
                "SELECT ts, price, shop, url, title, source, offer_id FROM prices WHERE watch_id=? ORDER BY ts",
                (watch_id,),
            )
        return [dict(row) for row in rows]

    def daily_bests(self, watch_id: str, days: int = HISTORY_DAYS) -> list[tuple[str, float]]:
        """One cheapest price per day, oldest first. days=0 means everything."""
        watch_id = self.resolve(watch_id)
        rows = self.db.execute(
            "SELECT date(ts,'unixepoch','localtime') AS day, MIN(price) AS low FROM prices"
            " WHERE watch_id=? GROUP BY day ORDER BY day",
            (watch_id,),
        )
        if not days or days <= 0:
            return [(row["day"], row["low"]) for row in rows]
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
        return [(row["day"], row["low"]) for row in rows if row["day"] >= cutoff]

    def reference(self, watch_id: str, days: int = HISTORY_DAYS) -> dict[str, Any]:
        """The price a drop is measured against: the median of daily lows."""
        days_list = self.daily_bests(watch_id, days)
        values = [price for _, price in days_list]
        if not values:
            return {"value": None, "points": 0, "low": None, "avg": None, "high": None, "days": days}
        return {
            "value": round(statistics.median(values), 2),
            "points": len(values),
            "low": min(values),
            "avg": round(statistics.fmean(values), 2),
            "high": max(values),
            "days": days,
        }

    def stats(self, watch_id: str, days: int = HISTORY_DAYS) -> dict[str, Any]:
        rows = self.history(watch_id, days)
        values = [row["price"] for row in rows]
        ref = self.reference(watch_id, days)
        watch = self.watch(watch_id)
        current = watch["best_price"]
        return {
            "points": len(values),
            "low": min(values) if values else None,
            "high": max(values) if values else None,
            "avg": round(statistics.fmean(values), 2) if values else None,
            "median": ref["value"],
            "reference_days": ref["points"],
            "current": current,
            "drop_pct": round((ref["value"] - current) / ref["value"] * 100, 1) if ref["value"] and current else None,
            "first_seen": rows[0]["ts"] if rows else None,
            "low_ever": watch["low_price"],
        }

    def change(self, watch_id: str, days: int = 7, current: float | None = None) -> float | None:
        """Signed change from the price `days` ago to now, in percent.

        Negative means the price dropped. A watch younger than the window is
        measured against its first price, because "since we started looking" is
        the honest answer there.
        """
        watch_id = self.resolve(watch_id)
        if current is None:
            current = self.watch(watch_id)["best_price"]
        if current is None:
            return None
        cutoff = int(time.time()) - days * 86400
        row = self.db.execute(
            "SELECT price FROM prices WHERE watch_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
            (watch_id, cutoff),
        ).fetchone()
        if row is None:
            row = self.db.execute(
                "SELECT price FROM prices WHERE watch_id=? ORDER BY ts LIMIT 1", (watch_id,)
            ).fetchone()
        if row is None or not row["price"]:
            return None
        return round((current - row["price"]) / row["price"] * 100, 1)

    def shop_breakdown(self, watch_id: str, days: int = 0) -> list[dict[str, Any]]:
        """Per shop: lowest, highest, last price and how many measurements."""
        grouped: dict[str, dict[str, Any]] = {}
        for row in self.history(watch_id, days):
            shop = row["shop"] or "onbekend"
            item = grouped.setdefault(
                shop,
                {"shop": shop, "low": row["price"], "high": row["price"], "last": row["price"],
                 "last_ts": row["ts"], "points": 0},
            )
            item["low"] = min(item["low"], row["price"])
            item["high"] = max(item["high"], row["price"])
            item["points"] += 1
            if row["ts"] >= item["last_ts"]:
                item["last"] = row["price"]
                item["last_ts"] = row["ts"]
        return sorted(grouped.values(), key=lambda item: (item["low"], item["shop"]))

    def daily_stats(self, watch_id: str, days: int = HISTORY_DAYS) -> list[tuple[str, float, float]]:
        """One price per day: (day, cheapest, average). days=0 means everything.

        The average is what Tweakers' prijsontwikkeling also keeps: the minimum
        alone cannot tell a genuine drop from one shop undercutting the rest for
        a day.
        """
        watch_id = self.resolve(watch_id)
        rows = self.db.execute(
            "SELECT date(ts,'unixepoch','localtime') AS day, MIN(price) AS low, AVG(price) AS avg"
            " FROM prices WHERE watch_id=? GROUP BY day ORDER BY day",
            (watch_id,),
        )
        cutoff = ""
        if days and days > 0:
            cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
        return [
            (row["day"], row["low"], round(row["avg"], 2))
            for row in rows
            if not cutoff or row["day"] >= cutoff
        ]

    def lowest_recent(self, watch_id: str, days: int = 180) -> dict[str, Any] | None:
        """The cheapest day in the window, with its date."""
        rows = self.daily_stats(watch_id, days)
        if not rows:
            return None
        day, low, _ = min(rows, key=lambda row: (row[1], row[0]))
        return {"price": low, "day": day}

    def analysis(self, watch_id: str) -> dict[str, Any]:
        """Everything the stats command and the panel need about one watch."""
        watch_id = self.resolve(watch_id)
        watch = self.watch(watch_id)
        current = watch["best_price"]
        periods = [dict(days=days, **self.stats(watch_id, days)) for days in (7, 30, 90, 0)]
        month = next(period for period in periods if period["days"] == 30)
        daily7 = self.daily_stats(watch_id, 7)
        daily30 = self.daily_stats(watch_id, 30)
        daily90 = self.daily_stats(watch_id, 90)
        avg7 = round(statistics.fmean(row[2] for row in daily7), 2) if daily7 else None
        avg30 = round(statistics.fmean(row[2] for row in daily30), 2) if daily30 else None
        avg90 = round(statistics.fmean(row[2] for row in daily90), 2) if daily90 else None
        savings = None
        if current is not None and month["high"] is not None and month["high"] > current:
            savings = {
                "amount": round(month["high"] - current, 2),
                "pct": round((month["high"] - current) / month["high"] * 100, 1),
            }
        return {
            "id": watch_id,
            "name": watch["name"],
            "current": current,
            "current_shop": watch["best_shop"],
            "low_ever": watch["low_price"],
            "low_at": watch["low_at"],
            "change_7d": self.change(watch_id, 7, current),
            "change_30d": self.change(watch_id, 30, current),
            "avg_7d": avg7,
            "avg_30d": avg30,
            "avg_90d": avg90,
            "periods": periods,
            "shops": self.shop_breakdown(watch_id, 0),
            "daily": [[day, low, avg] for day, low, avg in self.daily_stats(watch_id, 30)],
            "lowest_6m": self.lowest_recent(watch_id, 180),
            "savings": savings,
        }

    # -------------------------------------------------------- notification --

    def notified_once(self, watch_id: str, kind: str, bucket: str, now: int | None = None) -> bool:
        """True the first time this (kind, bucket) is asked; False afterwards."""
        now = now or int(time.time())
        row = self.db.execute(
            "SELECT 1 FROM notified WHERE watch_id=? AND kind=? AND bucket=?", (watch_id, kind, bucket)
        ).fetchone()
        if row:
            return False
        self.db.execute(
            "INSERT OR REPLACE INTO notified (watch_id, kind, bucket, ts) VALUES (?,?,?,?)",
            (watch_id, kind, bucket, now),
        )
        self.db.commit()
        return True

    def forget_notices(self, watch_id: str, keep_last: int = 200) -> None:
        self.db.execute(
            "DELETE FROM notified WHERE watch_id=? AND ts < ?",
            (watch_id, int(time.time()) - keep_last * 86400),
        )
        self.db.commit()

    # ---------------------------------------------------------------- json --

    def export(self, watch_id: str | None = None) -> dict[str, Any]:
        watches = [self.watch(self.resolve(watch_id))] if watch_id else self.watches()
        prices: list[dict[str, Any]] = []
        for watch in watches:
            for row in self.history(watch["id"], 0):
                row["watch_id"] = watch["id"]
                prices.append(row)
        return {
            "schema": SCHEMA_VERSION,
            "exported": int(time.time()),
            "watches": watches,
            "prices": prices,
        }

    def import_data(self, data: dict[str, Any], replace: bool = False) -> dict[str, int]:
        """Restore or merge an export. Returns what actually landed.

        Importing never invents a price: every row in `prices` is copied as it
        was written, and the watch's current price is then derived from the
        newest row. That keeps a restore from turning an old low into today's
        price.
        """
        counts = {"watches": 0, "offers": 0, "prices": 0, "skipped": 0}
        id_map: dict[str, str] = {}
        watched: list[str] = []

        for item in data.get("watches") or []:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                counts["skipped"] += 1
                continue
            old_id = str(item.get("id") or slugify(str(item["name"])))
            existing = self.db.execute("SELECT id FROM watches WHERE id=?", (old_id,)).fetchone()
            if existing and not replace:
                new_id = old_id
                fields = {key: item[key] for key in ("name", "note", "target", "threshold", "floor", "muted") if key in item}
                if fields:
                    self.update_watch(new_id, **fields)
            else:
                if existing:
                    self.remove_watch(old_id)
                new_id = old_id if not self.db.execute("SELECT 1 FROM watches WHERE id=?", (old_id,)).fetchone() else self._free_id(old_id)
                self.db.execute(
                    "INSERT INTO watches (id, name, note, target, threshold, floor, muted, created)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        new_id,
                        str(item["name"]).strip(),
                        str(item.get("note") or ""),
                        item.get("target"),
                        item.get("threshold"),
                        item.get("floor"),
                        int(bool(item.get("muted"))),
                        int(item.get("created") or time.time()),
                    ),
                )
                self.db.commit()
            id_map[old_id] = new_id
            watched.append(new_id)
            counts["watches"] += 1

            for offer in item.get("offers") or []:
                if not isinstance(offer, dict) or not offer.get("shop") or not offer.get("value"):
                    continue
                self.add_offer(
                    new_id,
                    str(offer["shop"]),
                    str(offer.get("kind") or "url"),
                    str(offer["value"]),
                    str(offer.get("label") or ""),
                )
                counts["offers"] += 1

        for row in data.get("prices") or []:
            if not isinstance(row, dict):
                counts["skipped"] += 1
                continue
            old_watch = str(row.get("watch_id") or "")
            watch_id = id_map.get(old_watch)
            if not watch_id:
                counts["skipped"] += 1
                continue
            offer_id = self._import_offer(watch_id, row, counts)
            if not offer_id:
                counts["skipped"] += 1
                continue
            try:
                ts = int(row["ts"])
                price = float(row["price"])
            except (KeyError, TypeError, ValueError):
                counts["skipped"] += 1
                continue
            if price <= 0:
                counts["skipped"] += 1
                continue
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO prices (watch_id, offer_id, ts, price, shop, url, title, source)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    watch_id,
                    offer_id,
                    ts,
                    price,
                    str(row.get("shop") or ""),
                    str(row.get("url") or ""),
                    str(row.get("title") or "")[:200],
                    str(row.get("source") or ""),
                ),
            )
            counts["prices"] += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

        self.db.commit()
        for watch_id in watched:
            self.recompute(watch_id)
        return counts

    def _import_offer(self, watch_id: str, row: dict[str, Any], counts: dict[str, int]) -> str:
        """Find or make the offer a price row belongs to."""
        shop = str(row.get("shop") or "")
        url = str(row.get("url") or "")
        if not shop:
            return ""
        candidates = [offer for offer in self.offers(watch_id) if offer["shop"] == shop]
        for offer in candidates:
            if url and offer["value"] == url:
                return offer["id"]
        if len(candidates) == 1:
            return candidates[0]["id"]
        if url:
            made = self.add_offer(watch_id, shop, "url", url)
        else:
            made = self.add_offer(watch_id, shop, "search", self.watch(watch_id)["name"])
        counts["offers"] += 1
        return made["id"]

    def recompute(self, watch_id: str) -> None:
        """Rebuild a watch's current price from the history that is there."""
        watch_id = self.resolve(watch_id)
        latest = self.db.execute(
            "SELECT MAX(ts) FROM prices WHERE watch_id=?", (watch_id,)
        ).fetchone()[0]
        if latest is None:
            return
        best = self.db.execute(
            "SELECT price, shop, url, title, source FROM prices WHERE watch_id=? AND ts=?"
            " ORDER BY price ASC LIMIT 1",
            (watch_id, latest),
        ).fetchone()
        low = self.db.execute(
            "SELECT price, ts FROM prices WHERE watch_id=? ORDER BY price ASC, ts ASC LIMIT 1",
            (watch_id,),
        ).fetchone()
        self.db.execute(
            "UPDATE watches SET best_price=?, best_shop=?, best_url=?, best_title=?, best_source=?,"
            " low_price=?, low_at=?, last_checked=? WHERE id=?",
            (
                best["price"],
                best["shop"],
                best["url"],
                best["title"],
                best["source"],
                low["price"],
                low["ts"],
                latest,
                watch_id,
            ),
        )
        for offer in self.offers(watch_id):
            row = self.db.execute(
                "SELECT price, ts FROM prices WHERE watch_id=? AND offer_id=? ORDER BY ts DESC LIMIT 1",
                (watch_id, offer["id"]),
            ).fetchone()
            if row:
                self.db.execute(
                    "UPDATE offers SET last_price=?, last_checked=? WHERE id=?",
                    (row["price"], row["ts"], offer["id"]),
                )
        self.db.commit()

    def status(self) -> dict[str, Any]:
        watches = self.watches()
        now = int(time.time())
        deals = 0
        for watch in watches:
            if watch["muted"]:
                continue
            if watch["target"] is not None and watch["best_price"] is not None and watch["best_price"] <= watch["target"]:
                deals += 1
                continue
            ref = self.reference(watch["id"])
            threshold = watch["threshold"] if watch["threshold"] is not None else 10.0
            if (
                watch["best_price"]
                and ref["value"]
                and ref["points"] >= 3
                and (ref["value"] - watch["best_price"]) / ref["value"] * 100 >= threshold
            ):
                deals += 1
        checked = [w["last_checked"] for w in watches if w["last_checked"]]
        broken = [
            {"watch": w["id"], "offer": o["id"], "shop": o["shop"], "error": o["last_error"]}
            for w in watches
            for o in w["offers"]
            if o["last_error"] and not is_soft(o["last_error"])
        ]
        latest = max(checked) if checked else None
        return {
            "schema": SCHEMA_VERSION,
            "db": str(self.path),
            "watches": watches,
            "deals": deals,
            "checked": latest,
            "checked_line": f"gekeken {when(latest)}" if latest else "nog nooit gekeken",
            "broken": broken,
            "age": now - latest if latest else None,
        }


def load(path: str | Path | None = None) -> Store:
    return Store(path)


#: Een winkel die een accessoiresprijs of een andere variant laat zien geeft geen
#: fout: de prijs is er, alleen niet bruikbaar voor deze watch. Dat onderscheid
#: telt, want een echte weigering kleurt je bar rood en zo'n noot niet.
SOFT_NOTE = "geen bruikbare prijs:"


def is_soft(note: str | None) -> bool:
    return bool(note) and str(note).startswith(SOFT_NOTE)


def money(value: float | None) -> str:
    if value is None:
        return "-"
    grouped = f"{value:,.2f}"
    return "\u20ac" + grouped.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.0f}%"


def when(ts: int | None) -> str:
    if not ts:
        return "nooit"
    delta = int(time.time()) - ts
    if delta < 90:
        return "zojuist"
    if delta < 3600:
        return f"{delta // 60} min geleden"
    if delta < 86400:
        return f"{delta // 3600} u geleden"
    if delta < 86400 * 30:
        return f"{delta // 86400} d geleden"
    return time.strftime("%d-%m-%Y", time.localtime(ts))


def euros(text: str) -> float | None:
    return parse_amount(text)


def parse_amount(text: str) -> float | None:
    from .extract import parse_price

    return parse_price(text)


def iter_rows(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]