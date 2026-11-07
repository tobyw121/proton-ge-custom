#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native macOS frontend. Linux Proton binaries are never downloaded here."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import queue
import shlex
import subprocess
import sys
import threading
import tarfile

from ge_wine import VERSION, selected_profile
from ge_wine_sessions import Session, LaunchCancelled, stop_all
from ge_wine_logging import RunLog
from ge_wine_dedup import compact_recording
from ge_wine_desktop import windows_links, installer
from ge_wine_macos_runtime import support_directory, ensure_wine, require_dependencies, manifest

PROFILES = {'auto':'Automatisch','standard':'Standard',
            'icarus-phoenix':'Icarus Phoenix (Mac-Prefix)','icarus-online':'Icarus Online (Mac-Prefix)'}


def prepare(options):
    executable = Path(options.executable).expanduser().resolve()
    if not executable.is_file():
        raise ValueError(f'Programmdatei nicht gefunden: {executable}')
    profile = selected_profile(executable,options.profile)
    name = profile if profile.startswith('icarus-') else hashlib.sha256(os.fsencode(executable.parent)).hexdigest()[:16]
    prefix = Path(options.prefix).expanduser().resolve() if options.prefix else support_directory()/'prefixes'/name
    if prefix.exists() and not prefix.is_dir():
        raise ValueError(f'Prefix ist kein Verzeichnis: {prefix}')
    environment = dict(os.environ,WINEPREFIX=str(prefix))
    for key in list(environment):
        if key in {'PROTONPATH','PROTON_USE_WINED3D','UMU_NO_RUNTIME'} or key.startswith('STEAM_COMPAT_'):
            environment.pop(key,None)
    # PyInstaller library settings must not override the external Wine runtime's loader.
    for key in ('DYLD_LIBRARY_PATH','DYLD_FRAMEWORK_PATH'):
        environment.pop(key,None)
    arguments = ['msiexec','/i',str(executable)] if executable.suffix.lower()=='.msi' else [str(executable)]
    return executable,profile,environment,arguments+options.arguments


def own_command():
    return [sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve())]


def write_shortcut(options,environment,executable,profile,report,arguments=None,name=None):
    if options.no_shortcuts:
        return
    desktop = Path.home()/'Desktop';desktop.mkdir(exist_ok=True)
    safe = ''.join(character if character.isalnum() or character in ' .-_' else '_' for character in (name or Path(executable).stem))[:70]
    key = environment['WINEPREFIX']+'\0'+str(executable)+'\0'+profile
    folder = desktop/f'GE-Wine-{safe}-{hashlib.sha256(key.encode()).hexdigest()[:10]}.app'
    if folder.exists() and not (folder/'Contents/Resources/ge-wine-shortcut').is_file():
        report(f'Fremde Desktop-Anwendung bleibt erhalten: {folder}\n');return
    binary = folder/'Contents/MacOS/launch';binary.parent.mkdir(parents=True,exist_ok=True)
    resources=folder/'Contents/Resources';resources.mkdir(exist_ok=True)
    command = own_command()+['--profile',profile,'--prefix',environment['WINEPREFIX']]
    if options.wine:
        command += ['--wine',options.wine]
    if options.full_trace:
        command += ['--full-trace']
    if getattr(options,'no_network_log',False):
        command += ['--no-network-log']
    command += [str(executable),*(options.arguments if arguments is None else arguments)]
    binary.write_text('#!/bin/sh\nexec '+shlex.join(command)+'\n');binary.chmod(0o755)
    with (folder/'Contents/Info.plist').open('wb') as output:
        plistlib.dump(dict(CFBundleExecutable='launch',CFBundleIdentifier='org.ge-wine.shortcut.'+hashlib.sha256(key.encode()).hexdigest()[:16],
                          CFBundleName=safe,CFBundlePackageType='APPL',CFBundleVersion=VERSION),output)
    (resources/'ge-wine-shortcut').write_text('GE-Wine macOS\n')
    report(f'Desktop-Verknüpfung: {folder}\n')


def installed_links(options,environment,executable,profile,before,report):
    prefix=Path(environment['WINEPREFIX'])
    start=prefix/'drive_c/windows/system32/start.exe'
    if not start.is_file():
        return
    for name,stamp in windows_links(prefix).items():
        link=Path(name)
        if before.get(name)==stamp or any(word in link.stem.lower() for word in ('uninstall','deinstall','repair')):
            continue
        write_shortcut(options,environment,start,profile,report,['/unix',str(link)],link.stem)


