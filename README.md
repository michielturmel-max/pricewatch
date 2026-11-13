# pricewatch

Volg een product over meerdere winkels en hoor wanneer het ergens goedkoop is.
Een bar-widget in Omarchy toont de goedkoopste prijs; de CLI bewaart de geschiedenis
en stuurt een melding bij je doelprijs, een flinke daling of een nieuw laagste.

Geen dependencies: python3 uit de standaardbibliotheek en één SQLite-bestand.
De winkels worden gelezen, niet omzeild met een API — dus wat je ziet is wat de
winkel op dat moment laat zien.

## Downloaden en installeren

```bash
git clone https://github.com/michielturmel-max/pricewatch.git
cd pricewatch
./install.sh              # bar-widget in ~/.config/omarchy/plugins/pricewatch
./install.sh timer        # optioneel: elke 30 minuten nakijken en melden
./install.sh cli          # optioneel: pricewatch in ~/.local/bin
```

Daarna staat de widget in de balk. Zo niet: `omarchy-restart-shell` of
`omarchy plugin enable pricewatch`. De CLI werkt met alleen `python3` uit de
standaardbibliotheek; voor de bar is Omarchy met de Quickshell-shell nodig.

`./install.sh version` toont de versie, `./install.sh uninstall` verwijdert de
plugin (je watches en geschiedenis blijven staan) en `./install.sh help` toont
alle opties.

## Snel starten

```bash
./install.sh              # plugin in ~/.config/omarchy/plugins/pricewatch
./install.sh timer        # elke 30 minuten op de achtergrond nakijken + melden
./install.sh cli          # pricewatch in ~/.local/bin

pricewatch zoek 'airpods pro 2'   # rondkijken; slaat niets op
pricewatch add 'AirPods Pro 2' \
  --url 'https://www.coolblue.nl/product/971808/apple-airpods-pro-2.html' \
  --doel 199 --min 120

pricewatch check          # nu alle prijzen nakijken
pricewatch list           # wat volg ik
```

`--min` is een bodem: alles wat eronder ligt is geen echte prijs (een hoesje van
€4,84 is geen AirPods Pro). Zonder bodem kan een winkel je een fout idee geven.

## De bar

- **Getal** — het aantal watches onder je doelprijs. Met één watch is dat de
  goedkoopste prijs; zonder watches een `0`.
- **Kleur** — oranje als er iets onder doelprijs is, rood als een winkel geen
  prijs geeft.
- **Klik** opent het paneel: per product de beste prijs, de winkel, je doelprijs,
  het koersverloop van de laatste dagen en welke winkels fout gingen.
- **Middenklik** kijkt direct na.

In het paneel:

| toets | wat het doet |
| --- | --- |
| `↑` `↓` | product kiezen |
| `enter` | gekozen product openen in je browser |
| `1`–`9` | dat product meteen openen |
| `e` | doelprijs bijstellen |
| `b` | bodem (minimumprijs) bijstellen |
| `m` | watch stilzetten of weer aanzetten |
| `s` | sorteren: prijs, naam of grootste daling |
| `f` | filteren: alles, onder doel, met storing of stil |
| `d` | gekozen watch verwijderen, na een vraag |
| `a` of `/` | zoekveld openen |
| `r` | alle prijzen nakijken |
| `esc` | zoeken afbreken, of het paneel sluiten |

Elke kaart toont naast de prijs het verschil met zeven dagen geleden, een advies
ten opzichte van het 90-daagse gemiddelde en het prijsverloop: het dagdieptepunt
als lijn en het gemiddelde van die dag erachter. Zo zie je of een daling een
uitschieter was of de nieuwe stand.

In het zoekveld: typ een product en druk op `enter`. De treffers staan goedkoopste
eerst; `↑` `↓` kiest en `enter` voegt die productpagina toe als watch, met een
doelprijs van 10% onder de gevonden prijs. `pricewatch zoek` doet hetzelfde
vanaf de terminal.

Achtergrondcontrole loopt via de systemd-timer (`./install.sh timer`), die ook de
meldingen stuurt. Het paneel stuurt ze alleen als je zelf op nakijken drukt.

## Meldingen

Je krijgt iets als:

- **doelprijs bereikt** — de prijs is onder of gelijk aan `--doel`.
- **nieuwe laagste prijs** — goedkoper dan alles wat we eerder zagen.
- **X% onder de 30-daagse prijs** — een daling vanaf het mediane dagminimum,
  standaard vanaf 10%, met `--drempel` aan te passen.

Elke melding komt per dag hoogstens één keer per soort, per product. Stilzetten
kan per watch (`pricewatch set <watch> --mute`) of per aanbod.

## Commando's

