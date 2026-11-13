import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// pricewatch in de bar: de goedkoopste prijs van je watches, met kleur voor de
// stand (doelprijs, nieuw laagste, fout). Klik opent het paneel met alle
// watches; daarin zoek je een product, voeg je het toe en verwijder je wat weg
// is. Middenklik of r kijkt de prijzen opnieuw na.
//
// Alles loopt via bin/pricewatch, dat de database leest. De zoekactie
// (bin/pricewatch zoek) doet netwerkverzoeken en slaat niets op; pas als je
// een treffer kiest wordt het een watch.
Panel {
  id: root
  moduleName: "pricewatch"
  ipcTarget: "pricewatch"

  implicitWidth: button.implicitWidth
  implicitHeight: bar ? bar.barSize : 26

  readonly property string binDir: Quickshell.env("HOME") + "/.config/omarchy/plugins/pricewatch/bin"
  readonly property string scriptPath: binDir + "/pricewatch"
  readonly property string terminalScript: binDir + "/pricewatch-terminal"
  readonly property int bodyFont: Style.font.body
  readonly property int captionFont: Style.font.caption
  readonly property int subtitleFont: Style.font.subtitle
  // Zoeken doet vier winkels achter elkaar; geef het netwerk even de tijd.
  readonly property int zoekTimeout: 20

  // Instellingen uit de manifest; met een terugvalwaarde, zodat een handmatig
  // geplaatste plugin ook werkt.
  readonly property int openInterval: Math.max(15, Number(setting("interval", 60)))
  readonly property int idleInterval: Math.max(60, Number(setting("idleInterval", 300)))

  property var status: ({ schema: 1, watches: [], broken: [], deals: 0, checked_line: "nog nooit gekeken" })
  property string fout: ""
  property bool bezig: false
  property string melding: ""

  // Paneeltoestand: welke rij is geselecteerd, of we zoeken, en wat de zoekactie
  // opleverde. Zoeken en verwijderen delen één lijst; de modus bepaalt wat een
  // tik doet.
  property string modus: "watches" // "watches" of "zoek"
  property int keuze: 0
  property var treffers: []
  property string zoekTerm: ""
  property bool zoekBezig: false

  // Filter en sortering gelden alleen voor de watchlijst; zoektreffers houden
  // de volgorde van de winkels aan. `bewerk` is welk veld onderaan openstaat.
  property string filter: "alles" // "alles", "doel", "storing", "stil"
  property string sortering: "prijs" // "prijs", "naam", "daling"
  property string bewerk: "" // "" of "doel"/"min"

  readonly property var watches: status && status.watches ? status.watches : []
  readonly property var kapot: status && status.broken ? status.broken.filter(function (b) { return b.error }) : []
  readonly property var onderDoel: watches.filter(function (w) { return w.verdict === "doelprijs" })
  // De lijst die het paneel toont. Sorteren op prijs zet watches zonder prijs
  // achteraan; sorteren op daling zet de grootste daling bovenaan.
  readonly property var zichtbaar: {
    var lijst = watches.filter(function (w) {
      if (filter === "doel") return w.verdict === "doelprijs"
      if (filter === "storing") return w.errors && w.errors.length > 0
      if (filter === "stil") return !!w.muted
      return true
    })
    if (sortering === "naam") {
      lijst.sort(function (a, b) {
        return String(a.name).toLowerCase() < String(b.name).toLowerCase() ? -1 : 1
      })
    } else if (sortering === "daling") {
      lijst.sort(function (a, b) { return root.daling(a) - root.daling(b) })
    } else {
      lijst.sort(function (a, b) { return root.prijs(a) - root.prijs(b) })
    }
    return lijst
  }
  readonly property var rijen: modus === "zoek" ? treffers : zichtbaar

  // In de bar: het aantal dat onder de doelprijs staat. Eén watch toont de prijs
  // zelf, want dan is één getal het hele verhaal.
  readonly property string barText: {
    if (root.bezig || root.zoekBezig) return "…"
    if (root.fout !== "") return "!"
    if (root.onderDoel.length > 0) return String(root.onderDoel.length) + "↓"
    if (watches.length === 0) return "0"
    if (watches.length === 1) {
      var en = watches[0]
      return en.best_price === null || en.best_price === undefined ? "—" : money(en.best_price)
    }
    return String(watches.length)
  }

  readonly property bool active: root.fout !== "" || root.kapot.length > 0 || root.onderDoel.length > 0
  readonly property string tip: {
    if (root.fout !== "") return "pricewatch: " + root.fout
    if (watches.length === 0) return "pricewatch — klik om een product te zoeken"
    var s = watches.length + " watch(es)"
    if (root.onderDoel.length > 0) s += " · " + root.onderDoel.length + " onder doelprijs"
    if (root.kapot.length > 0) s += " · " + root.kapot.length + " winkel(s) geven geen prijs"
    s += "\n" + (status && status.checked_line ? status.checked_line : "nog nooit gekeken")
    return s
  }

  function money(value) {
    if (value === null || value === undefined) return "—"
    var n = Number(value)
    return "€" + n.toFixed(2).replace(".", ",")
  }

  function prijs(w) {
    return w && w.best_price !== null && w.best_price !== undefined ? Number(w.best_price) : Infinity
  }

  function daling(w) {
    return w && w.change_7d !== null && w.change_7d !== undefined ? Number(w.change_7d) : Infinity
  }

  function changeText(value) {
    if (value === null || value === undefined) return ""
    var n = Number(value)
    var teken = n > 0 ? "+" : (n < 0 ? "\u2212" : "±")
    return teken + Math.abs(n).toFixed(1).replace(".", ",") + "%"
  }

  function huidige() {
    return root.rijen[root.keuze]
  }

  function filterNaam() {
    if (root.filter === "doel") return "onder doel"
    if (root.filter === "storing") return "met storing"
    if (root.filter === "stil") return "stil"
    return "alles"
  }

  function sorteringNaam() {
    if (root.sortering === "naam") return "naam"
    if (root.sortering === "daling") return "7 dagen"
    return "prijs"
  }

  function shopKleur(w) {
    if (w.verdict === "doelprijs" || w.verdict === "laagste") return Color.accent
    if (w.verdict === "onbekend") return Color.muted
    return root.barForeground
  }

  // ---- status lezen ------------------------------------------------------

  function leesUit(tekst) {
    var data = null
    try { data = JSON.parse(tekst) } catch (e) { data = null }
    if (!data || !data.watches) {
      root.fout = String(tekst || "").trim() || "geen antwoord van pricewatch"
      root.bezig = false
      return
    }
    root.status = data
    root.fout = ""
    root.bezig = false
    if (root.keuze >= root.rijen.length) root.keuze = Math.max(0, root.rijen.length - 1)
  }

  function ophalen() {
    if (root.bezig) return
    root.bezig = true
    statusProc.command = [root.scriptPath, "status", "--json"]
    statusProc.running = true
  }

  function controleren() {
    if (root.bezig) return
    root.bezig = true
    root.melding = "kijkt prijzen na…"
    checkProc.command = [root.scriptPath, "check", "--notify", "--json"]
    checkProc.running = true
  }

  function naCheck(tekst) {
    root.bezig = false
    root.melding = ""
    var data = null
    try { data = JSON.parse(tekst) } catch (e) { data = null }
    if (data && data.deals) root.melding = data.deals + " nieuwe melding(en)"
    else if (data && data.errors && data.errors.length) root.melding = data.errors.length + " winkel(s) geven geen prijs"
    else root.melding = "niets veranderd"
    root.ophalen()
    meldingTimer.restart()
  }

  // ---- zoeken ------------------------------------------------------------

  function startZoek() {
    root.modus = "zoek"
    root.zoekBezig = false
    root.treffers = []
    root.keuze = 0
    root.melding = ""
    Qt.callLater(function () { zoekVeld.forceActiveFocus() })
  }

  function stopZoek() {
    root.modus = "watches"
    root.zoekBezig = false
    root.treffers = []
    root.zoekTerm = ""
    root.keuze = 0
    keys.forceActiveFocus()
  }

  function zoek() {
    var term = String(zoekVeld.text || "").trim()
    if (term.length < 2) {
      root.melding = "typ minstens twee letters"
      return
    }
    root.zoekTerm = term
    root.zoekBezig = true
    root.melding = ""
    zoekProc.command = [root.scriptPath, "zoek", term, "--json", "--timeout", String(root.zoekTimeout)]
    zoekProc.running = true
  }

  function naZoek(tekst) {
    root.zoekBezig = false
    var data = null
    try { data = JSON.parse(tekst) } catch (e) { data = null }
    if (!data || !data.hits) {
      root.melding = "zoeken ging mis; zie de terminal voor de fout"
      return
    }
    root.treffers = data.hits
    root.keuze = 0
    var fouten = (data.errors || []).length
    var over = (data.skipped || []).length
    root.melding = root.treffers.length + " treffer(s) voor " + data.term
      + (fouten > 0 ? " · " + fouten + " winkel(s) gaven niets" : "")
      + (over > 0 ? " · " + over + " winkel(s) niet gevraagd" : "")
    meldingTimer.restart()
    // De treffers zijn binnen, dus het veld is klaar. Laat het veld los, anders
    // blijft de toetsenblokkade aanstaan en werken de pijltjes en cijfers niet.
    Qt.callLater(function () { keys.forceActiveFocus() })
  }

  // Een treffer wordt een watch op de productpagina zelf: de winkel herkent zich
  // aan het adres, en zo volgen we later die pagina in plaats van een zoekpagina.
  function voegTrefferToe(hit) {
    if (!hit) return
    // Sommige treffers kun je niet volgen: die winkel laadt zijn prijs met
    // JavaScript. Zeg dat meteen, anders lijkt het alsof er niets gebeurt.
    if (hit.watchable === false) {
      root.melding = hit.shop_name + " laadt de prijs met JavaScript; volg het met --zoek '"
        + hit.shop + "=" + root.zoekTerm + "'"
      meldingTimer.restart()
      return
    }
    var doel = Math.round(Number(hit.price) * 0.9 * 100) / 100
    root.bezig = true
    root.melding = "voegt " + hit.title.slice(0, 30) + " toe…"
    addProc.command = [
      root.scriptPath, "add", String(hit.title || root.zoekTerm).slice(0, 70),
      "--url", String(hit.url), "--doel", String(doel)
    ]
    addProc.running = true
  }

  function naToevoegen(tekst) {
    root.bezig = false
    var id = String(tekst || "").trim().split("\n")[0].replace("toegevoegd: ", "")
    root.melding = id ? "toegevoegd: " + id : (String(tekst || "").trim() || "toevoegen mislukt")
    root.stopZoek()
    root.ophalen()
    meldingTimer.restart()
  }

  // Een geweigerde toevoeging: zeg waarom, en haal de zoeklijst niet weg.
  function geweigerd(tekst) {
    root.bezig = false
    root.melding = String(tekst || "").trim() || "toevoegen geweigerd"
    meldingTimer.restart()
  }

  // ---- verwijderen --------------------------------------------------------

  function vraagVerwijdering() {
    if (root.modus !== "watches" || !root.huidige()) return
    delConfirm.opened = true
  }

  function verwijderUit() {
    var w = root.huidige()
    if (!w) return
    root.bezig = true
    root.melding = "verwijdert " + w.name + "…"
    delProc.command = [root.scriptPath, "rm", w.id]
    delProc.running = true
  }

  function naVerwijderen(tekst) {
    root.bezig = false
    delConfirm.opened = false
    var regel = String(tekst || "").trim().split("\n")[0]
    root.melding = regel || "verwijderd"
    root.ophalen()
    meldingTimer.restart()
  }

  // ---- bijstellen --------------------------------------------------------

  function startBewerk(soort) {
    var w = root.modus === "watches" ? root.huidige() : null
    if (!w) return
    root.bewerk = soort
    var waarde = soort === "doel" ? w.target : w.floor
    editVeld.text = waarde === null || waarde === undefined ? "" : String(waarde).replace(".", ",")
    root.melding = (soort === "doel" ? "nieuwe doelprijs voor " : "nieuwe bodem voor ") + w.name
    Qt.callLater(function () { editVeld.forceActiveFocus() })
  }

  function stopBewerk() {
    root.bewerk = ""
    root.melding = ""
    keys.forceActiveFocus()
  }

  function pasAan() {
    var w = root.huidige()
    if (!w || root.bewerk === "") return
    var tekst = String(editVeld.text || "").trim()
    // Leeg laten betekent: haal de grens weg. Wie "none" typt komt er ook.
    var waarde = tekst === "" || tekst === "0" ? "none" : tekst.replace(",", ".")
    root.bezig = true
    setProc.command = [
      root.scriptPath, "set", w.id,
      root.bewerk === "doel" ? "--doel" : "--min", waarde
    ]
    setProc.running = true
  }

  function schakelStil() {
    var w = root.modus === "watches" ? root.huidige() : null
    if (!w) return
    root.bezig = true
    setProc.command = [root.scriptPath, "set", w.id, w.muted ? "--unmute" : "--mute"]
    setProc.running = true
  }

  function naZet(tekst) {
    root.bezig = false
    root.bewerk = ""
    root.melding = String(tekst || "").trim().split("\n")[0] || "aangepast"
    root.ophalen()
    meldingTimer.restart()
    Qt.callLater(function () { keys.forceActiveFocus() })
  }

  function volgendeFilter() {
    var volgorde = ["alles", "doel", "storing", "stil"]
    root.filter = volgorde[(volgorde.indexOf(root.filter) + 1) % volgorde.length]
    root.keuze = 0
  }

  function volgendeSortering() {
    var volgorde = ["prijs", "naam", "daling"]
    root.sortering = volgorde[(volgorde.indexOf(root.sortering) + 1) % volgorde.length]
    root.keuze = 0
  }

  // ---- toetsen ------------------------------------------------------------

  function beweeg(dx, dy) {
    if (root.rijen.length === 0) return
    var volgende = root.keuze + dy
    if (volgende < 0) volgende = root.rijen.length - 1
    if (volgende >= root.rijen.length) volgende = 0
    root.keuze = volgende
  }

  // "1".."9" zijn de eerste negen regels, "0" de tiende.
  function rijVoorNummer(teken) {
    if (teken === "0") return 9
    var nr = parseInt(teken, 10)
    return nr >= 1 && nr <= 9 ? nr - 1 : -1
  }

  function nummerVanRij(index) {
    return index < 9 ? String(index + 1) : (index === 9 ? "0" : "")
  }

  function activeren() {
    if (root.modus === "zoek") {
      if (zoekVeld.activeFocus) {
        root.zoek()
        return
      }
      root.voegTrefferToe(root.treffers[root.keuze])
      return
    }
    var w = root.huidige()
    if (w && w.best_url) Qt.openUrlExternally(w.best_url)
  }

  function openWatch(watch) {
    if (watch && watch.best_url) Qt.openUrlExternally(watch.best_url)
  }

  function voegToe() {
    root.startZoek()
  }

  function sluiten() {
    if (root.bewerk !== "") {
      root.stopBewerk()
      return
    }
    if (root.modus === "zoek") {
      root.stopZoek()
      return
    }
    root.close()
  }

  // Vanuit de bar kun je niet typen, dus zetten we een terminal open met het
  // commando er al in. bin/pricewatch-terminal zoekt zelf een terminal.
  function terminal() {
    termProc.command = [root.terminalScript, "add"]
    termProc.running = true
  }

  function tik(knop) {
    if (knop === Qt.MiddleButton) {
      root.controleren()
      return
    }
    root.tonen()
  }

  // Panel.opened is read-only in deze Omarchy-versie; openen en sluiten gaat
  // via open()/close(). Toewijzen aan root.opened doet niets en logt een fout.
  function tonen() {
    if (root.opened) {
      root.close()
      return
    }
    root.open()
    root.ophalen()
  }

  Process {
    id: statusProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.leesUit(text)
    }
    stderr: StdioCollector {
      waitForEnd: true
    }
    onExited: if (!root.status || !root.status.watches) root.bezig = false
  }

  Process {
    id: checkProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.naCheck(text)
    }
    onExited: root.bezig = false
  }

  Process {
    id: zoekProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.naZoek(text)
    }
    onExited: root.zoekBezig = false
  }

  Process {
    id: addProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.naToevoegen(text)
    }
    // pricewatch schrijft zijn weigeringen naar stderr. Zonder deze regel ziet het
    // paneel niets als een toevoeging wordt geweigerd: geen watch, geen uitleg.
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.geweigerd(text)
    }
    onExited: root.bezig = false
  }

  Process {
    id: delProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.naVerwijderen(text)
    }
    onExited: root.bezig = false
  }

  Process {
    id: setProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.naZet(text)
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.geweigerd(text)
    }
    onExited: root.bezig = false
  }

  Process {
    id: termProc
  }

  Timer {
    id: meldingTimer
    interval: 8000
    onTriggered: root.melding = ""
  }

  Timer {
    id: prik
    repeat: true
    // Niet tijdens een zoekopdracht: die praat met het netwerk en heeft de CPU even nodig.
    running: !root.bezig && !root.zoekBezig
    interval: root.opened ? root.openInterval * 1000 : root.idleInterval * 1000
    onTriggered: root.ophalen()
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.barText
    fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
    fontSize: Style.font.body
    foreground: root.barForeground
    active: root.active
    activeColor: root.onderDoel.length > 0 ? Color.accent : Color.urgent
    tooltipText: root.tip
    onPressed: function(knop) { root.tik(knop) }
  }

  KeyboardPanel {
    id: paneel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keys
    contentWidth: Style.space(500)
    contentHeight: Style.space(470)

    PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      // Zit de cursor in het zoekveld of het bijstelveld, dan zijn alle toetsen
      // voor dat veld.
      blocked: (root.modus === "zoek" && zoekVeld.activeFocus)
        || (root.bewerk !== "" && editVeld.activeFocus)

      onMoveRequested: function(dx, dy) { root.beweeg(dx, dy) }
      onActivateRequested: root.activeren()
      onCloseRequested: root.sluiten()
      onDeleteRequested: root.modus === "watches" ? root.vraagVerwijdering() : root.stopZoek()

      onTextKey: function(t) {
        if (t === "r" || t === "R") { root.controleren(); return }
        if (t === "d" || t === "D") { root.vraagVerwijdering(); return }
        if (t === "a" || t === "A") { root.startZoek(); return }
        if (t === "/") { root.startZoek(); return }
        if (t === "q" || t === "Q") { root.sluiten(); return }
        if (root.modus === "watches") {
          if (t === "e" || t === "E") { root.startBewerk("doel"); return }
          if (t === "b" || t === "B") { root.startBewerk("min"); return }
          if (t === "m" || t === "M") { root.schakelStil(); return }
          if (t === "s" || t === "S") { root.volgendeSortering(); return }
          if (t === "f" || t === "F") { root.volgendeFilter(); return }
        }
        var rij = root.rijVoorNummer(t)
        if (rij < 0) return
        if (root.modus === "zoek") {
          // In de zoeklijst kiezen de cijfers alleen een regel; enter voegt toe.
          if (root.treffers[rij]) root.keuze = rij
        } else if (root.rijen[rij]) {
          root.keuze = rij
          root.openWatch(root.rijen[rij])
        }
      }
    }

    Item {
      anchors.fill: parent

      Column {
        anchors.fill: parent
        anchors.margins: Style.space(14)
        spacing: Style.space(8)

        Row {
          width: parent.width
          spacing: Style.space(8)

          Text {
            text: "Pricewatch"
            color: root.barForeground
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: root.subtitleFont
            font.bold: true
          }

          TextField {
            id: zoekVeld
            width: parent.width - 150 - 120
            visible: root.modus === "zoek"
            placeholderText: "zoek een product, bijv. mx master 3s"
            font.pixelSize: root.bodyFont
            verticalPadding: 4
            color: root.barForeground
            accent: Color.accent
            border.width: activeFocus ? 1 : 0
            border.color: Color.accent
            onAccepted: root.zoek()
            // Pijltje naar beneden/oben in het veld = spring naar de lijst.
            Keys.onPressed: function(event) {
              if ((event.key === Qt.Key_Down || event.key === Qt.Key_Up) && root.treffers.length > 0) {
                root.keuze = event.key === Qt.Key_Down ? 0 : root.treffers.length - 1
                keys.forceActiveFocus()
                event.accepted = true
              }
            }
            priority: Keys.BeforeItem
            // Klikken in het veld geeft het veld de focus terug; dan lopen de
            // toetsen weer naar de tekst in plaats van naar de trefferslijst.
            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.IBeamCursor
              onPressed: function (mouse) { mouse.accepted = false }
            }
          }

          Text {
            width: visible ? 0 : parent.width - 140 - 120
            visible: root.modus !== "zoek"
            text: root.bezig || root.zoekBezig ? "bezig…"
              : (root.status && root.status.checked_line ? root.status.checked_line : "")
            color: Color.muted
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: root.captionFont
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignRight
          }

          PanelActionButton {
            iconText: "↻"
            tooltipText: root.bezig ? "bezig" : "kijk alle prijzen na (r)"
            fontSize: root.bodyFont
            color: root.barForeground
            onClicked: root.controleren()
          }
        }

        // Sorteren en filteren: één blik zegt hoe de lijst nu is ingedeeld.
        Row {
          width: parent.width
          visible: root.modus === "watches" && root.watches.length > 0
          spacing: Style.space(10)

          Text {
            text: root.zichtbaar.length + " van " + root.watches.length + " watch(es)"
            color: Color.muted
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: root.captionFont
          }

          Text {
            text: "sorteren: " + root.sorteringNaam() + " (s)"
            color: Color.accent
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: root.captionFont
            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.volgendeSortering()
            }
          }

          Text {
            text: "filter: " + root.filterNaam() + " (f)"
            color: root.filter === "alles" ? Color.muted : Color.accent
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: root.captionFont
            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.volgendeFilter()
            }
          }
        }

        // Doelprijs of bodem bijstellen zonder de terminal.
        Row {
          width: parent.width
          visible: root.bewerk !== ""
          spacing: Style.space(6)

          Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.bewerk === "doel" ? "doelprijs" : "bodem"
            color: root.barForeground
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: root.captionFont
          }

          TextField {
            id: editVeld
            width: parent.width - 190
            placeholderText: root.bewerk === "doel" ? "bijv. 199 (leeg = uit)" : "bijv. 120 (leeg = uit)"
            font.pixelSize: root.bodyFont
            verticalPadding: 4
            color: root.barForeground
            accent: Color.accent
            border.width: activeFocus ? 1 : 0
            border.color: Color.accent
            onAccepted: root.pasAan()
            Keys.onEscapePressed: root.stopBewerk()
            priority: Keys.BeforeItem
          }

          PanelActionButton {
            iconText: "✓"
            tooltipText: "bewaren (enter)"
            fontSize: root.bodyFont
            color: root.barForeground
            onClicked: root.pasAan()
          }

          PanelActionButton {
            iconText: "✕"
            tooltipText: "annuleren (esc)"
            fontSize: root.bodyFont
            color: root.barForeground
            onClicked: root.stopBewerk()
          }
        }

        Text {
          width: parent.width
          visible: root.fout !== "" || root.melding !== ""
          text: root.fout !== "" ? root.fout : root.melding
          color: root.fout !== "" ? Color.urgent : Color.accent
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: root.captionFont
          wrapMode: Text.WordWrap
        }

        // Watches
        ListView {
          id: lijst
          width: parent.width
          height: parent.height - 175
          visible: root.modus === "watches" && root.zichtbaar.length > 0
          clip: true
          spacing: Style.space(6)
          model: root.zichtbaar
          currentIndex: root.keuze

          delegate: Rectangle {
            id: kaart
            required property var modelData
            required property int index
            width: lijst.width
            height: kaartInhoud.implicitHeight + Style.space(14)
            radius: Style.cornerRadius
            color: kaartHover.containsMouse || kaart.index === root.keuze
              ? Color.menu.selectedBackground
              : Color.menu.background
            border.width: kaart.index === root.keuze ? 1 : 0
            border.color: Color.accent

            MouseArea {
              id: kaartHover
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: {
                root.keuze = kaart.index
                root.openWatch(kaart.modelData)
              }
              onDoubleClicked: root.vraagVerwijdering()
            }

            Column {
              id: kaartInhoud
              anchors.fill: parent
              anchors.margins: Style.space(7)
              spacing: Style.space(2)

              Row {
                width: parent.width
                spacing: Style.space(6)

                Text {
                  width: parent.width - 150
                  text: (kaart.index + 1) + ".  " + kaart.modelData.name + (kaart.modelData.muted ? "  \U0001f507" : "")
                  color: root.barForeground
                  font.family: root.bar ? root.bar.fontFamily : Style.font.family
                  font.pixelSize: root.bodyFont
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  text: kaart.modelData.best_price === null || kaart.modelData.best_price === undefined
                    ? "—" : root.money(kaart.modelData.best_price)
                  color: root.shopKleur(kaart.modelData)
                  font.family: root.bar ? root.bar.fontFamily : Style.font.family
                  font.pixelSize: root.subtitleFont
                  font.bold: true
                }
              }

              Text {
                width: parent.width
                text: kaart.modelData.verdict + " · " + kaart.modelData.best_shop_name + " · " + kaart.modelData.offer_line
                color: Color.muted
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: root.captionFont
                elide: Text.ElideRight
              }

              // De vraag achter een watch is niet "wat kost het" maar "is dit
              // een goed moment". Zeven dagen en het 90-daagse gemiddelde zeggen
              // dat in één regel.
              Text {
                width: parent.width
                visible: text !== ""
                text: {
                  var delen = []
                  if (kaart.modelData.change_7d !== null && kaart.modelData.change_7d !== undefined)
                    delen.push("7 dagen " + root.changeText(kaart.modelData.change_7d))
                  if (kaart.modelData.advice) delen.push(kaart.modelData.advice)
                  return delen.join("  ·  ")
                }
                color: kaart.modelData.verdict === "doelprijs" || kaart.modelData.verdict === "laagste"
                  ? Color.accent : Color.muted
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: root.captionFont
                elide: Text.ElideRight
              }

              // Het prijsverloop zelf: het gemiddelde als grijze lijn, het
              // dagdieptepunt in accent. Zo zie je of een daling een uitschieter
              // was of de nieuwe stand.
              Item {
                width: parent.width
                height: Style.space(30)
                visible: kaart.modelData.daily && kaart.modelData.daily.length > 1
                property var dagen: kaart.modelData.daily || []
                onDagenChanged: grafiek.requestPaint()

                Canvas {
                  id: grafiek
                  anchors.fill: parent
                  onPaint: {
                    var ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    var rows = kaart.modelData.daily || []
                    if (rows.length < 2) return
                    var lo = Infinity
                    var hi = -Infinity
                    for (var i = 0; i < rows.length; i++) {
                      lo = Math.min(lo, rows[i][1], rows[i][2])
                      hi = Math.max(hi, rows[i][1], rows[i][2])
                    }
                    if (!isFinite(lo) || !isFinite(hi)) return
                    var pad = 2
                    var stap = (width - pad * 2) / (rows.length - 1)
                    function x(i) { return pad + i * stap }
                    function y(v) {
                      return hi === lo ? height / 2
                        : pad + (hi - v) * (height - pad * 2) / (hi - lo)
                    }
                    function lijn(kolom, kleur) {
                      ctx.beginPath()
                      ctx.strokeStyle = kleur
                      ctx.lineWidth = 1
                      for (var i = 0; i < rows.length; i++) {
                        var px = x(i)
                        var py = y(rows[i][kolom])
                        if (i === 0) ctx.moveTo(px, py)
                        else ctx.lineTo(px, py)
                      }
                      ctx.stroke()
                    }
                    lijn(2, Color.muted)
                    lijn(1, Color.accent)
                  }
                }
              }

              Text {
                width: parent.width
                visible: kaart.modelData.trend !== "" || kaart.modelData.target_line !== ""
                text: (kaart.modelData.trend || "") + "  " + (kaart.modelData.target_line || "")
                color: Color.muted
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: root.captionFont
                wrapMode: Text.WrapAnywhere
              }

              Text {
                width: parent.width
                visible: kaart.modelData.errors && kaart.modelData.errors.length > 0
                text: "\u26a0 " + kaart.modelData.errors.join(" · ")
                color: Color.urgent
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: root.captionFont
                wrapMode: Text.WordWrap
              }

              Text {
                width: parent.width
                visible: kaart.modelData.notes && kaart.modelData.notes.length > 0
                text: "· " + kaart.modelData.notes.join(" · ")
                color: Color.muted
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: root.captionFont
                wrapMode: Text.WordWrap
              }
            }
          }
        }

        // Zoekresultaten
        ListView {
          id: trefferLijst
          width: parent.width
          height: parent.height - 140
          visible: root.modus === "zoek" && root.treffers.length > 0
          clip: true
          spacing: Style.space(4)
          model: root.treffers
          currentIndex: root.keuze

          delegate: Rectangle {
            id: treffer
            required property var modelData
            required property int index
            width: trefferLijst.width
            height: Math.max(26, trefferTekst.implicitHeight + Style.space(10))
            radius: Style.cornerRadius
            color: trefferHover.containsMouse || treffer.index === root.keuze
              ? Color.menu.selectedBackground
              : Color.menu.background

            Row {
              anchors.fill: parent
              anchors.margins: Style.space(5)
              spacing: Style.space(6)

              Text {
                text: root.modus === "zoek" ? root.nummerVanRij(treffer.index) : ""
                visible: text !== ""
                color: root.barForeground
                opacity: 0.6
                width: Style.space(9)
                horizontalAlignment: Text.AlignRight
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: root.captionFont
              }

              Text {
                text: root.money(treffer.modelData.price)
                color: Color.accent
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: root.bodyFont
                font.bold: true
              }

              Text {
                id: trefferTekst
                width: parent.width - 100
                text: treffer.modelData.title + "  ·  " + treffer.modelData.shop_name
                color: root.barForeground
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: root.captionFont
                elide: Text.ElideRight
              }
            }

            // MouseArea LAATST zodat hij bovenop de tekst ligt en klikken vangt.
            MouseArea {
              id: trefferHover
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: {
                root.keuze = treffer.index
                root.voegTrefferToe(treffer.modelData)
              }
            }
          }
        }

        // Zoeken zonder treffers: een uitleg in plaats van een lege lijst.
        Column {
          width: parent.width
          spacing: Style.space(4)
          visible: root.modus === "zoek" && root.treffers.length === 0 && !root.zoekBezig

          Text {
            width: parent.width
            text: root.zoekBezig ? "zoekt…"
              : root.zoekTerm ? "niets gevonden voor " + root.zoekTerm + "."
              : "Typ een product en druk op enter. We vragen Amazon, Coolblue, MediaMarkt, Apple en AliExpress."
            color: Color.muted
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: root.captionFont
            wrapMode: Text.WordWrap
          }

          Text {
            width: parent.width
            visible: !zoekVeld.activeFocus
            text: "druk op / of a om het veld te krijgen"
            color: Color.muted
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: root.captionFont
          }
        }

        Text {
          width: parent.width
          visible: root.modus === "watches" && root.watches.length === 0
          text: "Nog geen watches.\nDruk op a of /, typ een product en druk op enter.\n\nOf in je shell:\n\n  pricewatch zoek 'airpods pro 2'\n  pricewatch add 'AirPods Pro 2' \\\n    --url '<adres van de treffer>' \\\n    --doel 199 --min 120"
          color: Color.muted
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: root.captionFont
          wrapMode: Text.WrapAnywhere
        }

        Text {
          width: parent.width
          visible: root.modus === "watches" && root.watches.length > 0 && root.zichtbaar.length === 0
          text: "geen watch met filter '" + root.filterNaam() + "'. Druk op f voor de volgende."
          color: Color.muted
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: root.captionFont
          wrapMode: Text.WordWrap
        }

        Text {
          width: parent.width
          text: root.modus === "zoek"
            ? "type een product + enter = zoeken · ↑↓ = kiezen · 1-0 = kiezen · enter = toevoegen · esc = terug"
            : "↑↓ kiezen · enter openen · 1-0 openen · e doel · b bodem · m stil · s sorteren · f filter · d verwijderen · a of / zoeken · r nakijken · esc sluiten"
          color: Color.muted
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: root.captionFont
          wrapMode: Text.WordWrap
        }
      }

      ConfirmDialog {
        id: delConfirm
        anchors.fill: parent
        message: root.huidige()
          ? "Verwijder '" + root.huidige().name + "' en de hele geschiedenis?"
          : "Verwijderen?"
        confirmText: "Verwijderen"
        cancelText: "Terug"
        onConfirmed: {
          delConfirm.opened = false
          root.verwijderUit()
        }
        onCanceled: delConfirm.opened = false
      }
    }
  }

  Component.onCompleted: root.ophalen()
}
