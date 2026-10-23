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

            def check():
                results.append(any(widget.winfo_class() == "TLabel" and
                                   widget.cget("text") == "Programm beendet."
                                   for widget in root.winfo_children()))
                root.destroy()

            root.after(200, start)
            root.after(2000, check)
            return root

        options = argparse.Namespace(executable=str(fixture.exe), arguments=["--test"],
                                     prefix=str(fixture.root / "prefix"),
                                     proton="GE-Proton", gameid="umu-default",
                                     profile="icarus-phoenix", diagnostics=True,
                                     nvidia_offload=False, low_latency=False)
        with patch.dict(os.environ, {"PATH": str(fixture.root)}), patch("tkinter.Tk", factory):
            self.assertEqual(test_launcher.launcher.gui(options), 0)
        self.assertEqual(results, [True])
