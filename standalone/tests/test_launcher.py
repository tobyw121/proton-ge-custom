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
        self.home = self.root / "home"
        installed = self.home / ".local/share/Steam/compatibilitytools.d" / launcher.PINNED_PROTON
        installed.mkdir(parents=True)
        (installed / "proton").write_text("test fixture\n")
        self.exe = self.root / "Spiel mit Leerzeichen;$(echo nein).exe"
        self.exe.touch()
        self.backend = self.root / "umu-run"
        self.backend.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if os.environ.get('TEST_CALL_LOG'):\n"
            "    with open(os.environ['TEST_CALL_LOG'], 'a') as log: log.write(json.dumps(sys.argv[1:])+'\\n')\n"
            "if sys.argv[1:2] == ['winetricks']:\n"
            "    verb=sys.argv[2]\n"
            "    if verb==os.environ.get('TEST_FAIL_VERB'): sys.exit(23)\n"
            "    prefix=Path(os.environ['WINEPREFIX']); prefix.mkdir(parents=True,exist_ok=True)\n"
            "    if not os.environ.get('TEST_NO_RECORD'):\n"
            "        with (prefix/'winetricks.log').open('a') as log: log.write(verb+'\\n')\n"
            "    sys.exit(0)\n"
            "print(json.dumps({'args':sys.argv[1:], 'prefix':os.environ['WINEPREFIX'],"
            "'proton':os.environ['PROTONPATH'], 'gameid':os.environ['GAMEID'], 'cwd':os.getcwd(),"
            "'graphics':{k:os.environ[k] for k in ('DXVK_HUD','DXVK_LOG_PATH','DXVK_LOG_LEVEL','PROTON_LOG','PROTON_LOG_DIR','DXVK_CONFIG',"
            "'__NV_PRIME_RENDER_OFFLOAD','__VK_LAYER_NV_optimus','PROTON_USE_WINED3D') if k in os.environ}}))\n"
            "sys.exit(int(os.environ.get('TEST_EXIT', '0')))\n")
        self.backend.chmod(0o755)

    def run_cli(self, *args, exit_code="0"):
        environment = dict(os.environ, PATH=str(self.root), TEST_EXIT=exit_code,
                           HOME=str(self.root / "home"))
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
        self.assertEqual(record["proton"], str(self.home / ".local/share/Steam/compatibilitytools.d" / launcher.PINNED_PROTON))
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
        self.assertTrue(records[0]["prefix"].endswith("/icarus-ge10-34-clean"))
        self.assertEqual(records[2]["cwd"], str(game.parent))

    def test_diagnostics_create_logs_and_forward_nvidia_and_latency_settings(self):
        prefix = self.root / "Icarus prefix"
        result = self.run_cli("--profile", "icarus-phoenix", "--diagnostics",
                              "--nvidia-offload", "--low-latency", "--prefix", prefix, self.exe)
        self.assertEqual(result.returncode, 0, result.stderr)
        graphics = json.loads(result.stdout)["graphics"]
        self.assertIn("api", graphics["DXVK_HUD"].split(","))
        self.assertTrue(Path(graphics["DXVK_LOG_PATH"]).is_dir())
        self.assertEqual(graphics["DXVK_LOG_LEVEL"], "info")
        self.assertEqual(graphics["PROTON_LOG"], "1")
        self.assertEqual(graphics["PROTON_LOG_DIR"], graphics["DXVK_LOG_PATH"])
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

    def test_recovery_defaults_and_explicit_overrides(self):
        with patch.dict(os.environ, {"HOME": str(self.home)}, clear=False), patch("shutil.which", return_value=str(self.backend)):
            environment = launcher.prepare(self.exe, [], profile="icarus-phoenix")[1]
            self.assertEqual(environment["WINEPREFIX"], str(self.home / ".local/share/ge-wine/prefixes/icarus-ge10-34-clean"))
            self.assertNotIn("floatEmulation", environment.get("DXVK_CONFIG", ""))
            self.assertEqual(launcher.PINNED_PROTON, "GE-Proton10-34")
            self.assertEqual(environment["PROTONPATH"], str(launcher.pinned_proton_directory()))
            with patch.dict(os.environ, {"DXVK_CONFIG": "d3d9.floatEmulation = Strict"}):
                custom = launcher.prepare(self.exe, [], prefix=self.root / "custom", proton="GE-Proton", profile="icarus-phoenix")[1]
            self.assertEqual(custom["WINEPREFIX"], str(self.root / "custom"))
            self.assertEqual(custom["PROTONPATH"], "GE-Proton")
            self.assertEqual(custom["DXVK_CONFIG"], "d3d9.floatEmulation = Strict")

    def test_auto_profile_requires_icarus_layout_and_preserves_explicit_standard(self):
        game = self.root / "ICARUS_Phoenix"
        (game / "Bin64").mkdir(parents=True)
        exe = game / "Launcher.exe"
        exe.touch()
        self.assertEqual(launcher.selected_profile(exe, "auto"), "icarus-phoenix")
        self.assertEqual(launcher.selected_profile(exe, "standard"), "standard")
        self.assertEqual(launcher.selected_profile(self.exe, "auto"), "standard")
        self.assertFalse(launcher.needs_prerequisites(game / "Bin64/Redist/setup.exe", "auto", True))

    def test_default_cli_detects_icarus_and_standard_can_bypass_setup(self):
        game = self.root / "ICARUS_Phoenix"
        (game / "Bin64").mkdir(parents=True)
        exe = game / "Launcher.exe"
        exe.touch()
        prefix = self.root / "automatic-prefix"
        result = self.run_cli("--prefix", prefix, exe)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(launcher.installed_verbs(prefix), set(launcher.VC_VERBS))
        plain = self.root / "standard-prefix"
        result = self.run_cli("--profile", "standard", "--prefix", plain, exe)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((plain / "winetricks.log").exists())

    def test_vc_setup_is_once_per_prefix_and_precedes_game(self):
        prefix = self.root / "prefix"
        calls = self.root / "calls.jsonl"
        arguments = ("--profile", "icarus-phoenix", "--prefix", prefix, self.exe)
        with patch.dict(os.environ, {"TEST_CALL_LOG": str(calls)}):
            for _ in range(2):
                result = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(records[:5], [["winetricks", verb] for verb in launcher.VC_VERBS])
        self.assertEqual(records[5:], [[str(self.exe)], [str(self.exe)]])

    def test_failed_vc_setup_stops_game_and_retry_resumes_missing_verbs(self):
        prefix = self.root / "prefix"
        calls = self.root / "calls.jsonl"
        arguments = ("--profile", "icarus-phoenix", "--prefix", prefix, self.exe)
        with patch.dict(os.environ, {"TEST_CALL_LOG": str(calls), "TEST_FAIL_VERB": "vcrun2012"}):
            result = self.run_cli(*arguments)
        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout, "")
        self.assertEqual(launcher.installed_verbs(prefix), {"vcrun2008", "vcrun2010"})
        with patch.dict(os.environ, {"TEST_CALL_LOG": str(calls)}):
            result = self.run_cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(records[3], ["winetricks", "vcrun2012"])
        self.assertEqual(records[-1], [str(self.exe)])

    def test_zero_exit_without_install_record_is_not_success(self):
        with patch.dict(os.environ, {"TEST_NO_RECORD": "1"}):
            result = self.run_cli("--profile", "icarus-phoenix", "--prefix", self.root / "prefix", self.exe)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("nicht bestätigt", result.stderr)

    def test_skip_switch_does_not_install_and_newer_vc14_is_preserved(self):
        prefix = self.root / "prefix"
        result = self.run_cli("--profile", "icarus-phoenix", "--no-prerequisites", "--prefix", prefix, self.exe)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((prefix / "winetricks.log").exists())
        prefix.mkdir()
        (prefix / "winetricks.log").write_text("vcrun2022\n")
        result = self.run_cli("--profile", "icarus-phoenix", "--prefix", prefix, self.exe)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("vcrun2015", launcher.installed_verbs(prefix))
        self.assertIn("vcrun2022", launcher.installed_verbs(prefix))


if __name__ == "__main__":
    unittest.main()
