# Icarus-VFUN-Diagnose unter Windows

`Collect-IcarusWindows.ps1` ist fuer Windows 10/11 mit Windows PowerShell 5.1
oder neuer gedacht. VFUN und Icarus muessen bereits installiert sein. Erst einen
Spielstart ohne Aufnahme testen, danach alle VFUN-/Icarus-Fenster schliessen.
Process Monitor kann das Verhalten von Anti-Cheat-Software beeinflussen; eine
abweichende Reaktion beim Mitschnitt ist als solche zu dokumentieren.

In PowerShell im Download-Verzeichnis starten:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Collect-IcarusWindows.ps1
```

Das Skript fordert per UAC Administratorrechte an. `Bypass` gilt nur fuer diesen
PowerShell-Prozess; die systemweite Ausfuehrungsrichtlinie wird nicht geaendert.
Der Launcher wird ebenfalls erhoeht gestartet; die Windows-Referenz ist deshalb
als Administrator-Lauf gekennzeichnet.

Es sucht den installierten `VFUNLauncher.exe` in gaengigen Verzeichnissen und
oeffnet sonst eine Dateiauswahl. Alternativ:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Collect-IcarusWindows.ps1 -VfunPath 'D:\VFUN\VLauncher\VFUNLauncher.exe' -ManualStop
```

## Ablauf

- Download von https://download.sysinternals.com/files/ProcessMonitor.zip.
- Pruefung der gueltigen Microsoft-Authenticode-Signatur vor Ausfuehrung.
- `/AcceptEula` akzeptiert die [Sysinternals-Lizenz](https://learn.microsoft.com/sysinternals/license-terms).
- CSV-Sicherung von Treibern, Diensten und Prozessen vor der Aufnahme.
- Ungefilterte Procmon-Aufnahme in eine PML-Datei, danach Start von VFUN.
- Im Launcher anmelden und selbst auf Spielen klicken. Anmeldung wird nicht automatisiert.
- Aufnahmeende ueber einen der beiden Buttons, die optionale Erkennung oder das Zeitlimit.
- CSV-Sicherung danach, Treibervergleich, System-/Anwendungsereignisse und Lauf-Metadaten.
- Procmon exportiert die Aufnahme als CSV; das Skript komprimiert sie zu `.csv.gz`.

Die Automatik prueft alle zwei Sekunden `Launcher`/`Launcher32`: Ein Prozess muss
ein Hauptfenster und die geladene `CryGame.dll` durchgehend mindestens 60 Sekunden
haben. **Das bestaetigt keine fertig geladene Spielwelt**; ein Menue oder Ladebild
kann diese Bedingung ebenfalls erfuellen. Der Stoppgrund dokumentiert diese
Unsicherheit. Mit `-ManualStop` oder dem Haken im Diagnosefenster abschalten.
Bei verweigertem Modulzugriff die Buttons benutzen. Bei GameGuard-Fehler 114 den
Fehler-Button druecken. Das normale Zeitlimit betraegt zehn Minuten; es laesst
sich mit `-MaximumMinutes 20` anpassen. `-StableSeconds 90` aendert die Haltezeit.
Vorher bestaetigen, dass genug freier Speicher vorhanden ist: PML und zeitweiliger
CSV-Export koennen zusammen mehrere GiB belegen.

Das Skript beendet seine Procmon-Aufnahme auch bei Skriptfehlern ueber `finally`.
Bei hartem Abbruch des PowerShell-Prozesses oder Stromausfall ist das nicht
moeglich. Laufendes fremdes Procmon wird erkannt; das Skript startet dann nicht.
Es beendet keine Spielprozesse und veraendert keine Spiel-/GameGuard-Dateien.

## Ergebnisse

Standard: Desktop\Icarus-Diagnose\DATUM-UHRZEIT-ZUFALL\

Zum Teilen: `aufnahme.csv.gz`, `lauf.json`, `treiber-*.csv`, `dienste-*.csv`,
`prozesse-*.csv`, `ereignisse-*.csv` und `skript.log`.
`aufnahme.pml` lokal behalten; heruntergeladene Programme unter `tools` nicht
mit hochladen. Die Aufnahme ist systemweit und kann private Pfade und
Anmeldetokens in Prozessargumenten enthalten. Vor Weitergabe pruefen; Original
fuer die lokale Analyse behalten. Process Monitor enthaelt keine vollstaendige
Aufzeichnung interner GameGuard-Funktionsaufrufe.

Syntaxpruefung erfolgt mit PowerShell; die interaktive UAC-/Procmon-/VFUN-Funktion
muss auf dem Windows-Zielrechner getestet werden. Ein erfolgreicher Windows-Trace
garantiert keine GameGuard-Kompatibilitaet unter Proton.
