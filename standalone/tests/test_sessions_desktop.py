import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import test_launcher
from test_launcher import launcher, SOURCE
import ge_wine_sessions as sessions
import ge_wine_desktop as desktop


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')})
        env.start()
        self.addCleanup(env.stop)

    def wait_for(self, condition):
        end = time.monotonic() + 6
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(.03)
        self.fail('Process did not reach expected state')

    def test_stop_kills_detached_children_but_not_unrelated_process(self):
        control = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'], start_new_session=True)
        self.addCleanup(lambda: control.poll() is None and control.kill())
        self.addCleanup(lambda: control.wait(timeout=5))
        session = sessions.Session()
        self.addCleanup(session.close)
        path = self.root / 'pids'
        script = ('import os,subprocess,sys,time; from pathlib import Path; '
                  'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(90)"],start_new_session=True); '
                  f'Path({str(path)!r}).write_text(str(os.getpid())+" "+str(p.pid)); time.sleep(90)')
        result = []
        thread = threading.Thread(target=lambda: result.append(session.run([sys.executable, '-c', script], dict(os.environ), str(self.root))))
        thread.start()
        self.addCleanup(session.stop)
        self.wait_for(path.exists)
        pids = [int(pid) for pid in path.read_text().split()]
        session.stop()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.wait_for(lambda: all(sessions.identity(pid) is None for pid in pids))
        self.assertIsNone(control.poll())
        self.assertNotEqual(result, [0])
        with self.assertRaises(sessions.LaunchCancelled):
            session.run([sys.executable, '-c', 'raise Exception("must not launch")'], dict(os.environ), str(self.root))
        control.kill()
        control.wait()

    def test_stale_controller_identity_cannot_kill_another_process(self):
        token = 'a' * 32
        path = sessions.records_directory() / (token + '.json')
        path.write_text(json.dumps(dict(session=token, controller=os.getpid(), start='stale', children={})))
        sessions.stop_all()
        self.assertFalse(path.exists())
        self.assertIsNotNone(sessions.identity(os.getpid()))

    def test_cli_kill_all_stops_multiple_registered_launchers(self):
        fixture = test_launcher.LauncherTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.backend.write_text(f'#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath(os.environ["PID_FILE"]).write_text(str(os.getpid()))\ntime.sleep(90)\n')
        env = dict(os.environ, HOME=str(fixture.home), PATH=str(fixture.root),
                   XDG_STATE_HOME=str(self.root / 'cli-state'), XDG_CONFIG_HOME=str(self.root / 'config'))
        controllers = []
        try:
            for i in range(2):
                path = self.root / f'pid-{i}'
                process = subprocess.Popen([sys.executable, str(SOURCE), str(fixture.exe)],
                                           env=dict(env, PID_FILE=str(path)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                controllers.append(process)
                self.wait_for(path.exists)
            result = subprocess.run([sys.executable, str(SOURCE), '--kill-all'], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('2 laufende Sitzungen', result.stdout)
            for process in controllers:
                self.assertEqual(process.wait(timeout=5), -15)
            for i in range(2):
                pid = int((self.root / f'pid-{i}').read_text())
                self.wait_for(lambda: sessions.identity(pid) is None)
        finally:
            for process in controllers:
                if process.poll() is None:
                    process.kill()
                process.wait()


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_launcher.LauncherTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.home = self.fixture.home
        self.home.mkdir(exist_ok=True)
        env = patch.dict(os.environ, {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.root / 'config')})
        env.start()
        self.addCleanup(env.stop)

    def test_localized_desktop_and_disabled_desktop(self):
        config = self.root / 'config'
        config.mkdir()
        (config / 'user-dirs.dirs').write_text('XDG_DESKTOP_DIR="$HOME/Schreibtisch"\n')
        path = desktop.write_shortcut('Spiel', ['--gui'], 'test', lambda s: None)
        self.assertEqual(path.parent, self.home / 'Schreibtisch')
        self.assertTrue(path.stat().st_mode & 0o100)
        (config / 'user-dirs.dirs').write_text('XDG_DESKTOP_DIR="$HOME"\n')
        self.assertIsNone(desktop.write_shortcut('disabled', [], 'disabled', lambda s: None))

    @unittest.skipUnless(shutil.which('gio') and shutil.which('desktop-file-validate'), 'Requires gio and desktop-file-validate')
    def test_real_desktop_launch_preserves_special_arguments(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        output = self.root / 'arguments.json'
        command = bin_dir / 'ge-wine'
        command.write_text(f'#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\nPath({str(output)!r}).write_text(json.dumps(sys.argv[1:]))\n')
        command.chmod(0o755)
        arguments = ['--profile', 'icarus-online', '--prefix', '/tmp/prefix with spaces',
                     '/tmp/a"b\\c$`%file.exe', 'a;b $(touch nope)', '100%']
        path = desktop.write_shortcut('Spiel', arguments, 'escaping', lambda s: None)
        result = subprocess.run(['desktop-file-validate', str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
        result = subprocess.run(['gio', 'launch', str(path)], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        end = time.monotonic() + 5
        while not output.exists() and time.monotonic() < end:
            time.sleep(.03)
        self.assertEqual(json.loads(output.read_text()), arguments)
        self.assertFalse((self.root / 'nope').exists())

    def test_cli_manual_shortcut_does_not_launch_program_and_keeps_profile(self):
        result = self.fixture.run_cli('--create-shortcut', '--profile', 'icarus-online', self.fixture.exe)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        links = list((self.home / 'Desktop').glob('*.desktop'))
        self.assertEqual(len(links), 1)
        content = links[0].read_text()
        self.assertIn('"icarus-online"', content)
        self.assertIn('icarus-vfun', content)
        self.assertIn(launcher.PINNED_PROTON, content)

    def test_installer_created_link_becomes_desktop_launcher_not_installer(self):
        prefix = self.root / 'installed'
        installer = self.root / 'Setup.exe'
        installer.touch()
        self.fixture.backend.write_text(f'''#!{sys.executable}
import os
from pathlib import Path
root=Path(os.environ['WINEPREFIX'])/'drive_c'
(root/'windows/system32').mkdir(parents=True,exist_ok=True)
(root/'windows/system32/start.exe').write_text('fixture')
links=root/'ProgramData/Microsoft/Windows/Start Menu/Programs'
links.mkdir(parents=True,exist_ok=True)
(links/'Installed Game.lnk').write_bytes(b'fixture')
(links/'Uninstall.lnk').write_bytes(b'fixture')
''')
        result = self.fixture.run_cli('--profile', 'standard', '--prefix', prefix, installer)
        self.assertEqual(result.returncode, 0, result.stderr)
        links = list((self.home / 'Desktop').glob('*.desktop'))
        self.assertEqual(len(links), 1)
        content = links[0].read_text()
        self.assertIn('Installed Game', content)
        self.assertIn('start.exe', content)
        self.assertIn('"/unix"', content)
        self.assertNotIn('Setup.exe', content)
        self.assertNotIn('Uninstall.lnk', content)
        if shutil.which('desktop-file-validate'):
            self.assertEqual(subprocess.run(['desktop-file-validate', str(links[0])]).returncode, 0)
