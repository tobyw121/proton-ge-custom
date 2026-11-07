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
sha256sum -c ge-wine_0.13.0_amd64.deb.sha256
sudo apt install ./ge-wine_0.13.0_amd64.deb
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

Seit 0.6.1 verwendet **Icarus Phoenix GE-Proton10-34**, waehrend
**Icarus Online/VFUN und Standard GE-Proton11-11-x86_64** verwenden. Der Nutzer hat einen erfolgreichen VFUN-Start
mit dieser Version gemeldet; ein erneuter Phoenix-Test steht noch aus.
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

Seit 0.4.2 verwendet das Icarus-Profil den separaten Prefix
`~/.local/share/ge-wine/prefixes/icarus-ge10-34-clean`. Die Oberfläche trägt
diesen Pfad bereits ein. Der zuvor mit Proton 11 verwendete Ordner
`icarus-ge10-frisch` bleibt erhalten und wird nicht automatisch übernommen.
Ein explizites `--prefix` hat Vorrang; mehrere Spielinstallationen müssen bei Bedarf getrennte
Prefixe bekommen. Launcher, Spiel und Installer des Icarus-Profils verwenden
standardmäßig denselben Prefix.

### Automatische Visual-C++- und Shader-Compiler-Einrichtung

Vor dem Spiel-/Launcher-Start im Icarus-Profil richtet GE-Wine die Winetricks-
Pakete `vcrun2008`, `vcrun2010`, `vcrun2012`, `vcrun2013` und `vcrun2015` im
gewählten Prefix ein. Ab 0.4.3 kommt `d3dcompiler_43` hinzu: Winetricks installiert
Microsofts Shader-Compiler und setzt dessen native DLL-Übersteuerung. Dabei wird die private UMU-Schnittstelle
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

Zum Überspringen dient der Haken **Icarus: VC++ und Shader-Compiler automatisch einrichten**
oder beispielsweise:

```sh
ge-wine --no-prerequisites --profile icarus-phoenix '/pfad/ICARUS_Phoenix/Launcher.exe'
```

Bestehende Prefixe werden mit der Version des gewaehlten Profils verwendet. Es wird keine automatische Prefix-Kopie erstellt. Nach dem Wechsel
diesen Prefix nicht mit GE10 zurueckstufen. Vor dem Update Spiel und Launcher schliessen.
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

## Icarus Online / VFUN und Phoenix ab 0.5.0

Phoenix verwendet ab 0.6.1 GE-Proton10-34, Online/VFUN GE-Proton11-11-x86_64.
Beide Profile richten VC++ 2008–2015 sowie
Microsofts `d3dcompiler_43` mit nativer DLL-Einstellung ueber UMU/Winetricks ein.
Bereits bestaetigte Pakete werden uebersprungen. `--no-prerequisites` bzw. der
Haken in der GUI schalten diese Einrichtung fuer beide Profile ab.

| Profil | Standard-Prefix |
| --- | --- |
| `icarus-phoenix` | `~/.local/share/ge-wine/prefixes/icarus-ge10-34-clean` |
| `icarus-online` | `~/.local/share/ge-wine/prefixes/icarus-vfun` |

Der Phoenix-Ordnername bleibt erhalten und wird wieder mit GE10 verwendet.
Die bestehenden Prefixe bleiben erhalten, ohne neue
Kopie oder automatische Sicherung. Vorhandene alte Sicherungen bleiben erhalten.
Ein ausdruecklich gewaehlter Prefix oder Proton-Build hat weiterhin Vorrang.

```sh
ge-wine --gui --profile icarus-phoenix
ge-wine --gui --profile icarus-online
ge-wine --profile icarus-online \
  "$HOME/.local/share/ge-wine/prefixes/icarus-vfun/drive_c/VFUN/VLauncher/VFUNLauncher.exe"
```

Im Anwendungsmenue gibt es fuer beide Profile einen eigenen Eintrag. Die GUI
passt den vorgeschlagenen Prefix an Profil und Programmdatei an, behaelt aber
einen individuell eingegebenen Prefix bei. Die automatische Erkennung erkennt
`Icarus_Online/Bin64` sowie VFUN-Launcher unter einem VFUN-Verzeichnis mit
installiertem `Icarus_Online/Bin64`. Ein beliebiger VFUN-Launcher ohne diese
Spielinstallation bekommt nicht automatisch das Icarus-Profil. Fuer den
VFUN-Installer im Downloads-Ordner das Online-Profil ausdruecklich auswaehlen.

Es werden keine GameGuard-Dateien veraendert und keine Anti-Cheat-Funktionen
abgeschaltet. Die frueher getestete EXE-Header-Korrektur fuer Installer wird
nicht automatisch angewandt. Die Grafikoption `d3d9.floatEmulation` wird nicht
erzwungen. Explizite DXVK-Umgebungsvariablen bleiben wirksam.

## Shader-Korrektur ab 0.4.3

