import json
import os
import subprocess
import time
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

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('Timed out waiting for a repetitions snapshot')

    def test_file_exists_immediately_and_monitor_updates_on_twenty_second_boundaries(self):
        class Clock:
            value = 0
            observed = threading.Event()
            def __call__(self):
                value = self.value
                self.observed.set()
                return value
        clock = Clock()
        environment = {'WINEPREFIX':str(self.root/'prefix')}
        log = RunLog(environment,['test.exe'],str(self.root),'standard','b'*32,clock=clock)
        self.addCleanup(log.close)
        self.assertEqual((log.path/'repetitions.jsonl').read_bytes(),b'')
        log.console('stdout','repeat\n'*3)
        with patch.object(log,'snapshot'):
            log.start()
            clock.value=19.9; clock.observed.clear()
            self.assertTrue(clock.observed.wait(2))
            self.assertEqual(self.repetitions(log),[])
            clock.value=20
            self.wait_for(lambda: bool(self.repetitions(log)))
            self.assertEqual(self.repetitions(log)[0]['count'],3)
            self.wait_for(lambda:log.next_repetitions_export==40)
            log.console('stdout','repeat\n'*2)
            clock.value=39.9; clock.observed.clear()
            self.assertTrue(clock.observed.wait(2))
            self.assertEqual(self.repetitions(log)[0]['count'],3)
            clock.value=40
            self.wait_for(lambda:self.repetitions(log)[0]['count']==5)
            self.assertTrue(log.thread.is_alive())
            metadata = json.loads((log.path/'session.json').read_text())
            self.wait_for(lambda:json.loads((log.path/'session.json').read_text())['deduplication']['suppressed_lines']==4)
            self.assertEqual(metadata['deduplication']['interval_seconds'],20)
        log.close()
        self.assertEqual(self.repetitions(log)[0]['count'],5)

    def test_live_native_reader_counts_only_new_complete_lines_without_modifying_file(self):
        log = self.recording()
        folder = Path(log.invocation({}))
        path = folder/'steam-default.log'
        with path.open('wb') as writer:
            writer.write(b'1.000:00ab:00cd:trace:repeat\n'*3+b'partial')
            writer.flush()
            original = path.read_bytes()
            log.publish_repetitions()
            row, = self.repetitions(log)
            self.assertEqual(row['count'],3)
            self.assertEqual(row['line_reference'],'raw-live')
            self.assertEqual(path.read_bytes(),original)
            log.publish_repetitions()
            self.assertEqual(self.repetitions(log)[0]['count'],3)
            writer.write(b' line\n1.100:00ab:00cd:trace:repeat\n');writer.flush()
            log.publish_repetitions()
            self.assertEqual(self.repetitions(log)[0]['count'],4)
        log.close()
        self.assertEqual(self.repetitions(log)[0]['count'],4)
        self.assertEqual(self.repetitions(log)[0]['line_reference'],'compact')
        self.assertEqual(path.read_bytes(),b'1.000:00ab:00cd:trace:repeat\npartial line\n')

    def test_live_read_budget_is_fair_to_other_files(self):
        log = self.recording()
        folder = Path(log.invocation({}))
        large = folder/'a.log'; small = folder/'b.log'
        large.write_bytes(b'a\n'*100);small.write_bytes(b'b\n'*3)
        log.live_reader.scan([large,small],max_bytes=8)
        log.live_reader.scan([large,small],max_bytes=8)
        log.index.export()
        counts = {row['file']:row['count'] for row in self.repetitions(log)}
        self.assertEqual(counts['calls/0001/a.log'],5)
        self.assertEqual(counts['calls/0001/b.log'],3)
        self.assertEqual(large.read_bytes(),b'a\n'*100)

    def test_native_log_truncation_resets_live_counts(self):
        log = self.recording()
        path = Path(log.invocation({}))/'native.log'
        path.write_bytes(b'old\n'*100)
        log.publish_repetitions()
        self.assertEqual(self.repetitions(log)[0]['count'],100)
        path.write_bytes(b'new\n'*2)
        log.publish_repetitions()
        row, = self.repetitions(log)
        self.assertEqual(row['count'],2)
        log.publish_repetitions()
        self.assertEqual(self.repetitions(log)[0]['count'],2)

    def test_failed_snapshot_replacement_preserves_last_complete_snapshot(self):
        log = self.recording()
        log.console('stdout','repeat\n'*5)
        log.publish_repetitions()
        previous = (log.path/'repetitions.jsonl').read_bytes()
        log.console('stdout','repeat\n'*2)
        with patch('ge_wine_dedup.os.replace',side_effect=OSError('test replace failed')):
            with self.assertRaisesRegex(OSError,'test replace failed'):
                log.publish_repetitions()
        self.assertEqual((log.path/'repetitions.jsonl').read_bytes(),previous)
        log.close()
        self.assertEqual(self.repetitions(log)[0]['count'],7)

    def test_last_published_snapshot_survives_sigterm_and_sigkill(self):
        source = Path(test_launcher.SOURCE).parent
        code = ("import sys,time;sys.path.insert(0,sys.argv[1]);"
                "import ge_wine_logging as module;module.REPETITIONS_INTERVAL=.05;"
                "log=module.RunLog({'WINEPREFIX':sys.argv[2]},['test.exe'],sys.argv[2],'standard','c'*32);"
                "log.console('stdout',('repeat'+chr(10))*5);log.start();"
                "print(str(log.path),flush=True);time.sleep(60)")
        for termination in ('terminate','kill'):
            with self.subTest(termination=termination):
                process = subprocess.Popen([sys.executable,'-c',code,str(source),str(self.root/termination)],
                                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    ready = process.stdout.readline().strip()
                    if not ready:
                        self.fail(process.stderr.read())
                    folder = Path(ready)
                    target = folder/'repetitions.jsonl'
                    self.wait_for(lambda:target.exists() and bool(target.read_bytes()))
                    getattr(process,termination)();process.wait(timeout=5)
                    rows = [json.loads(line) for line in target.read_text().splitlines()]
                    self.assertEqual(rows[0]['count'],5)
                finally:
                    if process.poll() is None:
                        process.kill();process.wait()
                    process.stdout.close();process.stderr.close()

    def test_wine_envelopes_are_ignored_but_payload_and_channels_are_kept(self):
        log = self.recording()
        first = '100.001:00ab:00cd:trace:call: value=10\n'
        self.assertEqual(log.console('stderr',first),first)
        self.assertEqual(log.console('stderr','100.002:00ab:00cd:trace:call: value=10\n'),'')
        self.assertEqual(log.console('stderr','other message\n'),'other message\n')
        self.assertEqual(log.console('stderr','100.003:00ab:00cd:trace:call: value=10\n'),'')
        for text in ('100.004:00ef:00cd:trace:call: value=10\n',
                     '100.004:00ab:00ef:trace:call: value=10\n'):
            self.assertEqual(log.console('stderr',text),'')
        changed='100.005:00ab:00cd:trace:call: value=11\n'
        self.assertEqual(log.console('stderr',changed),changed)
        self.assertEqual(log.console('stdout',first),first)
        log.close()
        self.assertEqual(len((log.path/'console.log').read_text().splitlines()),4)
        repeated, = self.repetitions(log)
        self.assertEqual(repeated['count'],5)
        self.assertEqual(repeated['line'],1)
        self.assertEqual(repeated['first_runtime_timestamp'],'100.001')
        self.assertEqual(repeated['last_runtime_timestamp'],'100.004')

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

    def test_retry_keeps_live_line_references_until_native_writer_closes(self):
        log = self.recording()
        path = Path(log.invocation({}))/'native.log'
        with path.open('w') as writer:
            writer.write('a\na\nb\nb\n');writer.flush()
            log.close()
            compact_recording(log.path)
            self.assertEqual([row['line_reference'] for row in self.repetitions(log)],['raw-live','raw-live'])
            self.assertEqual([row['line'] for row in self.repetitions(log)],[1,3])
        compact_recording(log.path)
        self.assertEqual([row['line_reference'] for row in self.repetitions(log)],['compact','compact'])
        self.assertEqual([row['line'] for row in self.repetitions(log)],[1,2])
        self.assertEqual([row['count'] for row in self.repetitions(log)],[2,2])

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
