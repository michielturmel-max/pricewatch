"""pricewatch - volg een product, hoor wanneer het ergens goedkoop is.

Nederlandse uitvoer, Engelse code. De uitvoer bestaat uit regels die een mens
leest en uit dezelfde gegevens als JSON (`--json`), zodat het paneel niets hoeft
te gokken.

    pricewatch add "AirPods Pro 2" --zoek amazon-nl=airpods pro 2 --doel 199
    pricewatch check --notify
    pricewatch status --json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from pathlib import Path
from typing import Any, NoReturn

from . import checker, deals, fetch, search, shops
from .store import SOFT_NOTE, Store, is_soft, money, pct, when

VERSION = "1.2.0"


def out(*parts: str) -> None:
    print(*parts)


def emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, default=str))


def die(message: str, code: int = 1) -> NoReturn:
    print(f"pricewatch: {message}", file=sys.stderr)
    raise SystemExit(code)


def open_store(args: argparse.Namespace) -> Store:
    return Store(args.db)


# ------------------------------------------------------------------ add ----


def add_watch(store: Store, args: argparse.Namespace) -> int:
    if not args.name and not args.zoek and not args.url:
        die("geef een naam, --zoek of --url")
    name = args.name or (args.zoek[0].split("=", 1)[-1] if args.zoek else args.url[0].rsplit("/", 1)[-1][:40])
    target = amount(args.doel) if args.doel else None
    if target is not None and target <= 0:
        die("doelprijs moet groter dan 0 zijn")
    threshold = float(args.drempel) if args.drempel is not None else None
    floor = amount(args.minimum) if args.minimum else None
    if floor is not None and target is not None and floor > target:
        die("de bodem ligt boven je doelprijs; dan slaan alle prijzen de bodem")
    # Alles eerst controleren, pas daarna de watch aanmaken: een geweigerde
    # regel hoort geen halve watch in de database achter te laten.
    planned: list[tuple[str, str, str]] = []
    for entry in args.zoek or []:
        shop_id, query = shops.parse_query(entry)
        if shop_id == "generic":
            die(f"--zoek {entry!r}: zet een winkel ervoor, bijvoorbeeld amazon-nl=usb-c hub")
        planned.append((shop_id, "search", query))
    for url in args.url or []:
        if not url.startswith(("http://", "https://")):
            die(f"--url {url!r}: geen http(s)-adres")
        shop_id = shops.detect(url)
        if shops.alleen_zoeken(shop_id):
            # Zo'n watch zou elke check opnieuw zeggen dat er geen prijs is,
            # zonder te vertellen waarom. Zeg het nu, bij het toevoegen.
            label = url.rsplit("/", 1)[-1].split(".", 1)[0].replace("-", " ").replace("_", " ")
            zoekterm = " ".join(args.name.split()) if args.name else label[:60]
            die(
                f"{shops.label(shop_id)} laadt zijn prijs met JavaScript; een product-URL blijft leeg. "
                f"Gebruik in plaats daarvan: --zoek '{shop_id}={zoekterm}'"
            )
        planned.append((shop_id, "url", url))
    if not planned:
        die("een watch zonder winkel of URL doet niets; geef --zoek of --url")

    watch = store.add_watch(name, note=args.note or "", target=target, threshold=threshold)
    for shop_id, kind, value in planned:
        store.add_offer(watch["id"], shop_id, kind, value)
    out(f"toegevoegd: {watch['id']}")
    if target is not None:
        out(f"  doelprijs {money(target)}")
    if floor is not None:
        store.update_watch(watch["id"], floor=floor)
        out(f"  bodem {money(floor)}")
    for offer in store.watch(watch["id"])["offers"]:
        kind = "zoeken" if offer["kind"] == "search" else "pagina"
        out(f"  {shops.label(offer['shop'])} ({kind}): {offer['value']}")
        known = shops.SHOPS.get(offer["shop"])
        if known and known.blocked:
            # Nu zeggen, niet pas als de eerste check rood wordt.
            out(f"    let op: deze winkel zet ons nu weg ({known.note})")
    out("  eerste prijzen ophalen met: pricewatch check")
    return 0


def amount(text: str) -> float:
    from .extract import parse_price

    value = parse_price(text)
    if value is None:
        cleaned = (text or "").strip().replace("€", "").replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = float(cleaned)
        except ValueError:
            value = None
    if value is None:
        die(f"geen prijs in {text!r}")
    return value


def remove_watch(store: Store, args: argparse.Namespace) -> int:
    watch_id = store.resolve(args.id)
    watch = store.watch(watch_id)
    store.remove_watch(watch_id)
    out(f"verwijderd: {watch_id} ({watch['name']})")
    return 0


def set_watch(store: Store, args: argparse.Namespace) -> int:
    fields: dict[str, Any] = {}
    if args.doel is not None:
        fields["target"] = None if args.doel == "none" else amount(args.doel)
    if args.drempel is not None:
        fields["threshold"] = None if args.drempel == "none" else float(args.drempel)
    if args.minimum is not None:
        fields["floor"] = None if args.minimum == "none" else amount(args.minimum)
    if args.mute:
        fields["muted"] = True
    if args.unmute:
        fields["muted"] = False
    if args.note is not None:
        fields["note"] = args.note
    if not fields:
        die("niets te wijzigen; geef --doel, --drempel, --mute, --unmute of --note")
    watch = store.update_watch(args.id, **fields)
    out(f"{watch['id']}: bijgewerkt")
    for key in ("target", "floor"):
        if key in fields:
            out(f"  {key}: {money(fields[key]) if fields[key] is not None else 'uit'}")
    if "threshold" in fields:
        out(f"  threshold: {pct(fields['threshold']) if fields['threshold'] is not None else 'uit'}")
    if "muted" in fields:
        out("  meldingen: " + ("uit" if fields["muted"] else "aan"))
    return 0


# ----------------------------------------------------------------- lezen ----


def list_watches(store: Store, args: argparse.Namespace) -> int:
    watches = store.watches()
    if args.json:
        emit([watch_view(store, w) for w in watches], True)
        return 0
    if not watches:
        out("geen watches. toevoegen met: pricewatch add \"naam\" --zoek amazon-nl=...")
        return 0
    for watch in watches:
        view = watch_view(store, watch)
        flag = " (stil)" if watch["muted"] else ""
        out(f"{view['id']}  {watch['name']}{flag}")
        if view["best_price"] is None:
            out(f"    nog geen prijs  {view['offer_line']}")
        else:
            out(
                f"    {money(view['best_price'])} bij {shops.label(view['best_shop'] or '')}"
                f"   {view['verdict']}   {view['trend']}   {view['checked_line']}"
            )
        if view["target"] is not None:
            out(f"    doel {money(view['target'])}{view['target_line']}")
        for error in view["errors"]:
            out(f"    ! {error}")
    return 0


def _target_line(watch: dict[str, Any]) -> str:
    target, best = watch.get("target"), watch.get("best_price")
    if target is None:
        return ""
    if best is None:
        return ""
    if best <= target:
        return " — bereikt"
    return f" — nog {money(target - best)} te gaan"


def watch_view(store: Store, watch: dict[str, Any]) -> dict[str, Any]:
    analysis = store.analysis(watch["id"])
    stats = next(period for period in analysis["periods"] if period["days"] == 30)
    history = store.history(watch["id"], 30)
    shops_seen = sorted({o["shop"] for o in watch["offers"]})
    errors = [
        f"{shops.label(o['shop'])}: {o['last_error']}" for o in watch["offers"] if o["last_error"] and not is_soft(o["last_error"])
    ]
    notes = [
        f"{shops.label(o['shop'])}: {o['last_error'][len(SOFT_NOTE):].strip()}"
        for o in watch["offers"]
        if is_soft(o["last_error"])
    ]
    return {
        "id": watch["id"],
        "name": watch["name"],
        "note": watch["note"],
        "muted": bool(watch["muted"]),
        "target": watch["target"],
        "threshold": watch.get("threshold"),
        "floor": watch.get("floor"),
        "best_price": watch["best_price"],
        "best_shop": watch["best_shop"],
        "best_shop_name": shops.label(watch["best_shop"] or ""),
        "best_url": watch["best_url"],
        "best_title": watch["best_title"],
        "low_ever": watch["low_price"],
        "created": watch["created"],
        "last_checked": watch["last_checked"],
        "checked_line": f"gekeken {when(watch['last_checked'])}",
        "shops": shops_seen,
        "offers": watch["offers"],
        "offer_line": ", ".join(shops.label(s) for s in shops_seen) or "geen winkels",
        "verdict": deals.verdict(watch, stats),
        "advice": deals.advice(watch, analysis),
        "target_line": _target_line(watch),
        "trend": deals.sparkline(history),
        "stats": stats,
        "change_7d": analysis["change_7d"],
        "change_30d": analysis["change_30d"],
        "avg_30d": analysis["avg_30d"],
        "avg_90d": analysis["avg_90d"],
        "lowest_6m": analysis["lowest_6m"],
        "daily": analysis["daily"],
        "savings": analysis["savings"],
        "errors": errors,
        "notes": notes,
    }


def show_watch(store: Store, args: argparse.Namespace) -> int:
    watch = store.watch(store.resolve(args.id))
    view = watch_view(store, watch)
    if args.json:
        emit(view, True)
        return 0
    out(f"{watch['name']}  ({view['id']})")
    if watch["note"]:
        out(f"  notitie: {watch['note']}")
    out(f"  beste prijs nu: {money(view['best_price'])} bij {view['best_shop_name'] or '-'}")
    if view["best_url"]:
        out(f"  {view['best_url']}")
    out(f"  ooit het laagst: {money(view['low_ever'])}")
    stats = view["stats"]
    if stats["points"]:
        out(
            f"  laatste 30 dagen: laagst {money(stats['low'])} \u00b7 gemiddeld {money(stats['avg'])}"
            f" \u00b7 hoogst {money(stats['high'])}  ({view['trend']})"
        )
    else:
        out("  laatste 30 dagen: nog geen geschiedenis")
    if view["target"] is not None:
        out(f"  doelprijs: {money(view['target'])}")
    if watch.get("threshold") is not None:
        out(f"  meldgrens: {pct(watch['threshold'])} onder de {stats['reference_days'] or 0} dag(en) gemiddeld")
    out("  winkels:")
    for offer in watch["offers"]:
        state = money(offer["last_price"]) if offer["last_price"] else "onbekend"
        line = f"    {shops.label(offer['shop'])}: {state} \u00b7 {when(offer['last_checked'])}"
        if offer["last_error"]:
            line += f" \u00b7 ! {offer['last_error']}"
        out(line)
        out(f"      {offer['value']}")
    return 0


def history_watch(store: Store, args: argparse.Namespace) -> int:
    watch_id = store.resolve(args.id)
    watch = store.watch(watch_id)
    days = args.dagen or 30
    rows = store.history(watch_id, days)
    if args.json:
        emit(rows, True)
        return 0
    if not rows:
        out(f"geen prijzen in de laatste {days} dagen")
        return 0
    last_day = None
    for row in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(row["ts"]))
        if day != last_day:
            out(day)
            last_day = day
        out(f"  {time.strftime('%H:%M', time.localtime(row['ts']))}  {money(row['price']):>10}  {shops.label(row['shop'])}")
    return 0


# ---------------------------------------------------------- statistieken ----


def change_text(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.1f}%".replace(".", ",")


def _period_label(days: int) -> str:
    return "alles" if not days or days <= 0 else f"{days} dagen"


def _period_line(period: dict[str, Any]) -> str:
    return (
        f"  {_period_label(period['days']):<9} {period['points']:>6}  "
        f"{money(period['low']):>10}  {money(period['high']):>10}  "
        f"{money(period['avg']):>10}  {money(period['median']):>10}"
    )


def _stats_watch(store: Store, analysis: dict[str, Any]) -> None:
    watch = store.watch(analysis["id"])
    current = money(analysis["current"])
    shop_name = shops.label(analysis["current_shop"] or "") or "-"
    out(f"{analysis['name']}  ({analysis['id']})")
    line = f"  nu {current} bij {shop_name}"
    line += f"  \u00b7  7 dagen {change_text(analysis['change_7d'])}"
    line += f"  \u00b7  30 dagen {change_text(analysis['change_30d'])}"
    out(line)
    if analysis["low_ever"] is not None:
        low_at = watch.get("low_at")
        stamp = f" ({time.strftime('%d-%m-%Y', time.localtime(low_at))})" if low_at else ""
        out(f"  ooit het laagst: {money(analysis['low_ever'])}{stamp}")
    six = analysis.get("lowest_6m")
    if six:
        out(f"  laagste in een half jaar: {money(six['price'])} ({six['day']})")
    advice = deals.advice(watch, analysis)
    if advice:
        out(f"  advies: {advice}")
    if analysis["savings"]:
        saving = analysis["savings"]
        out(f"  onder de hoogste prijs van de laatste 30 dagen: {money(saving['amount'])} ({pct(saving['pct'])})")
    out("")
    out(f"  {'periode':<9} {'punten':>6}  {'laagst':>10}  {'hoogst':>10}  {'gemiddeld':>10}  {'mediaan':>10}")
    for period in analysis["periods"]:
        out(_period_line(period))
    if analysis["shops"]:
        out("")
        out("  per winkel (ooit):")
        for item in analysis["shops"]:
            out(
                f"    {shops.label(item['shop']):<14} laagst {money(item['low']):>9}"
                f"  \u00b7  hoogst {money(item['high']):>9}"
                f"  \u00b7  laatst {money(item['last']):>9} ({when(item['last_ts'])})"
                f"  \u00b7  {item['points']} meting(en)"
            )
    if not any(period["points"] for period in analysis["periods"]):
        out("  nog geen prijzen; de eerste check vult dit.")


def stats_command(store: Store, args: argparse.Namespace) -> int:
    watches = [store.watch(store.resolve(args.id))] if args.id else store.watches()
    analyses = [store.analysis(watch["id"]) for watch in watches]
    if args.json:
        emit({"generated": int(time.time()), "watches": analyses}, True)
        return 0
    if not analyses:
        out("geen watches. toevoegen met: pricewatch add \"naam\" --zoek amazon-nl=...")
        return 0
    if len(analyses) == 1:
        _stats_watch(store, analyses[0])
        return 0
    out(f"{len(analyses)} watch(es)")
    for analysis in analyses:
        watch = store.watch(analysis["id"])
        advice = deals.advice(watch, analysis)
        out(
            f"  {analysis['id']:<22} {money(analysis['current']):>10}"
            f"  7d {change_text(analysis['change_7d']):>7}"
            f"  30d {change_text(analysis['change_30d']):>7}"
            f"  laagst ooit {money(analysis['low_ever']):>10}"
            + (f"  \u00b7 {advice}" if advice else "")
        )
    return 0


# ------------------------------------------------------- in- en uitvoer ----


CSV_FIELDS = ("watch_id", "name", "shop", "ts", "price", "url", "title", "source")


def _csv_text(payload: dict[str, Any]) -> str:
    names = {watch["id"]: watch["name"] for watch in payload.get("watches") or []}
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    with_prices = {row.get("watch_id") for row in payload.get("prices") or []}
    # Een watch zonder prijzen zou anders uit de csv verdwijnen. Eén regel
    # zonder prijs houdt hem in het bestand; bij het inlezen telt die als watch.
    for watch in payload.get("watches") or []:
        if watch["id"] not in with_prices:
            writer.writerow(
                {"watch_id": watch["id"], "name": watch["name"], "shop": "", "ts": "",
                 "price": "", "url": "", "title": "", "source": ""}
            )
    for row in payload.get("prices") or []:
        writer.writerow(
            {
                "watch_id": row.get("watch_id", ""),
                "name": names.get(row.get("watch_id", ""), ""),
                "shop": row.get("shop", ""),
                "ts": row.get("ts", ""),
                "price": row.get("price", ""),
                "url": row.get("url", ""),
                "title": row.get("title", ""),
                "source": row.get("source", ""),
            }
        )
    return buffer.getvalue()


def export_command(store: Store, args: argparse.Namespace) -> int:
    payload = store.export(args.id)
    fmt = args.formaat
    if fmt == "auto":
        fmt = "csv" if args.output and args.output.lower().endswith(".csv") else "json"
    if fmt == "csv":
        text = _csv_text(payload)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        out(
            f"geschreven: {args.output}"
            f" ({len(payload['watches'])} watch(es), {len(payload['prices'])} prijzen)"
        )
        return 0
    sys.stdout.write(text)
    return 0


def _timestamp(value: Any) -> int | None:
    if value in (None, ""):
        return int(time.time())
    try:
        return int(float(value))
    except (TypeError, ValueError):
        pass
    for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(time.mktime(time.strptime(str(value), pattern)))
        except ValueError:
            continue
    return None


def _csv_payload(text: str) -> dict[str, Any]:
    from .extract import parse_price

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    watches: dict[str, dict[str, Any]] = {}
    prices: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(text), dialect=dialect):
        clean = {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}
        watch_id = clean.get("watch_id") or clean.get("id") or clean.get("name") or clean.get("naam")
        name = clean.get("name") or clean.get("naam") or watch_id
        if not watch_id or not name:
            continue
        value = parse_price(clean.get("price") or clean.get("prijs"))
        watches.setdefault(watch_id, {"id": watch_id, "name": name})
        if value is None:
            continue
        prices.append(
            {
                "watch_id": watch_id,
                "shop": clean.get("shop") or clean.get("winkel") or "",
                "ts": _timestamp(clean.get("ts") or clean.get("tijd")),
                "price": value,
                "url": clean.get("url") or "",
                "title": clean.get("title") or clean.get("titel") or "",
                "source": clean.get("source") or clean.get("bron") or "csv",
            }
        )
    return {"watches": list(watches.values()), "prices": prices}


def import_command(store: Store, args: argparse.Namespace) -> int:
    try:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    except OSError as exc:
        die(str(exc))
    fmt = args.formaat
    if fmt == "auto":
        fmt = "json" if text.lstrip()[:1] in ("{", "[") else "csv"
    try:
        if fmt == "json":
            data = json.loads(text)
            if isinstance(data, list):
                data = {"watches": data}
            if not isinstance(data, dict):
                die("dit json-bestand bevat geen watches")
        else:
            data = _csv_payload(text)
    except json.JSONDecodeError as exc:
        die(f"geen geldige json: {exc}")
    if not data.get("watches"):
        die("geen watches in dit bestand")
    counts = store.import_data(data, replace=args.vervang)
    out(
        f"ge\u00efmporteerd: {counts['watches']} watch(es), {counts['offers']} winkel(s),"
        f" {counts['prices']} prijs(en)"
    )
    if counts["skipped"]:
        out(f"  overgeslagen: {counts['skipped']} regel(s) zonder bruikbare gegevens")
    return 0


# --------------------------------------------------------------- checken ----


def run_check(store: Store, args: argparse.Namespace) -> int:
    if args.progress and not args.json:
        out(f"kijken naar {args.id or 'alles'} ...")

    def progress(line: str) -> None:
        if args.progress:
            out(f"  {line}")

    with checker.run_lock(store) as acquired:
        if not acquired:
            # Niet een fout: de timer is dan al bezig en het heeft geen zin om
            # dezelfde winkels nogmaals te belasten.
            if args.json:
                emit([], True)
            else:
                out("er lokt al een check; deze slaat hij over")
            return 0
        results = checker.check_all(
            store, only=args.id, dry_run=args.dry_run, notify=args.notify, timeout=args.timeout, on_progress=progress
        )
    if args.json:
        emit(
            [
                {
                    "watch": r.watch_id,
                    "name": r.name,
                    "best_price": r.best_price,
                    "best_shop": r.best_shop,
                    "best_shop_name": shops.label(r.best_shop),
                    "deals": [d.as_dict() for d in r.deals],
                    "offers": [
                        {
                            "shop": o.offer.get("shop"),
                            "shop_name": shops.label(o.offer.get("shop") or ""),
                            "price": o.price,
                            "url": o.url,
                            "written": o.written,
                            "error": o.error,
                        }
                        for o in r.offers
                    ],
                }
                for r in results
            ],
            True,
        )
        return 0

    for result in results:
        if args.dry_run:
            for offer in result.offers:
                if offer.error:
                    out(f"  {shops.label(offer.offer.get('shop') or '')}: ! {offer.error}")
                else:
                    out(f"  {shops.label(offer.offer.get('shop') or '')}: {money(offer.price)}")
        best = money(result.best_price)
        line = f"{result.name}: {best}"
        if result.previous_best and result.best_price and result.best_price < result.previous_best:
            line += f" (was {money(result.previous_best)})"
        out(line)
        for offer in result.offers:
            if offer.error:
                out(f"  ! {shops.label(offer.offer.get('shop') or '')}: {offer.error}")
        for deal in result.deals:
            out(f"  \u2193 {deal.text}")
            if deal.url:
                out(f"    {deal.url}")
    if not args.dry_run and not args.notify and any(r.deals for r in results):
        out("meldingen stonden uit; met --notify krijg je ze ook als systeemmelding")
    return 0


def status_command(store: Store, args: argparse.Namespace) -> int:
    payload = store.status()
    # Eén keer uitrekenen: het paneel krijgt dezelfde views als deze tekst.
    views = [watch_view(store, watch) for watch in payload["watches"]]
    payload["watches"] = views
    if args.json:
        emit(payload, True)
        return 0
    out(f"pricewatch {VERSION} \u00b7 {len(views)} watch(es) \u00b7 {payload['deals']} deal(s) \u00b7 {payload['db']}")
    if payload.get("checked"):
        out(f"  laatst gekeken: {when(payload['checked'])}")
    else:
        out("  nog nooit gekeken")
    for view in views:
        price = money(view["best_price"])
        out(f"  {price:>10}  {view['name']}  {view['verdict']}  [{view['offer_line']}]")
    broken = [b for b in payload["broken"] if b["error"]]
    if broken:
        out(f"  {len(broken)} winkel(s) geven geen prijs:")
        for item in broken[:6]:
            out(f"    {item['shop']} ({item['watch']}): {item['error']}")
    return 0


def zoek_command(store: Store, args: argparse.Namespace) -> int:
    """Rondkijken in de winkels; er wordt niets opgeslagen."""
    term = " ".join(args.term.split())
    if len(term) < 2:
        die("geef een zoekterm van minstens twee letters")
    try:
        ids = search.shop_ids(args.shops)
    except ValueError as exc:
        die(str(exc))
    if args.progress and not args.json:
        out(f"zoek naar {term!r} ...")

    def progress(line: str) -> None:
        if args.progress:
            out(f"  {line}")

    results = search.search(term, args.shops or "", timeout=args.timeout, on_progress=progress)
    alle = search.hits(results)

    if args.json:
        emit(
            {
                "term": args.term,
                "shops": [r.as_dict() for r in results],
                "hits": [h.as_dict() for h in alle],
                "errors": [
                    {"shop": r.shop, "shop_name": r.shop_name, "error": r.error, "blocked": r.blocked}
                    for r in results
                    if r.error
                ],
                "blocked_shops": [r.shop_name for r in results if r.blocked],
                # Alleen als een winkel ons écht weigerde: bij een storing heeft
                # de Tweakers-omweg geen zin.
                "tweakers_url": search.tweakers_url(term) if any(r.blocked for r in results) else "",
                "skipped": [{"shop_name": naam, "reason": reden} for naam, reden in search.skipped(args.shops or "")],
            },
            True,
        )
        return 0

    out(f"{len(alle)} treffer(s) voor {term!r}")
    for result in results:
        if result.error:
            out(f"  \u2717 {result.shop_name:14} {result.error}")
            continue
        if not result.hits:
            out(f"  \u00b7 {result.shop_name:14} niets gevonden{': ' + result.note if result.note else ''}")
            continue
        out(f"  \u2713 {result.shop_name:14} {len(result.hits)} treffer(s)")

    # Een winkel die ons wegzet laat zijn prijs alleen aan een browser zien. Zeg
    # waar de mens die prijs wél ziet, in plaats van hem te laten denken dat zijn
    # verbinding stuk is.
    geweigerd = [r.shop_name for r in results if r.blocked]
    if geweigerd:
        out("")
        out(f"  {', '.join(geweigerd)}: prijs alleen zichtbaar in een browser (je verbinding is niet stuk).")
        out("  Zoek hetzelfde product op Tweakers Pricewatch:")
        out(f"    {search.tweakers_url(term)}")
        out("  en volg de productpagina die je daar vindt: pricewatch add '<naam>' --url '<tweakers-productpagina>'")

    if alle:
        out("")
        for index, hit in enumerate(alle[: args.limit], start=1):
            mark = f" [{hit.note}]" if hit.note else ""
            out(f"  {index:2}. {money(hit.price):>10}  {hit.shop_name:14} {hit.title[:58]}{mark}")
            out(f"      {hit.url[:110]}")

    overgeslagen = search.skipped(args.shops or "")
    if overgeslagen:
        out("")
        out("  niet gevraagd, ze zetten ons weg:")
        for naam, reden in overgeslagen:
            out(f"    {naam:14} {reden}")

    volgbaar = [h for h in alle if shops.zoek_adres(h.shop)]
    if alle:
        out("")
        if volgbaar:
            beste = volgbaar[0]
            doel = round(beste.price * 0.9, 2)
            out(f"  toevoegen met: pricewatch add '{beste.title[:40]}' --url '{beste.url}' --doel {doel:.2f}")
        else:
            shop_id = alle[0].shop
            out(
                f"  {shops.label(shop_id)} laadt zijn prijs met JavaScript, dus een product-URL blijft leeg. "
                f"Volg hem op de zoekpagina:"
            )
            out(f"  pricewatch add '{alle[0].title[:40]}' --zoek '{shop_id}={term}' --doel {round(alle[0].price * 0.9, 2):.2f}")
    return 0


def shops_command(store: Store, args: argparse.Namespace) -> int:
    if args.probe:
        for shop_id in [s.strip() for s in args.probe.split(",") if s.strip()]:
            shop = shops.SHOPS.get(shop_id)
            if not shop:
                die(f"onbekende winkel {shop_id!r}; zie: pricewatch shops")
            url = shop.url_for(args.query or "usb-c hub")
            if not url:
                out(f"{shop.name:14} geen zoekadres")
                continue
            state, note = fetch.probe(url, timeout=args.timeout)
            mark = {"ok": "\u2713", "blocked": "\u2717", "404": "\u2717", "netwerk": "?"}.get(state, "?")
            out(f"{mark} {shop.name:14} {state:8} {note}")
        return 0
    if args.json:
        emit(
            [
                {
                    "id": s.id,
                    "name": s.name,
                    "search_url": s.search_url,
                    "parser": s.parser or "generiek",
                    "blocked": s.blocked,
                    "note": s.note,
                }
                for s in shops.SHOPS.values()
            ],
            True,
        )
        return 0
    for shop in shops.SHOPS.values():
        mark = " \u2014 bekend geblokkeerd" if shop.blocked else ""
        out(f"{shop.id:14} {shop.name}{mark}")
        if shop.note:
            out(f"               {shop.note}")
    return 0


def open_command(store: Store, args: argparse.Namespace) -> int:
    import shutil
    import subprocess

    watch = store.watch(store.resolve(args.id))
    url = watch.get("best_url")
    if not url:
        die(f"{watch['id']} heeft nog geen prijs om te openen")
    opener = shutil.which("xdg-open") or shutil.which("open")
    if not opener:
        print(url)
        return 0
    subprocess.Popen([opener, url], start_new_session=True)
    out(f"geopend: {url}")
    return 0


# ------------------------------------------------------------------ main ----


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pricewatch", description="volg producten en hoor wanneer ze goedkoop zijn")
    parser.add_argument("--db", help="pad naar de database (standaard ~/.local/share/pricewatch)")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="voeg een watch toe")
    p.add_argument("name", nargs="?", help="naam van het product")
    p.add_argument("--zoek", action="append", metavar="WINKEL=ZOEKTERM", help="zoek dit product in deze winkel")
    p.add_argument("--url", action="append", metavar="URL", help="volg deze productpagina")
    p.add_argument("--doel", metavar="PRIJS", help="meld wanneer de prijs onder deze prijs zakt")
    p.add_argument("--drempel", metavar="PROCENT", help="meld vanaf deze daling (standaard 10)")
    p.add_argument("--min", dest="minimum", metavar="PRIJS", help="negeer prijzen onder deze bodem (accessoires)")
    p.add_argument("--note", help="notitie bij deze watch")
    p.set_defaults(func=add_watch)

    p = sub.add_parser("list", aliases=["ls"], help="overzicht van alle watches")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=list_watches)

    p = sub.add_parser("show", help="alle details van een watch")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=show_watch)

    p = sub.add_parser("history", aliases=["hist"], help="prijsverloop")
    p.add_argument("id")
    p.add_argument("--dagen", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=history_watch)

    p = sub.add_parser("check", help="kijk de prijzen na")
    p.add_argument("id", nargs="?", help="alleen deze watch (standaard: alles)")
    p.add_argument("--notify", action="store_true", help="stuur ook systeemmeldingen")
    p.add_argument("--dry-run", action="store_true", help="schrijf niets weg")
    p.add_argument("--progress", action="store_true")
    p.add_argument("--timeout", type=float, default=checker.TIMEOUT)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run_check)

    p = sub.add_parser("set", help="wijzig doelprijs, drempel of meldingen")
    p.add_argument("id")
    p.add_argument("--doel", metavar="PRIJS", help="'none' schakelt de doelprijs uit")
    p.add_argument("--drempel", metavar="PROCENT", help="'none' schakelt de drempel uit")
    p.add_argument("--min", dest="minimum", metavar="PRIJS", help="'none' haalt de bodem weg")
    p.add_argument("--mute", action="store_true", help="geen meldingen meer")
    p.add_argument("--unmute", action="store_true")
    p.add_argument("--note")
    p.set_defaults(func=set_watch)

    p = sub.add_parser("rm", aliases=["remove"], help="verwijder een watch en zijn geschiedenis")
    p.add_argument("id")
    p.set_defaults(func=remove_watch)

    p = sub.add_parser("stats", aliases=["analyse"], help="laagste, hoogste en gemiddelde prijs per periode")
    p.add_argument("id", nargs="?", help="alleen deze watch (standaard: alles)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=stats_command)

    p = sub.add_parser("export", help="schrijf watches en prijzen weg als json of csv")
    p.add_argument("id", nargs="?", help="alleen deze watch (standaard: alles)")
    p.add_argument("--formaat", choices=("auto", "json", "csv"), default="auto")
    p.add_argument("--output", metavar="BESTAND", help="schrijf naar dit bestand (standaard: naar het scherm)")
    p.set_defaults(func=export_command)

    p = sub.add_parser("import", help="lees watches en prijzen uit een json- of csv-bestand")
    p.add_argument("file", help="bestand, of - voor stdin")
    p.add_argument("--formaat", choices=("auto", "json", "csv"), default="auto")
    p.add_argument("--vervang", action="store_true", help="gooi een bestaande watch met dezelfde id weg")
    p.set_defaults(func=import_command)

    p = sub.add_parser("status", help="korte stand van zaken (ook voor het paneel)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=status_command)

    p = sub.add_parser("zoek", aliases=["search"], help="zoek een product in de winkels (slaat niets op)")
    p.add_argument("term", help="zoekterm, bijvoorbeeld 'mx master 3s'")
    p.add_argument("--shops", metavar="ID,ID", help="winkels om te vragen (default: amazon-nl, coolblue, mediamarkt, apple)")
    p.add_argument("--limit", type=int, default=12, help="hoeveel treffers tonen (default 12)")
    p.add_argument("--timeout", type=float, default=12.0)
    p.add_argument("--progress", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=zoek_command)

    p = sub.add_parser("shops", help="bekende winkels")
    p.add_argument("--probe", metavar="WINKELS", help="test deze winkels nu, komma-gescheiden")
    p.add_argument("--query", metavar="TEKST", help="zoekterm voor de probe")
    p.add_argument("--timeout", type=float, default=12.0, help="timeout per winkel bij --probe")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=shops_command)

    p = sub.add_parser("open", help="open de beste link van een watch in de browser")
    p.add_argument("id")
    p.set_defaults(func=open_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = open_store(args)
    try:
        return args.func(store, args)
    except KeyError as exc:
        die(f"onbekende watch {exc.args[0]!r}; zie: pricewatch list")
    except BrokenPipeError:
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())