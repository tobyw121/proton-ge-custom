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
            "'proton':os.environ['PROTONPATH'], 'gameid':os.environ['GAMEID'], 'cwd':os.getcwd(),"
            "'graphics':{k:os.environ[k] for k in ('DXVK_HUD','DXVK_LOG_PATH','DXVK_CONFIG',"
            "'__NV_PRIME_RENDER_OFFLOAD','__VK_LAYER_NV_optimus','PROTON_USE_WINED3D') if k in os.environ}}))\n"
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

    def test_icarus_launcher_game_and_redist_share_prefix(self):
        launcher_exe = self.root / "Launcher.exe"
        launcher_exe.touch()
        redist = self.root / "Bin64" / "Redist" / "vcredist.exe"
        redist.parent.mkdir(parents=True)
        redist.touch()
        game = self.root / "Bin64" / "Game.exe"
        game.touch()
        records = []
        for executable in (launcher_exe, redist, game):
            result = self.run_cli("--profile", "icarus-phoenix", executable)
            self.assertEqual(result.returncode, 0, result.stderr)
            records.append(json.loads(result.stdout))
        self.assertEqual(len({record["prefix"] for record in records}), 1)
        self.assertIn("icarus-phoenix-", records[0]["prefix"])
        self.assertEqual(records[2]["cwd"], str(game.parent))

    def test_diagnostics_create_logs_and_forward_nvidia_and_latency_settings(self):
        prefix = self.root / "Icarus prefix"
        result = self.run_cli("--profile", "icarus-phoenix", "--diagnostics",
                              "--nvidia-offload", "--low-latency", "--prefix", prefix, self.exe)
        self.assertEqual(result.returncode, 0, result.stderr)
        graphics = json.loads(result.stdout)["graphics"]
        self.assertIn("api", graphics["DXVK_HUD"].split(","))
        self.assertTrue(Path(graphics["DXVK_LOG_PATH"]).is_dir())
        self.assertEqual(graphics["__NV_PRIME_RENDER_OFFLOAD"], "1")
        self.assertEqual(graphics["__VK_LAYER_NV_optimus"], "NVIDIA_only")
        self.assertIn("dxgi.maxFrameLatency = 1", graphics["DXVK_CONFIG"])
        self.assertIn("d3d9.maxFrameLatency = 1", graphics["DXVK_CONFIG"])

    def test_icarus_removes_opengl_override_without_forcing_rendering_tweaks(self):
        with patch("shutil.which", return_value=str(self.backend)), patch.dict(
                os.environ, {"PROTON_USE_WINED3D": "1", "DXVK_CONFIG": "dxvk.numCompilerThreads = 4"}):
            environment = launcher.prepare(self.exe, [], profile="icarus-phoenix")[1]
            self.assertNotIn("PROTON_USE_WINED3D", environment)
            self.assertEqual(environment["DXVK_CONFIG"], "dxvk.numCompilerThreads = 4")
            standard = launcher.prepare(self.exe, [])[1]
            self.assertEqual(standard["PROTON_USE_WINED3D"], "1")
            latency = launcher.prepare(self.exe, [], low_latency=True)[1]
            self.assertIn("dxvk.numCompilerThreads = 4; dxgi.maxFrameLatency = 1", latency["DXVK_CONFIG"])


if __name__ == "__main__":
    unittest.main()
