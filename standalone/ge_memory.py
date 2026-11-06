#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Linux numeric memory inspector for user-owned processes."""
import argparse
import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import uuid

from ge_wine_sessions import identity

TYPES = {'Int8': '<b', 'UInt8': '<B', 'Int16': '<h', 'UInt16': '<H',
         'Int32': '<i', 'UInt32': '<I', 'Int64': '<q', 'UInt64': '<Q',
         'Float32': '<f', 'Float64': '<d', 'Bool': '<?'}
AUTO = 'Automatisch'
MODES = ('Gleich', 'Geaendert', 'Unveraendert', 'Gestiegen', 'Gesunken')


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
               regions=None, max_hits=100000, aligned=True, include_shared=False):
    if not patterns or max_bytes < 0 or max_hits < len(patterns):
        raise ValueError('Ungueltige Suchparameter.')
    regions = list(memory.regions() if regions is None else regions)
    regions = [r for r in regions if r.permissions[:2] == 'rw' and
               (include_shared or r.permissions.endswith('p')) and (not r.name.startswith('/dev/') or r.name.startswith('/dev/shm/'))]
    eligible = sum(r.end - r.start for r in regions)
    budget = max_bytes or eligible
    quota = max_hits // len(patterns)
    counts = {kind: 0 for kind in patterns}
    grouped = {}
    for kind, pattern in patterns.items():
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


def refine_hits(memory, previous, mode, patterns, cancel):
    if mode not in MODES:
        raise ValueError('Unbekannter Vergleich.')
    result = {}
    for key, current in sampled_hits(memory,previous,cancel):
        if current is None:
            continue
        hit = previous[key]
        if mode == 'Gleich':
            keep = current == patterns.get(hit.kind)
        elif mode == 'Geaendert':
            keep = current != hit.baseline
        elif mode == 'Unveraendert':
            keep = current == hit.baseline
        elif mode == 'Gestiegen':
            keep = unpack_value(current, hit.kind) > unpack_value(hit.baseline, hit.kind)
        else:
            keep = unpack_value(current, hit.kind) < unpack_value(hit.baseline, hit.kind)
        if keep:
            result[key] = Hit(hit.address,hit.kind,current,hit.initial)
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

