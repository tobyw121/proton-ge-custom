import json
from pathlib import Path
import tempfile
import sys
import threading
import unittest
from unittest.mock import patch

import test_launcher
from ge_wine_dedup import LogIndex, compact_recording
from ge_wine_logging import RunLog


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def recording(self):
        environment = {'WINEPREFIX':str(self.root/'prefix')}
        recording = RunLog(environment,['test.exe'],str(self.root),'standard','a'*32)
        self.addCleanup(recording.close)
        return recording

    def repetitions(self, recording):
        return [json.loads(line) for line in (recording.path/'repetitions.jsonl').read_text().splitlines()]

    def test_timestamps_are_ignored_but_pid_tid_parameters_and_channels_are_kept(self):
        log = self.recording()
        first = '100.001:00ab:00cd:trace:call: value=10\n'
        self.assertEqual(log.console('stderr',first),first)
        self.assertEqual(log.console('stderr','100.002:00ab:00cd:trace:call: value=10\n'),'')
        self.assertEqual(log.console('stderr','other message\n'),'other message\n')
        self.assertEqual(log.console('stderr','100.003:00ab:00cd:trace:call: value=10\n'),'')
        for text in ('100.004:00ef:00cd:trace:call: value=10\n',
                     '100.004:00ab:00ef:trace:call: value=10\n',
                     '100.004:00ab:00cd:trace:call: value=11\n'):
            self.assertEqual(log.console('stderr',text),text)
        self.assertEqual(log.console('stdout',first),first)
        log.close()
        self.assertEqual(len((log.path/'console.log').read_text().splitlines()),6)
        repeated, = self.repetitions(log)
        self.assertEqual(repeated['count'],3)
        self.assertEqual(repeated['line'],1)
        self.assertEqual(repeated['first_runtime_timestamp'],'100.001')
        self.assertEqual(repeated['last_runtime_timestamp'],'100.003')

    def test_many_repetitions_use_one_line_and_keep_complete_count(self):
        log = self.recording()
        self.assertEqual(log.console('stderr','repeat\n'*12000),'repeat\n')
        summary = log.close()
        self.assertEqual(summary['suppressed_lines'],11999)
        self.assertEqual(self.repetitions(log)[0]['count'],12000)
        self.assertEqual((log.path/'console.log').read_text().count('[stderr] repeat'),1)

    def test_evicted_messages_are_still_deduplicated_without_unbounded_memory(self):
        log = self.recording()
        log.console('stdout','first\n')
        for index in range(3000):
            log.console('stdout',f'unique {index}\n')
        self.assertLessEqual(len(log.index.cache),2048)
        self.assertEqual(log.console('stdout','first\n'),'')
        log.close()
        self.assertEqual(len((log.path/'console.log').read_text().splitlines()),3001)
        self.assertEqual(self.repetitions(log)[0]['count'],2)

    def test_parallel_streams_and_structured_events_keep_their_counts(self):
        log = self.recording()
        def write():
            for _ in range(500):
                log.console('stdout','same\n')
                log.event('monitor_error',error='same error')
        threads = [threading.Thread(target=write) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        log.event('monitor_error',error='different error')
        log.close()
        repeated = self.repetitions(log)
        self.assertEqual(sorted(row['count'] for row in repeated),[2000,2000])
        events = [json.loads(row) for row in (log.path/'events.jsonl').read_text().splitlines()]
        self.assertEqual(len([row for row in events if row['event']=='monitor_error']),2)

    def test_open_native_log_is_left_intact_and_can_be_retried_idempotently(self):
        log = self.recording()
        environment = {}
        folder = Path(log.invocation(environment))
        path = folder/'Game_d3d9.log'
        writer = path.open('w')
        try:
            writer.write('info: repeated\n'*40); writer.flush()
            summary = log.close()
            self.assertEqual(summary['pending'],['calls/0001/Game_d3d9.log'])
            self.assertEqual(len(path.read_text().splitlines()),40)
        finally:
            writer.close()
        summary = compact_recording(log.path)
        self.assertEqual(path.read_text(),'info: repeated\n')
        self.assertEqual(summary['suppressed_lines'],39)
        self.assertEqual(summary['pending'],[])
        self.assertEqual(compact_recording(log.path),summary)
        self.assertEqual(self.repetitions(log)[0]['count'],40)

    def test_active_recording_cannot_be_rewritten_by_manual_command(self):
        log = self.recording()
        with self.assertRaisesRegex(ValueError,'noch geöffnet'):
            compact_recording(log.path)

    def test_failed_atomic_replacement_preserves_original_and_retry_counts(self):
        path = self.root/'native.log'
        original = b'one\none\ntwo\n'
        path.write_bytes(original)
        index = LogIndex(self.root)
        self.addCleanup(index.close)
        with patch('ge_wine_dedup.os.replace',side_effect=OSError('test replace failed')):
            with self.assertRaisesRegex(OSError,'test replace failed'):
                index.compact(path)
        self.assertEqual(path.read_bytes(),original)
        index.compact(path)
        self.assertEqual(path.read_bytes(),b'one\ntwo\n')
        self.assertEqual(index.export()['suppressed_lines'],1)

    def test_real_backend_console_and_native_files_are_compacted_automatically(self):
        fixture = test_launcher.LauncherTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.backend.write_text(f'''#!{sys.executable}
import os,sys
from pathlib import Path
folder=Path(os.environ['PROTON_LOG_DIR'])
with (folder/'steam-default.log').open('wb') as log:
    for i in range(2000): log.write(f'{{i}}.000:00ab:00cd:trace:call: value=10\\n'.encode())
    log.write(b'2000.000:00ab:00cd:trace:call: value=11\\n')
    log.write(b'\\xff invalid byte\\n\\xff invalid byte\\n\\xfe different byte\\n')
for _ in range(2000): print('repeat console',file=sys.stderr)
''')
        prefix = fixture.root/'prefix'
        result = fixture.run_cli('--prefix',prefix,fixture.exe)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stderr.count('repeat console'),1)
        root, = (prefix/'logs').iterdir()
        data = (root/'calls/0001/steam-default.log').read_bytes()
        self.assertEqual(data,b'0.000:00ab:00cd:trace:call: value=10\n2000.000:00ab:00cd:trace:call: value=11\n\xff invalid byte\n\xfe different byte\n')
        metadata = json.loads((root/'session.json').read_text())
        self.assertEqual(metadata['deduplication']['suppressed_lines'],3999)
        repeated = [json.loads(line) for line in (root/'repetitions.jsonl').read_text().splitlines()]
        self.assertEqual(sorted(row['count'] for row in repeated),[2,2000,2000])
        self.assertEqual(compact_recording(root)['suppressed_lines'],3999)

    def test_two_compactors_cannot_rewrite_the_same_recording(self):
        index = LogIndex(self.root)
        self.addCleanup(index.close)
        with self.assertRaisesRegex(ValueError,'bereits geöffnet'):
            LogIndex(self.root)

    def test_legacy_command_normalizes_iso_and_color_without_losing_payload(self):
        fixture = test_launcher.LauncherTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        folder = fixture.root/'old-logs'; folder.mkdir()
        (folder/'session.json').write_text(json.dumps(dict(schema=1,session='a'*32,ended='old')))
        (folder/'console.log').write_text('[2026-11-07T10:00:00+00:00] [stderr] \\x1b[31mwarning\\x1b[0m\n'.replace('\\x1b','\x1b') +
                                          '[2026-11-07T10:01:00+00:00] [stderr] warning\n')
        result = fixture.run_cli('--compact-logs',folder)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['suppressed_lines'],1)
        self.assertEqual(len((folder/'console.log').read_text().splitlines()),1)
