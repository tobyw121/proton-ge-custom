GE-Wine für macOS (experimentell)
===============================

Installation
------------
macOS 13 oder neuer. Das passende Intel- oder Apple-Silicon-Paket herunterladen.
DMG öffnen und GE-Wine.app nach Programme ziehen. Alternativ die ZIP entpacken.
Python und Tk sind enthalten; Homebrew ist auf dem Benutzer-Mac nicht erforderlich.

Die Anwendung ist nur ad-hoc signiert, nicht mit einer Apple Developer ID signiert
oder notarisiert. macOS kann beim ersten Öffnen eine Freigabe für diese konkrete
Anwendung verlangen. Keine globalen Gatekeeper-Einstellungen deaktivieren.

Beim ersten Programmstart lädt GE-Wine die in macos_runtime.json festgelegte
native Wine-Laufzeit mit SHA256-Prüfung nach. Diese wird ausschließlich unter
~/Library/Application Support/GE-Wine/runtimes installiert. Wine wird nicht separat
über Homebrew benötigt. Updates der Laufzeit erfolgen über eine neue GE-Wine-Version.

Die gewählte Wine-Version benötigt GStreamer.framework für alle Benutzer.
Wenn es fehlt, lädt GE-Wine den offiziellen, SHA256-geprüften GStreamer-Installer
und öffnet in der GUI den normalen macOS-Installationsdialog. Dort die Installation
selbst bestätigen; anschließend „Programm starten“ erneut wählen. GE-Wine führt
keine stillen sudo-/Administrator-Installationen aus.

Auf Apple Silicon benötigt diese Intel-Wine-Laufzeit zusätzlich Rosetta 2 von Apple.
Ist es nicht verfügbar, zeigt GE-Wine eine entsprechende Meldung. Es wird keine
Rosetta-Lizenz automatisch akzeptiert. Die GE-Wine-Oberfläche selbst läuft nativ.

Bedienung
---------
EXE/MSI auswählen, optional einen eigenen Prefix oder eine vorhandene native
Wine.app angeben und starten. Für Installer und die installierte Anwendung
jeweils denselben Prefix wählen. MSI wird über msiexec ausgeführt.

Die CLI liegt unter:
/Applications/GE-Wine.app/Contents/MacOS/GE-Wine

Beispiele:
"/Applications/GE-Wine.app/Contents/MacOS/GE-Wine" --doctor
"/Applications/GE-Wine.app/Contents/MacOS/GE-Wine" --setup-runtime
"/Applications/GE-Wine.app/Contents/MacOS/GE-Wine" --prefix "$HOME/Library/Application Support/GE-Wine/prefixes/mein-spiel" /pfad/Spiel.exe
"/Applications/GE-Wine.app/Contents/MacOS/GE-Wine" --kill-all

Dateien und Logs
----------------
Standard-Prefixe: ~/Library/Application Support/GE-Wine/prefixes/
Logs: <Prefix>/logs/<Datum_Uhrzeit_Sitzung>/
Netzwerkverbindungen werden standardmäßig in network.jsonl erfasst (TCP/UDP,
Prozess, IP/Port, Zustand; Abfrage jede Sekunde, keine Pakete oder Nutzdaten).
macOS kann den Zugriff verweigern; dies wird als network_coverage_error vermerkt.
Abschalten über das GUI-Häkchen oder --no-network-log.
Die Wiederholungsübersicht wird sofort angelegt und alle 20 Sekunden aktualisiert.
„Sofort beenden“, profilbezogene Desktop-.app-Verknüpfungen und die Erkennung
neuer Windows-.lnk-Verknüpfungen sind vorhanden. Prozessdetails werden unter macOS
über native Prozessinformationen (psutil) statt Linux-/proc ermittelt. macOS-
Berechtigungen können die Sichtbarkeit einzelner Hintergrundprozesse begrenzen.

Grenzen und Tests
-----------------
Dies ist ein macOS-Frontend mit nativer Wine-Laufzeit. Die offiziellen GE-Proton-
Archive sind Linux-Binärdateien und werden nicht auf macOS heruntergeladen.
Die Icarus-Profile trennen Mac-Prefixe; die Linux-Pins GE10-34/GE11-11 und deren
Patches sowie die automatische VC++-/Shader-Compiler-Einrichtung gelten hier nicht.
Windows-Laufzeiten und Grafikkompatibilität müssen unter macOS separat geprüft
werden. WineD3D/OpenGL bzw. die gewählte native Wine-Installation bestimmen den
Renderer; Linux-DXVK-/Vulkan-Funktionen werden nicht vorgetäuscht.
GE-Memory und der Linux-Speedhack sind nicht Bestandteil des Mac-Pakets.

GitHub Actions baut DMG/ZIP für Intel und Apple Silicon. Auf Intel führt der Build
zusätzlich eine minimale Windows-x64-Konsolen-EXE durch Wine aus (ExitProcess(42));
Der Konsolentest deaktiviert den Mac-Grafiktreiber auf dem CI-Runner.
Grafik und Multimedia/GStreamer werden davon nicht als getestet ausgegeben. Auf ARM
wird das native Frontend gebaut/geprüft; ein Spiele-/Rosetta-Lauf wird damit nicht
behauptet. Icarus und dessen Anti-Cheat werden durch diesen Build nicht getestet.