Im Icarus-Profil wird der Microsoft-Shader-Compiler `d3dcompiler_43` vor dem
Spielstart automatisch über denselben UMU-/Proton-Build im gewählten Prefix
eingerichtet. Der Nutzer hat bestätigt, dass dessen manuelle Installation die
Darstellungsprobleme auf seinem Setup behebt. Zuvor meldete Wines eingebauter
Compiler wiederholt `E5001: Modifiers are not allowed on casts`.

Bestehende Prefixe bekommen nur das fehlende Paket; bestätigte Installationen
werden nicht erneut ausgeführt. Bereits manuell installiertes `d3dcompiler_43`
wird anhand des Winetricks-Protokolls erkannt. Die Einstellung gilt nur für das
Icarus-Profil (auch bei automatischer Erkennung); `--no-prerequisites` schaltet
auch diese Installation ab. Ab 0.5.0 gilt dies fuer beide Icarus-Profile;
Die profilabhaengige Proton-Version wird mit den bisherigen Prefix-Pfaden verwendet. Es gibt keine Änderung an den Spieldateien und keinen neuen Proton-Build.

Die Steuerung ist automatisiert mit einem Test-Backend geprüft. Ein neuer
Spieltest der automatischen Installation auf dem Zielrechner steht noch aus.

## Sofort beenden und Desktop-Verknuepfungen ab 0.6.0

In der GUI beendet **Sofort beenden** die aktuelle gestartete GE-Wine-Anwendung
und ihre erfassten Kindprozesse hart. Die GUI bleibt offen. Nicht gespeicherte
Aenderungen der Anwendung koennen verloren gehen. Ein laufender Proton-Download
wird kooperativ nach dem aktuellen Netzwerkzugriff abgebrochen. Die automatische
Laufzeit-Einrichtung setzt beim naechsten Start bei fehlenden Paketen wieder an.

Alle von dieser Version gestarteten Sitzungen des aktuellen Linux-Benutzers,
auch andere GE-Wine-GUI-Fenster und abgekoppelte markierte Kindprozesse:

```sh
ge-wine --kill-all
```

GE-Wine startet Backend-Aufrufe in eigenen Prozessgruppen und markiert deren
Umgebung mit einer Sitzungs-ID. Kontrollprozesse werden mit PID und Linux-
Startzeit registriert; veraltete PID-Eintraege duerfen keine anderen Prozesse
beenden. Es wird nicht pauschal nach `wine` oder `python` gesucht. Bereits vor
diesem Update gestartete unmarkierte Sitzungen sind nicht automatisch erfasst.
Die Prozessverwaltung ist mit echten Linux-Kindprozessen getestet; das Verhalten
abgekoppelter Prozesse innerhalb der UMU-Runtime muss auf dem Zielrechner
ueberprueft werden.

Beim ersten GUI-Start erstellt GE-Wine eine eigene Desktop-Verknuepfung.
Beim Start einer normalen Anwendung wird ein direkter Desktop-Link angelegt.
Nach einem erfolgreichen Installer-Lauf werden neue/geaenderte Windows-Links
(`.lnk`) aus Windows-Desktop und Startmenue als GE-Wine-Desktop-Links uebernommen.
Deinstallationslinks werden ausgeschlossen. Fuer Icarus Online wird der installierte
VFUNLauncher erkannt. Die Windows-Links starten ueber Wines `start.exe /unix`;
ihr Windows-Ziel und Arbeitsverzeichnis bleiben in der `.lnk` enthalten.

Jeder Link behaelt das aufgeloeste Profil, den Prefix, die
Spielkennung und die gewaehlten Diagnose-/Grafikoptionen. Ab 0.6.1 folgen neue
Links der Proton-Profilvorgabe. Eine ausdruecklich ausgewaehlte Proton-Version
wird weiterhin fest gespeichert. Der Zielpfad wird
absolut gespeichert. Desktop-Exec-Argumente werden ohne Shell geschrieben und
fuer Leerzeichen, Prozentzeichen, Anfuehrungszeichen und andere Sonderzeichen
maskiert. Ein erneuter Start aktualisiert denselben GE-Wine-Link.

Installiert ein Programm ohne Windows-Verknuepfungen, kann GE-Wine dessen
Startprogramm nicht immer automatisch bestimmen. Dann die installierte EXE
in der GUI auswaehlen und **Desktop-Verknuepfung erstellen** anklicken, oder:

```sh
ge-wine --create-shortcut --profile icarus-online \
  --prefix "$HOME/.local/share/ge-wine/prefixes/icarus-vfun" \
  '/pfad/zur/installierten/Anwendung.exe'
```

`--create-shortcut` erstellt nur den Link und startet weder Programm noch
Laufzeit-Einrichtung. Die Verknuepfungen verwenden den Desktop aus
`~/.config/user-dirs.dirs` bzw. `XDG_CONFIG_HOME`, zum Beispiel `Schreibtisch`.
Ist der Desktop dort deaktiviert, werden keine Dateien im Home-Verzeichnis
abgelegt. Neue Links sind ausfuehrbar; wenn verfuegbar wird `gio` fuer die
Vertrauensmarkierung verwendet. Abhaengig vom Desktop kann beim ersten Start
noch eine Bestaetigung erforderlich sein. Das Debian-Paket schreibt bei der
Installation als root nicht auf den Desktop eines geratenen Benutzers.