def launch(options,session,report,output=None,interactive=False):
    executable,profile,environment,arguments = prepare(options)
    before=windows_links(environment['WINEPREFIX'])
    recording = RunLog(environment,['native-wine',*arguments],str(executable.parent),profile,session.token,options.full_trace,network_log=not getattr(options,'no_network_log',False))
    session.log=recording
    def logged(text):
        visible=recording.console('ge-wine',text)
        if visible: report(visible)
    result=None;failure=None
    try:
        logged(f'macOS-Prefix: {environment["WINEPREFIX"]}\nAufzeichnung: {recording.path}\n')
        recording.start()
        binary=ensure_wine(logged,session.cancelled,options.wine)
        session.check()
        if not options.wine:
            require_dependencies(logged,session.cancelled,interactive=interactive)
        command=[str(binary),*arguments]
        recording.metadata['command']=command
        recording.metadata['macos_runtime']=dict(wine=str(binary),source='custom' if options.wine else manifest()['wine']['version'])
        recording._metadata()
        if not installer(executable):
            write_shortcut(options,environment,executable,profile,logged)
        result=session.run(command,environment,str(executable.parent),output)
        if result==0 and not session.cancelled.is_set():
            installed_links(options,environment,executable,profile,before,logged)
        return result
    except BaseException as error:
        failure=type(error).__name__+': '+str(error);raise
    finally:
        session.log=None
        summary=recording.close(result,failure)
        report(f'Logs abgeschlossen: {summary.get("suppressed_lines",0)} Wiederholungen zusammengefasst.\n')


