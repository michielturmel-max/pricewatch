# pricewatch AGENTS.md

Werktijdregels voor dit project. Kort, want elke regel kost context.

## Bouwen en testen

- `./test/run-all.sh` draait alles: unittest, CLI-tests, `qmllint`, `omarchy plugin validate`.
- Losse suites: `python3 -m unittest discover -s test`, `bash test/cli.test.sh`.
- Live check (netwerk): `PRICEWATCH_HOME=/tmp/pw ./bin/pricewatch check --timeout 25`.
- QML is alleen syntax te controleren met `qmllint -I /usr/share/omarchy/shell Panel.qml`;
  gedrag test je in de echte bar.
- Pakket bouwen: `./dist.sh` (dist/pricewatch-<versie>.tar.gz).

## Stijl

- Python: alleen de standaardbibliotheek, geen dependencies, `from __future__ import annotations`,
  type-hints op functies, Nederlands voor teksten die een mens leest, Engels voor identifiers.
- QML: `qs.Ui` (`Panel`, `KeyboardPanel`, `PanelKeyCatcher`, `WidgetButton`, `PanelActionButton`),
  met voorbeelden in `/usr/share/omarchy/shell/plugins/` (clock, radio-atlas). Nederlands in de
  interface, comments in het Nederlands.
- `Panel.opened` is **read-only**: openen en sluiten gaat met `open()`/`close()`/`toggle()`.
  `root.opened = ...` doet niets en logt `TypeError: Cannot assign to read-only property`.
  Let op: `~/Work/80procent/Panel.qml` doet dit wél en heeft daardoor dezelfde fout.
- Commentaar in code alleen als het *waarom* niet uit de code blijkt (een winkel die omzeilt wordt,
  een prijs die misleidend kan zijn). Geen commentaar dat de regel erboven herhaalt.

## Regels voor dit domein

- Een prijs die we niet herkennen is géén prijs. Liever niets dan een accessoiresprijs
  (hoes, mat, kabel) of een aanbevolen prijs.
- Vertrouw nooit een winkel die zichzelf niet herkent: `blocked`, cooldowns en de laatste fout
  blijven staan tot een volgende check iets beters laat zien.
- Schrijf niets naar de database tijdens `--dry-run` of voordat de plausibiliteitschecks
  (`plausible`, `_drop_implausible`, de bodem van de watch) geslaagd zijn.
- Een mislukte winkel mag nooit de vorige prijs of de vorige geschiedenis wissen.
- Elke nieuwe winkel of parser komt met een fixture in `test/fixtures/` en een test in
  `test/test_extract.py`. Een parser zonder fixture is niet af.
- Zoeken (`pricewatch zoek`, `src/pricewatch/search.py`) is de omgekeerde richting van checken:
  een term in, producten met prijs en productadres uit. Het slaat niets op; pas wat je kiest wordt
  een watch, en dan via de **productpagina** (`--url`), zodat je later die pagina volgt in plaats van
  een zoekpagina. Dedupe per winkel op (url, titel): zoekpagina's herhalen producten.
- Het paneel heeft twee lijsten en één `keuze`: `watches` en de zoektreffers. `modus` bepaalt wat
  `enter` doet. Een QML-id mag geen naam delen met een functie (QML stopt met een dubbele naam).
- Refurbished en tweedehands zijn een ander product: een zoektreffer met zo'n woord telt niet mee
  tenzij de watch er zelf om vraagt (Tweakers houdt ze ook apart).
- `export` schrijft elke prijsregel met zijn `watch_id`; `import` vult de huidige prijs uit de
  nieuwste regel, niet uit het dieptepunt, en telt dubbelen niet nog eens op.

## Grenzen

- Niets installeren, plugins inschakelen, systemd starten of notificaties sturen zonder dat de
  mens daarom vraagt. Dat is het laatste stapje en vraagt een `./install.sh` van een mens.
- De database hoort bij de machine van de mens: `PRICEWATCH_HOME` voor testen, anders
  `~/.local/share/pricewatch/pricewatch.db`.