## Getrennte Proton-Versionen ab 0.6.1

- Icarus Phoenix: GE-Proton10-34, inklusive SHA512-geprueftem Download.
- Icarus Online / VFUN: GE-Proton11-11-x86_64.
- Die GUI passt die vorgeschlagene Version beim Profilwechsel automatisch an.
- `--proton` oder eine manuell gewaehlte Version haben weiterhin Vorrang.
- Neue Desktop-Links ohne explizite Versionswahl folgen der Profilvorgabe.

Alte Phoenix-Links aus 0.5.0/0.6.0 enthalten moeglicherweise einen festen
GE11-Pfad. In der neuen GUI Phoenix auswaehlen und den Link mit
**Desktop-Verknuepfung erstellen** erneuern. Es gibt keinen automatischen
Prefix-Wechsel und keine neue Prefix-Kopie. Wurde ein Phoenix-Prefix bereits
mit GE11 verwendet, ist die Kompatibilitaet beim Wechsel zurueck auf GE10
auf dem Zielrechner zu pruefen; es wird kein Wiederherstellungserfolg behauptet.

## GE-Memory: nativer Linux-Speicherinspektor ab 0.7.0

Das Paket enthaelt eine eigene Python/Tk-Anwendung mit Menueeintrag **GE-Memory**:

```sh
ge-memory
ge-memory --list
ge-memory --pid 12345
```

GE-Memory ist eine unabhaengige erste Implementierung mit grundlegenden
Speichereditor-Funktionen. Es ist kein vollstaendiger Cheat-Engine-Fork und
benoetigt keine Windows-Cheat-Engine-EXE.

- Eigene Prozesse auswaehlen; Filter fuer GE-Wine/Wine anhand der Prozessumgebung.
- Exakte Zahlensuche: Int8/16/32/64, UInt8/16/32/64, Float32/64 und Bool; little-endian.
- Standardmaessig automatische Suche ueber alle darstellbaren Zahlentypen.
- Folgesuche: gleich, geaendert, unveraendert, gestiegen oder gesunken.
- Ausgewaehlte Werte typgerecht schreiben, zuruecklesen und maximal 64 Treffer einfrieren (alle 300 ms).
- 2000 Treffer nach Typen verteilt anzeigen; standardmaessig maximal 100000 Treffer je Typ.
- Suche abbrechen; Standardlimit 0 = alle geeigneten Speicherbereiche, optional begrenzbar bis 65536 MiB.

Standardmaessig werden les-/schreibbare private Speicherbereiche und natuerlich
ausgerichtete Werte durchsucht. Gemeinsamer RAM und ungerichtete Suche sind optional.
Geraetemappings unter /dev werden uebersprungen; /dev/shm ist fuer gemeinsamen RAM zugelassen. Das Limit zaehlt auch uebersprungene Bereiche;
kein Treffer bedeutet bei begrenzter Suche nicht, dass der Wert nirgendwo existiert.
Float-Suche vergleicht exakte gepackte Werte, ohne Toleranzintervall. Die Tabelle zeigt den aktuellen Wert, den Wert der letzten Suche, den Erstwert
und den Typ getrennt. Live-Anzeige veraendert die Vergleichsbasis der Folgesuche nicht.
Eine neue Erstsuche oder Prozessauswahl entfernt bisherige Freeze-Eintraege.
PID und Prozessstartzeit werden beim Zugriff geprueft, damit eine wiederverwendete
PID nicht versehentlich als derselbe Prozess behandelt wird.

### Gemeinsam mit GE-Wine starten

1. GE-Memory oeffnen und **GE-Wine starten** anklicken.
2. In diesem GE-Wine-Fenster die Anwendung starten.
3. Prozessliste in GE-Memory aktualisieren und den eigentlichen Spielprozess
   waehlen; bei Icarus kann dieser ebenfalls `Launcher.exe` heissen.
4. Datentyp und sichtbaren Wert eingeben, Erstsuche ausfuehren und durch weitere
   Suchlaeufe eingrenzen. Wert aendern bzw. einfrieren nur an bekannten Treffern.

Linux entscheidet ueber den Zugriff auf `/proc/<pid>/mem`. GE-Memory verwendet
keine automatische Root-Ausfuehrung und aendert weder Yama noch andere
System-Schutzregeln. Beim ueblichen `ptrace_scope=1` hilft der Start als
Kindprozess des Inspektors. Bereits ausserhalb dieses Baums laufende Wine-Server,
strengere Systemregeln und geschuetzte Prozesse koennen weiterhin den Zugriff
verhindern. In diesem Fall wird ein Zugriffsfehler angezeigt, keine scheinbar
erfolgreiche leere Suche. Speicherfehler koennen die Zielanwendung zum Absturz
bringen; beim Beenden des Zielprozesses wird Einfrieren gestoppt.

