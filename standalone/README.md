# GE-Wine (Prototyp)

Grafische Oberfläche und Startbefehl für Linux Mint 22.x / Ubuntu 24.04,
64-Bit x86. Kein offizielles Wine-, Proton- oder GE-Proton-Produkt.

Das Debian-Paket enthält die Oberfläche und einen privaten UMU-Backendstand
(`e2b203a1fdd2af9f35166f5713cb3f85d72587e2`, Version 1.4.4).
Eine separate Wine-, Steam- oder UMU-Installation ist nicht nötig.
Python/Tk, die Python-Abhängigkeiten und Vulkan-Bibliotheken installiert APT.
GE-Proton und Steam Linux Runtime werden erst beim Start durch UMU bezogen;
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
sha256sum -c ge-wine_0.1.0_amd64.deb.sha256
sudo apt install ./ge-wine_0.1.0_amd64.deb
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

Standardmäßig lädt UMU mit `PROTONPATH=GE-Proton` die offizielle GE-Proton-Version
herunter. Das ist **kein Build des hier ausgewählten Forks**. Dessen zusätzliche
Patches sind erst mit einem eigenen, fertig gebauten Proton-Paket verfügbar:

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
