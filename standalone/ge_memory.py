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

TYPES = {'Int32': '<i', 'Int64': '<q', 'Float32': '<f', 'Float64': '<d'}
MODES = ('Gleich', 'Geaendert', 'Unveraendert', 'Gestiegen', 'Gesunken')


def pack_value(text, kind):
    try:
        value = float(text.replace(',', '.')) if kind.startswith('Float') else int(text, 16 if text.lower().startswith(('0x', '-0x')) else 10)
        return struct.pack(TYPES[kind], value)
    except (ValueError, OverflowError, struct.error, KeyError) as error:
        raise ValueError(f'Ungueltiger Wert fuer {kind}: {text}') from error


def unpack_value(data, kind):
    return struct.unpack(TYPES[kind], data)[0]


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
        if len(data) not in (4, 8):
            raise ValueError('Es werden nur 4- oder 8-Byte-Zahlen geschrieben.')
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


def first_scan(memory, pattern, max_bytes, cancel, progress=lambda n: None, regions=None, max_hits=100000):
    """Exact, naturally aligned values; bounded private writable RAM scan."""
    if not pattern or max_bytes <= 0:
        raise ValueError('Suchwert und Speicherlimit muessen positiv sein.')
    matches = {}
    scanned = skipped = 0
    size = len(pattern)
    regions = memory.regions() if regions is None else regions
    for region in regions:
        if region.permissions[:2] != 'rw' or not region.permissions.endswith('p'):
            continue
        address = region.start
        tail = b''
        while address < region.end and scanned < max_bytes:
            if cancel.is_set():
                return matches, scanned, skipped, True
            count = min(1024 * 1024, region.end - address, max_bytes - scanned)
            try:
                data = memory.read(address, count)
            except PermissionError:
                raise
            except ProcessLookupError:
                raise
            except OSError:
                skipped += count
                address += count
                scanned += count
                tail = b''
                continue
            block = tail + data
            base = address - len(tail)
            offset = block.find(pattern)
            while offset != -1:
                found = base + offset
                if found % size == 0:
                    matches[found] = pattern
                    if len(matches) >= max_hits:
                        return matches, scanned + len(data), skipped, True
                offset = block.find(pattern, offset + 1)
            tail = block[-(size - 1):] if len(data) == count else b''
            skipped += count - len(data)
            scanned += count
            address += count
            progress(scanned)
    eligible = sum(r.end - r.start for r in regions if r.permissions[:2] == 'rw' and r.permissions.endswith('p'))
    return matches, scanned, skipped, scanned < eligible


def next_scan(memory, previous, kind, mode, pattern, cancel):
    size = struct.calcsize(TYPES[kind])
    result = {}
    for address, old in previous.items():
        if cancel.is_set():
            break
        try:
            current = memory.read(address, size)
            if len(current) != size:
                continue
            keep = current == pattern if mode == 'Gleich' else (
                current != old if mode == 'Geaendert' else current == old if mode == 'Unveraendert' else
                unpack_value(current, kind) > unpack_value(old, kind) if mode == 'Gestiegen' else
                unpack_value(current, kind) < unpack_value(old, kind))
            if keep:
                result[address] = current
        except (PermissionError, ProcessLookupError):
            raise
        except OSError:
            continue
    return result


def process_list(wine_only=True):
    processes = []
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit() or int(directory.name) == os.getpid() or identity(int(directory.name)) is None:
            continue
        try:
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
            if wine_only and not prefix and b'GE_WINE_SESSION_ID' not in env:
                continue
            processes.append((int(directory.name), name, prefix))
        except (OSError, ValueError):
            continue
    return sorted(processes)


