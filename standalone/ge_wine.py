#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A desktop and command-line frontend for GE-Proton through UMU."""

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import queue
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import threading
from urllib.request import urlopen

from ge_wine_sessions import Session, LaunchCancelled, stop_all
from ge_wine_desktop import (windows_links, installer, installed_shortcuts,
                             program_shortcut, write_shortcut)


VERSION = "0.6.1"
PINNED_PROTON = "GE-Proton11-11-x86_64"
PROTON_URL = ("https://github.com/GloriousEggroll/proton-ge-custom/releases/download/"
              "GE-Proton11-11/GE-Proton11-11-x86_64.tar.gz")
PHOENIX_PROTON = "GE-Proton10-34"
PROTON_URLS = {
    PINNED_PROTON: PROTON_URL,
    PHOENIX_PROTON: "https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton10-34/GE-Proton10-34.tar.gz",
}


def profile_proton(profile):
    return PHOENIX_PROTON if profile == "icarus-phoenix" else PINNED_PROTON


PROFILES = {"auto": "Automatisch erkennen", "standard": "Standard",
            "icarus-phoenix": "Icarus Phoenix – RTX 2070 / 3060",
            "icarus-online": "Icarus Online – VFUN"}
ICARUS_PROFILES = {"icarus-phoenix", "icarus-online"}
ICARUS_VERBS = ("vcrun2008", "vcrun2010", "vcrun2012", "vcrun2013", "vcrun2015",
                "d3dcompiler_43")


def pinned_proton_directory(version=PINNED_PROTON):
    return Path.home() / ".local/share/Steam/compatibilitytools.d" / version


def icarus_prefix(profile="icarus-phoenix"):
    name = "icarus-vfun" if profile == "icarus-online" else "icarus-ge10-34-clean"
    return Path.home() / ".local/share/ge-wine/prefixes" / name


def ensure_proton(environment, report, cancelled=None):
    if cancelled and cancelled.is_set():
        raise LaunchCancelled()
    version = next((name for name in PROTON_URLS
                    if environment["PROTONPATH"] == str(pinned_proton_directory(name))), None)
    if version is None:
        return
    target = pinned_proton_directory(version)
    url = PROTON_URLS[version]
    target.parent.mkdir(parents=True, exist_ok=True)
    with (target.parent / ".ge-wine-proton-download.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "proton").is_file():
            report(f"Verwende {version}.\n")
            return
        if target.exists():
            raise ValueError(f"Unvollständiger Proton-Ordner: {target}. Bitte einen vollständigen Build auswählen.")
        report(f"Lade {version} herunter und prüfe SHA512 …\n")
        with urlopen(url.removesuffix(".tar.gz") + ".sha512sum", timeout=60) as response:
            checksum = response.read(32769)
        if len(checksum) > 32768:
            raise ValueError("Ungültige Proton-Prüfsummendatei.")
        expected = None
        for line in checksum.decode("ascii").splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1].lstrip("*") == version + ".tar.gz":
                expected = fields[0].lower()
        if not expected or not re.fullmatch(r"[0-9a-f]{128}", expected):
            raise ValueError("Keine passende SHA512-Prüfsumme für das Proton-Archiv gefunden.")
        with tempfile.TemporaryDirectory(prefix=".ge-wine-download-", dir=target.parent) as temporary:
            archive = Path(temporary) / "proton.tar.gz"
            digest = hashlib.sha512()
            size = 0
            with urlopen(url, timeout=60) as response, archive.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    if cancelled and cancelled.is_set():
                        raise LaunchCancelled()
                    size += len(chunk)
                    if size > 2 * 1024**3:
                        raise ValueError("Proton-Download ist unerwartet groß.")
                    digest.update(chunk)
                    output.write(chunk)
                    if size % (64 * 1024**2) == 0:
                        report(f"Proton-Download: {size // 1024**2} MiB …\n")
            if digest.hexdigest() != expected:
                raise ValueError("SHA512-Prüfung fehlgeschlagen; Proton wird nicht installiert.")
            report("SHA512 stimmt. Entpacke Proton …\n")
            extracted = Path(temporary) / "extracted"
            extracted.mkdir()
            with tarfile.open(archive, "r:gz") as source:
                for member in source.getmembers():
                    if not Path(member.name).parts or Path(member.name).parts[0] != version:
                        raise ValueError("Unerwarteter Pfad im Proton-Archiv.")
                for member in source.getmembers():
                    if cancelled and cancelled.is_set():
                        raise LaunchCancelled()
                    source.extract(member, extracted, filter="data")
            if not (extracted / version / "proton").is_file():
                raise ValueError("Das geprüfte Archiv enthält kein Proton-Startprogramm.")
            (extracted / version).rename(target)
            report(f"{version} ist installiert.\n")


