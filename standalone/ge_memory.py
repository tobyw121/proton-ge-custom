#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Linux numeric memory inspector for user-owned processes."""
import argparse
import ctypes
from dataclasses import dataclass
import os
import math
import json
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import tempfile
import uuid

from ge_wine_sessions import identity
from ge_speed import SpeedControl, validate_factor

TYPES = {'Int8': '<b', 'UInt8': '<B', 'Int16': '<h', 'UInt16': '<H',
         'Int32': '<i', 'UInt32': '<I', 'Int64': '<q', 'UInt64': '<Q',
         'Float32': '<f', 'Float64': '<d', 'Bool': '<?'}
AUTO = 'Automatisch'
MODES = ('Gleich', 'Nicht gleich', 'Geaendert', 'Unveraendert', 'Gestiegen', 'Gesunken',
         'Groesser als', 'Kleiner als', 'Zwischen', 'Um Wert gestiegen', 'Um Wert gesunken')
VALUE_MODES = {'Gleich', 'Nicht gleich', 'Groesser als', 'Kleiner als', 'Zwischen', 'Um Wert gestiegen', 'Um Wert gesunken'}


def pack_value(text, kind):
    try:
        text = str(text).strip()
        if kind == 'Bool':
            if text.lower() not in ('0', '1', 'true', 'false'):
                raise ValueError('Bool: nur 0/1 oder true/false')
            value = text.lower() in ('1', 'true')
        else:
            value = float(text.replace(',', '.')) if kind.startswith('Float') else int(text, 16 if text.lower().startswith(('0x', '-0x')) else 10)
        return struct.pack(TYPES[kind], value)
    except (ValueError, OverflowError, struct.error, KeyError) as error:
        raise ValueError(f'Ungueltiger Wert fuer {kind}: {text}') from error


def patterns_for(text, kinds):
    result = {}
    for kind in kinds:
        try:
            result[kind] = pack_value(text, kind)
        except ValueError:
            pass
    if not result:
        raise ValueError('Der Wert passt zu keinem der ausgewaehlten Datentypen.')
    return result


def unpack_value(data, kind):
    return struct.unpack(TYPES[kind], data)[0]


def show_value(data, kind):
    if data is None:
        return 'Nicht lesbar'
    value = unpack_value(data, kind)
    if kind == 'Bool':
        return '1' if value else '0'
    return format(value, '.7g' if kind == 'Float32' else '.15g') if kind.startswith('Float') else str(value)


@dataclass(frozen=True)
class Hit:
    address: int
    kind: str
    baseline: bytes
    initial: bytes

    @property
    def key(self):
        return self.address, self.kind


@dataclass
class ScanResult:
    hits: dict
    scanned: int
    skipped: int
    capped_types: set
    budget_limited: bool = False
    cancelled: bool = False

    @property
    def limited(self):
        return bool(self.capped_types or self.budget_limited or self.cancelled)


@dataclass(frozen=True)
class Region:
    start: int
    end: int
    permissions: str
    name: str = ''


class ProcessMemory:
    def __init__(self, pid):
        self.pid = int(pid)
        self.stamp = identity(self.pid)
        if self.stamp is None:
            raise ProcessLookupError('Prozess fehlt oder gehoert einem anderen Benutzer.')
        self.read_fd = None
        self.write_fd = None

    def check(self):
        if identity(self.pid) != self.stamp:
            raise ProcessLookupError('Prozess wurde beendet oder PID wurde wiederverwendet.')

    def regions(self):
        self.check()
        result = []
        for line in Path(f'/proc/{self.pid}/maps').read_text().splitlines():
            fields = line.split(None, 5)
            start, end = (int(n, 16) for n in fields[0].split('-'))
            result.append(Region(start, end, fields[1], fields[5] if len(fields) > 5 else ''))
        return result

    def read(self, address, size):
        self.check()
        if address < 0 or not 0 < size <= 1024 * 1024:
            raise ValueError('Ungueltiger Speicherbereich.')
        if self.read_fd is None:
            self.read_fd = os.open(f'/proc/{self.pid}/mem', os.O_RDONLY | os.O_CLOEXEC)
        self.check()
        return os.pread(self.read_fd, size, address)

    def write(self, address, data):
        self.check()
        if len(data) not in (1, 2, 4, 8):
            raise ValueError('Nur Zahlen mit 1, 2, 4 oder 8 Byte werden geschrieben.')
        if not any(r.start <= address and address + len(data) <= r.end and r.permissions[:2] == 'rw'
                   for r in self.regions()):
            raise ValueError('Adresse liegt nicht in einem aktuell les- und schreibbaren Bereich.')
        if self.write_fd is None:
            self.write_fd = os.open(f'/proc/{self.pid}/mem', os.O_RDWR | os.O_CLOEXEC)
        self.check()
        if os.pwrite(self.write_fd, data, address) != len(data):
            raise OSError('Der Wert wurde nur teilweise geschrieben.')

    def close(self):
        for attr in ('read_fd', 'write_fd'):
            fd = getattr(self, attr)
            if fd is not None:
                os.close(fd)
                setattr(self, attr, None)


def scan_types(memory, patterns, max_bytes, cancel, progress=lambda n: None,
               regions=None, max_hits=100000, aligned=True, include_shared=False, float_tolerance=0.0):
    if not patterns or max_bytes < 0 or max_hits < len(patterns):
        raise ValueError('Ungueltige Suchparameter.')
    if not math.isfinite(float_tolerance) or float_tolerance < 0:
        raise ValueError('Float-Toleranz muss endlich und mindestens 0 sein.')
    regions = list(memory.regions() if regions is None else regions)
    regions = [r for r in regions if r.permissions[:2] == 'rw' and
               (include_shared or r.permissions.endswith('p')) and (not r.name.startswith('/dev/') or r.name.startswith('/dev/shm/'))]
    eligible = sum(r.end - r.start for r in regions)
    budget = max_bytes or eligible
    quota = max_hits // len(patterns)
    counts = {kind: 0 for kind in patterns}
    grouped = {}; approximate = {}
    for kind, pattern in patterns.items():
        if float_tolerance and kind.startswith('Float'):
            target = unpack_value(pattern, kind)
            if not math.isfinite(target):
                raise ValueError('Float-Suchwert muss bei Toleranz endlich sein.')
            approximate[kind] = target
        else:
            grouped.setdefault(pattern, []).append(kind)
    hits = {}; capped = set(); scanned = skipped = 0
    tail_size = max(map(len, patterns.values())) - 1
    for region in regions:
        address = region.start
        tail = b''
        while address < region.end and scanned < budget:
            if cancel.is_set():
                return ScanResult(hits, scanned, skipped, capped, cancelled=True)
            count = min(1024 * 1024, region.end - address, budget - scanned)
            try:
                data = memory.read(address, count)
            except (PermissionError, ProcessLookupError):
                raise
            except OSError:
                skipped += count; address += count; scanned += count; tail = b''
                continue
            block = tail + data
            base = address - len(tail)
            for pattern, kinds in grouped.items():
                if cancel.is_set():
                    return ScanResult(hits, scanned, skipped, capped, cancelled=True)
                active = [kind for kind in kinds if kind not in capped]
                if not active:
                    continue
                offset = block.find(pattern)
                while offset != -1:
                    found = base + offset
                    # Do not count overlap-tail matches from the preceding chunk twice.
                    if found + len(pattern) > address:
                        for kind in active:
                            if kind in capped or (aligned and found % len(pattern)):
                                continue
                            hit = Hit(found, kind, pattern, pattern)
                            hits[hit.key] = hit
                            counts[kind] += 1
                            if counts[kind] >= quota:
                                capped.add(kind)
                    if all(kind in capped for kind in active):
                        break
                    offset = block.find(pattern, offset + 1)
            for kind, target in approximate.items():
                if kind in capped:
                    continue
                size = struct.calcsize(TYPES[kind]); decoder = struct.Struct(TYPES[kind])
                step = size if aligned else 1
                start = (-base) % size if aligned else 0
                for offset in range(start, len(block)-size+1, step):
                    if offset % 4096 < step and cancel.is_set():
                        return ScanResult(hits, scanned, skipped, capped, cancelled=True)
                    if base+offset+size <= address:
                        continue
                    number = decoder.unpack_from(block, offset)[0]
                    if math.isfinite(number) and abs(number-target) <= float_tolerance:
                        raw = block[offset:offset+size]
                        hit = Hit(base+offset, kind, raw, raw)
                        hits[hit.key] = hit; counts[kind] += 1
                        if counts[kind] >= quota:
                            capped.add(kind); break
            tail = block[-tail_size:] if tail_size and len(data) == count else b''
            skipped += count - len(data)
            scanned += count; address += count
            progress(scanned)
            if len(capped) == len(patterns):
                return ScanResult(hits, scanned, skipped, capped)
    return ScanResult(hits, scanned, skipped, capped, budget_limited=scanned < eligible)