def gui(options):
    import tkinter as tk
    from tkinter import ttk,filedialog,messagebox
    from tkinter.scrolledtext import ScrolledText
    root=tk.Tk();root.title('GE-Wine für macOS');root.geometry('850x690')
    root.columnconfigure(1,weight=1);root.rowconfigure(8,weight=1)
    executable=tk.StringVar(value=options.executable or '')
    arguments=tk.StringVar(value=shlex.join(options.arguments));prefix=tk.StringVar(value=options.prefix or '')
    wine=tk.StringVar(value=options.wine or '');profile=tk.StringVar(value=options.profile)
    if sys.platform=='darwin':
        root.createcommand('::tk::mac::OpenDocument',lambda *paths:executable.set(paths[0]) if paths else None)
    network_log=tk.BooleanVar(value=not getattr(options,'no_network_log',False))
    full_trace=tk.BooleanVar(value=options.full_trace)
    values=[('Windows-Programm',executable),('Argumente',arguments),('Prefix (leer = automatisch)',prefix),('Wine.app (leer = automatisch)',wine)]
    for row,(label,variable) in enumerate(values):
        ttk.Label(root,text=label).grid(row=row,column=0,sticky='w',padx=14,pady=8)
        ttk.Entry(root,textvariable=variable).grid(row=row,column=1,sticky='ew',padx=8,pady=8)
    def browse(variable,directory=False):
        value=filedialog.askdirectory() if directory else filedialog.askopenfilename()
        if value:variable.set(value)
    ttk.Button(root,text='Öffnen …',command=lambda:browse(executable)).grid(row=0,column=2,padx=12)
    ttk.Button(root,text='Wählen …',command=lambda:browse(prefix,True)).grid(row=2,column=2,padx=12)
    ttk.Button(root,text='Wählen …',command=lambda:browse(wine,True)).grid(row=3,column=2,padx=12)
    ttk.Label(root,text='Profil').grid(row=4,column=0,sticky='w',padx=14)
    ttk.Combobox(root,textvariable=profile,values=list(PROFILES),state='readonly').grid(row=4,column=1,sticky='ew',padx=8)
    ttk.Checkbutton(root,text='API-Aufrufe protokollieren (langsam / große Logs)',variable=full_trace).grid(row=5,column=0,columnspan=3,sticky='w',padx=14,pady=10)
    ttk.Checkbutton(root,text='Netzwerkverbindungen protokollieren',variable=network_log).grid(row=6,column=0,sticky='w',padx=14)
    status=tk.StringVar(value='Wine für macOS wird automatisch eingerichtet. Apple Silicon benötigt Rosetta 2.\nGStreamer wird bei Bedarf über den macOS-Installationsdialog eingerichtet.')
    ttk.Label(root,textvariable=status,wraplength=800).grid(row=7,column=0,columnspan=3,sticky='w',padx=14,pady=8)
    display=ScrolledText(root,height=16,state='disabled');display.grid(row=8,column=0,columnspan=3,sticky='nsew',padx=14,pady=10)
    events=queue.Queue();session=Session();running=False
    def start():
        nonlocal running
        try:
            chosen=argparse.Namespace(executable=executable.get(),arguments=shlex.split(arguments.get()),prefix=prefix.get(),wine=wine.get(),profile=profile.get(),full_trace=full_trace.get(),no_network_log=not network_log.get(),no_shortcuts=options.no_shortcuts)
            prepare(chosen)
        except (OSError,ValueError) as error:
            messagebox.showerror('GE-Wine',str(error));return
        session.reset();running=True;start_button.configure(state='disabled');stop_button.configure(state='normal')
        status.set('Wird gestartet / Laufzeit wird eingerichtet …')
        def worker():
            report=lambda text:events.put(('log',text))
            try:
                result=launch(chosen,session,report,report,interactive=True)
                events.put(('done',result))
            except LaunchCancelled:
                events.put(('done',130))
            except (OSError,ValueError,RuntimeError,tarfile.TarError) as error:
                events.put(('error',str(error)))
        threading.Thread(target=worker,daemon=True).start()
    start_button=ttk.Button(root,text='Programm starten',command=start);start_button.grid(row=6,column=0,padx=14,pady=8)
    def stop():
        session.stop();status.set('Programm wird beendet …')
    stop_button=ttk.Button(root,text='Sofort beenden',command=stop,state='disabled');stop_button.grid(row=6,column=1,sticky='w',padx=8)
    def poll():
        nonlocal running
        for _ in range(200):
            try:kind,value=events.get_nowait()
            except queue.Empty:break
            if kind=='log':
                display.configure(state='normal');display.insert('end',value);display.see('end');display.configure(state='disabled')
            else:
                running=False;start_button.configure(state='normal');stop_button.configure(state='disabled')
                status.set(('Fehler: '+value) if kind=='error' else ('Programm beendet.' if value==0 else f'Beendet mit Code {value}. Siehe Protokoll.'))
        root.after(100,poll)
    def close():
        if running:
            messagebox.showinfo('Programm läuft','Zuerst das Programm schließen oder „Sofort beenden“ wählen.');return
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',close);root.after(100,poll)
    try:root.mainloop()
    finally:session.close()
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description='GE-Wine für macOS mit nativer Wine-Laufzeit. Linux-GE-Proton, GE-Memory und Speedhack sind hier nicht verfügbar.')
    parser.add_argument('--version',action='version',version=f'GE-Wine macOS {VERSION}')
    parser.add_argument('--gui',action='store_true');parser.add_argument('--wine',help='Native Wine.app oder ausführbare wine-Datei; Standard: automatischer Download')
    parser.add_argument('--prefix');parser.add_argument('--profile',choices=PROFILES,default='auto')
    parser.add_argument('--no-network-log',action='store_true')
    parser.add_argument('--full-trace',action='store_true');parser.add_argument('--no-shortcuts',action='store_true')
    parser.add_argument('--kill-all',action='store_true');parser.add_argument('--compact-logs',metavar='ORDNER')
    parser.add_argument('--doctor',action='store_true',help='macOS-Build und gebündelte Python-/Tk-Version prüfen')
    parser.add_argument('--setup-runtime',action='store_true',help='Native Wine-Laufzeit herunterladen; Multimedia-Abhängigkeit wird beim Programmstart geprüft')
    parser.add_argument('executable',nargs='?');parser.add_argument('arguments',nargs=argparse.REMAINDER)
    options=parser.parse_args([value for value in (sys.argv[1:] if argv is None else argv) if not value.startswith('-psn_')])
    report=lambda text:print(text,end='',file=sys.stderr,flush=True)
    try:
        if options.doctor:
            import tkinter
            print(json.dumps(dict(version=VERSION,platform=sys.platform,architecture=__import__('platform').machine(),tk=tkinter.TkVersion,runtime=manifest()['wine']['version'])));return 0
        if options.compact_logs:
            print(json.dumps(compact_recording(options.compact_logs)));return 0
        if options.kill_all:
            print(f'GE-Wine: {stop_all()} Sitzungen beendet.');return 0
        if options.setup_runtime:
            print(ensure_wine(report,explicit=options.wine))
            return 0
        if options.gui or not options.executable:
            return gui(options)
        session=Session()
        try:return launch(options,session,report)
        except (LaunchCancelled,KeyboardInterrupt):session.stop();return 130
        finally:session.close()
    except (OSError,ValueError,RuntimeError,tarfile.TarError) as error:
        print(f'GE-Wine macOS: {error}',file=sys.stderr);return 1


if __name__=='__main__':
    sys.exit(main())