`--random-name` setzt optional einen kosmetischen Linux-Prozessnamen. Es ist
keine Tarnung: Prozessargumente, GUI-Titel und Speicherzugriffe bleiben sichtbar.
Es gibt keine Zusage fuer GameGuard-/Anti-Cheat-Kompatibilitaet. Kernel-Debugger,
Assembler, Pointer-Scanner und Cheat-Engine-Tabellen werden in dieser ersten
Version nicht implementiert.

Die Tests pruefen Lesen, Schreiben, Suchgrenzen, Folgesuche, PID-Identitaet und
GUI-Suche samt Freeze/Freigabe an echten selbst gestarteten Linux-Testprozessen.
In der Cloud ist der Schreibtest ausserhalb der schreibgeschuetzten Sandbox
noetig; der Zugriff bleibt auf diese Testprozesse beschraenkt. Ein echter
Proton-/Icarus-Speichertest ist hier weiterhin nicht moeglich und steht auf dem
Zielrechner aus. Phoenix bleibt auf GE10-34, Online auf GE11-11.

## Live-Werte, Datentypen und Spielerkennung ab 0.8.0

**Automatisch** durchsucht in einem Durchlauf alle numerischen Darstellungen,
die den eingegebenen Wert repraesentieren koennen. Werte ausserhalb des jeweiligen
Typbereichs werden fuer diesen Typ uebersprungen. Text, Bytefolgen und Pointer-
Scanner sind damit nicht implementiert. Ein Rohspeicher enthaelt keine verlaessliche
Information ueber den urspruenglichen C/C++-Datentyp; dieselbe Adresse kann deshalb
mehrfach mit verschiedenen passenden Typen auftauchen. Die gewaehlte Trefferzeile
bestimmt beim Schreiben exakt Typ und Bytebreite, unabhaengig vom aktuellen
Suchtyp-Feld. Bool erlaubt nur 0/1 oder true/false. Ueberlaeufe und widerspruechliche
ueberlappende Schreibziele werden vor dem ersten Schreibzugriff abgelehnt.

Das Trefferlimit ist pro Typ getrennt, damit haeufige Byte-Werte keine Float-
oder Int64-Treffer verdraengen. **Treffer/Typ** ist zwischen 100 und 1000000
einstellbar. Hohe Grenzen benoetigen entsprechend viel RAM. Das Speicherlimit
0 umfasst alle geeigneten virtuellen RAM-Bereiche, nicht nur residenten RSS.
Trefferlimits, uebersprungene Bereiche und Teilscans werden im Status angezeigt.
Folgesuche und Live-Ansicht buendeln benachbarte Werte in seitenweisen Lesezugriffen.

Die Live-Anzeige aktualisiert den sichtbaren Tabellenbereich und ausgewaehlte
Treffer alle 300 ms. Aenderungen werden kurz farbig markiert; das Feld **Neuer Wert**
wird dabei nicht laufend ueberschrieben. Folgesuchen vergleichen gegen den letzten
Suchlauf. Erstwerte bleiben erhalten. Einfrieren schreibt ebenfalls mit dem
Treffertyp; nach einer Folgesuche weggefilterte Freeze-Treffer werden freigegeben.

Nach **Schreiben** wird der Prozessspeicher sofort zurueckgelesen. Stimmt er schon
nicht mehr mit dem Zielwert ueberein, meldet die GUI dies. Eine Anwendung kann
Werte spaeter neu berechnen oder aus Serverdaten wiederherstellen. Erfolgreiches
Ruecklesen belegt den RAM-Wert zu diesem Zeitpunkt, nicht eine serverseitige
Aenderung oder die Wahl des richtigen logischen Spielwerts. Mehrere getrennte
Schreibadressen bilden keine atomare Transaktion.

**Spiel erkennen (>1 GiB)** beruecksichtigt Hauptprozesse des aktuellen Benutzers
mit GE-Wine-/Wine-Zuordnung und mehr als 1 GiB RSS. Threads werden nicht einzeln
behandelt; VFUN-/Icarus-Patcher, GameGuard und bekannte Runtime-Helfer werden
als Kandidaten ausgeschlossen. Ein eindeutiger Kandidat wird vorausgewaehlt,
ein einzelner CryGame.dll-Kandidat hat Vorrang. Bei mehreren unklaren Kandidaten
ist die Auswahl manuell. Bereits gewaehlte Zielprozesse werden nicht allein
wegen eines anderen RAM-Verbrauchs gewechselt. **Hauptprozess**, RSS und
Kandidaten-Markierung erscheinen in der Prozessauswahl. RAM-Verbrauch allein
beweist keine Spieleigenschaft und die Auswahl gibt keine zusaetzlichen Zugriffsrechte.


## GE-Memory ab 0.9.0

Die dunkle Oberfläche führt von der Prozessauswahl über die Suche zur Bearbeitung.
Erstsuche sucht einen exakten Wert; der Vergleich gilt nur für die Folgesuche.
Nicht verfügbare Aktionen sind deaktiviert. Automatisch berücksichtigt alle elf Datentypen.