def sampled_hits(memory, hits, cancel):
    """Stream ordered hits with a single-page cache instead of one syscall per value."""
    cached_page = None
    cached_data = b''
    for key, hit in sorted(hits.items(), key=lambda item: item[0]):
        if cancel.is_set():
            return
        page = hit.address & ~4095
        size = len(hit.baseline)
        try:
            if page != cached_page:
                cached_page = page
                try:
                    cached_data = memory.read(page,4096)
                except (PermissionError,ProcessLookupError):
                    raise
                except OSError:
                    cached_data = b''
            offset = hit.address-page
            data = cached_data[offset:offset+size]
            if len(data) != size:
                data = memory.read(hit.address,size)
            yield key, data if len(data)==size else None
        except (PermissionError,ProcessLookupError):
            raise
        except OSError:
            yield key,None


def refine_hits(memory, previous, mode, patterns, cancel, upper=None, float_tolerance=0.0):
    if mode not in MODES:
        raise ValueError('Unbekannter Vergleich.')
    if not math.isfinite(float_tolerance) or float_tolerance < 0:
        raise ValueError('Float-Toleranz muss endlich und mindestens 0 sein.')
    if mode == 'Zwischen':
        if not upper or not patterns or any(kind not in upper or
                unpack_value(pattern, kind) > unpack_value(upper[kind], kind)
                for kind, pattern in patterns.items()):
            raise ValueError('Gültige Unter- und Obergrenze erforderlich.')
    result = {}
    for key, current in sampled_hits(memory, previous, cancel):
        if current is None:
            continue
        hit = previous[key]; kind = hit.kind
        if mode in VALUE_MODES and kind not in patterns:
            continue
        tolerance = float_tolerance if kind.startswith('Float') else 0
        number = unpack_value(current, kind)
        baseline = unpack_value(hit.baseline, kind)
        target = unpack_value(patterns[kind], kind) if mode in VALUE_MODES and kind in patterns else None
        if mode in ('Gleich', 'Nicht gleich'):
            same = (math.isfinite(number) and math.isfinite(target) and abs(number-target) <= tolerance) if tolerance else current == patterns[kind]
            keep = same if mode == 'Gleich' else not same
        elif mode in ('Geaendert', 'Unveraendert'):
            same = (math.isfinite(number) and math.isfinite(baseline) and abs(number-baseline) <= tolerance) if tolerance else current == hit.baseline
            keep = same if mode == 'Unveraendert' else not same
        elif mode == 'Gestiegen':
            keep = number > baseline + tolerance
        elif mode == 'Gesunken':
            keep = number < baseline - tolerance
        elif mode == 'Groesser als':
            keep = number > target + tolerance
        elif mode == 'Kleiner als':
            keep = number < target - tolerance
        elif mode == 'Zwischen':
            keep = target-tolerance <= number <= unpack_value(upper[kind], kind)+tolerance
        else:
            delta = target if mode == 'Um Wert gestiegen' else -target
            keep = math.isfinite(number) and math.isfinite(baseline) and abs((number-baseline)-delta) <= tolerance
        if keep:
            result[key] = Hit(hit.address, kind, current, hit.initial)
    return result


def live_read(memory, hits):
    """One pread per page for visible hits; never mutate scan baselines."""
    pages = {}; values = {}
    for key, hit in hits.items():
        page = hit.address & ~4095
        size = len(hit.baseline)
        try:
            if page not in pages:
                pages[page] = memory.read(page, 4096)
            offset = hit.address - page
            data = pages[page][offset:offset + size]
            if len(data) != size:
                data = memory.read(hit.address, size)
            values[key] = data if len(data) == size else None
        except (PermissionError, ProcessLookupError):
            raise
        except OSError:
            try:
                data = memory.read(hit.address, size)
                values[key] = data if len(data) == size else None
            except (PermissionError, ProcessLookupError):
                raise
            except OSError:
                values[key] = None
    return values


def write_hits(memory, hits, selected, text, frozen=None):
    selected = list(selected)
    if not selected:
        raise ValueError('Zuerst Treffer auswaehlen.')
    # Encode ALL selected types before the first write (range/width validation).
    encoded = {key: pack_value(text, hits[key].kind) for key in selected}
    combined = dict(frozen or {}); combined.update(encoded)
    spans = sorted((hits[key].address, data) for key, data in combined.items())
    previous = None
    for address, data in spans:
        if previous and address < previous[0] + len(previous[1]):
            if address != previous[0] or data != previous[1]:
                raise ValueError('Ueberlappende Typen ausgewaehlt. Pro Speicherbereich nur einen passenden Typ waehlen.')
        previous = (address, data)
    written = set(); actual = {}
    for key in selected:
        hit = hits[key]; data = encoded[key]
        if (hit.address, data) not in written:
            memory.write(hit.address, data)
            written.add((hit.address, data))
        current = memory.read(hit.address, len(data))
        actual[key] = current if len(current) == len(data) else None
    return encoded, actual


def first_scan(memory, pattern, max_bytes, cancel, progress=lambda n: None, regions=None, max_hits=100000):
    # Backward-compatible single-type API used by the original backend tests.
    kind = {1: 'Int8', 2: 'Int16', 4: 'Int32', 8: 'Int64'}[len(pattern)]
    result = scan_types(memory, {kind: pattern}, max_bytes, cancel, progress, regions, max_hits)
    return {hit.address: hit.baseline for hit in result.hits.values()}, result.scanned, result.skipped, result.limited


def next_scan(memory, previous, kind, mode, pattern, cancel):
    hits = {(address, kind): Hit(address, kind, old, old) for address, old in previous.items()}
    result = refine_hits(memory, hits, mode, {kind: pattern}, cancel)
    return {hit.address: hit.baseline for hit in result.values()}


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    prefix: str
    rss: int
    candidate: bool
    cryengine: bool = False

    @property
    def rank(self):
        return (int(self.cryengine), int(self.name.casefold() in ('launcher.exe', 'game.exe')), self.rss)


