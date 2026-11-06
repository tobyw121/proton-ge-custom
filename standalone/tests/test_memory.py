import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import test_launcher
import ge_memory as memory

CHILD = r'''
import ctypes,json,sys
number=ctypes.c_int32(1370291737)
wide=ctypes.c_int64(12345678901234)
floating=ctypes.c_float(17.25)
block=ctypes.create_string_buffer(2*1024*1024+32)
base=ctypes.addressof(block)
# Naturally aligned value spanning the inspector's first 1 MiB read boundary.
start=base+1
boundary_address=start+1024*1024-1
ctypes.c_int32.from_address(boundary_address).value=1370291737
print(json.dumps(dict(number=ctypes.addressof(number),wide=ctypes.addressof(wide),
 floating=ctypes.addressof(floating),start=start,end=base+len(block),boundary=boundary_address)),flush=True)
for line in sys.stdin:
 if line.strip()=='change': number.value+=1
 print(json.dumps(dict(number=number.value,wide=wide.value,floating=floating.value)),flush=True)
'''


class MemoryTests(unittest.TestCase):
    def setUp(self):
        env = dict(os.environ, WINEPREFIX='/tmp/ge-memory-test-prefix')
        self.child = subprocess.Popen([sys.executable, '-u', '-c', CHILD], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.addCleanup(self.cleanup)
        self.addresses = json.loads(self.child.stdout.readline())
        self.mem = memory.ProcessMemory(self.child.pid)
        self.addCleanup(self.mem.close)

    def cleanup(self):
        self.child.kill()
        self.child.wait()
        for stream in (self.child.stdin, self.child.stdout, self.child.stderr):
            stream.close()

    def state(self, command='get'):
        self.child.stdin.write(command+'\n')
        self.child.stdin.flush()
        return json.loads(self.child.stdout.readline())

    def test_read_write_and_pid_identity(self):
        address = self.addresses['number']
        self.assertEqual(memory.unpack_value(self.mem.read(address, 4), 'Int32'), 1370291737)
        self.mem.write(address, memory.pack_value('82', 'Int32'))
        self.mem.write(self.addresses['wide'], memory.pack_value('998877665544', 'Int64'))
        self.mem.write(self.addresses['floating'], memory.pack_value('32,5', 'Float32'))
        self.assertEqual(self.state(), dict(number=82, wide=998877665544, floating=32.5))
        self.mem.stamp = 'stale'
        with self.assertRaises(ProcessLookupError):
            self.mem.write(address, memory.pack_value('99', 'Int32'))

    def test_scan_cross_chunk_refine_and_cancellation(self):
        pattern = memory.pack_value('1370291737', 'Int32')
        region = memory.Region(self.addresses['start'], self.addresses['end'], 'rw-p')
        result, scanned, skipped, limited = memory.first_scan(self.mem, pattern, 4*1024**2,
                                                              threading.Event(), regions=[region])
        self.assertIn(self.addresses['boundary'], result)
        self.assertEqual(skipped, 0)
        self.assertFalse(limited)
        previous = {self.addresses['number']: pattern}
        self.state('change')
        changed = memory.next_scan(self.mem, previous, 'Int32', 'Gestiegen', b'', threading.Event())
        self.assertIn(self.addresses['number'], changed)
        self.assertEqual(memory.unpack_value(changed[self.addresses['number']], 'Int32'), 1370291738)
        cancel = threading.Event(); cancel.set()
        self.assertEqual(memory.first_scan(self.mem, pattern, 1024, cancel, regions=[region])[0], {})

    def test_nonwritable_and_permissions_are_not_silently_ignored(self):
        region = next(r for r in self.mem.regions() if r.permissions.startswith('r--'))
        with self.assertRaises(ValueError):
            self.mem.write(region.start, memory.pack_value('0', 'Int32'))
        with patch.object(self.mem, 'read', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                memory.first_scan(self.mem, memory.pack_value('2','Int32'), 1024,
                                  threading.Event(), regions=[memory.Region(4096,8192,'rw-p')])

    def test_process_list_discovers_wine_marker_without_commandline_tokens(self):
        rows = memory.process_list(True)
        self.assertIn(self.child.pid, [r[0] for r in rows])
        self.assertEqual(next(r[2] for r in rows if r[0]==self.child.pid), '/tmp/ge-memory-test-prefix')

    @unittest.skipUnless(os.environ.get('DISPLAY'), 'Requires X display')
    def test_gui_scan_and_write_real_child(self):
        import tkinter as tk
        original = tk.Tk
        checks = []
        address = self.addresses['number']

        def factory():
            root = original()

            def check(attempt=0):
                try:
                    tree = next(w for w in root.winfo_children() if w.winfo_class() == 'Treeview')
                    frames = {int(w.grid_info()['row']): w for w in root.winfo_children() if w.winfo_class() == 'TFrame'}
                    if attempt == 0:
                        entry = next(w for w in frames[3].winfo_children() if w.winfo_class() == 'TEntry')
                        entry.delete(0,'end'); entry.insert(0,'1370291737')
                        button = next(w for w in frames[4].winfo_children() if w.cget('text') == 'Erstsuche')
                        button.invoke()
                    if tree.exists(str(address)):
                        tree.selection_set(str(address))
                        entry = next(w for w in frames[6].winfo_children() if w.winfo_class() == 'TEntry')
                        entry.delete(0,'end'); entry.insert(0,'45454545')
                        button = next(w for w in frames[6].winfo_children() if w.winfo_class() == 'TButton' and w.cget('text') == 'Einfrieren')
                        button.invoke()
                        checks.append(self.state()['number'])
                        self.state('change')
                        root.after(600, lambda: check_frozen(tree, frames))
                    elif attempt > 100:
                        checks.append('timeout'); finish()
                    else:
                        root.after(100,lambda:check(attempt+1))
                except Exception as error:
                    checks.append(str(error)); finish()

            def check_frozen(tree, frames):
                try:
                    checks.append(self.state()['number'])
                    tree.selection_set(str(address))
                    next(w for w in frames[6].winfo_children() if w.winfo_class() == 'TButton' and w.cget('text') == 'Freigeben').invoke()
                    self.state('change')
                    root.after(400, lambda: (checks.append(self.state()['number']), finish()))
                except Exception as error:
                    checks.append(str(error)); finish()

            def finish():
                for timer in root.tk.call('after','info'):
                    root.after_cancel(timer)
                root.tk.call(root.protocol('WM_DELETE_WINDOW'))

            root.after(200, check)
            return root

        with patch('tkinter.Tk',factory), patch('tkinter.messagebox.showerror', side_effect=lambda *args: checks.append(args)):
            self.assertEqual(memory.gui(self.child.pid),0)
        self.assertEqual(checks,[45454545,45454545,45454546])