Die Trefferliste lässt sich nach Typ filtern und mit 500 Treffern je Seite vollständig
durchblättern. Aktuelle Werte werden live gelesen, Änderungen kurz gelb markiert.
„Live-Werte“ pausiert nur die Anzeige; eingefrorene Werte bleiben fixiert.
Letzte Suche und Erstwert bleiben als Vergleich erhalten. Einfrieren schreibt den
eingegebenen Wert regelmäßig, Freigeben beendet dies für die Auswahl.

Schreiben verwendet den ursprünglichen Datentyp und liest das Ergebnis zurück.
Strg+C kopiert ausgewählte Adressen, Strg+F fokussiert den Suchwert, Strg+R
aktualisiert Prozesse, Eingabe startet Erst- oder Folgesuche, Escape bricht eine Suche ab.
Beim Prozesswechsel werden Treffer und Fixierungen verworfen.


## Automatische Prefix-Aufzeichnung ab 0.10.0

Jeder Start über CLI, GUI oder eine GE-Wine-Verknüpfung erzeugt einen eigenen Ordner:
`<Prefix>/logs/JJJJ-MM-TT_HH-MM-SS_Mikrosekunden_Sitzung/`. Der genaue Pfad steht
auch in der Konsole bzw. im GUI-Protokoll. Bereits beim Einrichten der Laufzeiten
werden Ausgaben gesammelt. Wiederholte und gleichzeitige Starts überschreiben sich nicht.

- `session.json`: Profil, Proton-Pfad, Startaufruf, Arbeitsordner, ausgewählte
  Diagnosevariablen, Start-/Endzeit, Rückgabecode und noch beobachtete Prozesse.
- `console.log`: zeitgestempelte GE-Wine-Meldungen, stdout und stderr. Die
  Konsolenausgabe bleibt gleichzeitig sichtbar.
- `events.jsonl`: direkte Aufrufe mit Start/Ende und Rückgabecode sowie alle 250 ms
  beobachtete Prozesse mit PID, Startkennung, Elternprozess, Namen, ausführbarer
  Datei und Befehlszeile. Prozesse mit derselben Prefix-Variable werden als
  `shared-prefix` gekennzeichnet, weil sie auch zu einer anderen Sitzung gehören können.
- `calls/0001/`, `calls/0002/`, …: Proton- und DXVK-Dateien je Backend-Aufruf,
  damit z. B. Winetricks und der anschließende Spielstart sich nicht überschreiben.
  Die Dateien werden direkt von der gewählten Laufzeit erzeugt. Wine erhält
  zusätzlich Prozess-, Thread-, DLL-, Modul- und Exception-Diagnosekanäle.

Die Aufzeichnung läuft bis zum Ende des gestarteten Backends und seiner offenen
Ausgabekanäle. Dauerhaft verbleibende Hintergrundprozesse stehen im Abschlussbericht.
Sehr kurze Linux-Prozesse können zwischen zwei Stichproben fehlen; Wine-Prozesslogs
und direkt protokollierte Aufrufe ergänzen diese Stichproben. Abbrüche durch
Stromausfall oder SIGKILL/SIGTERM können den Abschlussbericht verhindern.
Das ist keine vollständige Kernel-/Systemaufruf-Aufzeichnung.

Zusätzlich für Wine-API-Aufrufe:

```bash
ge-wine --full-trace --prefix "$HOME/.local/share/ge-wine/prefixes/icarus-vfun" /pfad/VLauncher.exe
```

Alternativ in der GUI „API-Aufrufe aufzeichnen“ aktivieren. Dies ergänzt die
Wine-Kanäle `relay` und `snoop`; deren Abdeckung hängt von Wine und der Anwendung
ab. Es werden damit keine beliebigen nativen Linux-Funktionsaufrufe aufgezeichnet.
Die Option kann sehr stark bremsen und enorme Logdateien erzeugen. Es gibt keine
automatische Löschung oder Größenbegrenzung. `--diagnostics` schaltet weiterhin
zusätzlich das GPU-/FPS-HUD ein; Dateilogs werden auch ohne diesen Schalter erstellt.

Die Aufzeichnungsordner werden mit 0700, die GE-Wine-eigenen Dateien mit 0600
angelegt. Das gesamte Prozess-Environment wird nicht gespeichert. Befehlszeilen,
Programm-Ausgaben und API-Traces können trotzdem Anmeldedaten enthalten; vor dem
Weitergeben prüfen. Externe Proton-/DXVK-Dateien verwenden deren eigene Dateirechte
innerhalb des geschützten Ordners.

## Präzisere RAM-Suche ab 0.10.0

Unter „Suchoptionen“ lässt sich eine absolute Float-Toleranz einstellen, z. B.
`0,1`, wenn die Anzeige gerundet `17,3` zeigt, im RAM aber `17,25` steht. Sie gilt
für Float32/Float64 in Erst- und Folgesuche; die tolerante Erstsuche ist langsamer.
`0` bleibt die schnelle exakte Suche. Die Treffer speichern stets die tatsächlich
gelesenen Bytes, einschließlich Erstwert und Vergleichswert.