| commando | wat het doet |
| --- | --- |
| `zoek "term" [--shops id,id] [--json]` | producten zoeken; slaat niets op |
| `add "naam" --zoek shop=zoekterm [--url adres]` | watch toevoegen, meerdere winkels mag |
| `list` / `ls` | alle watches met beste prijs en stand |
| `show <watch>` | details, winkels en laatste fouten |
| `history <watch>` | prijsverloop per dag |
| `stats [<watch>] [--json]` | laagst, hoogst, gemiddeld en mediaan per periode, plus advies |
| `export [<watch>] [--formaat json\|csv] [--output bestand]` | watches en prijzen bewaren |
| `import <bestand> [--vervang]` | een export terugzetten of samenvoegen |
| `check [--notify] [--dry-run]` | prijzen nakijken; `--dry-run` schrijft niets |
| `set <watch> --doel/--drempel/--min/--mute` | doel, drempel, bodem of stilte aanpassen |
| `rm <watch>` | watch weg, inclusief geschiedenis |
| `status [--json]` | korte stand; de `--json` voedt het paneel |
| `shops [--probe id,id]` | bekende winkels; `--probe` test of ze nog antwoorden |
| `open <watch>` | beste aanbod openen in je browser |

Een watch herken je aan zijn id (`airpods-pro-2`), maar de naam mag ook.

`pricewatch stats` laat per watch zien wat de prijs deed in 7, 30 en 90 dagen en
sinds het begin: laagste, hoogste, gemiddelde en mediane dagminimum. Daaruit
volgt het advies — onder je doelprijs, laagste in een half jaar, of juist boven
het 90-daagse gemiddelde. Dat is dezelfde vraag die de Tweakers Pricewatch bij
een product beantwoordt met zijn prijsontwikkeling: niet alleen "wat kost het",
maar "is dit een goed moment".

`export` schrijft alles naar json of csv; `import` leest het terug en telt
dubbelen niet nog eens op. Bij het importeren wordt de huidige prijs uit de
nieuwste regel gehaald, niet uit het dieptepunt van vroeger, zodat een oude
aanbieding niet als de prijs van vandaag blijft staan.

## Winkels

| id | winkel | zoeken werkt |
| --- | --- | --- |
| `amazon-nl` | Amazon.nl | ja, soms rate-limited |
| `coolblue` | Coolblue | ja, via categoriepagina |
| `mediamarkt` | MediaMarkt | ja |
| `apple` | Apple | ja |
| `informatique` | Informatique | ja |
| `proshop` | ProShop | vaak 429, product-URL werkt |
| `tweakers` | Tweakers Pricewatch | nee (captcha), productpagina werkt |
| `bol` | bol.com | nee (403), product-URL werkt |
| `azerty`, `alternate`, `paradiso`, `megekko`, `fnac` | diverse | nee, product-URL werkt |

Tweakers vergelijkt zelf tientallen winkels. Een pricewatch-productpagina lezen
we als één aanbieding: de laagste nieuwe prijs, met de winkel erbij. Refurbished
en tweedehands staan daar los van en tellen niet mee, net zoals ze hier niet
meetellen voor een nieuwe watch. Zoeken op Tweakers vraagt om een captcha en
blijft daarom overgeslagen; geef de productpagina met `--url`.

`pricewatch shops --probe amazon-nl,coolblue` zegt welke winkels nu antwoorden.
Een winkel die weigert, blijft in je lijst staan met de fout erbij; de vorige prijs
blijft gewoon staan. Je kunt elke winkel met `--url` een productpagina geven, dan
lezen we die pagina rechtstreeks.

## Waar staat het

```
~/.local/share/pricewatch/pricewatch.db   watches, prijzen, geschiedenis
~/.local/share/pricewatch/cooldown.json   welke winkel even niet mag
journalctl --user -u pricewatch-check.service   log van de timer
~/.config/systemd/user/pricewatch-check.* de timer
PRICEWATCH_HOME=...                       zet alles ergens anders (handig voor testen)
```

## Ontwikkelen

```bash
./test/run-all.sh                        # unittest, CLI, QML en manifest
python3 -m unittest discover -s test     # alleen python
PRICEWATCH_HOME=/tmp/pw ./bin/pricewatch check   # live, raakt geen echte database
./dist.sh                                # dist/pricewatch-<versie>.tar.gz
```

Nieuwe winkel of parser? Zie `AGENTS.md`: een fixture in `test/fixtures/` en een
test in `test/test_extract.py` horen erbij, anders is de parser niet af.

## Grenzen

- Prijzen komen uit de openbare zoekpagina van de winkel. Prijzen, voorraad en
  verzending kunnen per gebruiker, per cookie en per moment verschillen.
- Je watcht wat de winkel toont; een aanbevolen of afgeprijsde "vanaf"-prijs
  wordt alleen geaccepteerd als er geen andere, duidelijkere prijs bij staat.
- Er gaat niets naar een externe dienst. De winkelRequests zijn gewone
  paginaopvrachten, met een rustige pauze per winkel (cooldown).

## Licentie

MIT, zie [LICENSE](LICENSE).