def is_game_candidate(name, rss, is_wine):
    helpers = {'vlauncher.exe', 'vfunlauncher.exe', 'icaruslauncher.exe', 'gameguard.des',
               'gamemon.des', 'gamemon64.des', 'ggerror.des', 'wineserver', 'wineserver64',
               'winedevice.exe', 'explorer.exe', 'services.exe', 'xalia.exe',
               'python', 'python3', 'python3.12', 'python3.13', 'bwrap', 'pv-bwrap', 'umu.exe'}
    return bool(is_wine and rss > 1024**3 and name.casefold() not in helpers)


def process_list(wine_only=True, detailed=False):
    processes = []
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit() or int(directory.name) == os.getpid() or identity(int(directory.name)) is None:
            continue
        try:
            pid = int(directory.name)
            status = {}
            for line in (directory / 'status').read_text().splitlines():
                key, _, value = line.partition(':'); status[key] = value.strip()
            if int(status.get('Tgid', pid)) != pid:
                continue
            rss = int(status.get('VmRSS', '0 kB').split()[0]) * 1024
            name = (directory / 'comm').read_text().strip()
            env = {}
            try:
                environment = (directory / 'environ').read_bytes()
            except OSError:
                environment = b''
            for item in environment.split(b'\0'):
                key, _, value = item.partition(b'=')
                if key in (b'WINEPREFIX', b'GE_WINE_SESSION_ID'):
                    env[key] = value.decode(errors='replace')
            prefix = env.get(b'WINEPREFIX', '')
            is_wine = bool(prefix or b'GE_WINE_SESSION_ID' in env)
            if is_wine:
                try:
                    for part in (directory / 'cmdline').read_bytes().split(b'\0'):
                        text = part.decode(errors='replace')
                        if text.casefold().endswith(('.exe', '.des')):
                            name = text.replace('\\', '/').rsplit('/', 1)[-1]
                            break
                except OSError:
                    pass
            cryengine = False
            if is_game_candidate(name,rss,is_wine):
                try:
                    cryengine = 'crygame.dll' in (directory / 'maps').read_text().casefold()
                except OSError:
                    pass
            if wine_only and not is_wine:
                continue
            candidate = is_game_candidate(name,rss,is_wine)
            processes.append(ProcessInfo(pid, name, prefix, rss, candidate, cryengine))
        except (OSError, ValueError):
            continue
    processes.sort(key=lambda p: p.pid)
    return processes if detailed else [(p.pid, p.name, p.prefix) for p in processes]


def preferred_game(processes):
    candidates = [p for p in processes if p.candidate]
    # A unique CryEngine candidate is stronger evidence than RSS alone.
    cry = [p for p in candidates if p.cryengine]
    return cry[0].pid if len(cry) == 1 else candidates[0].pid if len(candidates) == 1 else None