Folgesuchen unterstützen zusätzlich „Nicht gleich“, „Groesser als“, „Kleiner als“,
„Zwischen“ (einschließlich Grenzen) sowie „Um Wert gestiegen/gesunken“. Bei „Zwischen“
ist der Suchwert die Untergrenze; die Obergrenze erscheint gesondert. Toleranz gilt
auch für Float-Vergleiche und Änderungen. Integer werden weiterhin exakt behandelt.
Die Vergleiche beziehen sich auf die letzte Suche, nicht auf die laufende Anzeige.
Dies grenzt numerische Kandidaten ein; daraus lässt sich keine verlässliche Bedeutung
einer Spielvariable oder ein serverseitiger Wert ableiten.


## Speedhack ab 0.11.0 (experimentell)

1. Die Anwendung neu über `ge-wine --speed-control /pfad/Programm.exe` starten.
   Alternativ in der GE-Wine-GUI „Speedhack-Steuerung für GE-Memory“ aktivieren.
   Der Button „GE-Wine starten/öffnen“ in GE-Memory aktiviert diese Vorbereitung.
2. In GE-Memory den tatsächlichen Spielprozess auswählen. Die Speedhack-Steuerung
   steht direkt unter der Prozessauswahl; „Speedhack …“ öffnet zusätzlich die Details.
3. Faktor eingeben und „Speedhack aktiv“ einschalten oder „Faktor anwenden“ wählen.
   Ausschalten setzt den Prozess auf 1× zurück und behält den gewählten Faktor für
   erneutes Aktivieren. Änderungen des Faktors mit „Faktor anwenden“ bestätigen. Schnellwahl: 0,5× / 1× / 2×; erlaubt sind
   0,05× bis 20×. „Zurück auf 1×“ stellt die normale Zeitrate wieder her.
4. Die Anzeige unterscheidet angeforderte und von der Zeitbibliothek bestätigte
   Faktoren. Eine Bestätigung bedeutet, dass abgefangene Zeitabfragen skaliert werden;
   die Wirkung auf das konkrete Spiel muss separat geprüft werden.

Alle vorbereiteten Prozesse starten zunächst bei 1×. Nur die ausgewählte PID wird
geändert. Beim regulären Schließen von GE-Memory werden die von diesem Fenster
geänderten Faktoren auf 1× angefordert. Beim Ende des GE-Wine-Starts werden noch
lebende vorbereitete Prozesse ebenfalls auf 1× zurückgesetzt und die Steuerdateien
entfernt. Ein harter Abbruch von GE-Memory kann den Faktor bis zum manuellen Reset
oder dem Ende der GE-Wine-Sitzung aktiv lassen.

CLI-Steuerung (PID ersetzen):

```bash
ge-memory --pid 12345 --speed 2
ge-memory --pid 12345 --speed-status
ge-memory --pid 12345 --speed 1
```

Die CLI meldet den aktuellen Zustand als JSON; eine noch ausstehende Übernahme
ist `pending: true`. CLI-Anforderungen bleiben über das Ende des CLI-Befehls hinaus
aktiv, bis sie zurückgesetzt werden oder der GE-Wine-Start endet.

Technik und Abdeckung: Eine beim Start sichtbare `LD_PRELOAD`-Bibliothek für 64- und
32-Bit-Linux skaliert `clock_gettime(CLOCK_MONOTONIC/CLOCK_MONOTONIC_RAW)` pro Prozess.
Sie erhält den akkumulierten virtuellen Zeitwert bei Faktorwechseln, statt die
Systemlaufzeit mit dem neuen Faktor zu multiplizieren. Systemuhr, Kalenderzeit und
andere Prozesse bleiben unverändert. Die Steuerdateien sind benutzerprivat und an
PID plus Linux-Prozessstartkennung gebunden. Host- und Runtime-PIDs werden über
`NSpid` zugeordnet, falls die Runtime einen eigenen PID-Namensraum verwendet.
Es gibt keine nachträgliche Injektion,
Tarnung oder Änderung von ptrace-/Kernel-Schutzregeln.

Wine kann seine Performance-Counter über diese Uhren abfragen. Direkte CPU-Zähler,
Wine-GetTickCount aus gemeinsamem Serverspeicher, eigene Engine-Timer, Warteaufrufe
und serverseitige Spielzeit werden dadurch nicht vollständig skaliert. Gemischte
Zeitquellen können die Kompatibilität beeinträchtigen. Statische Programme sowie
Umgebungen, die LD_PRELOAD entfernen (z. B. bestimmte UMU-Gamescope-Sitzungen),
können nicht vorbereitet sein; GE-Memory zeigt dann die fehlende Bibliothek an.
Diese Implementierung ist kein garantierter Speedhack für jedes Wine-Spiel.
Der Icarus-/UMU-Echtbetrieb ist in dieser Cloud-Umgebung nicht getestet.

GE-Wine protokolliert die Vorbereitung in `events.jsonl` und archiviert
Faktoranforderungen in `speed-commands.jsonl` im datierten Startordner. Während
des Starts liegen Bibliotheken und Steuerdateien in einem privaten `/tmp`-Ordner,
der an die Steam-Runtime weitergereicht und bei regulärem Ende entfernt wird.

