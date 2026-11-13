#!/usr/bin/env bash
# Tests voor de pricewatch CLI. Alles draait tegen een weggegooide database en
# een nep-netwerk, dus er is geen winkel nodig en er gaat niets naar buiten.

set -uo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PRICEWATCH="$HERE/../bin/pricewatch"

PASS=0
FAIL=0

ok() { printf '  \033[32mok\033[0m   %s\n' "$*"; PASS=$((PASS + 1)); }
no() {
  printf '  \033[31mFAIL\033[0m %s\n' "$*"
  printf '       %s\n' "${2:-}"
  FAIL=$((FAIL + 1))
}

assert_eq() {
  local want=$1 got=$2 name=$3
  if [[ $want == "$got" ]]; then ok "$name"; else no "$name" "wil: '$want'  kreeg: '$got'"; fi
}

info() { printf '  %s\n' "$*"; }

assert_contains() {
  local haystack=$1 needle=$2 name=$3
  if [[ $haystack == *"$needle"* ]]; then ok "$name"; else no "$name" "'$needle' ontbreekt in: $haystack"; fi
}

assert_fails() {
  local name=$1
  shift
  if "$@" >/dev/null 2>&1; then no "$name" "moet een fout geven"; else ok "$name"; fi
}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export PRICEWATCH_HOME="$WORK/state"
mkdir -p "$PRICEWATCH_HOME"

