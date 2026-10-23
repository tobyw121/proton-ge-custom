# GE-Wine (Prototyp)

Grafische Oberfläche und Startbefehl für Linux Mint 22.x / Ubuntu 24.04,
64-Bit x86. Kein offizielles Wine-, Proton- oder GE-Proton-Produkt.

Das Debian-Paket enthält die Oberfläche und einen privaten UMU-Backendstand
(`e2b203a1fdd2af9f35166f5713cb3f85d72587e2`, Version 1.4.4).
Eine separate Wine-, Steam- oder UMU-Installation ist nicht nötig.
Python/Tk, die Python-Abhängigkeiten und Vulkan-Bibliotheken installiert APT.
GE-Proton und Steam Linux Runtime werden bei Bedarf beim ersten Start bezogen;
Internet, ausreichend freier Speicher und passende Grafiktreiber sind erforderlich.
Der erste Start kann länger dauern. Es handelt sich nicht um ein Offline-Paket.

## Paket bauen und installieren

### Mit GitHub Actions

Der Workflow **GE-Wine Debian package** in `.github/workflows/ge-wine.yml`
baut auf Ubuntu 24.04. Er startet bei Änderungen an `standalone/` oder am
Workflow selbst sowie manuell über **Actions → GE-Wine Debian package → Run workflow**.
Für den manuellen Start muss die Workflow-Datei zunächst auf dem Standardbranch
vorhanden sein. Ein Push auf einen anderen Branch startet den Workflow ebenfalls
automatisch, wenn sich die genannten Dateien ändern.

Der Job führt CLI- und GUI-Tests unter Xvfb aus, baut das Paket, installiert es
auf dem Runner und prüft den privaten UMU-Backendstart und den Desktop-Eintrag.
Unter **Artifacts** des erfolgreichen Laufs liegt
`ge-wine-mint22-ubuntu24.04-amd64` mit `.deb`, SHA256-Prüfsumme und
Launcher-Quelltextarchiv; Aufbewahrungszeit: 14 Tage.
Das Artefakt herunterladen, entpacken und wie unten installieren.
Der Job baut den Launcher; ein vollständiger Build dieses Proton-Forks und
Windows-/GPU-Tests sind darin nicht enthalten.

### Lokal

```sh
bash standalone/build_deb.sh /tmp/ge-wine-dist
cd /tmp/ge-wine-dist
sha256sum -c ge-wine_0.4.1_amd64.deb.sha256
sudo apt install ./ge-wine_0.4.1_amd64.deb
```

Anschließend im Anwendungsmenü **GE-Wine** öffnen oder:

```sh
ge-wine '/pfad/zu/programm.exe' --programmargument
ge-wine --gui
```

Bei Rechtsklick auf eine EXE kann GE-Wine über „Öffnen mit“ gewählt werden.
Das Paket ändert die Standardzuordnung nicht automatisch und ersetzt keinen
vorhandenen `wine`-Befehl.

## Installer und installierte Programme

Für Installer und die installierte Anwendung denselben Prefix verwenden:

```sh
ge-wine --prefix "$HOME/Games/mein-programm" ~/Downloads/setup.exe
ge-wine --prefix "$HOME/Games/mein-programm" \
  "$HOME/Games/mein-programm/drive_c/Program Files/Mein Programm/app.exe"
```

Ein Prefix enthält virtuelle Windows-Laufwerke, Registry und Programmdaten.
In der Oberfläche denselben Ordner im Feld „Programmordner (Prefix)“ wählen.
Ohne Angabe wird je Verzeichnis der EXE ein eigener Prefix unter
`~/.local/share/ge-wine/prefixes` gewählt.

## Version und Spiele-Fixes

Seit 0.4.0 ist standardmäßig **GE-Proton11-11-x86_64** fest eingestellt.
Der Download stammt aus dem offiziellen Release, wird vor dem Entpacken mit
dessen SHA512-Datei geprüft und anschließend wiederverwendet. Mit
`--proton GE-Proton` kann weiterhin ausdrücklich die jeweils neueste
GE-Proton-Version über UMU gewählt werden. Das ist **kein Quellcode-Build dieses Repositorys**. Beim Vergleich
des ursprünglichen Fork-Stands `88004a04` mit dem offiziellen GE-Proton gab es
keine eigenen zusätzlichen Proton-Patches. Künftige eigene Builds lassen sich
weiterhin über ihren entpackten Ordner auswählen:

```sh
ge-wine --proton /pfad/zum/entpackten/eigenen-build programm.exe
ge-wine --gameid umu-DEINE-SPIELKENNUNG spiel.exe
```

