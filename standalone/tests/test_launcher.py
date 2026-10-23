import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "ge_wine.py"
spec = importlib.util.spec_from_file_location("ge_wine", SOURCE)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.exe = self.root / "Spiel mit Leerzeichen;$(echo nein).exe"
        self.exe.touch()
        self.backend = self.root / "umu-run"
        self.backend.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "print(json.dumps({'args':sys.argv[1:], 'prefix':os.environ['WINEPREFIX'],"
            "'proton':os.environ['PROTONPATH'], 'gameid':os.environ['GAMEID'], 'cwd':os.getcwd()}))\n"
            "sys.exit(int(os.environ.get('TEST_EXIT', '0')))\n")
        self.backend.chmod(0o755)

    def run_cli(self, *args, exit_code="0"):
        environment = dict(os.environ, PATH=str(self.root), TEST_EXIT=exit_code)
        environment.pop("PROTONPATH", None)
        environment.pop("GAMEID", None)
        return subprocess.run([sys.executable, str(SOURCE), *map(str, args)],
                              env=environment, text=True, capture_output=True)

    def test_real_subprocess_preserves_arguments_and_environment(self):
        prefix = self.root / "Mein Prefix"
        result = self.run_cli("--prefix", prefix, "--gameid", "umu-test", self.exe,
                              "--fullscreen", "argument mit leerzeichen", "$(touch nope)")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["args"], [str(self.exe), "--fullscreen",
                                         "argument mit leerzeichen", "$(touch nope)"])
        self.assertEqual(record["prefix"], str(prefix))
        self.assertEqual(record["proton"], "GE-Proton")
        self.assertEqual(record["gameid"], "umu-test")
        self.assertEqual(record["cwd"], str(self.root))

    def test_backend_failure_is_not_reported_as_success(self):
        result = self.run_cli(self.exe, exit_code="37")
        self.assertEqual(result.returncode, 37)

    def test_missing_program_does_not_start_backend(self):
        result = self.run_cli(self.root / "missing.exe")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nicht gefunden", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_custom_build_validated_and_selected(self):
        proton = self.root / "Mein Proton"
        proton.mkdir()
        invalid = self.run_cli("--proton", proton, self.exe)
        self.assertEqual(invalid.returncode, 1)
        (proton / "proton").touch()
        valid = self.run_cli("--proton", proton, self.exe)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(json.loads(valid.stdout)["proton"], str(proton))

    def test_default_prefix_is_stable_and_separates_directories(self):
        with patch("shutil.which", return_value=str(self.backend)):
            first = launcher.prepare(self.exe, [])[1]["WINEPREFIX"]
            sibling = self.root / "another.exe"
            sibling.touch()
            self.assertEqual(first, launcher.prepare(sibling, [])[1]["WINEPREFIX"])
            other = self.root / "other"
            other.mkdir()
            (other / "game.exe").touch()
            self.assertNotEqual(first, launcher.prepare(other / "game.exe", [])[1]["WINEPREFIX"])

    def test_missing_backend_gives_actionable_error(self):
        self.backend.unlink()
        result = self.run_cli(self.exe)
        self.assertEqual(result.returncode, 1)
        self.assertIn("vollständige GE-Wine-Paket", result.stderr)


if __name__ == "__main__":
    unittest.main()
