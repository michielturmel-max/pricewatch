#!/usr/bin/env bash
# Officiële Omarchy-plugininstallatie voor pricewatch: volg producten over
# meerdere winkels en zie de beste prijs in de bar. Werkt vanaf elke locatie.
#
#   install     installeer of ververs de plugin (standaard)
#   timer       installeer de systemd user-timer die op de achtergrond meekijkt
#   timer-off   haal de timer weer weg
#   cli         symlink pricewatch in ~/.local/bin (optioneel, handig in de shell)
#   version     toon gebundelde en geïnstalleerde versie
#   uninstall   verwijder de plugin; je watches en geschiedenis blijven staan
set -euo pipefail

SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$SRC_DIR/manifest.json"
PLUGINS_DIR="$HOME/.config/omarchy/plugins"
UNITS_DIR="$HOME/.config/systemd/user"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf 'installer: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
gebruik: ./install.sh [commando]

commando:
  install     (standaard) installeer of ververs de plugin
  timer       installeer en start de systemd user-timer voor achtergrondcontrole
  timer-off   stop de timer en verwijder de units
  cli         leg pricewatch in ~/.local/bin
  version     toon gebundelde en geïnstalleerde versie
  uninstall   verwijder de plugin (watches en database blijven)
  help        dit overzicht
EOF
}

require_omarchy() {
  command -v omarchy >/dev/null 2>&1 || fail "omarchy is niet gevonden; dit is een Omarchy-plugin"
  command -v jq >/dev/null 2>&1 || fail "jq is niet geïnstalleerd"
}

plugin_id()      { jq -r '.id // empty' "$MANIFEST"; }
bundled_version() { jq -r '.version // "0"' "$MANIFEST"; }

installed_version() {
  local m="$PLUGINS_DIR/$(plugin_id)/manifest.json"
  [[ -f $m ]] && jq -r '.version // "0"' "$m" || echo ""
}

# De prijzen worden ook buiten de shell opgehaald; dat doet de timer, elke
# 30 minuten, met dezelfde database als het paneel.
install_timer() {
  local id
  id=$(plugin_id)
  mkdir -p "$UNITS_DIR"
  cp -a "$SRC_DIR/systemd/pricewatch-check.service" "$UNITS_DIR/pricewatch-check.service"
  cp -a "$SRC_DIR/systemd/pricewatch-check.timer" "$UNITS_DIR/pricewatch-check.timer"
  # Het script staat in de plugin, dus daar blijven de units naar verwijzen.
  info "units in $UNITS_DIR gekopieerd (plugin $id)"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
    systemctl --user enable --now pricewatch-check.timer
    say "timer staat aan"
    systemctl --user list-timers --no-pager pricewatch-check.timer | sed -n '1,3p' || true
  else
    info "systemctl niet gevonden; start de timer zelf met:"
    info "  systemctl --user enable --now pricewatch-check.timer"
  fi
}

remove_timer() {
  if systemctl --user list-unit-files pricewatch-check.timer >/dev/null 2>&1; then
    systemctl --user disable --now pricewatch-check.timer || true
  fi
  rm -f "$UNITS_DIR/pricewatch-check.service" "$UNITS_DIR/pricewatch-check.timer"
  command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload || true
  say "timer uit"
}

install_cli() {
  mkdir -p "$HOME/.local/bin"
  ln -sf "$PLUGINS_DIR/$(plugin_id)/bin/pricewatch" "$HOME/.local/bin/pricewatch"
  say "pricewatch staat in ~/.local/bin"
  info "in deze sessie nog niet beschikbaar? hash -r"
}

install() {
  require_omarchy
  local id
  id=$(plugin_id)

  say "[1/4] Plugin valideren (officiële schema- en veiligheidscheck)"
  omarchy plugin validate "$SRC_DIR"
  info "ok: $id"

  say "[2/4] Kopiëren naar $PLUGINS_DIR/$id"
  local stage="$PLUGINS_DIR/.$id.install.$$"
  rm -rf "$stage"
  mkdir -p "$stage"
  cp -a "$SRC_DIR/manifest.json" "$SRC_DIR/Panel.qml" "$stage/"
  cp -a "$SRC_DIR/bin" "$stage/bin"
  cp -a "$SRC_DIR/src" "$stage/src"
  cp -a "$SRC_DIR/systemd" "$stage/systemd"
  chmod +x "$stage"/bin/pricewatch
  find "$stage" -name '__pycache__' -type d -prune -exec rm -rf {} +

  if [[ -e $PLUGINS_DIR/$id ]]; then
    mkdir -p "$HOME/.config/omarchy/plugin-backups"
    cp -a "$PLUGINS_DIR/$id" "$HOME/.config/omarchy/plugin-backups/$id-$(date +%Y%m%d-%H%M%S)"
    info "vorige installatie bewaard in plugin-backups"
  fi
  rm -rf "$PLUGINS_DIR/$id"
  mv "$stage" "$PLUGINS_DIR/$id"

  say "[3/4] Plugins opnieuw laten ontdekken en inschakelen"
  omarchy-shell shell rescanPlugins >/dev/null || true
  if ! omarchy plugin list --json 2>/dev/null | jq -e --arg id "$id" 'any(.[]; .id == $id and .enabled)' >/dev/null 2>&1; then
    for (( attempt = 0; attempt < 40; attempt++ )); do
      omarchy plugin list --json 2>/dev/null | jq -e --arg id "$id" 'any(.[]; .id == $id)' >/dev/null 2>&1 && break
      sleep 0.05
    done
    local section
    section=$(jq -r '.barWidget.defaultSection // "right"' "$MANIFEST")
    omarchy plugin enable "$id" "$section"
  fi

  say "[4/4] Klaar"
  info "De database staat op ~/.local/share/pricewatch/pricewatch.db"
  info 'Zoek een product (in het paneel met a of /, of hier):'
  info "  pricewatch zoek 'airpods pro 2'"
  info "  pricewatch add 'AirPods Pro 2' --url '<adres van de treffer>' --doel 199 --min 120"
  info "Achtergrondcontrole: ./install.sh timer"
  info "Versies: ./install.sh version | verwijderen: ./install.sh uninstall"
}

uninstall() {
  require_omarchy
  local id
  id=$(plugin_id)
  if omarchy plugin list --json 2>/dev/null | jq -e --arg id "$id" 'any(.[]; .id == $id)' >/dev/null 2>&1; then
    omarchy plugin remove "$id"
  else
    info "plugin $id is niet geïnstalleerd; niets te doen"
  fi
  say "Je watches en geschiedenis blijven in ~/.local/share/pricewatch staan."
}

version() {
  say "Versie-overzicht"
  info "gebundeld (deze map):  $(bundled_version)"
  local inst
  inst=$(installed_version)
  [[ -n $inst ]] && info "geïnstalleerd:          $inst" || info "niet geïnstalleerd op deze machine"
}

case "${1:-install}" in
  install|"") install ;;
  timer) install_timer ;;
  timer-off) remove_timer ;;
  cli) install_cli ;;
  version|info) version ;;
  uninstall|remove|rm) uninstall ;;
  help|-h|--help) usage ;;
  *) fail "onbekend commando '$1' (zie: ./install.sh help)" ;;
esac