Für den Quellbau werden zusätzlich `gcc-multilib` und `libc6-dev-i386` benötigt.
`bash standalone/build_speed.sh /pfad/ausgabe` erzeugt beide Architekturen; der
Paketbau führt dies automatisch aus. Für Entwicklung ohne installierte Anwendung
lässt sich `GE_WINE_SPEED_LIBRARY_DIR=/pfad/ausgabe` setzen. Die Bibliotheksquellen
und das Buildskript liegen auch unter `/usr/share/doc/ge-wine/`.


## Duplikatfreie Logs ab 0.12.0

GE-Wine behält pro Datei nur das erste Auftreten einer Meldung, auch wenn sie
zwischen anderen Meldungen immer wieder auftaucht. Für den Vergleich werden
ANSI-Farbcodes und bekannte vorangestellte Zeitstempel ignoriert (Wine-Laufzeit
und ISO-Zeitstempel in eckigen Klammern). Bei erkannten Wine-Meldungen werden
auch die vorangestellten Prozess- und Thread-IDs ignoriert: derselbe Inhalt aus
mehreren Prozessen erscheint einmal. Schweregrad, Wine-Kanal, Adressen, Fehlercodes,
Parameter, Leerzeichen und unterschiedliche Ausgabekanäle bleiben unterscheidbar.
Prozess-IDs innerhalb des Meldungsinhalts oder strukturierter Ereignisse bleiben erhalten.
Die erste Originalzeile bleibt erhalten. Bei strukturierten GE-Wine-Ereignissen
wird nur das äußere `time`-Feld beim Vergleich ignoriert.

- `console.log` und `events.jsonl` werden bereits während der Aufzeichnung
  dedupliziert. Auch GUI und Terminal zeigen dieselbe Konsolenmeldung nur einmal.
- Proton-/DXVK-Dateien unter `calls/` werden beim Abschluss atomar kompaktiert.
  Noch offene Schreibzugriffe werden soweit über `/proc` erkennbar berücksichtigt;
  betroffene Dateien bleiben unverändert und stehen in `deduplication.pending`.
  Während des Spielbetriebs können externe Rohlogs deshalb zunächst weiter wachsen.
- `repetitions.jsonl` enthält je wiederholter Meldung genau einen
  Eintrag mit Quelldatei, Zeilennummer im kompakten Log, Gesamtanzahl sowie erstem
  und letztem Auftreten. Die Meldung wird darin nicht nochmals kopiert. Bei externen
  Dateien sind `first_seen`/`last_seen` Verarbeitungszeiten; originale Zeitpräfixe
  stehen in `first_runtime_timestamp`/`last_runtime_timestamp`, sofern vorhanden.
- `session.json` enthält unter `deduplication` Gesamtzahl, eindeutige Zeilen und
  unterdrückte Wiederholungen.
- `dedup.sqlite3` speichert Fingerabdrücke, Zähler und Dateistände auf der Platte.
  Ein auf 2048 Meldungen begrenzter Cache bündelt häufige Zähler-Updates.
  Dadurch wird keine mit jeder neuen Logzeile wachsende RAM-Liste benötigt.

Die Zwischenreihenfolge einzelner Wiederholungen wird zusammengefasst. Wer einen
Ablauf untersucht, kann weiterhin die ersten Originalmeldungen, unterschiedliche
Parameter sowie Häufigkeiten und Zeitspannen vergleichen. Nach harten Abbrüchen
können die letzten noch nicht gespeicherten Zähler fehlen.

Bereits vorhandene oder zunächst noch offene Dateien lassen sich später verarbeiten:

```bash
ge-wine --compact-logs "$HOME/.local/share/ge-wine/prefixes/icarus-vfun/logs/2026-11-07_12-00-00_123456_ab12cd34"
```

Den Beispielordner durch den tatsächlichen datierten Startordner ersetzen. Dieser
Befehl startet kein Spiel. Er verarbeitet unkomprimierte `.log`-Dateien und
`events.jsonl`, verändert keine Spiel-/Prefix-Dateien außerhalb dieses Logordners
und lässt Archive sowie Speedhack-Anforderungen unverändert. Aktive GE-Wine-
Aufzeichnungen werden abgelehnt. Gleichzeitige Kompaktierungen sind gesperrt.
Unveränderte, bereits kompaktierte Dateien werden nicht erneut gezählt. Eine
zwischenzeitlich ersetzte oder geänderte Datei wird als neue Dateigeneration
eingelesen; deren alte Wiederholungszähler werden dabei ersetzt.


## Laufende Wiederholungsübersicht ab 0.12.1

`repetitions.jsonl` wird bereits beim Start einer Aufzeichnung angelegt, zunächst
ggf. leer. Während der laufenden GE-Wine-Sitzung wird die Datei im 20-Sekunden-Takt
aktualisiert, auch wenn keine neuen Wiederholungen hinzukommen. Beim regulären
Abschluss erfolgt zusätzlich ein letzter Export. Der Speicherort bleibt der
datierte Startordner innerhalb von `<Prefix>/logs/`.

