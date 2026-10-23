#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A desktop and command-line frontend for GE-Proton through UMU."""

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import queue
import shlex
import subprocess
import sys
import threading


VERSION = "0.3.0"
PROFILES = {"auto": "Automatisch erkennen", "standard": "Standard",
            "icarus-phoenix": "Icarus Phoenix – RTX 2070 / 3060"}
VC_VERBS = ("vcrun2008", "vcrun2010", "vcrun2012", "vcrun2013", "vcrun2015")


def selected_profile(executable, profile):
    if profile != "auto":
        return profile
    for directory in Path(executable).expanduser().resolve().parents:
        name = directory.name.casefold().replace("_", "").replace("-", "").replace(" ", "")
        if name == "icarusphoenix" and (directory / "Launcher.exe").is_file() and (directory / "Bin64").is_dir():
            return "icarus-phoenix"
    return "standard"


def installed_verbs(prefix):
    try:
        return set((prefix / "winetricks.log").read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        return set()


def needs_prerequisites(executable, profile, enabled):
    return enabled and selected_profile(executable, profile) == "icarus-phoenix" and not any(
        part.casefold() == "redist" for part in Path(executable).parts)


def install_prerequisites(command, environment, directory, run, report):
    """Use UMU's supported winetricks entry point and its success records."""
    prefix = Path(environment["WINEPREFIX"])
    lock_directory = prefix.parent / ".ge-wine-locks"
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(os.fsencode(prefix)).hexdigest() + ".lock"
    backend = command[:3] if command[1:3] == ["-m", "umu"] else command[:1]
    report("Prüfe Visual C++ 2008–2015 im Programmordner …\n")
    with (lock_directory / lock_name).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for verb in VC_VERBS:
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
    report("VC++-Laufzeiten sind eingerichtet.\n")
    return 0


def prepare(executable, arguments, prefix=None, proton="GE-Proton", gameid="umu-default",
            profile="auto", diagnostics=False, nvidia_offload=False, low_latency=False):
    if profile not in PROFILES:
        raise ValueError(f"Unbekanntes Profil: {profile}")
    executable = Path(executable).expanduser().resolve()
    if not executable.is_file():
        raise ValueError(f"Programmdatei nicht gefunden: {executable}")
    profile = selected_profile(executable, profile)
    if not gameid.strip():
        raise ValueError("Die Spielkennung darf nicht leer sein.")
    if proton != "GE-Proton":
        proton_path = Path(proton).expanduser().resolve()
        if not (proton_path / "proton").is_file():
            raise ValueError("Der GE-Proton-Ordner muss die Datei 'proton' enthalten.")
        proton = str(proton_path)
    if not prefix:
        install_root = executable.parent
        if profile == "icarus-phoenix":
            # The server distributes Launcher.exe beside Bin64/Redist.
            # Its redistributables and game must use the launcher's prefix.
            for parent in executable.parents:
                if (parent / "Launcher.exe").is_file():
                    install_root = parent
                    break
        identifier = hashlib.sha256(os.fsencode(install_root)).hexdigest()[:16]
        if profile != "standard":
            identifier = f"{profile}-{identifier}"
        prefix = Path.home() / ".local/share/ge-wine/prefixes" / identifier
    prefix = Path(prefix).expanduser().resolve()
    if prefix.exists() and not prefix.is_dir():
        raise ValueError(f"Der Programmordner ist kein Verzeichnis: {prefix}")
    environment = dict(os.environ)
    environment.update(WINEPREFIX=str(prefix), PROTONPATH=proton, GAMEID=gameid,
                       STORE=environment.get("STORE", "none"))
    if profile == "icarus-phoenix":
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


def launch_cli(options):
    command, environment, directory = prepare(
        options.executable, options.arguments, options.prefix, options.proton, options.gameid,
        options.profile, options.diagnostics, options.nvidia_offload, options.low_latency)
    if options.diagnostics:
        Path(environment["DXVK_LOG_PATH"]).mkdir(parents=True, exist_ok=True)
    if needs_prerequisites(options.executable, options.profile, not options.no_prerequisites):
        result = install_prerequisites(
            command, environment, directory,
            lambda cmd, env, cwd: subprocess.call(cmd, env=env, cwd=cwd),
            lambda text: print(text, end="", file=sys.stderr, flush=True))
        if result:
            return result
    return subprocess.call(command, env=environment, cwd=directory)


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
    proton = tk.StringVar(value=options.proton)
    gameid = tk.StringVar(value=options.gameid)
    profile = tk.StringVar(value=PROFILES[options.profile])
    diagnostics = tk.BooleanVar(value=options.diagnostics)
    nvidia_offload = tk.BooleanVar(value=options.nvidia_offload)
    low_latency = tk.BooleanVar(value=options.low_latency)
    prerequisites = tk.BooleanVar(value=not options.no_prerequisites)
    events = queue.Queue()
    running = False
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
    ttk.Checkbutton(settings, text="Diagnose: GPU, DX-Version, FPS", variable=diagnostics).pack(anchor="w")
    ttk.Checkbutton(settings, text="NVIDIA-GPU erzwingen (Hybrid-Laptop)", variable=nvidia_offload).pack(anchor="w")
    ttk.Checkbutton(settings, text="Kürzere Render-Warteschlange (experimentell)", variable=low_latency).pack(anchor="w")
    ttk.Checkbutton(settings, text="Icarus: VC++ 2008–2015 automatisch einrichten", variable=prerequisites).pack(anchor="w")

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
    ttk.Label(root, text="GE-Proton = offizieller Download. Alternativ eigenen Build-Ordner wählen.\n"
              "Leerer Programmordner: automatisch je Programmverzeichnis. Für Installer und\n"
              "installierte Anwendung denselben eigenen Ordner wählen.").grid(
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

    def worker(command, environment, directory, bootstrap):
        try:
            def run(child_command, child_environment, child_directory):
                with subprocess.Popen(child_command, env=child_environment, cwd=child_directory,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, errors="replace", bufsize=1) as process:
                    for line in process.stdout:
                        events.put(("log", line))
                    return process.wait()
            if bootstrap:
                result = install_prerequisites(command, environment, directory, run,
                                               lambda text: events.put(("log", text)))
                if result:
                    events.put(("done", result))
                    return
            events.put(("done", run(command, environment, directory)))
        except OSError as error:
            events.put(("error", str(error)))

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
        running = True
        button.configure(state="disabled")
        log(f"Programmordner: {environment['WINEPREFIX']}\n")
        profile_key = next(key for key, value in PROFILES.items() if value == profile.get())
        log(f"Profil: {PROFILES[selected_profile(executable.get(), profile_key)]}\n")
        if diagnostics.get():
            log(f"DXVK-Protokolle: {environment['DXVK_LOG_PATH']}\n")
        status.set("Wird gestartet. Der erste Download kann mehrere Minuten dauern.")
        bootstrap = needs_prerequisites(executable.get(), profile_key, prerequisites.get())
        threading.Thread(target=worker, args=(command, environment, directory, bootstrap), daemon=True).start()

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
                if kind == "error":
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
    button.grid(row=8, column=0, columnspan=3, pady=4)
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    root.mainloop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Windows-Programme mit GE-Proton starten.")
    parser.add_argument("--version", action="version", version=f"GE-Wine {VERSION}")
    parser.add_argument("--gui", action="store_true", help="Grafische Oberfläche öffnen")
    parser.add_argument("--prefix", help="Gemeinsamer Programmordner für Installer und Anwendung")
    parser.add_argument("--proton", default=os.environ.get("PROTONPATH", "GE-Proton"),
                        help="GE-Proton für offiziellen Download oder Pfad zu eigenem Build")
    parser.add_argument("--gameid", default=os.environ.get("GAMEID", "umu-default"))
    parser.add_argument("--profile", choices=PROFILES, default="auto")
    parser.add_argument("--no-prerequisites", action="store_true", help="Automatische VC++-Einrichtung überspringen")
    parser.add_argument("--diagnostics", action="store_true", help="DXVK-HUD und Protokolle aktivieren")
    parser.add_argument("--nvidia-offload", action="store_true", help="NVIDIA-GPU auf Hybrid-Systemen wählen")
    parser.add_argument("--low-latency", action="store_true", help="Experimentell: maximal ein Frame in der Warteschlange")
    parser.add_argument("executable", nargs="?")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    try:
        return gui(options) if options.gui or not options.executable else launch_cli(options)
    except (ValueError, OSError) as error:
        print(f"GE-Wine: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