def gui(initial_pid=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise ValueError("Keine grafische Sitzung. Fuer die Prozessliste --list verwenden.") from error
    root.title('GE-Memory - Linux-Speicherinspektor')
    root.geometry('980x700')
    root.columnconfigure(1, weight=1)
    root.rowconfigure(5, weight=1)
    wine_only = tk.BooleanVar(value=initial_pid is None)
    pid_value = tk.StringVar()
    process_box = ttk.Combobox(root, textvariable=pid_value, state='readonly', width=75)
    ttk.Label(root, text='Prozess').grid(row=0, column=0, padx=10, pady=8)
    process_box.grid(row=0, column=1, sticky='ew')
    process_ids = {}
    memory = None
    results = {}
    frozen = {}
    kind_of_results = 'Int32'
    events = queue.Queue()
    cancel = threading.Event()
    busy = False
    status = tk.StringVar(value='Prozess auswaehlen. Es gelten die Linux-Zugriffsrechte.')
    kind = tk.StringVar(value='Int32')
    mode = tk.StringVar(value='Gleich')
    value = tk.StringVar(value='100')
    limit = tk.StringVar(value='256')
    new_value = tk.StringVar(value='100')
    controls = ttk.Frame(root)
    controls.grid(row=3, column=0, columnspan=3, sticky='ew', padx=10, pady=8)
    ttk.Label(controls, text='Typ').pack(side='left')
    ttk.Combobox(controls, textvariable=kind, values=list(TYPES), state='readonly', width=10).pack(side='left', padx=6)
    ttk.Label(controls, text='Suchwert').pack(side='left')
    ttk.Entry(controls, textvariable=value, width=16).pack(side='left', padx=6)
    ttk.Combobox(controls, textvariable=mode, values=MODES, state='readonly', width=14).pack(side='left')
    ttk.Label(controls, text='Scanlimit MiB').pack(side='left', padx=6)
    ttk.Entry(controls, textvariable=limit, width=7).pack(side='left')
    tree = ttk.Treeview(root, columns=('address', 'value', 'freeze'), show='headings', selectmode='extended')
    for column, label in [('address', 'Adresse (hex)'), ('value', 'Wert der letzten Suche'), ('freeze', 'Eingefroren')]:
        tree.heading(column, text=label)
    tree.grid(row=5, column=0, columnspan=2, sticky='nsew', padx=(10, 0))
    scroll = ttk.Scrollbar(root, orient='vertical', command=tree.yview)
    scroll.grid(row=5, column=2, sticky='ns', padx=(0, 10))
    tree.configure(yscrollcommand=scroll.set)

    def refresh():
        nonlocal process_ids
        if busy:
            return
        rows = process_list(wine_only.get())
        process_ids = {f'{pid}  {name}  {prefix}': pid for pid, name, prefix in rows}
        process_box['values'] = list(process_ids)
        if initial_pid and not pid_value.get():
            found = next((label for label, pid in process_ids.items() if pid == initial_pid), '')
            pid_value.set(found)

    def display():
        selected = tree.selection()
        tree.delete(*tree.get_children())
        for address, data in list(results.items())[:2000]:
            tree.insert('', 'end', iid=str(address), values=(hex(address), str(unpack_value(data, kind_of_results)),
                                                           'Ja' if address in frozen else ''))
        tree.selection_set([item for item in selected if tree.exists(item)])

    def error_message(error):
        if isinstance(error, PermissionError):
            return ('Linux verweigert den Speicherzugriff. GE-Wine ueber dieses Fenster starten und die Anwendung dort '
                    'neu starten. Bereits laufende Wine-Server koennen ausserhalb dieses Prozessbaums liegen. '
                    'Es werden keine System-Schutzregeln geaendert.')
        return str(error)

    def choose_process():
        nonlocal memory, results
        if busy:
            return
        frozen.clear()
        results = {}
        if memory:
            memory.close()
            memory = None
        try:
            memory = ProcessMemory(process_ids[pid_value.get()])
            status.set(f'PID {memory.pid} ausgewaehlt. Erstsuche starten.')
        except (OSError, KeyError, ValueError) as error:
            status.set(error_message(error))
        display()

    def start_scan(first):
        nonlocal busy, results, kind_of_results
        if busy:
            return
        if memory is None:
            messagebox.showerror('Kein Prozess', 'Zuerst einen Prozess auswaehlen.')
            return
        try:
            chosen_kind = kind.get() if first else kind_of_results
            pattern = pack_value(value.get(), chosen_kind) if first or mode.get() == 'Gleich' else b''
            max_bytes = int(limit.get()) * 1024**2
            if not 1024**2 <= max_bytes <= 4096 * 1024**2:
                raise ValueError('Scanlimit: 1 bis 4096 MiB.')
            if not first and not results:
                raise ValueError('Keine Treffer fuer eine Folgesuche vorhanden.')
        except ValueError as error:
            messagebox.showerror('Suche', str(error))
            return
        if first:
            frozen.clear()
            results = {}
            kind_of_results = chosen_kind
        cancel.clear()
        busy = True
        process_box.configure(state='disabled')
        chosen_mode = mode.get()
        previous = dict(results)
        target = memory
        status.set('Suche laeuft ...')

        def worker():
            try:
                if first:
                    matches, count, skipped, limited = first_scan(
                        target, pattern, max_bytes, cancel,
                        lambda count: events.put(('progress', f'{count // 1024**2} MiB geprueft ...')))
                    note = f'{len(matches)} Treffer; {count // 1024**2} MiB geprueft, {skipped // 1024} KiB uebersprungen.'
                    if limited:
                        note += ' Suche abgebrochen oder durch Speicher-/Trefferlimit begrenzt.'
                else:
                    matches = next_scan(target, previous, chosen_kind, chosen_mode, pattern, cancel)
                    note = f'{len(matches)} Treffer nach Folgesuche.'
                    if cancel.is_set():
                        note += ' Abgebrochen; nur ein Teil der Adressen wurde geprueft.'
                if len(matches) > 2000:
                    note += ' Die ersten 2000 werden angezeigt.'
                events.put(('result', (matches, note)))
            except (OSError, ValueError) as error:
                events.put(('error', error))
        threading.Thread(target=worker, daemon=True).start()

    def write_values(freeze=False):
        if busy or memory is None:
            return
        try:
            selected = [int(item) for item in tree.selection()]
            if not selected:
                raise ValueError('Zuerst Treffer auswaehlen.')
            if freeze and len(frozen.keys() | set(selected)) > 64:
                raise ValueError('Maximal 64 eingefrorene Adressen gleichzeitig.')
            data = pack_value(new_value.get(), kind_of_results)
            for address in selected:
                memory.write(address, data)
                results[address] = data
                if freeze:
                    frozen[address] = data
            display()
            status.set(f'{len(selected)} Wert(e) geschrieben' + (' und eingefroren.' if freeze else '.'))
        except (OSError, ValueError) as error:
            messagebox.showerror('Speicherzugriff', error_message(error))

    def unfreeze():
        for item in tree.selection():
            frozen.pop(int(item), None)
        display()

    def launch_ge_wine():
        try:
            program = Path(__file__).with_name('ge_wine.py')
            subprocess.Popen([sys.executable, str(program), '--gui'], start_new_session=True)
            status.set('GE-Wine geoeffnet. Anwendung dort starten, danach Prozessliste aktualisieren.')
        except OSError as error:
            messagebox.showerror('GE-Wine', str(error))

    ttk.Button(root, text='Aktualisieren', command=refresh).grid(row=0, column=2, padx=10)
    ttk.Checkbutton(root, text='Nur GE-Wine/Wine-Prozesse', variable=wine_only, command=refresh).grid(row=1, column=1, sticky='w')
    ttk.Button(root, text='GE-Wine starten', command=launch_ge_wine).grid(row=1, column=2, padx=10)
    ttk.Label(root, text='Nur eigene Prozesse und beschreibbarer privater Speicher. Keine Zusage fuer Anti-Cheat-geschuetzte Anwendungen.',
              wraplength=940).grid(row=2, column=0, columnspan=3, padx=10, sticky='w')
    buttons = ttk.Frame(root)
    buttons.grid(row=4, column=0, columnspan=3, sticky='w', padx=10, pady=8)
    ttk.Button(buttons, text='Erstsuche', command=lambda: start_scan(True)).pack(side='left', padx=4)
    ttk.Button(buttons, text='Folgesuche', command=lambda: start_scan(False)).pack(side='left', padx=4)
    ttk.Button(buttons, text='Suche abbrechen', command=cancel.set).pack(side='left', padx=4)
    edit = ttk.Frame(root)
    edit.grid(row=6, column=0, columnspan=3, sticky='w', padx=10, pady=8)
    ttk.Label(edit, text='Neuer Wert').pack(side='left')
    ttk.Entry(edit, textvariable=new_value, width=18).pack(side='left', padx=5)
    ttk.Button(edit, text='Schreiben', command=write_values).pack(side='left', padx=4)
    ttk.Button(edit, text='Einfrieren', command=lambda: write_values(True)).pack(side='left', padx=4)
    ttk.Button(edit, text='Freigeben', command=unfreeze).pack(side='left', padx=4)
    ttk.Label(root, textvariable=status, wraplength=950).grid(row=7, column=0, columnspan=3, sticky='w', padx=10, pady=8)
    process_box.bind('<<ComboboxSelected>>', lambda event: choose_process())

    def poll():
        nonlocal busy, results
        for _ in range(200):
            try:
                action, data = events.get_nowait()
            except queue.Empty:
                break
            if action == 'progress':
                status.set(data)
            else:
                busy = False
                process_box.configure(state='readonly')
                if action == 'result':
                    results, note = data
                    display()
                    status.set(note)
                else:
                    status.set(error_message(data))
        if memory is not None and frozen:
            try:
                for address, data in list(frozen.items()):
                    memory.write(address, data)
            except (OSError, ValueError) as error:
                frozen.clear()
                status.set('Einfrieren beendet: ' + error_message(error))
                display()
        root.after(250, poll)

    def close():
        cancel.set()
        frozen.clear()
        if memory:
            memory.close()
        root.destroy()

    refresh()
    if pid_value.get():
        choose_process()
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(250, poll)
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