json_field() { python3 -c "import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }

echo "watches toevoegen en opzoeken"
out=$("$PRICEWATCH" add "AirPods Pro 2" --zoek "amazon-nl=airpods pro 2" --zoek "coolblue=airpods pro 2" --doel 199 --min 120)
assert_contains "$out" "toegevoegd: airpods-pro-2" "add geeft het id terug"
assert_contains "$out" "€199,00" "add toont de doelprijs"
assert_contains "$out" "Amazon.nl" "add toont de winkels"
assert_eq 1 "$("$PRICEWATCH" list --json | json_field 'len(d)')" "een watch aangemaakt"
assert_eq 2 "$("$PRICEWATCH" list --json | json_field 'len(d[0]["offers"])')" "twee winkels opgeslagen"
assert_eq "AirPods Pro 2" "$("$PRICEWATCH" show airpods-pro-2 --json | json_field 'd["name"]')" "show leest de naam terug"
assert_eq "199.0" "$("$PRICEWATCH" show airpods-pro-2 --json | json_field 'd["target"]')" "doelprijs blijft staan"
assert_eq "120.0" "$("$PRICEWATCH" show airpods-pro-2 --json | json_field 'd["floor"]')" "bodem blijft staan"

echo "een tweede watch, en zoeken op naam"
"$PRICEWATCH" add "MX Master 3S" --url "https://www.coolblue.nl/product/970149/x.html" --doel 79 >/dev/null
assert_eq "mx-master-3s" "$("$PRICEWATCH" show "MX Master 3S" --json | json_field 'd["id"]')" "opzoeken op naam werkt"
assert_eq "coolblue" "$("$PRICEWATCH" show mx-master-3s --json | json_field 'd["offers"][0]["shop"]')" "winkel uit de URL herkend"

echo "wijzigen"
"$PRICEWATCH" set airpods-pro-2 --doel 179 >/dev/null
assert_eq "179.0" "$("$PRICEWATCH" show airpods-pro-2 --json | json_field 'd["target"]')" "nieuwe doelprijs"
"$PRICEWATCH" set airpods-pro-2 --mute >/dev/null
assert_eq "True" "$("$PRICEWATCH" show airpods-pro-2 --json | json_field 'd["muted"]')" "stil zetten"
"$PRICEWATCH" set airpods-pro-2 --unmute >/dev/null
assert_eq "False" "$("$PRICEWATCH" show airpods-pro-2 --json | json_field 'd["muted"]')" "weer aanzetten"
"$PRICEWATCH" set airpods-pro-2 --doel none >/dev/null
assert_eq "None" "$("$PRICEWATCH" show airpods-pro-2 --json | json_field 'd["target"]')" "doelprijs uitzetten"
assert_fails "set zonder argumenten klapt" "$PRICEWATCH" set airpods-pro-2

echo "status voor het paneel"
out=$("$PRICEWATCH" status)
assert_contains "$out" "2 watch(es)" "status telt de watches"
assert_eq 0 "$("$PRICEWATCH" status --json | json_field 'd["deals"]')" "zonder prijzen geen deals"
assert_contains "$("$PRICEWATCH" status --json)" '"schema"' "status levert json"

echo "fouten die een mens mag maken"
assert_fails "watch zonder winkel wordt geweigerd" "$PRICEWATCH" add "Lege watch"
assert_fails "--zoek zonder winkel wordt geweigerd" "$PRICEWATCH" add "Leeg" --zoek "geen winkel=ding"
assert_fails "--url zonder scheme wordt geweigerd" "$PRICEWATCH" add "Leeg" --url "bol.com/nl/p/x"
assert_fails "onbekende watch geeft een fout" "$PRICEWATCH" show bestaat-niet
assert_fails "onbekend commando geeft een fout" "$PRICEWATCH" klets
assert_eq 2 "$("$PRICEWATCH" list --json | json_field 'len(d)')" "geweigerde adds laten geen watch achter"

echo "verwijderen"
"$PRICEWATCH" rm airpods-pro-2 >/dev/null
assert_eq 1 "$("$PRICEWATCH" list --json | json_field 'len(d)')" "watch weg"
assert_fails "verwijderde watch bestaat niet meer" "$PRICEWATCH" show airpods-pro-2

echo "winkels"
assert_contains "$("$PRICEWATCH" shops)" "amazon-nl" "shops noemt Amazon"
url=$("$PRICEWATCH" shops --json | python3 -c "import json,sys; print([s for s in json.load(sys.stdin) if s['id']=='amazon-nl'][0]['search_url'].replace('{q}','usb-c+hub'))")
assert_eq "https://www.amazon.nl/s?k=usb-c+hub" "$url" "zoekadres klopt"

echo "geschiedenis van een verse watch is leeg"
assert_contains "$("$PRICEWATCH" history mx-master-3s)" "geen prijzen" "lege geschiedenis zegt dat"

echo "statistieken"
assert_contains "$("$PRICEWATCH" stats mx-master-3s)" "nog geen prijzen" "stats zonder prijzen zegt dat"
assert_eq 1 "$("$PRICEWATCH" stats mx-master-3s --json | json_field 'len(d["watches"])')" "stats json gaat over één watch"
assert_eq "mx-master-3s" "$("$PRICEWATCH" stats mx-master-3s --json | json_field 'd["watches"][0]["id"]')" "stats noemt de watch"

echo "in- en uitvoer"
bestand="$WORK/import.json"
python3 -c '
import json, sys, time
now = int(time.time())
json.dump({
    "schema": 2,
    "watches": [{"id": "imported-lamp", "name": "Imported Lamp", "offers": [
        {"id": "o1", "shop": "coolblue", "kind": "url", "value": "https://www.coolblue.nl/product/1/x.html"}]}],
    "prices": [
        {"watch_id": "imported-lamp", "offer_id": "o1", "shop": "coolblue", "ts": now - 2 * 86400, "price": 30.0, "url": "https://www.coolblue.nl/product/1/x.html"},
        {"watch_id": "imported-lamp", "offer_id": "o1", "shop": "coolblue", "ts": now - 86400, "price": 25.0, "url": "https://www.coolblue.nl/product/1/x.html"},
        {"watch_id": "imported-lamp", "offer_id": "o1", "shop": "coolblue", "ts": now, "price": 20.0, "url": "https://www.coolblue.nl/product/1/x.html"}],
}, open(sys.argv[1], "w"))
' "$bestand"
out=$("$PRICEWATCH" import "$bestand")
assert_contains "$out" "geïmporteerd: 1 watch(es)" "import telt de watch"
assert_contains "$out" "3 prijs(en)" "import telt de prijzen"
assert_eq "20.0" "$("$PRICEWATCH" show imported-lamp --json | json_field 'd["best_price"]')" "import zet de huidige prijs op de nieuwste regel"
assert_eq "20.0" "$("$PRICEWATCH" show imported-lamp --json | json_field 'd["low_ever"]')" "import bewaart het dieptepunt"
assert_eq "-33.3" "$("$PRICEWATCH" stats imported-lamp --json | json_field 'd["watches"][0]["change_7d"]')" "stats rekent de daling uit"
out=$("$PRICEWATCH" stats imported-lamp)
assert_contains "$out" "laagste in een half jaar" "stats noemt de halfjaarsbodem"
assert_contains "$out" "per winkel" "stats splitst per winkel"

"$PRICEWATCH" export imported-lamp --output "$WORK/een.json" >/dev/null
assert_eq 3 "$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["prices"]))' "$WORK/een.json")" "export bewaart de prijzen"
"$PRICEWATCH" export imported-lamp --output "$WORK/een.csv" >/dev/null
assert_eq 3 "$(python3 -c 'import csv,sys; print(sum(1 for r in csv.DictReader(open(sys.argv[1])) if r["price"]))' "$WORK/een.csv")" "csv-export bevat de prijzen"
mkdir -p "$WORK/ander"
out=$(PRICEWATCH_HOME="$WORK/ander" "$PRICEWATCH" import "$WORK/een.csv")
assert_contains "$out" "3 prijs(en)" "csv-import leest de prijzen terug"
assert_eq "20.0" "$(PRICEWATCH_HOME="$WORK/ander" "$PRICEWATCH" show imported-lamp --json | json_field 'd["best_price"]')" "csv-import herstelt de prijs"

"$PRICEWATCH" export mx-master-3s --output "$WORK/leeg.csv" >/dev/null
mkdir -p "$WORK/derde"
out=$(PRICEWATCH_HOME="$WORK/derde" "$PRICEWATCH" import "$WORK/leeg.csv")
assert_contains "$out" "1 watch(es)" "een watch zonder prijzen overleeft een csv-export"

echo "gelijktijdige check wordt geweigerd"
lock="$PRICEWATCH_HOME/check.lock"
: > "$lock"
if command -v flock >/dev/null 2>&1; then
  ( exec 9>"$lock"; flock -n 9; PRICEWATCH=bin/pricewatch true; sleep 3 ) &
  holder=$!
  sleep 0.4
  out=$("$PRICEWATCH" check --json)
  wait "$holder" 2>/dev/null || true
  assert_eq "[]" "$(printf '%s' "$out" | tr -d ' \n')" "check slaat zichzelf over"
else
  info "flock ontbreekt; overgeslagen"
fi

echo "zoeken vraagt het netwerk, dus hier alleen de winkel-lijst en de foutpaden"
assert_contains "$("$PRICEWATCH" --help)" "zoek een product" "zoek staat in de hoofdhelp"
assert_contains "$("$PRICEWATCH" zoek --help)" "--shops" "zoek kan winkels kiezen"

# Een onbekende winkel stopt vóór het netwerk: geen treffers, wel een duidelijke fout.
out=$("$PRICEWATCH" zoek "usb-c hub" --shops bolcom 2>&1)
code=$?
assert_eq "1" "$code" "zoek met onbekende winkel stopt"
assert_contains "$out" "onbekende winkel" "zoek noemt de onbekende winkel"

# Te korte termen stoppen voordat er iets naar een winkel gaat.
out=$("$PRICEWATCH" zoek "" --shops coolblue 2>&1)
assert_contains "$out" "zoekterm" "lege zoekterm wordt geweigerd"
out=$("$PRICEWATCH" zoek "a" --shops coolblue 2>&1)
assert_contains "$out" "zoekterm" "een-letter zoekterm wordt geweigerd"

# Winkels die we niet vragen worden zichtbaar genoemd, met hun reden. "generic"
# vraagt niemand om, dus dit hoeft het netwerk niet.
out=$("$PRICEWATCH" zoek "usb-c hub" --shops generic 2>&1)
assert_contains "$out" "ze zetten ons weg" "zoek zegt welke winkels overgeslagen zijn"
assert_contains "$out" "bol.com" "zoek noemt bol.com als overgeslagen"

# Een winkel die zijn prijs met JavaScript laadt krijgt geen watch op een
# product-URL: die zou elke check leeg terugkomen.
out=$("$PRICEWATCH" add "Tempo" --url "https://nl.aliexpress.com/item/12345.html" 2>&1)
code=$?
assert_eq "1" "$code" "product-URL van een zoek-winkel stopt"
assert_contains "$out" "JavaScript" "de weigering zegt waarom"
assert_contains "$out" "--zoek" "de weigering stelt zoeken voor"
out=$("$PRICEWATCH" ls --json | json_field '[w["id"] for w in d]')
assert_eq "False" "$(printf '%s' "$out" | grep -q tempo && echo True || echo False)" "er komt geen halve watch achter"

printf '\n  %s geslaagd, %s mislukt\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
