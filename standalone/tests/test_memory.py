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

    def test_auto_scan_keeps_types_and_writes_only_their_byte_width(self):
        base = self.addresses['start']-1
        kinds = list(memory.TYPES)
        expected = {}
        for index, kind in enumerate(kinds):
            address = base+64+index*32
            data = memory.pack_value('1' if kind=='Bool' else '100',kind)
            self.mem.write(address,data)
            expected[(address,kind)] = data
        outcome = memory.scan_types(self.mem,memory.patterns_for('100',memory.TYPES),8192,
                                    threading.Event(),regions=[memory.Region(base,base+8192,'rw-p')])
        for key,data in expected.items():
            if key[1]=='Bool':
                continue
            self.assertIn(key,outcome.hits)
            self.assertEqual(outcome.hits[key].baseline,data)
        for key,data in expected.items():
            address,kind = key
            self.mem.write(address-4,b'ABCD')
            self.mem.write(address+len(data),b'WXYZ')
            hit = memory.Hit(address,kind,data,data)
            encoded,actual = memory.write_hits(self.mem,{key:hit},[key],'0')
            self.assertEqual(actual[key],memory.pack_value('0',kind))
            self.assertEqual(encoded[key],actual[key])
            self.assertEqual(self.mem.read(address-4,4),b'ABCD')
            self.assertEqual(self.mem.read(address+len(data),4),b'WXYZ')

    def test_live_reads_preserve_scan_baseline_and_first_value(self):
        address = self.addresses['number']; key = (address,'Int32')
        original = memory.pack_value('1370291737','Int32')
        hit = memory.Hit(address,'Int32',original,original)
        self.state('change')
        sample = memory.live_read(self.mem,{key:hit})
        self.assertEqual(memory.unpack_value(sample[key],'Int32'),1370291738)
        self.assertEqual(hit.baseline,original)
        follow = memory.refine_hits(self.mem,{key:hit},'Geaendert',{},threading.Event())
        self.assertIn(key,follow)
        self.assertEqual(follow[key].initial,original)
        self.assertEqual(follow[key].baseline,sample[key])

    def test_byte_hit_limit_does_not_starve_float_search(self):
        base = self.addresses['start']-1
        for offset in range(0,64,4):
            self.mem.write(base+offset,b'dddd')
        floating = base+4096
        self.mem.write(floating,memory.pack_value('100','Float32'))
        outcome = memory.scan_types(self.mem,memory.patterns_for('100',['UInt8','Float32']),8192,
                                    threading.Event(),regions=[memory.Region(base,base+8192,'rw-p')],max_hits=4)
        self.assertIn((floating,'Float32'),outcome.hits)
        self.assertIn('UInt8',outcome.capped_types)
        self.assertLessEqual(len(outcome.hits),4)

    def test_invalid_mixed_write_is_rejected_before_any_memory_change(self):
        base = self.addresses['start']-1
        keys = [(base+64,'Int32'),(base+96,'Int8')]
        hits = {}
        for address,kind in keys:
            data = memory.pack_value('10',kind); self.mem.write(address,data)
            hits[(address,kind)] = memory.Hit(address,kind,data,data)
        with self.assertRaises(ValueError):
            memory.write_hits(self.mem,hits,keys,'300')
        self.assertEqual(self.mem.read(keys[0][0],4),memory.pack_value('10','Int32'))
        address = keys[0][0]
        overlap = memory.Hit(address,'Float32',memory.pack_value('1','Float32'),b'0000')
        hits[(address,'Float32')] = overlap
        with self.assertRaisesRegex(ValueError,'Ueberlappende'):
            memory.write_hits(self.mem,hits,[keys[0],(address,'Float32')],'20')

    def test_readback_reports_target_overwrite_instead_of_false_success(self):
        address = self.addresses['number']; key = (address,'Int32')
        old = self.mem.read(address,4)
        hit = memory.Hit(address,'Int32',old,old)
        real_read = self.mem.read
        def changed_read(address,size):
            self.state('change')
            return real_read(address,size)
        with patch.object(self.mem,'read',side_effect=changed_read):
            encoded,actual = memory.write_hits(self.mem,{key:hit},[key],'82')
        self.assertEqual(memory.unpack_value(encoded[key],'Int32'),82)
        self.assertEqual(memory.unpack_value(actual[key],'Int32'),83)

    def test_game_recognition_prefers_main_game_over_large_launcher(self):
        self.assertTrue(memory.is_game_candidate('launcher.exe',5*1024**3,True))
        self.assertFalse(memory.is_game_candidate('VLauncher.exe',3*1024**3,True))
        self.assertFalse(memory.is_game_candidate('GameMon.des',2*1024**3,True))
        self.assertFalse(memory.is_game_candidate('game.exe',1024**3,True))
        self.assertFalse(memory.is_game_candidate('game.exe',5*1024**3,False))
        game = memory.ProcessInfo(136833,'launcher.exe','/prefix',5*1024**3,True,True)
        other = memory.ProcessInfo(700,'other.exe','/prefix2',2*1024**3,True,False)
        self.assertEqual(memory.preferred_game([other,game]),136833)
        self.assertIsNone(memory.preferred_game([other,memory.ProcessInfo(800,'second.exe','/p',3*1024**3,True)]))

    @unittest.skipUnless(os.environ.get('DISPLAY'), 'Requires X display')
    def test_gui_pagination_type_filter_and_action_states(self):
        import tkinter as tk
        original = tk.Tk
        failures = []
        base = self.addresses['start'] + 64
        hits = {}
        for index in range(1100):
            hit = memory.Hit(base + index*8, 'Int32', bytes(4), bytes(4))
            hits[hit.key] = hit
        hit = memory.Hit(base, 'Float32', bytes(4), bytes(4))
        hits[hit.key] = hit

        def factory():
            root = original()
            def finish():
                for timer in root.tk.call('after', 'info'):
                    root.after_cancel(timer)
                root.tk.call(root.protocol('WM_DELETE_WINDOW'))
            def verify(attempt=0):
                try:
                    widgets = root.memory_widgets
                    tree = widgets['tree']
                    if attempt == 0:
                        self.assertTrue(widgets['refine'].instate(['disabled']))
                        self.assertTrue(widgets['write'].instate(['disabled']))
                        widgets['first'].invoke()
                    if not tree.get_children():
                        self.assertLess(attempt, 40)
                        root.after(100, lambda:verify(attempt+1)); return
                    self.assertEqual(len(tree.get_children()), 500)
                    widgets['next'].invoke()
                    self.assertEqual(len(tree.get_children()), 500)
                    widgets['next'].invoke()
                    self.assertEqual(len(tree.get_children()), 101)
                    self.assertTrue(widgets['next'].instate(['disabled']))
                    widgets['filter'].set('Float32')
                    widgets['filter'].event_generate('<<ComboboxSelected>>')
                    self.assertEqual(len(tree.get_children()), 1)
                    self.assertTrue(widgets['previous'].instate(['disabled']))
                    tree.selection_set(tree.get_children()[0])
                    tree.event_generate('<<TreeviewSelect>>')
                    self.assertFalse(widgets['write'].instate(['disabled']))
                    self.assertTrue(widgets['release'].instate(['disabled']))
                    widgets['filter'].set('UInt16')
                    widgets['filter'].event_generate('<<ComboboxSelected>>')
                    self.assertEqual(len(tree.get_children()), 0)
                    self.assertTrue(widgets['write'].instate(['disabled']))
                    self.assertFalse(widgets['refine'].instate(['disabled']))
                    finish()
                except Exception as error:
                    failures.append(str(error)); finish()
            root.after(200, verify)
            return root
        outcome = memory.ScanResult(hits, 4096, 0, set())
        with patch('tkinter.Tk', factory), patch.object(memory, 'scan_types', return_value=outcome):
            memory.gui(self.child.pid)
        self.assertEqual(failures, [])

    def test_gui_scan_and_write_real_child(self):
        import tkinter as tk
        original = tk.Tk
        checks = []
        live_checks = []
        address = self.addresses['number']

        def factory():
            root = original()

            def check(attempt=0):
                try:
                    frames = root.memory_widgets
                    tree = frames['tree']
                    if attempt == 0:
                        entry = frames['search']
                        entry.delete(0,'end'); entry.insert(0,'1370291737')
                        button = frames['first']
                        button.invoke()
                    if tree.exists(f'{address}:Int32'):
                        self.state('change')
                        root.after(450,lambda: check_live(tree,frames))
                    elif attempt > 100:
                        checks.append('timeout'); finish()
                    else:
                        root.after(100,lambda:check(attempt+1))
                except Exception as error:
                    checks.append(str(error)); finish()

            def check_live(tree,frames):
                try:
                    values = tree.item(f'{address}:Int32','values')
                    live_checks.append(tuple(str(v) for v in values[2:5]))
                    tree.selection_set(f'{address}:Int32')
                    tree.event_generate('<<TreeviewSelect>>')
                    entry = frames['write']
                    entry.delete(0,'end'); entry.insert(0,'45454545')
                    frames['freeze'].invoke()
                    checks.append(self.state()['number'])
                    self.state('change')
                    root.after(600,lambda: check_frozen(tree,frames))
                except Exception as error:
                    checks.append(str(error)); finish()

            def check_frozen(tree, frames):
                try:
                    checks.append(self.state()['number'])
                    tree.selection_set(f'{address}:Int32')
                    frames['release'].invoke()
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
        self.assertEqual(live_checks,[('1370291738','1370291737','1370291737')])