def launch_speed_program(executable, context_pid=None, expected_stamp=None):
    """Start through GE-Wine with clock support; preserve an explicitly selected prefix."""
    executable=Path(executable).expanduser().resolve()
    if not executable.is_file():
        raise ValueError('Programmdatei nicht gefunden.')
    command=[sys.executable,str(Path(__file__).with_name('ge_wine.py')),'--speed-control']
    if context_pid is not None:
        stamp=identity(context_pid)
        if stamp is None or expected_stamp is not None and stamp != expected_stamp:
            raise ProcessLookupError('Der ausgewählte Prozess wurde beendet.')
        environment={}
        for entry in Path(f'/proc/{context_pid}/environ').read_bytes().split(b'\0'):
            key,separator,value=entry.partition(b'=')
            if separator and key in (b'WINEPREFIX',b'PROTONPATH',b'GAMEID'):
                environment[key.decode()]=os.fsdecode(value)
        if identity(context_pid)!=stamp:
            raise ProcessLookupError('Der ausgewählte Prozess wurde beendet.')
        for key,flag in (('WINEPREFIX','--prefix'),('PROTONPATH','--proton'),('GAMEID','--gameid')):
            if environment.get(key):command.extend([flag,environment[key]])
    command.append(str(executable))
    token=uuid.uuid4().hex
    logs=Path(os.environ.get('XDG_STATE_HOME',Path.home()/'.local/state'))/'ge-memory/launches'
    logs.mkdir(parents=True,exist_ok=True,mode=0o700)
    # A regular file keeps the game independent of the lifetime of the GUI's output reader.
    with tempfile.NamedTemporaryFile(prefix=token+'-',suffix='.log',dir=logs,delete=False) as output:
        log=Path(output.name)
        try:
            process=subprocess.Popen(command,env=dict(os.environ,GE_MEMORY_LAUNCH_ID=token),
                                     stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        except BaseException:
            log.unlink(missing_ok=True)
            raise
    return process,token,executable.name.casefold(),log


def launched_speed_target(rows, token, executable):
    """Only auto-select processes belonging to this launch, with a usable clock controller."""
    owned=[]
    marker=('GE_MEMORY_LAUNCH_ID='+token).encode()
    for row in rows:
        try:
            if marker not in Path(f'/proc/{row.pid}/environ').read_bytes().split(b'\0'):
                continue
            SpeedControl(row.pid)
            owned.append(row)
        except (OSError,ValueError):
            continue
    game=preferred_game(owned)
    if game is not None:return game
    exact=[row.pid for row in owned if row.name.casefold()==executable]
    return exact[0] if len(exact)==1 else None

def gui(initial_pid=None):
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    import itertools
    import time
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise ValueError('Keine grafische Sitzung. Fuer die Prozessliste --list verwenden.') from error
    root.title('GE-Memory · Speicherinspektor')
    root.geometry('1280x780'); root.minsize(1120, 700)
    style = ttk.Style(root); style.theme_use('clam')
    palette = dict(bg='#111827', panel='#1f2937', field='#111827', text='#e5e7eb', muted='#9ca3af', accent='#60a5fa')
    root.configure(background=palette['bg'])
    style.configure('.', background=palette['panel'], foreground=palette['text'], font=('DejaVu Sans', 10))
    style.configure('TFrame', background=palette['bg'])
    style.configure('Card.TFrame', background=palette['panel'])
    style.configure('TLabel', background=palette['panel'])
    style.configure('Title.TLabel', background=palette['bg'], font=('DejaVu Sans', 22, 'bold'))
    style.configure('Subtitle.TLabel', background=palette['bg'], foreground=palette['muted'])
    style.configure('Section.TLabel', font=('DejaVu Sans', 11, 'bold'))
    style.configure('Muted.TLabel', foreground=palette['muted'])
    style.configure('TButton', padding=(12, 8), borderwidth=0)
    style.map('TButton', background=[('active', '#374151')], foreground=[('disabled', '#6b7280')])
    style.configure('Accent.TButton', background='#2563eb', foreground='white')
    style.map('Accent.TButton', background=[('disabled', '#374151'), ('active', '#1d4ed8')])
    style.configure('Horizontal.TProgressbar', background='#3b82f6', troughcolor=palette['panel'], borderwidth=0)
    style.configure('TEntry', fieldbackground=palette['field'], insertcolor=palette['text'], padding=7)
    style.configure('TCombobox', fieldbackground=palette['field'], padding=6, arrowsize=14)
    style.map('TCombobox', fieldbackground=[('readonly', palette['field'])], selectbackground=[('readonly', palette['field'])], selectforeground=[('readonly', palette['text'])])
    style.configure('TCheckbutton', background=palette['panel'], padding=4)
    style.map('TCheckbutton', background=[('active', palette['panel'])])
    style.configure('Treeview', background=palette['field'], fieldbackground=palette['field'], foreground=palette['text'], rowheight=30, borderwidth=0)
    style.configure('Treeview.Heading', background=palette['panel'], foreground=palette['muted'], padding=9, font=('DejaVu Sans', 9, 'bold'))
    style.map('Treeview', background=[('selected', '#1e40af')], foreground=[('selected', 'white')])
    root.option_add('*TCombobox*Listbox.background', palette['field'])
    root.option_add('*TCombobox*Listbox.foreground', palette['text'])
    root.columnconfigure(0, weight=1); root.rowconfigure(2, weight=1)
    header = ttk.Frame(root, padding=(22, 16)); header.grid(row=0, column=0, sticky='ew')
    ttk.Label(header, text='GE-Memory', style='Title.TLabel').pack(side='left')
    ttk.Label(header, text='Prozess wählen  →  Suchen  →  Werte bearbeiten', style='Subtitle.TLabel').pack(side='right')
    connection = ttk.Frame(root, style='Card.TFrame', padding=14)
    connection.grid(row=1, column=0, sticky='ew', padx=20, pady=(0, 14)); connection.columnconfigure(1, weight=1)
    ttk.Label(connection, text='01  Prozess', style='Section.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 14))
    wine_only = tk.BooleanVar(value=initial_pid is None)
    pid_value = tk.StringVar()
    process_box = ttk.Combobox(connection, textvariable=pid_value, state='readonly', width=60)
    process_box.grid(row=0, column=1, sticky='ew', padx=(0, 8))
    process_ids = {}; memory = None; results = {}; frozen = {}; live = {}; changed_at = {}
    visible_keys = []; row_keys = {}; events = queue.Queue(); cancel = threading.Event(); busy = False; watching = True
    page = 0; page_size = 500
    status = tk.StringVar(value='Spielerkennung bevorzugt GE-Wine-Hauptprozesse mit mehr als 1 GiB RSS.')
    kind = tk.StringVar(value=AUTO); mode = tk.StringVar(value='Gleich')
    value = tk.StringVar(value='100'); limit = tk.StringVar(value='0'); new_value = tk.StringVar(value='100')
    per_type_limit = tk.StringVar(value='100000')
    tolerance_text = tk.StringVar(value='0'); upper_text = tk.StringVar(value='1000')
    aligned = tk.BooleanVar(value=True); shared = tk.BooleanVar(value=False)
    type_filter = tk.StringVar(value='Alle Typen'); page_text = tk.StringVar(); selection_text = tk.StringVar(value='Treffer auswählen, um einen Wert zu bearbeiten.')
    live_enabled = tk.BooleanVar(value=True)
    speed_target = None; speed_modified = {}
    speed_launches = {}; next_launch_scan = 0
    speed_enabled = tk.BooleanVar(value=False)
    speed_factor = tk.StringVar(value='2'); speed_status = tk.StringVar(value='Zuerst einen Prozess auswählen.')
    speed_title = tk.StringVar(value='Kein Zielprozess')
    body = ttk.Frame(root); body.grid(row=2, column=0, sticky='nsew', padx=20)
    body.columnconfigure(1, weight=1); body.rowconfigure(0, weight=1)
    controls = ttk.Frame(body, style='Card.TFrame', padding=16, width=270)
    controls.grid(row=0, column=0, sticky='ns', padx=(0, 14)); controls.columnconfigure(0, weight=1)
    ttk.Label(controls, text='02  Suche', style='Section.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 16))
    ttk.Label(controls, text='Suchwert').grid(row=1, column=0, sticky='w')
    search_entry = ttk.Entry(controls, textvariable=value, width=24, name='search_value'); search_entry.grid(row=2, column=0, sticky='ew', pady=(5, 12))
    range_panel = ttk.Frame(controls, style='Card.TFrame'); range_panel.grid(row=8, column=0, sticky='ew', pady=(10, 0))
    ttk.Label(range_panel, text='Obergrenze', style='Muted.TLabel').pack(anchor='w')
    upper_entry = ttk.Entry(range_panel, textvariable=upper_text, width=22); upper_entry.pack(fill='x', pady=(5, 0))
    ttk.Label(controls, text='Datentyp · nur für die Erstsuche').grid(row=3, column=0, sticky='w')
    kind_box = ttk.Combobox(controls, textvariable=kind, values=[AUTO, *TYPES], state='readonly', width=22)
    kind_box.grid(row=4, column=0, sticky='ew', pady=(5, 12))
    ttk.Label(controls, text='Vergleich · nur für die Folgesuche').grid(row=5, column=0, sticky='w')
    mode_box = ttk.Combobox(controls, textvariable=mode, values=MODES, state='readonly', width=22)
    mode_box.grid(row=6, column=0, sticky='ew', pady=(5, 14))
    buttons = ttk.Frame(controls, style='Card.TFrame'); buttons.grid(row=7, column=0, sticky='ew'); buttons.columnconfigure(0, weight=1)
    options = tk.Toplevel(root); options.title('GE-Memory · Suchoptionen'); options.withdraw()
    options.configure(background=palette['panel']); options.resizable(False, False)
    options.protocol('WM_DELETE_WINDOW', options.withdraw)
    def show_options():
        options.deiconify(); options.lift()
    ttk.Button(controls, text='Suchoptionen …', command=show_options).grid(row=9, column=0, sticky='ew', pady=(16, 0))
    search_hint = ttk.Label(controls, text='Live zeigt den aktuellen Wert.\nLetzte Suche bleibt der Vergleich\nfür die nächste Folgesuche.', style='Muted.TLabel', justify='left')
    search_hint.grid(row=10, column=0, sticky='w', pady=(18, 0))
    advanced = ttk.Frame(options, style='Card.TFrame', padding=20); advanced.pack(fill='both', expand=True); advanced.columnconfigure(0, weight=1)
    ttk.Label(advanced, text='Suchbereich', style='Section.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 8))
    ttk.Label(advanced, text='Limit in MiB · 0 = gesamter Bereich', style='Muted.TLabel').grid(row=1, column=0, sticky='w')
    ttk.Entry(advanced, textvariable=limit, width=12).grid(row=2, column=0, sticky='ew', pady=(5, 10))
    ttk.Label(advanced, text='Maximale Treffer pro Datentyp', style='Muted.TLabel').grid(row=3, column=0, sticky='w')
    ttk.Entry(advanced, textvariable=per_type_limit, width=12).grid(row=4, column=0, sticky='ew', pady=(5, 8))
    ttk.Checkbutton(advanced, text='Ausgerichtete Adressen', variable=aligned).grid(row=5, column=0, sticky='w')
    ttk.Checkbutton(advanced, text='Gemeinsamen RAM einschließen', variable=shared).grid(row=6, column=0, sticky='w')
    ttk.Label(advanced, text='Float-Toleranz (absolut, 0 = exakt)').grid(row=8, column=0, sticky='w', pady=(12, 0))
    ttk.Entry(advanced, textvariable=tolerance_text).grid(row=9, column=0, sticky='ew', pady=(5, 0))
    ttk.Label(advanced, text='Toleranz in der Erstsuche ist langsamer.\nSie gilt nur für Float32 / Float64.', style='Muted.TLabel').grid(row=10, column=0, sticky='w', pady=(8, 0))
    ttk.Label(advanced, text='Automatisch prüft alle 11 Typen.\nGleiche Bytes können mehrere\nInterpretationen ergeben.', style='Muted.TLabel', justify='left').grid(row=7, column=0, sticky='w', pady=(12, 0))
    content = ttk.Frame(body, style='Card.TFrame', padding=14); content.grid(row=0, column=1, sticky='nsew')
    content.columnconfigure(0, weight=1); content.rowconfigure(1, weight=1)
    toolbar = ttk.Frame(content, style='Card.TFrame'); toolbar.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 12))
    ttk.Label(toolbar, text='03  Treffer', style='Section.TLabel').pack(side='left', padx=(0, 12))
    filter_box = ttk.Combobox(toolbar, textvariable=type_filter, values=['Alle Typen', *TYPES], width=14, state='readonly'); filter_box.pack(side='left')
    ttk.Checkbutton(toolbar, text='Live-Werte (300 ms)', variable=live_enabled).pack(side='right')
    tree = ttk.Treeview(content, columns=('address','type','value','baseline','initial','freeze'), show='headings', selectmode='extended', name='results')
    for column, label, width in [('address','Adresse',145),('type','Typ',80),('value','Aktuell',135),('baseline','Letzte Suche',135),('initial','Erstwert',135),('freeze','Fixiert',65)]:
        tree.heading(column, text=label); tree.column(column, width=width, minwidth=60, stretch=True)
    tree.tag_configure('changed', foreground='#fbbf24'); tree.tag_configure('unreadable', foreground='#f87171')
    tree.grid(row=1, column=0, sticky='nsew')
    scroll = ttk.Scrollbar(content, orient='vertical', command=tree.yview)
    scroll.grid(row=1,column=1,sticky='ns'); tree.configure(yscrollcommand=scroll.set)
    pagination = ttk.Frame(content, style='Card.TFrame'); pagination.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(10, 0))
    ttk.Label(pagination, textvariable=page_text, style='Muted.TLabel').pack(side='left')
    edit = ttk.Frame(content, style='Card.TFrame'); edit.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(16, 0))
    ttk.Separator(edit).grid(row=0, column=0, columnspan=5, sticky='ew', pady=(0, 12))
    ttk.Label(edit, textvariable=selection_text, style='Muted.TLabel').grid(row=1, column=0, columnspan=5, sticky='w', pady=(0, 8))
    ttk.Label(edit, text='Neuer Wert').grid(row=2, column=0, sticky='w', padx=(0, 8))
    write_entry = ttk.Entry(edit, textvariable=new_value, width=17, name='write_value'); write_entry.grid(row=2, column=1, sticky='ew', padx=(0, 8))
    edit.columnconfigure(1, weight=1)
    write_type = tk.StringVar(value='Der Datentyp des Treffers bleibt beim Schreiben erhalten.')
    ttk.Label(edit, textvariable=write_type, style='Muted.TLabel').grid(row=3, column=0, columnspan=5, sticky='w', pady=(8, 0))
    footer = ttk.Frame(root, padding=(20, 12)); footer.grid(row=3, column=0, sticky='ew'); footer.columnconfigure(0, weight=1)
    progress = ttk.Progressbar(footer, mode='indeterminate', value=0); progress.grid(row=0, column=0, sticky='ew', pady=(0, 8))
    ttk.Label(footer, textvariable=status, style='Subtitle.TLabel', wraplength=1150).grid(row=1, column=0, sticky='w')

    speed_window = tk.Toplevel(root); speed_window.withdraw(); speed_window.title('GE-Memory · Speedhack')
    speed_window.configure(background=palette['bg']); speed_window.resizable(False, False)
    speed_window.protocol('WM_DELETE_WINDOW', speed_window.withdraw)
    speed_panel = ttk.Frame(speed_window, style='Card.TFrame', padding=22); speed_panel.pack(fill='both', expand=True)
    ttk.Label(speed_panel, text='Speedhack', style='Section.TLabel').grid(row=0, column=0, columnspan=4, sticky='w')
    ttk.Label(speed_panel, textvariable=speed_title, style='Muted.TLabel').grid(row=1, column=0, columnspan=4, sticky='w', pady=(8, 16))
    ttk.Label(speed_panel, text='Geschwindigkeit').grid(row=2, column=0, sticky='w', padx=(0, 10))
    speed_entry = ttk.Entry(speed_panel, textvariable=speed_factor, width=12); speed_entry.grid(row=2, column=1, sticky='ew')
    ttk.Label(speed_panel, text='×  (0,05 bis 20)').grid(row=2, column=2, columnspan=2, sticky='w', padx=8)
    ttk.Label(speed_panel, textvariable=speed_status, style='Muted.TLabel', wraplength=490, justify='left').grid(row=5, column=0, columnspan=4, sticky='w', pady=(16, 8))
    ttk.Label(speed_panel, text='Experimentell: skaliert monotone Zeitabfragen.\nAndere Spieltimer und Serverzeit können unverändert bleiben.\nBeim Schließen von GE-Memory werden geänderte Faktoren auf 1× gesetzt.',
              style='Muted.TLabel', wraplength=490, justify='left').grid(row=6, column=0, columnspan=4, sticky='w', pady=(8, 0))

    def refresh_speed():
        nonlocal speed_target
        if memory is None:
            speed_target = None; speed_enabled.set(False); speed_status.set('Zuerst einen Prozess auswählen.'); speed_title.set('Kein Zielprozess')
        else:
            speed_title.set(f'Zielprozess: PID {memory.pid}')
            try:
                if speed_target is None or speed_target.pid != memory.pid or speed_target.stamp != memory.stamp:
                    speed_target = SpeedControl(memory.pid)
                    requested = speed_target.read()['requested']
                    speed_factor.set(f'{requested:g}' if requested != 1 else '2')
                info = speed_target.read()
                speed_enabled.set(info['requested'] != 1)
                if info['pending']:
                    speed_status.set(f"{info['requested']:g}× angefordert · wartet auf Zeitabfrage.\nZuletzt bestätigt: {info['applied']:g}× · Zeitabfragen: {info['calls']}")
                else:
                    speed_status.set(f"{'Aktiv' if info['requested'] != 1 else 'Deaktiviert'} · Faktor {info['applied']:g}× von der Zeitbibliothek bestätigt.\nZeitabfragen: {info['calls']} · Wirkung im Spiel bitte prüfen.")
            except (OSError, ValueError) as error:
                speed_target = None; speed_enabled.set(False)
                speed_status.set('Keine erreichbare Zeitbibliothek. Über „Programm starten …“ hier in GE-Memory neu starten.\n' + str(error))
        for widget in (speed_apply, speed_reset, speed_toggle, speed_inline_toggle, speed_inline_apply, *speed_presets):
            widget.configure(state='normal' if speed_target is not None and not busy else 'disabled')

    def set_speed(reset=False, preserve_factor=False):
        try:
            if memory is None:
                raise ValueError('Zuerst einen Prozess auswählen.')
            controller = SpeedControl(memory.pid)
            factor = 1 if reset else validate_factor(speed_factor.get())
            controller.set(factor)
            speed_modified[(controller.pid, controller.stamp)] = controller
            if not preserve_factor:
                speed_factor.set(f'{factor:g}')
            refresh_speed()
        except (OSError, ValueError) as error:
            refresh_speed()
            messagebox.showerror('Speedhack', str(error))

    def toggle_speed():
        set_speed(reset=not speed_enabled.get(), preserve_factor=True)

    def show_speed():
        refresh_speed()
        if speed_target is not None:
            try:
                requested = speed_target.read()['requested']
                if requested != 1:
                    speed_factor.set(f'{requested:g}')
            except (OSError, ValueError):
                refresh_speed()
        speed_window.deiconify(); speed_window.lift()

    speed_toggle = ttk.Checkbutton(speed_panel, text='Speedhack aktiv', variable=speed_enabled, command=toggle_speed)
    speed_toggle.grid(row=8, column=0, columnspan=4, sticky='w', pady=(12, 0))
    speed_apply = ttk.Button(speed_panel, text='Anwenden', command=set_speed, style='Accent.TButton')
    speed_apply.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(12, 0), padx=(0, 8))
    speed_reset = ttk.Button(speed_panel, text='Zurück auf 1×', command=lambda:set_speed(True, preserve_factor=True))
    speed_reset.grid(row=3, column=2, columnspan=2, sticky='ew', pady=(12, 0))
    speed_presets = []
    for index, factor in enumerate((.5, 1, 2)):
        def preset(value=factor):
            speed_factor.set(f'{value:g}'); set_speed()
        button = ttk.Button(speed_panel, text=f'{factor:g}×', command=preset)
        button.grid(row=4, column=index, sticky='ew', pady=(8, 0), padx=(0, 6))
        speed_presets.append(button)
    ttk.Button(speed_panel, text='Programm starten …', command=lambda:launch_program()).grid(row=7, column=0, columnspan=4, sticky='ew', pady=(16, 0))
    ttk.Button(header, text='Speedhack …', command=show_speed).pack(side='right', padx=(0, 18))
    speed_inline = ttk.Frame(connection, style='Card.TFrame')
    speed_inline.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(14, 0))
    speed_inline_toggle = ttk.Checkbutton(speed_inline, text='Speedhack aktiv', variable=speed_enabled, command=toggle_speed)
    speed_inline_toggle.pack(side='left', padx=(0, 16))
    ttk.Label(speed_inline, text='Faktor').pack(side='left', padx=(0, 8))
    speed_inline_entry = ttk.Entry(speed_inline, textvariable=speed_factor, width=10)
    speed_inline_entry.pack(side='left')
    ttk.Label(speed_inline, text='×  (0,05–20)').pack(side='left', padx=8)
    speed_inline_apply = ttk.Button(speed_inline, text='Faktor anwenden', command=set_speed)
    speed_inline_apply.pack(side='left', padx=8)
    speed_inline_entry.bind('<Return>', lambda event:set_speed())
    ttk.Label(connection, textvariable=speed_status, style='Muted.TLabel', wraplength=1000).grid(row=3, column=0, columnspan=4, sticky='w', pady=(6, 0))

    def error_message(error):
        if isinstance(error, PermissionError):
            return ('Linux verweigert Speicherzugriff. Programm über „Programm starten …“ neu starten; bereits laufende '
                    'Wine-Server koennen ausserhalb dieses Prozessbaums liegen. Keine System-Schutzregeln werden geaendert.')
        return str(error)

    def display():
        nonlocal visible_keys, row_keys
        selected = tree.selection(); tree.delete(*tree.get_children())
        filtered = [key for key in results if type_filter.get() == 'Alle Typen' or key[1] == type_filter.get()]
        # Interleave types so automatic searches remain useful on the first page.
        groups = [[key for key in filtered if key[1] == name] for name in TYPES]
        ordered = [key for group in itertools.zip_longest(*groups) for key in group if key is not None]
        nonlocal page
        pages = max(1, (len(ordered)+page_size-1)//page_size)
        page = min(page, pages-1)
        visible_keys = ordered[page*page_size:(page+1)*page_size]
        page_text.set(f'{len(filtered):,} von {len(results):,} Treffern · Seite {page+1}/{pages}')
        prev_button.configure(state='normal' if page > 0 else 'disabled')
        next_button.configure(state='normal' if page+1 < pages else 'disabled')
        row_keys = {}
        for key in visible_keys:
            hit = results[key]; iid = f'{hit.address}:{hit.kind}'; row_keys[iid] = key
            tree.insert('','end',iid=iid,values=(hex(hit.address),hit.kind,show_value(live.get(key,hit.baseline),hit.kind),
                        show_value(hit.baseline,hit.kind),show_value(hit.initial,hit.kind),'Ja' if key in frozen else ''))
        tree.selection_set([item for item in selected if tree.exists(item)])
        selection_changed(); sync_controls()

    def choose_process():
        nonlocal memory, results, live, watching
        if busy:
            return
        frozen.clear(); results = {}; live = {}; changed_at.clear(); watching = True
        if memory:
            memory.close(); memory = None
        try:
            memory = ProcessMemory(process_ids[pid_value.get()])
            status.set(f'PID {memory.pid} ausgewaehlt. Erstsuche starten.')
        except (OSError,KeyError,ValueError) as error:
            status.set(error_message(error))
        display()

    def refresh(auto=False):
        nonlocal process_ids
        if busy:
            return
        current = memory.pid if memory else initial_pid
        rows = process_list(wine_only.get(), detailed=True)
        rows.sort(key=lambda p: (not p.candidate, not p.cryengine, -p.rss, p.pid))
        process_ids = {f'{p.pid}  {p.name}  {p.rss/1024**3:.2f} GiB' +
                       ('  [Spiel-Kandidat]' if p.candidate else '') + f'  {p.prefix}':p.pid for p in rows}
        process_box['values'] = list(process_ids)
        detected = preferred_game(rows) if auto else None
        target = detected if detected is not None else current
        label = next((label for label,pid in process_ids.items() if pid == target),'')
        pid_value.set(label)
        if label and (memory is None or memory.pid != target):
            choose_process()
        if auto and detected is None:
            status.set('Kein eindeutiger Spielprozess >1 GiB erkannt. Hauptprozess bitte selbst auswaehlen.')

    def start_scan(first):
        nonlocal busy, results, live
        if busy:
            return
        if memory is None:
            messagebox.showerror('Kein Prozess','Zuerst einen Prozess auswaehlen.'); return
        try:
            kinds = list(TYPES) if first and kind.get() == AUTO else [kind.get()] if first else set(h.kind for h in results.values())
            patterns = patterns_for(value.get(), kinds) if first or mode.get() in VALUE_MODES else {}
            upper_patterns = patterns_for(upper_text.get(), kinds) if not first and mode.get() == 'Zwischen' else None
            tolerance = float(tolerance_text.get().replace(',', '.'))
            if not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError('Float-Toleranz muss endlich und mindestens 0 sein.')
            if upper_patterns is not None and any(kind not in upper_patterns or unpack_value(data, kind) > unpack_value(upper_patterns[kind], kind) for kind, data in patterns.items()):
                raise ValueError('Obergrenze muss mindestens der Untergrenze entsprechen.')
            max_bytes = int(limit.get()) * 1024**2
            hit_budget = int(per_type_limit.get())
            if not 100 <= hit_budget <= 1000000:
                raise ValueError('Trefferlimit pro Typ: 100 bis 1000000.')
            if not 0 <= max_bytes <= 65536 * 1024**2:
                raise ValueError('Scanlimit: 0 (gesamt) bis 65536 MiB.')
            if not first and not results:
                raise ValueError('Keine Treffer fuer eine Folgesuche vorhanden.')
        except ValueError as error:
            messagebox.showerror('Suche',str(error)); return
        if first:
            frozen.clear(); results = {}; live = {}; changed_at.clear(); display()
        cancel.clear(); busy = True; sync_controls(); progress.start(12)
        previous = dict(results); target = memory; chosen_mode = mode.get()
        use_alignment = aligned.get(); use_shared = shared.get()
        status.set('Suche laeuft ...')

        def worker():
            last_progress = [0.0]
            def report_progress(count):
                now = time.monotonic()
                if now-last_progress[0]>=0.1:
                    events.put(('progress',f'{count//1024**2} MiB geprueft ...'))
                    last_progress[0] = now
            try:
                if first:
                    outcome = scan_types(target,patterns,max_bytes,cancel,
                        report_progress,
                        aligned=use_alignment,include_shared=use_shared,max_hits=hit_budget*len(patterns),float_tolerance=tolerance)
                    matches = outcome.hits
                    note = f'{len(matches)} typisierte Treffer; {outcome.scanned//1024**2} MiB geprueft; {outcome.skipped//1024} KiB uebersprungen.'
                    if outcome.capped_types:
                        note += ' Trefferlimit: ' + ', '.join(sorted(outcome.capped_types)) + '.'
                    if outcome.budget_limited:
                        note += ' Speicherlimit erreicht.'
                else:
                    matches = refine_hits(target,previous,chosen_mode,patterns,cancel,upper=upper_patterns,float_tolerance=tolerance)
                    note = f'{len(matches)} Treffer nach Folgesuche.'
                if cancel.is_set():
                    note += ' Abgebrochen; Teilergebnisse.'
                events.put(('result',(matches,note)))
            except (OSError,ValueError) as error:
                events.put(('error',error))
        threading.Thread(target=worker,daemon=True).start()

    def selected_keys():
        return [row_keys[item] for item in tree.selection() if item in row_keys]

    def write_values(freeze=False):
        nonlocal watching
        if busy or memory is None:
            return
        try:
            keys = selected_keys()
            if freeze and len(frozen.keys() | set(keys))>64:
                raise ValueError('Maximal 64 eingefrorene Treffer gleichzeitig.')
            encoded, actual = write_hits(memory,results,keys,new_value.get(),frozen)
            live.update(actual); watching = True
            for key,data in encoded.items():
                if freeze or key in frozen:
                    frozen[key] = data
                changed_at[key] = time.monotonic()
            mismatch = sum(actual[key] != data for key,data in encoded.items())
            status.set(f'{len(encoded)} typgerecht geschrieben und zurueckgelesen.' +
                       (f' {mismatch} Werte bereits abweichend/nicht lesbar.' if mismatch else ' Ruecklesen stimmt ueberein.') +
                       (' Einfrieren aktiv.' if freeze else ''))
            update_rows(keys); sync_controls()
        except (OSError,ValueError) as error:
            messagebox.showerror('Speicherzugriff',error_message(error))

    def update_rows(keys):
        for key in keys:
            hit = results.get(key); iid = f'{key[0]}:{key[1]}'
            if hit is None or not tree.exists(iid):
                continue
            data = live.get(key,hit.baseline)
            tree.set(iid,'value',show_value(data,hit.kind)); tree.set(iid,'freeze','Ja' if key in frozen else '')
            tree.item(iid,tags=('unreadable',) if data is None else
                      ('changed',) if time.monotonic()-changed_at.get(key,-100)<1.5 else ())

    def unfreeze():
        keys = selected_keys()
        for key in keys:
            frozen.pop(key,None)
        update_rows(keys); sync_controls()
        status.set(f'{len(keys)} ausgewählte Treffer freigegeben.')

    def selection_changed(event=None):
        keys = selected_keys()
        types = sorted({key[1] for key in keys})
        selection_text.set(f'{len(keys)} ausgewählt · {len(frozen)} fixiert' if keys else 'Treffer auswählen, um einen Wert zu bearbeiten.')
        sync_controls()
        write_type.set('Schreibtyp: ' + ', '.join(types) if types else 'Schreibtyp: jeweiliger Treffer')
        if len(keys)==1:
            key = keys[0]; data = live.get(key,results[key].baseline)
            if data is not None:
                number = unpack_value(data,key[1])
                new_value.set(str(int(number)) if key[1]=='Bool' else str(number))

    def launch_program():
        executable=filedialog.askopenfilename(parent=root,title='Programm mit Speedhack-Unterstützung starten',
                                             filetypes=[('Windows-Programme','*.exe'),('Alle Dateien','*')])
        if not executable:return
        try:
            process,token,name,log=launch_speed_program(executable,memory.pid if memory else None,memory.stamp if memory else None)
            speed_launches[token]=dict(process=process,name=name,lines=[],auto=True,selected=None,log=log)
            status.set('Programm wird gestartet. Speedhack-Vorbereitung und Prozessauswahl erfolgen automatisch …')
            def watch():
                with log.open(encoding='utf-8',errors='replace') as output:
                    while True:
                        line=output.readline()
                        if line:
                            events.put(('speed_launch_output',(token,line.rstrip())))
                        elif process.poll() is not None:
                            break
                        else:
                            time.sleep(.2)
                events.put(('speed_launch_exit',(token,process.wait())))
            threading.Thread(target=watch,daemon=True).start()
        except (OSError,ValueError) as error:
            messagebox.showerror('Programm starten',str(error))

    def sync_controls():
        process_box.configure(state='disabled' if busy else 'readonly')
        for button in (refresh_button, detect_button, launch_button):
            button.configure(state='disabled' if busy else 'normal')
        first_button.configure(state='normal' if memory is not None and not busy else 'disabled')
        refine_button.configure(state='normal' if results and not busy else 'disabled')
        stop_button.configure(state='normal' if busy else 'disabled')
        mode_box.configure(state='readonly' if results and not busy else 'disabled')
        if mode.get() == 'Zwischen' and results:
            range_panel.grid(); search_hint.grid_remove()
        else:
            range_panel.grid_remove(); search_hint.grid()
        kind_box.configure(state='disabled' if busy else 'readonly')
        upper_entry.configure(state='disabled' if busy else 'normal')
        keys = selected_keys()
        for widget in (write_entry, write_button, freeze_button):
            widget.configure(state='normal' if keys and not busy else 'disabled')
        release_button.configure(state='normal' if any(key in frozen for key in keys) and not busy else 'disabled')

    def change_page(delta=0):
        nonlocal page
        page = max(0, page+delta) if delta else 0
        display()

    def copy_address(event=None):
        keys = selected_keys()
        if keys:
            root.clipboard_clear(); root.clipboard_append('\n'.join(hex(key[0]) for key in keys))
            status.set(f'{len(keys)} Adressen kopiert.')
        return 'break'

    refresh_button = ttk.Button(connection, text='Aktualisieren', command=refresh); refresh_button.grid(row=0, column=2)
    detect_button = ttk.Button(connection, text='Spiel erkennen', command=lambda:refresh(True)); detect_button.grid(row=0, column=3, padx=(8, 0))
    ttk.Checkbutton(connection, text='Nur GE-Wine / Wine', variable=wine_only, command=refresh).grid(row=1, column=1, sticky='w', pady=(8, 0))
    launch_button=ttk.Button(connection, text='Programm starten …', command=launch_program, style='Accent.TButton')
    launch_button.grid(row=1, column=2, columnspan=2, sticky='e', pady=(8, 0))
    first_button = ttk.Button(buttons, text='Erstsuche', command=lambda:start_scan(True), style='Accent.TButton'); first_button.grid(row=0, column=0, sticky='ew', pady=(0, 6))
    refine_button = ttk.Button(buttons, text='Folgesuche', command=lambda:start_scan(False)); refine_button.grid(row=1, column=0, sticky='ew', pady=(0, 6))
    stop_button = ttk.Button(buttons, text='Suche abbrechen', command=cancel.set); stop_button.grid(row=2, column=0, sticky='ew')
    prev_button = ttk.Button(pagination, text='Zurück', command=lambda:change_page(-1)); prev_button.pack(side='left', padx=(12, 6))
    next_button = ttk.Button(pagination, text='Weiter', command=lambda:change_page(1)); next_button.pack(side='left')
    write_button = ttk.Button(edit, text='Schreiben', command=write_values, style='Accent.TButton'); write_button.grid(row=2, column=2, padx=(0, 6))
    freeze_button = ttk.Button(edit, text='Einfrieren', command=lambda:write_values(True)); freeze_button.grid(row=2, column=3, padx=(0, 6))
    release_button = ttk.Button(edit, text='Freigeben', command=unfreeze); release_button.grid(row=2, column=4)
    # Semantic handles also allow UI tests without depending on the layout.
    root.memory_widgets = dict(tree=tree, search=search_entry, write=write_entry, first=first_button,
                               refine=refine_button, freeze=freeze_button, release=release_button,
                               filter=filter_box, next=next_button, previous=prev_button, live=live_enabled,
                               mode=mode_box, upper=upper_entry, tolerance=tolerance_text,
                               speed_apply=speed_apply, speed_reset=speed_reset, speed_factor=speed_factor,
                               speed_enabled=speed_enabled, speed_toggle=speed_inline_toggle,
                               speed_inline=speed_inline, speed_inline_apply=speed_inline_apply, speed_launch=launch_button,
                               speed_status=speed_status, speed_window=speed_window)
    def manual_process(event=None):
        for launch in speed_launches.values():launch['auto']=False
        choose_process()
    process_box.bind('<<ComboboxSelected>>',manual_process)
    mode_box.bind('<<ComboboxSelected>>', lambda event:sync_controls())
    filter_box.bind('<<ComboboxSelected>>', lambda event:change_page())
    tree.bind('<<TreeviewSelect>>',selection_changed)
    tree.bind('<Control-c>', copy_address)
    search_entry.bind('<Return>', lambda event:start_scan(not bool(results)))
    root.bind('<Escape>', lambda event:cancel.set() if busy else None)
    root.bind('<Control-r>', lambda event:refresh())
    root.bind('<Control-f>', lambda event:search_entry.focus_set())

    def poll():
        nonlocal busy, results, live, watching, next_launch_scan
        for _ in range(200):
            try:
                action,data = events.get_nowait()
            except queue.Empty:
                break
            if action=='speed_launch_output':
                token,line=data
                if token in speed_launches:
                    lines=speed_launches[token]['lines'];lines.append(line);del lines[:-20]
                    if speed_launches[token]['selected'] is None:status.set(line)
                continue
            if action=='speed_launch_exit':
                token,result=data
                if result:
                    launch=speed_launches.get(token,{})
                    messagebox.showerror('Programmstart fehlgeschlagen',f'GE-Wine wurde mit Code {result} beendet.\nStartprotokoll: {launch.get("log","")}\n'+'\n'.join(launch.get('lines',[])[-10:]))
                # Descendants may survive the launching executable; keep their launch marker.
                continue
            if action=='progress':
                status.set(data)
            else:
                busy = False; progress.stop(); sync_controls()
                if action=='result':
                    results,note = data
                    discarded = set(frozen)-set(results)
                    for key in discarded:
                        frozen.pop(key,None)
                    if discarded:
                        note += f' {len(discarded)} weggefilterte Freeze-Treffer freigegeben.'
                    changed_at.clear()
                    live = {key:hit.baseline for key,hit in results.items()}
                    display(); status.set(note)
                else:
                    status.set(error_message(data))
        if memory is not None and watching:
            try:
                for key,data in list(frozen.items()):
                    memory.write(key[0],data)
                if visible_keys and live_enabled.get():
                    # Refresh viewport and selected hits, not all 100k scan candidates.
                    top = int(tree.yview()[0]*len(visible_keys))
                    count = max(5, tree.winfo_height()//18+5)
                    keys = set(visible_keys[max(0,top-2):top+count]) | set(selected_keys())
                    samples = live_read(memory,{key:results[key] for key in keys if key in results})
                    for key,data in samples.items():
                        if data != live.get(key):
                            changed_at[key] = time.monotonic()
                    live.update(samples); update_rows(samples)
            except (OSError,ValueError) as error:
                frozen.clear(); watching = False
                for key in visible_keys:
                    live[key] = None
                update_rows(visible_keys)
                status.set('Speicherzugriff beendet: '+error_message(error))
        if speed_launches and not busy and time.monotonic() >= next_launch_scan:
            next_launch_scan=time.monotonic()+1
            rows=process_list(True,detailed=True)
            for token,launch in speed_launches.items():
                if not launch['auto']:continue
                target=launched_speed_target(rows,token,launch['name'])
                if target is not None and target != launch['selected']:
                    refresh()
                    label=next((label for label,pid in process_ids.items() if pid==target),'')
                    if label:
                        pid_value.set(label);choose_process();launch['selected']=target
                        status.set('Gestarteter Prozess ausgewählt. Speedhack jetzt hier aktivieren und Faktor einstellen.')
        refresh_speed()
        root.after(300,poll)

    def close():
        for controller in speed_modified.values():
            try:
                controller.set(1)
            except (OSError, ValueError):
                pass
        cancel.set(); frozen.clear()
        if memory:
            memory.close()
        root.destroy()

    refresh(auto=initial_pid is None)
    display()
    refresh_speed()
    root.protocol('WM_DELETE_WINDOW',close)
    root.after(300,poll)
    root.mainloop()
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(description='Nativer Linux-Speicherinspektor fuer GE-Wine und eigene Prozesse.')
    parser.add_argument('--list', action='store_true', help='Eigene Prozesse auflisten')
    parser.add_argument('--pid', type=int, help='Prozess in der GUI vorauswaehlen')
    parser.add_argument('--speed', help='Speedhack-Faktor für einen mit --speed-control gestarteten --pid-Prozess setzen (0,05–20)')
    parser.add_argument('--speed-status', action='store_true', help='Bestätigten Speedhack-Zustand für --pid als JSON anzeigen')
    parser.add_argument('--random-name', action='store_true', help='Kosmetischer Zufallsname des Linux-Prozesses; keine Tarnung')
    options = parser.parse_args(argv)
    if (options.speed is not None or options.speed_status) and options.pid is None:
        parser.error('--speed und --speed-status benötigen --pid')
    if options.random_name:
        ctypes.CDLL(None).prctl(15, ctypes.c_char_p(uuid.uuid4().hex[:15].encode()), 0, 0, 0)
    try:
        if options.speed is not None or options.speed_status:
            controller = SpeedControl(options.pid)
            info = controller.set(options.speed) if options.speed is not None else controller.read()
            print(json.dumps(info, ensure_ascii=False))
            return 0
        if options.list:
            for pid, name, prefix in process_list(False):
                print(f'{pid}\t{name}\t{prefix}')
            return 0
        return gui(options.pid)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f'GE-Memory: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
