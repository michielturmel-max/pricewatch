#!/usr/bin/env bash
# Alle tests van pricewatch: python (unittest) en de CLI in bash.
# Geen netwerk nodig; de live winkels worden getest met ./bin/pricewatch check.
set -uo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname "$HERE")

printf '\033[1mpython (unittest)\033[0m\n'
( cd "$ROOT" && python3 -m unittest discover -s test -v ) || exit 1

printf '\n\033[1mcli (bash)\033[0m\n'
bash "$HERE/cli.test.sh" || exit 1

printf '\n\033[1mqml\033[0m\n'
if command -v qmllint >/dev/null 2>&1; then
  qmllint -I /usr/share/omarchy/shell "$ROOT/Panel.qml" && printf '  Panel.qml ok\n'
else
  printf '  qmllint niet gevonden; overgeslagen\n'
fi

printf '\n\033[1mplugin\033[0m\n'
if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin validate "$ROOT" && printf '  manifest ok\n'
else
  printf '  omarchy niet gevonden; overgeslagen\n'
fi

printf '\n\033[1m%sklaar\033[0m\n' "$(printf '\033[32m')"