def gui(initial_pid=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    import itertools
    import time
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise ValueError('Keine grafische Sitzung. Fuer die Prozessliste --list verwenden.') from error
    root.title('GE-Memory - Live-Speicherinspektor')
    root.geometry('1150x740')
    root.columnconfigure(1, weight=1); root.rowconfigure(5, weight=1)
    wine_only = tk.BooleanVar(value=initial_pid is None)
    pid_value = tk.StringVar()
    process_box = ttk.Combobox(root, textvariable=pid_value, state='readonly', width=80)
    ttk.Label(root, text='Hauptprozess').grid(row=0, column=0, padx=10, pady=8)
    process_box.grid(row=0, column=1, sticky='ew')
    process_ids = {}; memory = None; results = {}; frozen = {}; live = {}; changed_at = {}
    visible_keys = []; row_keys = {}; events = queue.Queue(); cancel = threading.Event(); busy = False; watching = True
    status = tk.StringVar(value='Spielerkennung bevorzugt GE-Wine-Hauptprozesse mit mehr als 1 GiB RSS.')
    kind = tk.StringVar(value=AUTO); mode = tk.StringVar(value='Gleich')
    value = tk.StringVar(value='100'); limit = tk.StringVar(value='0'); new_value = tk.StringVar(value='100')
    per_type_limit = tk.StringVar(value='100000')
    aligned = tk.BooleanVar(value=True); shared = tk.BooleanVar(value=False)
    controls = ttk.Frame(root); controls.grid(row=3, column=0, columnspan=3, sticky='ew', padx=10, pady=8)
    ttk.Label(controls, text='Suchtyp').pack(side='left')
    ttk.Combobox(controls, textvariable=kind, values=[AUTO, *TYPES], state='readonly', width=14).pack(side='left', padx=6)
    ttk.Label(controls, text='Suchwert').pack(side='left')
    ttk.Entry(controls, textvariable=value, width=16).pack(side='left', padx=6)
    ttk.Combobox(controls, textvariable=mode, values=MODES, state='readonly', width=14).pack(side='left')
    ttk.Label(controls, text='Limit MiB (0=gesamt)').pack(side='left', padx=6)
    ttk.Entry(controls, textvariable=limit, width=7).pack(side='left')
    ttk.Label(controls,text='Treffer/Typ').pack(side='left',padx=6)
    ttk.Entry(controls,textvariable=per_type_limit,width=9).pack(side='left')
    tree = ttk.Treeview(root, columns=('address','type','value','baseline','initial','freeze'), show='headings', selectmode='extended')
    for column, label, width in [('address','Adresse (hex)',155),('type','Typ',90),('value','Aktueller Wert',190),
                                 ('baseline','Letzte Suche',180),('initial','Erstwert',180),('freeze','Eingefroren',90)]:
        tree.heading(column, text=label); tree.column(column, width=width)
    tree.tag_configure('changed', foreground='#b05a00')
    tree.tag_configure('unreadable', foreground='#a02020')
    tree.grid(row=5, column=0, columnspan=2, sticky='nsew', padx=(10,0))
    scroll = ttk.Scrollbar(root, orient='vertical', command=tree.yview)
    scroll.grid(row=5,column=2,sticky='ns',padx=(0,10)); tree.configure(yscrollcommand=scroll.set)

    def error_message(error):
        if isinstance(error, PermissionError):
            return ('Linux verweigert Speicherzugriff. GE-Wine ueber dieses Fenster neu starten; bereits laufende '
                    'Wine-Server koennen ausserhalb dieses Prozessbaums liegen. Keine System-Schutzregeln werden geaendert.')
        return str(error)

    def display():
        nonlocal visible_keys, row_keys
        selected = tree.selection(); tree.delete(*tree.get_children())
        groups = {name:[] for name in TYPES}
        for key in results:
            if len(groups[key[1]])<2000:
                groups[key[1]].append(key)
        grouped = list(groups.values())
        visible_keys = []
        for group in itertools.zip_longest(*grouped):
            for key in group:
                if key is not None:
                    visible_keys.append(key)
                    if len(visible_keys) == 2000:
                        break
            if len(visible_keys) == 2000:
                break
        row_keys = {}
        for key in visible_keys:
            hit = results[key]; iid = f'{hit.address}:{hit.kind}'; row_keys[iid] = key
            tree.insert('','end',iid=iid,values=(hex(hit.address),hit.kind,show_value(live.get(key,hit.baseline),hit.kind),
                        show_value(hit.baseline,hit.kind),show_value(hit.initial,hit.kind),'Ja' if key in frozen else ''))
        tree.selection_set([item for item in selected if tree.exists(item)])

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
        target = current if current else preferred_game(rows) if auto else None
        label = next((label for label,pid in process_ids.items() if pid == target),'')
        pid_value.set(label)
        if label and memory is None:
            choose_process()
        elif auto and not label:
            status.set('Kein eindeutiger Spielprozess >1 GiB erkannt. Hauptprozess bitte selbst auswaehlen.')

    def start_scan(first):
        nonlocal busy, results, live
        if busy:
            return
        if memory is None:
            messagebox.showerror('Kein Prozess','Zuerst einen Prozess auswaehlen.'); return
        try:
            kinds = list(TYPES) if first and kind.get() == AUTO else [kind.get()] if first else set(h.kind for h in results.values())
            patterns = patterns_for(value.get(), kinds) if first or mode.get() == 'Gleich' else {}
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
        cancel.clear(); busy = True; process_box.configure(state='disabled')
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
                        aligned=use_alignment,include_shared=use_shared,max_hits=hit_budget*len(patterns))
                    matches = outcome.hits
                    note = f'{len(matches)} typisierte Treffer; {outcome.scanned//1024**2} MiB geprueft; {outcome.skipped//1024} KiB uebersprungen.'
                    if outcome.capped_types:
                        note += ' Trefferlimit: ' + ', '.join(sorted(outcome.capped_types)) + '.'
                    if outcome.budget_limited:
                        note += ' Speicherlimit erreicht.'
                else:
                    matches = refine_hits(target,previous,chosen_mode,patterns,cancel)
                    note = f'{len(matches)} Treffer nach Folgesuche.'
                if cancel.is_set():
                    note += ' Abgebrochen; Teilergebnisse.'
                if len(matches)>2000:
                    note += ' 2000 Treffer nach Typen verteilt sichtbar.'
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
            update_rows(keys)
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
        update_rows(keys)

    def selection_changed(event=None):
        keys = selected_keys()
        types = sorted({key[1] for key in keys})
        write_type.set('Schreibtyp: ' + ', '.join(types) if types else 'Schreibtyp: jeweiliger Treffer')
        if len(keys)==1:
            key = keys[0]; data = live.get(key,results[key].baseline)
            if data is not None:
                number = unpack_value(data,key[1])
                new_value.set(str(int(number)) if key[1]=='Bool' else str(number))

    def launch_ge_wine():
        try:
            subprocess.Popen([sys.executable,str(Path(__file__).with_name('ge_wine.py')),'--gui'],start_new_session=True)
            status.set('GE-Wine geoeffnet. Anwendung dort starten, danach Spiel erkennen.')
        except OSError as error:
            messagebox.showerror('GE-Wine',str(error))

    ttk.Button(root,text='Aktualisieren',command=refresh).grid(row=0,column=2,padx=10)
    filters = ttk.Frame(root); filters.grid(row=1,column=1,sticky='w')
    ttk.Checkbutton(filters,text='Nur GE-Wine/Wine',variable=wine_only,command=refresh).pack(side='left')
    ttk.Button(filters,text='Spiel erkennen (>1 GiB)',command=lambda:refresh(True)).pack(side='left',padx=8)
    ttk.Checkbutton(filters,text='Ausgerichtete Suche',variable=aligned).pack(side='left')
    ttk.Checkbutton(filters,text='Gemeinsamen RAM einschliessen',variable=shared).pack(side='left',padx=6)
    ttk.Button(root,text='GE-Wine starten',command=launch_ge_wine).grid(row=1,column=2,padx=10)
    ttk.Label(root,text='Mehrere Typen koennen dieselben Bytes passend interpretieren. Fuer Schreiben den passenden Treffer waehlen.',
              wraplength=1100).grid(row=2,column=0,columnspan=3,padx=10,sticky='w')
    buttons = ttk.Frame(root); buttons.grid(row=4,column=0,columnspan=3,sticky='w',padx=10,pady=8)
    ttk.Button(buttons,text='Erstsuche',command=lambda:start_scan(True)).pack(side='left',padx=4)
    ttk.Button(buttons,text='Folgesuche',command=lambda:start_scan(False)).pack(side='left',padx=4)
    ttk.Button(buttons,text='Suche abbrechen',command=cancel.set).pack(side='left',padx=4)
    edit = ttk.Frame(root); edit.grid(row=6,column=0,columnspan=3,sticky='w',padx=10,pady=8)
    ttk.Label(edit,text='Neuer Wert').pack(side='left'); ttk.Entry(edit,textvariable=new_value,width=18).pack(side='left',padx=5)
    ttk.Button(edit,text='Schreiben',command=write_values).pack(side='left',padx=4)
    ttk.Button(edit,text='Einfrieren',command=lambda:write_values(True)).pack(side='left',padx=4)
    ttk.Button(edit,text='Freigeben',command=unfreeze).pack(side='left',padx=4)
    write_type = tk.StringVar(value='Schreibtyp: jeweiliger Treffer')
    ttk.Label(edit,textvariable=write_type).pack(side='left',padx=10)
    ttk.Label(root,textvariable=status,wraplength=1100).grid(row=7,column=0,columnspan=3,sticky='w',padx=10,pady=8)
    process_box.bind('<<ComboboxSelected>>',lambda event:choose_process())
    tree.bind('<<TreeviewSelect>>',selection_changed)

    def poll():
        nonlocal busy, results, live, watching
        for _ in range(200):
            try:
                action,data = events.get_nowait()
            except queue.Empty:
                break
            if action=='progress':
                status.set(data)
            else:
                busy = False; process_box.configure(state='readonly')
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
                if visible_keys:
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
        root.after(300,poll)

    def close():
        cancel.set(); frozen.clear()
        if memory:
            memory.close()
        root.destroy()

    refresh(auto=True)
    root.protocol('WM_DELETE_WINDOW',close)
    root.after(300,poll)
    root.mainloop()
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(description='Nativer Linux-Speicherinspektor fuer GE-Wine und eigene Prozesse.')
    parser.add_argument('--list', action='store_true', help='Eigene Prozesse auflisten')
    parser.add_argument('--pid', type=int, help='Prozess in der GUI vorauswaehlen')
    parser.add_argument('--random-name', action='store_true', help='Kosmetischer Zufallsname des Linux-Prozesses; keine Tarnung')
    options = parser.parse_args(argv)
    if options.random_name:
        ctypes.CDLL(None).prctl(15, ctypes.c_char_p(uuid.uuid4().hex[:15].encode()), 0, 0, 0)
    try:
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