Jeder Export schreibt zunächst eine vollständige temporäre Datei, synchronisiert
sie und ersetzt dann atomar `repetitions.jsonl`. Ein SIGTERM/SIGKILL oder ein Absturz
des GE-Wine-Steuerprozesses lässt daher den letzten vollständig veröffentlichten
Zwischenstand zurück. Die seitdem neu aufgetretenen Meldungen sind darin noch
nicht enthalten. Nach dem Tod des Loggers erfolgen keine weiteren Aktualisierungen.

Auch Proton-/DXVK-Dateien werden während des Laufs inkrementell und ausschließlich
lesend ausgewertet. Bereits erfasste Zeilen werden nicht erneut gezählt; unvollständige
letzte Zeilen folgen erst nach ihrem Zeilenumbruch. Offene Rohdateien bleiben dabei
unverändert. Bei vielen bzw. sehr großen Dateien begrenzen 8 MiB und ungefähr zwei
Sekunden Lesezeit je Durchlauf die Zusatzlast; die Dateien kommen abwechselnd an
die Reihe. Dadurch können deren Live-Zähler nachlaufen. `live_indexing_pending`
zeigt noch nicht vollständig eingelesene Dateien, `live_indexing_errors` Lesefehler.
Beim Abschluss werden geschlossene Rohdateien weiterhin vollständig kompaktiert.

In `session.json` stehen unter `deduplication` jetzt auch `updated_at`,
`interval_seconds: 20` und der Zustand beim Export (`running`/`finished`).
Die tatsächliche Ausführung kann unter hoher System-/Datenträgerlast verzögert sein.
`line_reference: raw-live` in der Wiederholungsübersicht verweist auf die Zeile der
noch offenen Rohdatei. `line_reference: compact` verweist auf eine bereits
kompaktierte Datei, insbesondere die laufend gefilterte Konsole.


## macOS-Build ab 0.13.0

GitHub Actions baut zusätzlich eine native `GE-Wine.app` als DMG und ZIP für
Intel (`x86_64`) und Apple Silicon (`arm64`). Python/Tk und die Prozessbibliothek
sind enthalten. Wine für macOS wird beim ersten Start SHA256-geprüft nachgeladen.
Die Laufzeitquelle ist in `macos_runtime.json` festgelegt; die Archive von Linux-
GE-Proton werden auf dem Mac nicht verwendet.

Voraussetzungen und Unterschiede stehen in [README-macos.md](README-macos.md).
Die gewählte Intel-Wine-Laufzeit braucht auf Apple Silicon Rosetta 2 sowie unter
macOS GStreamer.framework über dessen regulären Installer. Die App ist nicht
notarisiert. Mac-Icarus-Prefixe sind getrennt; die Linux-GE10/GE11-Patches und
GE-Memory/Speedhack werden nicht als Mac-kompatibel ausgegeben. Die Linux-Ausgabe
behält ihre bisherigen Proton-Pins und Funktionen.


## Netzwerkverbindungen automatisch protokollieren

Neue Starts protokollieren standardmäßig die beobachteten TCP-/UDP-Sockets der
der Sitzung bzw. dem Prefix zugeordneten Prozesse. Die Datei liegt unter
`<Prefix>/logs/<Datum_Uhrzeit_Sitzung>/network.jsonl`. Jeder Eintrag enthält
Zeitpunkt, Prozess-ID/Startkennung, Prozessnamen, Protokoll, lokale und entfernte
IP/Port sowie den beobachteten Verbindungszustand. Neue Verbindungen, Änderungen
und nicht mehr beobachtete Sockets werden erfasst; unveränderte Zustände erzeugen
keine weiteren Zeilen. Wiederkehrende identische Ereignisse fließen in die
20-sekündlich aktualisierte `repetitions.jsonl` ein.

Die Abfrage erfolgt einmal pro Sekunde, unter Linux über die eigenen `/proc`-
Socket-Deskriptoren und deren Netzwerk-Namensraum, unter macOS über psutil.
Es werden keine fremden Host-Verbindungen allein wegen ihrer IP oder ihres Ports
einer Instanz zugeschlagen. Bereits im Prefix laufende Prozesse können mehreren
Aufzeichnungen desselben Prefixes zugeordnet sein. Zugriffsfehler werden als
`network_coverage_error` erfasst. Die Einstellung und Grenzen stehen in
`session.json` unter `network_logging`.

Dies ist Verbindungsdiagnose, kein vollständiger Paketmitschnitt: kurzlebige
Sockets und einzelne unverbundene UDP-Ziele können zwischen Abfragen fehlen.
Es werden keine Paketinhalte, DNS-Abfragen, HTTP-Inhalte oder Byte-Zähler
mitgeschnitten und keine TLS-Verbindungen entschlüsselt. Es sind keine
Administratorrechte erforderlich. Für vollständige PCAP-Aufzeichnungen wäre
ein zusätzlicher Capture-Mechanismus mit geeigneten Berechtigungen erforderlich.

Abschalten: GUI-Häkchen „Netzwerkverbindungen protokollieren“ entfernen oder
`ge-wine --no-network-log /pfad/Programm.exe` verwenden. Desktop-Verknüpfungen
übernehmen diese Einstellung. Andere Logdateien bleiben aktiv.