def selected_profile(executable, profile):
    if profile != "auto":
        return profile
    for directory in Path(executable).expanduser().resolve().parents:
        name = directory.name.casefold().replace("_", "").replace("-", "").replace(" ", "")
        if name == "icarusphoenix" and (directory / "Launcher.exe").is_file() and (directory / "Bin64").is_dir():
            return "icarus-phoenix"
        if name == "icarusonline" and (directory / "Bin64").is_dir():
            return "icarus-online"
        # VFUN's generic launcher is only identified when Icarus is installed nearby.
        if name == "vfun" and (directory / "Icarus_Online/Bin64").is_dir():
            return "icarus-online"
    return "standard"


def installed_verbs(prefix):
    try:
        return set((prefix / "winetricks.log").read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        return set()


def needs_prerequisites(executable, profile, enabled):
    return enabled and selected_profile(executable, profile) in ICARUS_PROFILES and not any(
        part.casefold() == "redist" for part in Path(executable).parts)


def install_prerequisites(command, environment, directory, run, report):
    """Use UMU's supported winetricks entry point and its success records."""
    prefix = Path(environment["WINEPREFIX"])
    lock_directory = prefix.parent / ".ge-wine-locks"
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(os.fsencode(prefix)).hexdigest() + ".lock"
    backend = command[:3] if command[1:3] == ["-m", "umu"] else command[:1]
    report("Prüfe Visual C++ 2008–2015 und Shader-Compiler 43 im Programmordner …\n")
    with (lock_directory / lock_name).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for verb in ICARUS_VERBS:
            installed = installed_verbs(prefix)
            if verb in installed:
                continue
            if verb == "vcrun2015" and installed.intersection({"vcrun2017", "vcrun2019", "vcrun2022"}):
                report("Eine neuere kompatible VC++-14-Laufzeit ist bereits eingerichtet.\n")
                continue
            report(f"Installiere {verb} über UMU/Winetricks …\n")
            # UMU automatically supplies -q to its bundled Winetricks.
            result = run([*backend, "winetricks", verb], environment, directory)
            if result != 0:
                report(f"Installation von {verb} fehlgeschlagen (Code {result}); Launcher nicht gestartet.\n")
                return result
            if verb not in installed_verbs(prefix):
                report(f"Installation von {verb} nicht bestätigt; Launcher nicht gestartet.\n")
                return 1
    report("VC++-Laufzeiten und Shader-Compiler 43 sind eingerichtet.\n")
    return 0


def prepare(executable, arguments, prefix=None, proton=None, gameid="umu-default",
            profile="auto", diagnostics=False, nvidia_offload=False, low_latency=False):
    if profile not in PROFILES:
        raise ValueError(f"Unbekanntes Profil: {profile}")
    executable = Path(executable).expanduser().resolve()
    if not executable.is_file():
        raise ValueError(f"Programmdatei nicht gefunden: {executable}")
    profile = selected_profile(executable, profile)
    if not gameid.strip():
        raise ValueError("Die Spielkennung darf nicht leer sein.")
    if not proton:
        proton = profile_proton(profile)
    version = next((name for name, url in PROTON_URLS.items() if proton in {name, url}), None)
    if version is not None:
        proton = str(pinned_proton_directory(version))
    elif proton != "GE-Proton":
        proton_path = Path(proton).expanduser().resolve()
        if not (proton_path / "proton").is_file():
            raise ValueError("Der GE-Proton-Ordner muss die Datei 'proton' enthalten.")
        proton = str(proton_path)
    if not prefix and profile in ICARUS_PROFILES:
        prefix = icarus_prefix(profile)
    if not prefix:
        identifier = hashlib.sha256(os.fsencode(executable.parent)).hexdigest()[:16]
        prefix = Path.home() / ".local/share/ge-wine/prefixes" / identifier
    prefix = Path(prefix).expanduser().resolve()
    if prefix.exists() and not prefix.is_dir():
        raise ValueError(f"Der Programmordner ist kein Verzeichnis: {prefix}")
    environment = dict(os.environ)
    environment.update(WINEPREFIX=str(prefix), PROTONPATH=proton, GAMEID=gameid,
                       STORE=environment.get("STORE", "none"))
    if profile in ICARUS_PROFILES:
        # Let GE-Proton select DXVK normally, instead of inheriting an OpenGL override.
        environment.pop("PROTON_USE_WINED3D", None)
    if nvidia_offload:
        environment.update(__NV_PRIME_RENDER_OFFLOAD="1", __GLX_VENDOR_LIBRARY_NAME="nvidia",
                           __VK_LAYER_NV_optimus="NVIDIA_only")
    if low_latency:
        previous = environment.get("DXVK_CONFIG", "").rstrip("; ")
        settings = "dxgi.maxFrameLatency = 1; d3d9.maxFrameLatency = 1"
        environment["DXVK_CONFIG"] = f"{previous}; {settings}" if previous else settings
    if diagnostics:
        environment["DXVK_HUD"] = "devinfo,api,fps,frametimes,compiler"
        environment["DXVK_LOG_PATH"] = str(prefix.parent / (prefix.name + "-logs"))
        # Proton otherwise defaults DXVK_LOG_LEVEL to 'none', even with a log path.
        environment["DXVK_LOG_LEVEL"] = "info"
        environment["PROTON_LOG"] = "1"
        environment["PROTON_LOG_DIR"] = environment["DXVK_LOG_PATH"]
    bundled = Path(__file__).resolve().parent / "backend" / "umu"
    if bundled.is_dir():
        # Invoke a private backend without changing the user's umu-run or wine commands.
        command = [sys.executable, "-m", "umu", str(executable), *arguments]
        environment["PYTHONPATH"] = str(bundled.parent)
    else:
        from shutil import which
        backend = which("umu-run")
        if not backend:
            raise ValueError("UMU fehlt. Installiere das vollständige GE-Wine-Paket.")
        command = [backend, str(executable), *arguments]
    return command, environment, str(executable.parent)


def report_shortcut(action, report):
    try:
        return action()
    except (OSError, ValueError) as error:
        report(f"Desktop-Verknüpfung konnte nicht erstellt werden: {error}\n")


def application_run(options, command, environment, directory, session, report, output=None):
    profile = selected_profile(options.executable, options.profile)
    before = windows_links(environment["WINEPREFIX"])
    ensure_proton(environment, report, session.cancelled)
    session.check()
    if needs_prerequisites(options.executable, options.profile, not options.no_prerequisites):
        result = install_prerequisites(command, environment, directory,
                                      lambda cmd, env, cwd: session.run(cmd, env, cwd, output), report)
        if result:
            return result
    session.check()
    if not installer(options.executable) and not any(
            part.casefold() in {"system32", "syswow64"} for part in Path(options.executable).parts):
        report_shortcut(lambda: program_shortcut(options, environment, options.executable, profile, report), report)
    result = session.run(command, environment, directory, output)
    if result == 0 and not session.cancelled.is_set():
        report_shortcut(lambda: installed_shortcuts(options, environment, options.executable,
                                                   profile, before, report), report)
    return result


def launch_cli(options):
    command, environment, directory = prepare(
        options.executable, options.arguments, options.prefix, options.proton, options.gameid,
        options.profile, options.diagnostics, options.nvidia_offload, options.low_latency)
    if options.diagnostics:
        Path(environment["DXVK_LOG_PATH"]).mkdir(parents=True, exist_ok=True)
    report = lambda text: print(text, end="", file=sys.stderr, flush=True)
    if options.create_shortcut:
        program_shortcut(options, environment, options.executable,
                         selected_profile(options.executable, options.profile), report)
        return 0
    session = Session()
    try:
        return application_run(options, command, environment, directory, session, report)
    except (LaunchCancelled, KeyboardInterrupt):
        session.stop()
        return 130
    finally:
        session.close()


def gui(options):
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
        root = tk.Tk()
    except (ImportError, RuntimeError) as error:
        raise ValueError(f"Grafische Oberfläche nicht verfügbar: {error}") from error
    except Exception as error:
        if error.__class__.__name__ != "TclError":
            raise
        raise ValueError("Keine grafische Sitzung verfügbar. Nutze ge-wine programm.exe.") from error

    root.title("GE-Wine")
    root.geometry("860x680")
    root.columnconfigure(1, weight=1)
    root.rowconfigure(10, weight=1)
    executable = tk.StringVar(value=options.executable or "")
    arguments = tk.StringVar(value=shlex.join(options.arguments))
    prefix = tk.StringVar(value=options.prefix or "")
    proton = tk.StringVar(value=options.proton or "")
    gameid = tk.StringVar(value=options.gameid)
    profile = tk.StringVar(value=PROFILES[options.profile])
    diagnostics = tk.BooleanVar(value=options.diagnostics)
    nvidia_offload = tk.BooleanVar(value=options.nvidia_offload)
    low_latency = tk.BooleanVar(value=options.low_latency)
    prerequisites = tk.BooleanVar(value=not options.no_prerequisites)

    suggested_prefix = [""]
    suggested_proton = [""]

    def refresh_prefix(*unused):
        key = next(key for key, label in PROFILES.items() if label == profile.get())
        key = selected_profile(executable.get(), key)
        suggestion = str(icarus_prefix(key)) if key in ICARUS_PROFILES else ""
        if not options.prefix and prefix.get() in {"", suggested_prefix[0]}:
            prefix.set(suggestion)
        suggested_prefix[0] = suggestion
        version = profile_proton(key)
        if not options.proton and proton.get() in {"", suggested_proton[0]}:
            proton.set(version)
        suggested_proton[0] = version

    executable.trace_add("write", refresh_prefix)
    profile.trace_add("write", refresh_prefix)
    refresh_prefix()
    events = queue.Queue()
    running = False
    session = Session()
    labels = ["Windows-Programm", "Argumente", "Programmordner (Prefix)",
              "GE-Proton-Version", "Spielkennung (optional)"]
    variables = [executable, arguments, prefix, proton, gameid]
    for row, (label, variable) in enumerate(zip(labels, variables)):
        ttk.Label(root, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        ttk.Entry(root, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
    ttk.Label(root, text="Spielprofil").grid(row=5, column=0, sticky="w", padx=12)
    ttk.Combobox(root, textvariable=profile, values=list(PROFILES.values()), state="readonly").grid(
        row=5, column=1, columnspan=2, sticky="ew", padx=(0, 12))
    settings = ttk.Frame(root)
    settings.grid(row=6, column=0, columnspan=3, sticky="w", padx=12, pady=8)
    ttk.Checkbutton(settings, text="Diagnose: GPU/FPS und Proton-/DXVK-Logdateien", variable=diagnostics).pack(anchor="w")
    ttk.Checkbutton(settings, text="NVIDIA-GPU erzwingen (Hybrid-Laptop)", variable=nvidia_offload).pack(anchor="w")
    ttk.Checkbutton(settings, text="Kürzere Render-Warteschlange (experimentell)", variable=low_latency).pack(anchor="w")
    ttk.Checkbutton(settings, text="Icarus: VC++ und Shader-Compiler automatisch einrichten", variable=prerequisites).pack(anchor="w")

    def select_file():
        selected = filedialog.askopenfilename(filetypes=[("Windows-Programme", "*.exe"),
                                                         ("Alle Dateien", "*")])
        if selected:
            executable.set(selected)

    def select_folder(variable):
        selected = filedialog.askdirectory()
        if selected:
            variable.set(selected)

    ttk.Button(root, text="Öffnen …", command=select_file).grid(row=0, column=2, padx=12)
    ttk.Button(root, text="Wählen …", command=lambda: select_folder(prefix)).grid(row=2, column=2)
    ttk.Button(root, text="Wählen …", command=lambda: select_folder(proton)).grid(row=3, column=2)
    ttk.Label(root, text="Phoenix: GE-Proton10-34; Online/VFUN: GE-Proton11-11.\n"
              "Phoenix: icarus-ge10-34-clean; Online/VFUN: icarus-vfun.\n"
              "Beide Profile: nativer Shader-Compiler 43; bisherige Prefixe beibehalten.").grid(
                  row=7, column=0, columnspan=3, sticky="w", padx=12, pady=8)
    status = tk.StringVar(value="Bereit. Beim ersten Start werden GE-Proton und die Laufzeit heruntergeladen.")
    ttk.Label(root, textvariable=status, wraplength=740).grid(
        row=9, column=0, columnspan=3, sticky="w", padx=12, pady=8)
    output = ScrolledText(root, height=14, state="disabled")
    output.grid(row=10, column=0, columnspan=3, sticky="nsew", padx=12, pady=8)

    def log(text):
        output.configure(state="normal")
        output.insert("end", text)
        output.see("end")
        output.configure(state="disabled")

    def snapshot():
        return argparse.Namespace(
            executable=executable.get(), arguments=shlex.split(arguments.get()),
            prefix=prefix.get(), proton=(None if not options.proton and proton.get() == suggested_proton[0]
                                       else proton.get()), gameid=gameid.get().strip() or "umu-default",
            profile=next(key for key, value in PROFILES.items() if value == profile.get()),
            diagnostics=diagnostics.get(), nvidia_offload=nvidia_offload.get(),
            low_latency=low_latency.get(), no_prerequisites=not prerequisites.get())

    def worker(command, environment, directory, launch_options):
        try:
            report = lambda text: events.put(("log", text))
            result = application_run(launch_options, command, environment, directory, session, report, report)
            events.put(("done", result))
        except LaunchCancelled:
            events.put(("done", 130))
        except (OSError, ValueError, tarfile.TarError) as error:
            events.put(("error", str(error)))

    def stop_now():
        session.stop()
        stop_button.configure(state="disabled")
        status.set("GE-Wine-Programm wird sofort beendet …")

    def create_desktop():
        try:
            chosen = snapshot()
            _, environment, _ = prepare(chosen.executable, chosen.arguments, chosen.prefix, chosen.proton,
                                         chosen.gameid, chosen.profile, chosen.diagnostics,
                                         chosen.nvidia_offload, chosen.low_latency)
            program_shortcut(chosen, environment, chosen.executable,
                             selected_profile(chosen.executable, chosen.profile), log)
        except (OSError, ValueError) as error:
            messagebox.showerror("Verknüpfung nicht möglich", str(error))

    def start():
        nonlocal running
        try:
            command, environment, directory = prepare(
                executable.get(), shlex.split(arguments.get()), prefix.get(),
                proton.get(), gameid.get().strip() or "umu-default",
                next(key for key, value in PROFILES.items() if value == profile.get()),
                diagnostics.get(), nvidia_offload.get(), low_latency.get())
            if diagnostics.get():
                Path(environment["DXVK_LOG_PATH"]).mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError) as error:
            messagebox.showerror("Start nicht möglich", str(error))
            return
        launch_options = snapshot()
        session.reset()
        running = True
        stop_button.configure(state="normal")
        button.configure(state="disabled")
        log(f"Programmordner: {environment['WINEPREFIX']}\n")
        profile_key = next(key for key, value in PROFILES.items() if value == profile.get())
        log(f"Profil: {PROFILES[selected_profile(executable.get(), profile_key)]}\n")
        if environment.get("DXVK_CONFIG"):
            log(f"DXVK_CONFIG: {environment['DXVK_CONFIG']}\n")
        if diagnostics.get():
            log(f"Proton-/DXVK-Protokolle: {environment['DXVK_LOG_PATH']}\n")
        status.set("Wird gestartet. Der erste Download kann mehrere Minuten dauern.")
        threading.Thread(target=worker, args=(command, environment, directory, launch_options), daemon=True).start()

    def poll():
        nonlocal running
        for _ in range(200):
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                log(value)
            else:
                running = False
                button.configure(state="normal")
                stop_button.configure(state="disabled")
                if session.cancelled.is_set():
                    status.set("Programm sofort beendet.")
                elif kind == "error":
                    log(value + "\n")
                    status.set("Start fehlgeschlagen. Details stehen im Protokoll.")
                else:
                    status.set("Programm beendet." if value == 0 else
                               f"Programm oder Laufzeit mit Fehlercode {value} beendet. Siehe Protokoll.")
        root.after(100, poll)

    def close():
        if running:
            messagebox.showinfo("Programm läuft", "Bitte zuerst das Windows-Programm schließen.")
            return
        root.destroy()

    button = ttk.Button(root, text="Programm starten", command=start)
    button.grid(row=8, column=0, pady=4)
    stop_button = ttk.Button(root, text="Sofort beenden", command=stop_now, state="disabled")
    stop_button.grid(row=8, column=1, pady=4)
    ttk.Button(root, text="Desktop-Verknüpfung erstellen", command=create_desktop).grid(row=8, column=2, pady=4)
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    report_shortcut(lambda: write_shortcut("GE-Wine", ["--gui"], "ge-wine-gui", log), log)
    try:
        root.mainloop()
    finally:
        session.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Windows-Programme mit GE-Proton starten.")
    parser.add_argument("--version", action="version", version=f"GE-Wine {VERSION}")
    parser.add_argument("--kill-all", action="store_true", help="Alle registrierten GE-Wine-Sitzungen und ihre Kindprozesse sofort beenden")
    parser.add_argument("--create-shortcut", action="store_true", help="Desktop-Verknüpfung mit aktuellen Startoptionen erstellen, ohne zu starten")
    parser.add_argument("--gui", action="store_true", help="Grafische Oberfläche öffnen")
    parser.add_argument("--prefix", help="Gemeinsamer Programmordner für Installer und Anwendung")
    parser.add_argument("--proton",
                        help="Profilvorgabe: Phoenix GE10-34, Online/Standard GE11-11; alternativ GE-Proton oder Build-Ordner")
    parser.add_argument("--gameid", default=os.environ.get("GAMEID", "umu-default"))
    parser.add_argument("--profile", choices=PROFILES, default="auto")
    parser.add_argument("--no-prerequisites", action="store_true", help="Automatische Icarus-Laufzeit- und Shader-Compiler-Einrichtung überspringen")
    parser.add_argument("--diagnostics", action="store_true", help="DXVK-HUD sowie Proton- und DXVK-Dateilogs aktivieren")
    parser.add_argument("--nvidia-offload", action="store_true", help="NVIDIA-GPU auf Hybrid-Systemen wählen")
    parser.add_argument("--low-latency", action="store_true", help="Experimentell: maximal ein Frame in der Warteschlange")
    parser.add_argument("executable", nargs="?")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    try:
        if options.kill_all:
            count = stop_all()
            print(f"GE-Wine: {count} laufende Sitzungen beendet.")
            return 0
        if options.create_shortcut and not options.executable:
            parser.error("--create-shortcut benötigt eine Programmdatei")
        return gui(options) if options.gui or not options.executable else launch_cli(options)
    except (ValueError, OSError, tarfile.TarError) as error:
        print(f"GE-Wine: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
