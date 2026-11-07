import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import test_launcher


import unittest
class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_launcher.LauncherTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.prefix = self.fixture.root / 'prefix'

    def recordings(self):
        return sorted((self.prefix / 'logs').iterdir())

    def test_automatic_logs_include_background_processes_and_both_streams(self):
        self.fixture.backend.write_text(f'''#!{sys.executable}
import json, subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.8)'])
print(json.dumps(dict(child=child.pid)), flush=True)
print('stderr fixture', file=sys.stderr, flush=True)
child.wait()
''')
        with patch.dict(os.environ, {'TEST_PRIVATE_CREDENTIAL': 'must-not-persist-environment'}):
            result = self.fixture.run_cli('--prefix', self.prefix, self.fixture.exe)
        self.assertEqual(result.returncode, 0, result.stderr)
        child_pid = json.loads(result.stdout)['child']
        folder, = self.recordings()
        self.assertRegex(folder.name, r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{6}_[a-f0-9]{8}$')
        data = json.loads((folder / 'session.json').read_text())
        self.assertEqual(data['exit_code'], 0)
        self.assertEqual(data['prefix'], str(self.prefix))
        self.assertEqual(data['environment']['PROTON_LOG_DIR'], str(folder))
        self.assertIn('+process', data['environment']['WINEDEBUG'])
        events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
        self.assertTrue(any(row['event']=='process_observed' and row['pid']==child_pid for row in events))
        self.assertTrue(any(row['event']=='launch_exited' and row['exit_code']==0 for row in events))
        console = (folder / 'console.log').read_text()
        self.assertIn('[stdout]', console)
        self.assertIn('[stderr] stderr fixture', console)
        self.assertIn('stderr fixture', result.stderr)
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        for file in folder.rglob('*'):
            self.assertEqual(file.stat().st_mode & 0o777, 0o700 if file.is_dir() else 0o600)
            if file.is_file() and file.suffix in {'.json','.jsonl','.log'}:
                self.assertNotIn('must-not-persist-environment', file.read_text())

    def test_full_trace_and_repeated_launches_do_not_overwrite(self):
        for _ in range(2):
            result = self.fixture.run_cli('--prefix', self.prefix, '--full-trace', self.fixture.exe)
            self.assertEqual(result.returncode, 0, result.stderr)
        folders = self.recordings()
        self.assertEqual(len(folders), 2)
        for folder in folders:
            data = json.loads((folder / 'session.json').read_text())
            self.assertTrue(data['full_trace'])
            self.assertIn('+relay,+snoop', data['environment']['WINEDEBUG'])
            self.assertIn('ended', data)

    def test_prerequisite_and_game_logs_have_separate_directories(self):
        result = self.fixture.run_cli('--prefix', self.prefix, '--profile', 'icarus-phoenix', self.fixture.exe)
        self.assertEqual(result.returncode, 0, result.stderr)
        folder, = self.recordings()
        events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
        calls = [row for row in events if row['event']=='launch_requested']
        self.assertEqual(len(calls), 7)  # Six prerequisites followed by the game.
        paths = {row['backend_log_dir'] for row in calls}
        self.assertEqual(len(paths), 7)
        self.assertTrue(all(Path(path).is_dir() and Path(path).parent == folder / 'calls' for path in paths))

    def test_recording_error_is_reported_and_does_not_block_output_readers(self):
        from ge_wine_logging import RunLog
        from ge_wine_sessions import Session
        with patch.dict(os.environ, {'XDG_STATE_HOME': str(self.fixture.root / 'state')}):
            session = Session()
        self.addCleanup(session.close)
        environment = dict(os.environ, WINEPREFIX=str(self.prefix))
        command = [sys.executable, '-c', "print('test-output')"]
        recording = RunLog(environment, command, str(self.fixture.root), 'standard', session.token)
        self.addCleanup(recording.close)
        session.log = recording
        with patch.object(recording, 'console', side_effect=OSError('test disk full')):
            with self.assertRaisesRegex(OSError, 'test disk full'):
                session.run(command, environment, str(self.fixture.root), lambda text: None)

    def test_failed_backend_is_recorded_with_real_exit_code(self):
        result = self.fixture.run_cli('--prefix', self.prefix, self.fixture.exe, exit_code='37')
        self.assertEqual(result.returncode, 37)
        data = json.loads((self.recordings()[0] / 'session.json').read_text())
        self.assertEqual(data['exit_code'], 37)
        self.assertIsNone(data['error'])