Der Build-Ordner muss das ausführbare Proton-Paket mit der Datei `proton`
enthalten, nicht nur den Quellcode. Spielkennungen stehen in der
[UMU-Datenbank](https://umu.openwinecomponents.org/). Ohne Kennung wird
`umu-default` verwendet; spielbezogene Fixes können dann fehlen.
Die in der gewählten GE-Proton-Version enthaltenen Patches bleiben erhalten.
Nicht jedes Windows-Programm, jede Steam-Funktion oder jedes Anti-Cheat-System
funktioniert damit. Nur vertrauenswürdige Programme ausführen; Wine ist keine
Sicherheitsisolierung.

## Icarus Phoenix: RTX 2070 und RTX 3060

Das Profil `icarus-phoenix` und der Menüeintrag **Icarus Phoenix (GE-Wine)**
sind für beide Grafikkarten identisch. Seit Version 0.3.0 ist die Standardauswahl
**Automatisch erkennen**: Ein Ordner namens `ICARUS_Phoenix` (auch mit Leerzeichen
oder Bindestrich) mit `Launcher.exe` und einem Verzeichnis `Bin64` wird erkannt.
Ein anderer Installationsordner lässt sich ausdrücklich über das Profil auswählen.
`--profile standard` deaktiviert die Erkennung.
Dies ist ein Start- und Diagnoseprofil, kein gemessener Performance-Patch
und keine neu kompilierte Proton-Version. Ein aktueller, zur DXVK-Version
passender NVIDIA-Vulkan-Treiber muss auf dem Rechner installiert sein.

Den Client über die [offizielle Downloadseite](https://icarus-phoenix.online/download)
beziehen und entpacken. Die Seite nennt etwa 30 GB freien Speicher und den
Start über `Launcher.exe` **im ursprünglichen Spielordner**:

```sh
ge-wine '/pfad/ICARUS_Phoenix/Launcher.exe'
# Alternativ ausdrücklich auswählen:
ge-wine --profile icarus-phoenix '/pfad/ICARUS_Phoenix/Launcher.exe'
```

Seit 0.4.0 verwendet das Icarus-Profil standardmäßig den vom Nutzer gewünschten
Prefix `~/.local/share/ge-wine/prefixes/icarus-ge10-frisch`. Auf dem Rechner von
`ssc` ergibt das `/home/ssc/.local/share/ge-wine/prefixes/icarus-ge10-frisch`.
Die Oberfläche trägt diesen Pfad bereits ein. Der Ordnername bleibt auf Wunsch
erhalten, auch wenn der Inhalt auf Proton 11 aktualisiert wird. Ein explizites
`--prefix` hat Vorrang; mehrere Spielinstallationen müssen bei Bedarf getrennte
Prefixe bekommen. Launcher, Spiel und Installer des Icarus-Profils verwenden
standardmäßig denselben Prefix.

### Automatische Visual-C++-Einrichtung

Vor dem Spiel-/Launcher-Start im Icarus-Profil richtet GE-Wine die Winetricks-
Pakete `vcrun2008`, `vcrun2010`, `vcrun2012`, `vcrun2013` und `vcrun2015` im
gewählten Prefix ein. Dabei wird die private UMU-Schnittstelle
`umu-run winetricks <paket>` verwendet; UMU startet Winetricks mit `-q`.
Es wird kein System-Wine installiert. Microsoft-Installer werden durch
Winetricks heruntergeladen und mit dessen normalen Prüfsummenprüfungen verwendet.
Eine Internetverbindung ist bei der Ersteinrichtung erforderlich.

Die bestätigten Einträge in `<prefix>/winetricks.log` bestimmen, welche Pakete
noch fehlen. Eine vorhandene `vcrun2017`-, `vcrun2019`- oder `vcrun2022`-
Installation erfüllt die VC++-2015/14-Anforderung und wird nicht heruntergestuft.
Eine Sperre je Prefix verhindert parallele Ersteinrichtungen durch GE-Wine.
Ein Fehler oder fehlender Installationseintrag stoppt den Launcher-Start;
beim nächsten Versuch werden nur die noch fehlenden Pakete erneut versucht.
Manuell ausgeführte Installer unter `Redist` lösen keine zusätzliche automatische
Einrichtung aus.

Zum Überspringen dient der Haken **Icarus: VC++ 2008–2015 automatisch einrichten**
oder beispielsweise:

```sh
ge-wine --no-prerequisites --profile icarus-phoenix '/pfad/ICARUS_Phoenix/Launcher.exe'
```

Einen bestehenden Prefix nicht zwischen GE-Proton 11 und 10 herabstufen.
Für Versionsvergleiche jeweils einen eigenen leeren Prefix wählen.
Die VC++-Einrichtung ist kein bestätigter Fix für den zuvor beobachteten
Stacküberlauf des Launchers. Der Launcher bringt selbst .NET Core 3.1.32 mit;
VC++-Pakete ersetzen diese Laufzeit nicht. Die Setup-Steuerung wird mit einem
Test-Backend geprüft; die Microsoft-Installer und der Phoenix-Start müssen
noch auf dem Zielrechner geprüft werden.

GE-Proton übernimmt DX9/DX11 über DXVK. Das Profil entfernt eine geerbte
`PROTON_USE_WINED3D`-Einstellung, damit nicht versehentlich der OpenGL-Pfad
verwendet wird. Es erzwingt weder einen Grafikmodus im Spiel noch verändert
es Spieldateien. Welche DX-Modi der aktuelle Phoenix-Client anbietet, muss
im Launcher/Spiel geprüft werden; die Server-Webseite dokumentiert das nicht.

Für den ersten Test:

```sh
ge-wine --profile icarus-phoenix --diagnostics '/pfad/ICARUS_Phoenix/Launcher.exe'
```

Das DXVK-HUD zeigt GPU, Direct3D-Modus, FPS, Framezeiten und Shader-Compiler-
Aktivität. Seit 0.4.1 werden außerdem `DXVK_LOG_LEVEL=info`, `PROTON_LOG=1`
und `PROTON_LOG_DIR` gesetzt. Proton- und DXVK-Protokolle liegen gemeinsam
neben dem Prefix in einem Ordner mit Endung `-logs`. `steam-default.log`
entsteht beim Proton-Start; `*_d3d9.log` bzw. `*_d3d11.log` entstehen erst,
wenn ein Prozess die jeweilige Grafikbibliothek initialisiert. Ein leerer
DXVK-Ordner beweist daher nicht, dass das Spiel erfolgreich gestartet ist. Bei einem Hybrid-Laptop zusätzlich `--nvidia-offload`
verwenden bzw. den entsprechenden Haken in der Oberfläche setzen. Auf einem
Desktop mit nur einer GPU ist das normalerweise nicht nötig.

Mit `--low-latency` lässt sich experimentell die Render-Warteschlange für
DX9 und DX11 auf einen Frame begrenzen. Das kann die Eingabelatenz verringern,
aber FPS oder Gleichmäßigkeit verschlechtern; deshalb ist es standardmäßig
ausgeschaltet. Für einen Vergleich dieselbe Szene, Auflösung und Qualität
mehrmals mit und ohne diese Option testen und den Shader-Cache warm laufen
lassen. Es werden keine pauschalen Async-, Speicher- oder Thread-Tweaks erzwungen.

Profilübergabe, gemeinsamer Prefix, Diagnose und GPU-Optionen werden mit einem
Test-Backend geprüft. Der vollständige Phoenix-Client und die beiden echten
Grafikkarten standen hier nicht für einen Spiel-/Performance-Test bereit.

## Prüfung und Grenzen

```sh
python3 -m unittest discover -s standalone/tests -v
```

Die automatisierten Tests prüfen Argumente, Pfade, Prefixe, Backendwahl und
Fehlerweitergabe. Sie ersetzen keine Windows-/GPU-Integrationstests auf Mint.
Mit `DISPLAY` wird zusätzlich die Oberfläche samt Prozessstart geprüft;
ohne Anzeige wird dieser Test übersprungen. In der Cloud wurde dieser Test mit
Xvfb ausgeführt. Das gepackte UMU-Backend wurde über `python3 -m umu --version`
geprüft. Ein vollständiger Windows-Start wurde nicht ausgeführt: Die Cloud
verweigert das Schreiben von `/proc/self/uid_map` für Benutzer-Namensräume.
Ein Test auf einer echten Mint-22-Installation steht aus.
Ein voller Build dieses Proton-Forks
scheiterte zuvor beim SDK-Import an 32 GiB Speicher mit Docker `vfs`.
Dieser Launcher benötigt den großen Build-SDK-Container nicht.

Das Paket enthält UMU unter GPL-3.0; dessen vollständiger zugehöriger
Quelltextstand liegt unter `/usr/share/doc/ge-wine/umu-source.tar.gz`.
Weitere Lizenzhinweise stehen in den jeweiligen UMU-Quelldateien.

## Aktuelle Icarus-Vorgaben ab 0.4.0

- Prefix: `~/.local/share/ge-wine/prefixes/icarus-ge10-frisch`.
- GE-Proton: `GE-Proton11-11-x86_64` aus
  https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton11-11/GE-Proton11-11-x86_64.tar.gz
- Im Icarus-Profil: `d3d9.floatEmulation = True` wird an `DXVK_CONFIG` angehängt.
  Eine ausdrücklich im Prozess gesetzte `d3d9.floatEmulation`-Einstellung hat
  Vorrang; andere DXVK-Einstellungen bleiben erhalten.

Vor dem ersten Start mit Proton 11 Spiel und Launcher schließen. Wenn im
Prefix eine ältere GE-Proton-Version eingetragen ist, erstellt GE-Wine vor
der Aktualisierung eine Kopie unter `<prefix-parent>/.ge-wine-backups/`.
Die Sicherung erhält Symlinks und ersetzt keine vorhandenen Dateien. Bei
Download-, Prüfsummen-, Entpack- oder Sicherungsfehlern wird der Start abgebrochen.
Ein bereits durch UMU oder manuell installierter vollständiger Ordner für
GE-Proton11-11-x86_64 wird wiederverwendet.

Die Sicherung und der Downloader sind mit Test-Archiven und Test-Prefixen
geprüft. Zusätzlich wurden Download, SHA512-Prüfung und sichere Entpackung
des echten offiziellen GE-Proton11-11-x86_64-Archivs in der Cloud ausgeführt. Diese Vorgaben entsprechen dem Nutzerwunsch und sind kein bestätigter
Fix für den früheren Launcher-Absturz oder die CryEngine-Darstellungsfehler.
