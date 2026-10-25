import argparse
import os
import unittest
from unittest.mock import patch

import test_launcher


@unittest.skipUnless(os.environ.get("DISPLAY"), "Requires an X display (for example Xvfb)")
class GuiTests(unittest.TestCase):
    def test_window_launches_backend_and_reports_completion(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("Tk is not installed")
        fixture = test_launcher.LauncherTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original = tk.Tk
        results = []

        def factory():
            root = original()

            def start():
                for widget in root.winfo_children():
                    if widget.winfo_class() == "TButton" and widget.cget("text") == "Programm starten":
                        widget.invoke()

            def check(attempt=0):
                complete = any(widget.winfo_class() == "TLabel" and
                                   widget.cget("text") == "Programm beendet."
                                   for widget in root.winfo_children())
                if not complete and attempt < 50:
                    root.after(200, lambda: check(attempt + 1))
                    return
                results.append(complete)
                for timer in root.tk.call("after", "info"):
                    root.after_cancel(timer)
                root.destroy()

            root.after(200, start)
            root.after(400, check)
            return root

        options = argparse.Namespace(executable=str(fixture.exe), arguments=["--test"],
                                     prefix=str(fixture.root / "prefix"),
                                     proton=test_launcher.launcher.PINNED_PROTON, gameid="umu-default",
                                     profile="icarus-phoenix", diagnostics=True,
                                     nvidia_offload=False, low_latency=False,
                                     no_prerequisites=False)
        with patch.dict(os.environ, {"PATH": str(fixture.root), "HOME": str(fixture.home)}), patch("tkinter.Tk", factory):
            self.assertEqual(test_launcher.launcher.gui(options), 0)
        self.assertEqual(results, [True])

    def test_profile_switch_updates_suggested_prefix_but_keeps_custom_path(self):
        import tkinter as tk
        fixture = test_launcher.LauncherTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original = tk.Tk
        results = []
        versions = []

        def factory():
            root = original()

            def inspect():
                try:
                    entries = {int(w.grid_info()["row"]): w for w in root.winfo_children()
                               if w.winfo_class() == "TEntry"}
                    combo = next(w for w in root.winfo_children() if w.winfo_class() == "TCombobox")
                    results.append(entries[2].get())
                    versions.append(entries[3].get())
                    combo.set(test_launcher.launcher.PROFILES["icarus-phoenix"])
                    results.append(entries[2].get())
                    versions.append(entries[3].get())
                    entries[2].delete(0, "end")
                    entries[2].insert(0, str(fixture.root / "custom"))
                    combo.set(test_launcher.launcher.PROFILES["icarus-online"])
                    results.append(entries[2].get())
                    versions.append(entries[3].get())
                finally:
                    for timer in root.tk.call("after", "info"):
                        root.after_cancel(timer)
                    root.destroy()

            root.after(200, inspect)
            return root

        options = argparse.Namespace(executable=str(fixture.exe), arguments=[], prefix=None,
                                     proton=None, gameid="umu-default",
                                     profile="icarus-online", diagnostics=False, nvidia_offload=False,
                                     low_latency=False, no_prerequisites=False)
        with patch.dict(os.environ, {"HOME": str(fixture.home)}), patch("tkinter.Tk", factory):
            self.assertEqual(test_launcher.launcher.gui(options), 0)
        base = fixture.home / ".local/share/ge-wine/prefixes"
        self.assertEqual(results, [str(base / "icarus-vfun"), str(base / "icarus-ge10-34-clean"),
                                   str(fixture.root / "custom")])

        self.assertEqual(versions, [test_launcher.launcher.PINNED_PROTON,
                                    test_launcher.launcher.PHOENIX_PROTON,
                                    test_launcher.launcher.PINNED_PROTON])

    def test_immediate_stop_terminates_the_running_backend(self):
        import tkinter as tk
        import sys
        fixture = test_launcher.LauncherTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        pidfile = fixture.root / 'backend.pid'
        fixture.backend.write_text(f'#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(90)\n')
        original = tk.Tk
        results = []

        def factory():
            root = original()

            def inspect(attempt=0):
                buttons = {w.cget('text'): w for w in root.winfo_children() if w.winfo_class() == 'TButton'}
                if attempt == 0:
                    buttons['Programm starten'].invoke()
                labels = [w.cget('text') for w in root.winfo_children() if w.winfo_class() == 'TLabel']
                if pidfile.exists() and str(buttons['Sofort beenden'].cget('state')) != 'disabled':
                    buttons['Sofort beenden'].invoke()
                if 'Programm sofort beendet.' in labels or attempt > 60:
                    results.append('Programm sofort beendet.' in labels)
                    for timer in root.tk.call('after', 'info'):
                        root.after_cancel(timer)
                    root.destroy()
                else:
                    root.after(100, lambda: inspect(attempt + 1))

            root.after(200, inspect)
            return root

        options = argparse.Namespace(executable=str(fixture.exe), arguments=[], prefix=str(fixture.root / 'prefix'),
                                     proton=test_launcher.launcher.PINNED_PROTON, gameid='umu-default',
                                     profile='standard', diagnostics=False, nvidia_offload=False,
                                     low_latency=False, no_prerequisites=False)
        with patch.dict(os.environ, {'HOME': str(fixture.home), 'PATH': str(fixture.root),
                                     'XDG_STATE_HOME': str(fixture.root / 'state')}), patch('tkinter.Tk', factory):
            self.assertEqual(test_launcher.launcher.gui(options), 0)
        self.assertEqual(results, [True])
        import ge_wine_sessions
        self.assertIsNone(ge_wine_sessions.identity(int(pidfile.read_text())))
