# SPDX-License-Identifier: GPL-3.0-or-later
"""Private, per-launch logs. Sampling complements Wine's process trace."""
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import tempfile
import sys
import time

from ge_wine_sessions import TAG, identity
from ge_wine_dedup import LogIndex, LiveLogReader, compact_files


def timestamp():
    return datetime.now().astimezone().isoformat(timespec='microseconds')


REPETITIONS_INTERVAL = 20


class RunLog:
    def __init__(self, environment, command, directory, profile, token, full_trace=False, clock=time.monotonic):
        prefix = Path(environment['WINEPREFIX'])
        prefix.mkdir(parents=True, exist_ok=True)
        base = prefix / 'logs'
        base.mkdir(exist_ok=True, mode=0o700)
        self.path = base / (datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f') + '_' + token[:8])
        self.path.mkdir(mode=0o700)
        self.lock = threading.RLock()
        self.streams = {}
        for name in ('events.jsonl', 'console.log'):
            fd = os.open(self.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.streams[name] = os.fdopen(fd, 'w', encoding='utf-8', buffering=1)
        self.index = LogIndex(self.path)
        self.live_reader = LiveLogReader(self.index)
        self.clock = clock
        self.next_repetitions_export = self.clock()+REPETITIONS_INTERVAL
        self.lines = {"console.log": 0, "events.jsonl": 0}
        self.closed = False
        self.token = token
        self.prefixes = {str(prefix), str(prefix / 'pfx')}
        self.done = threading.Event()
        self.known = {}
        self.thread = None
        self.invocation_number = 0
        channels = '+timestamp,+pid,+tid,+process,+thread,+loaddll,+module,+seh'
        if full_trace:
            channels += ',+relay,+snoop'
        previous = environment.get('WINEDEBUG', '').strip(',')
        environment.update(PROTON_LOG='1', PROTON_LOG_DIR=str(self.path),
                           DXVK_LOG_PATH=str(self.path), DXVK_LOG_LEVEL='info', UMU_LOG='debug',
                           WINEDEBUG=','.join(part for part in (previous, channels) if part))
        self.metadata = dict(schema=1, started=timestamp(), session=token, prefix=str(prefix),
                             profile=profile, command=command, cwd=directory, full_trace=full_trace,
                             environment={key: value for key, value in environment.items()
                                          if key in {'WINEPREFIX', 'PROTONPATH', 'GAMEID', 'STORE', 'WINEDEBUG',
                                                     'PROTON_LOG', 'PROTON_LOG_DIR', 'DXVK_CONFIG', 'DXVK_HUD',
                                                     'DXVK_LOG_PATH', 'DXVK_LOG_LEVEL', 'UMU_LOG'}},
                             backend_log_layout='calls/NNNN/',
                             coverage='stdout/stderr, direct launches, Wine debug channels; ' + ('macOS' if sys.platform=='darwin' else 'Linux') + ' process sampling every 250 ms; no kernel syscall trace')
        self._metadata()
        self.event('recording_start', profile=profile)
        self.publish_repetitions(scan_native=False)

    def _metadata(self):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path, encoding='utf-8', delete=False) as output:
                temporary = Path(output.name)
                json.dump(self.metadata, output, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path / 'session.json')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def event(self, event, **fields):
        with self.lock:
            text = json.dumps(dict(time=timestamp(), event=event, **fields), ensure_ascii=False) + '\n'
            if self.index.record('events.jsonl',text.encode(),self.lines['events.jsonl']+1,structured=True):
                self.streams['events.jsonl'].write(text)
                self.lines['events.jsonl'] += 1

    def console(self, source, text):
        visible = []
        with self.lock:
            for part in text.splitlines(keepends=True):
                payload = f'[{source}] {part.rstrip(chr(10)+chr(13))}'
                if self.index.record('console.log',payload.encode('utf-8'),self.lines['console.log']+1):
                    self.streams['console.log'].write(f'[{timestamp()}] {payload}\n')
                    self.lines['console.log'] += 1
                    visible.append(part)
        return ''.join(visible)

    def invocation(self, environment):
        with self.lock:
            self.invocation_number += 1
            parent = self.path / 'calls'
            parent.mkdir(exist_ok=True, mode=0o700)
            directory = parent / f'{self.invocation_number:04d}'
            directory.mkdir(mode=0o700)
            environment.update(PROTON_LOG_DIR=str(directory), DXVK_LOG_PATH=str(directory))
            return str(directory)

    def snapshot(self):
        current = {}
        if sys.platform=='darwin':
            from ge_wine_macos_runtime import process_snapshot
            current = process_snapshot(self.token,self.prefixes)
        else:
            for path in Path('/proc').iterdir():
                if not path.name.isdigit():
                    continue
                pid = int(path.name)
                stamp = identity(pid)
                if stamp is None:
                    continue
                try:
                    # Do not persist the rest of the environment (credentials, cookies, etc.).
                    entries = (path / 'environ').read_bytes().split(b'\0')
                    tagged = (TAG + '=' + self.token).encode() in entries
                    in_prefix = any(('WINEPREFIX=' + prefix).encode() in entries for prefix in self.prefixes)
                    if not (tagged or in_prefix):
                        continue
                    stat = (path / 'stat').read_text().rsplit(') ', 1)[1].split()
                    command = [part.decode('utf-8', 'replace') for part in (path / 'cmdline').read_bytes().split(b'\0') if part]
                    name = (path / 'comm').read_text().strip()
                    try:
                        executable = os.readlink(path / 'exe')
                    except OSError:
                        executable = None
                    if identity(pid) != stamp:
                        continue
                    key = (pid, stamp)
                    current[key] = dict(pid=pid, start_ticks=stamp, parent=int(stat[1]), name=name,
                                        command=command, executable=executable,
                                        association='session' if tagged else 'shared-prefix')
                except (OSError, ValueError, IndexError):
                    continue
        for key, row in current.items():
            if key not in self.known:
                self.event('process_observed', **row)
            elif row != self.known[key]:
                self.event('process_changed', **row)
        for key, row in self.known.items():
            if key not in current:
                self.event('process_no_longer_observed', pid=row['pid'], start_ticks=row['start_ticks'])
        self.known = current

    def publish_repetitions(self, scan_native=True):
        backlog,errors = self.live_reader.scan((self.path/'calls').rglob('*.log')) if scan_native else ([],[])
        for entry in errors:
            self.event('native_log_scan_error',**entry)
        with self.lock:
            for name,output in self.streams.items():
                output.flush()
                self.index.checkpoint(name)
            native_files = list(self.live_reader.positions)
            summary = self.index.export(native_files,live_files=native_files)
            summary.update(state='running',updated_at=timestamp(),interval_seconds=REPETITIONS_INTERVAL,
                           live_indexing_pending=backlog,live_indexing_errors=errors)
            self.metadata['deduplication'] = summary
            self._metadata()
            return summary

    def start(self):
        self.snapshot()
        def monitor():
            while not self.done.wait(.25):
                try:
                    self.snapshot()
                    current = self.clock()
                    if current >= self.next_repetitions_export:
                        self.publish_repetitions()
                        # Keep the cadence without a burst of catch-up exports after a slow scan.
                        self.next_repetitions_export += ((self.clock()-self.next_repetitions_export)//REPETITIONS_INTERVAL+1)*REPETITIONS_INTERVAL
                except OSError as error:
                    self.event('monitor_error', error=str(error))
        self.thread = threading.Thread(target=monitor, daemon=True)
        self.thread.start()

    def close(self, result=None, error=None):
        if self.closed:
            return self.metadata.get('deduplication', {})
        self.done.set()
        if self.thread:
            self.thread.join()
        try:
            self.snapshot()
            self.event('recording_end', exit_code=result, error=error,
                       still_observed=[row['pid'] for row in self.known.values()])
            self.metadata.update(ended=timestamp(),exit_code=result,error=error,
                                 still_observed=list(self.known.values()))
            self._metadata()
            for name, output in self.streams.items():
                output.flush()
                self.index.checkpoint(name)
            pending = compact_files(self.index, (self.path/'calls').rglob('*.log'))
            backlog,errors = self.live_reader.scan([self.path/file for file in pending]) if pending else ([],[])
            summary = self.index.export(pending,live_files=pending)
            summary.update(state='finished',updated_at=timestamp(),interval_seconds=REPETITIONS_INTERVAL,
                           live_indexing_pending=backlog,live_indexing_errors=errors)
            self.metadata.update(ended=timestamp(), exit_code=result, error=error,
                                 still_observed=list(self.known.values()), deduplication=summary)
            self._metadata()
            return summary
        except OSError as processing_error:
            self.metadata['log_processing_error'] = str(processing_error)
            self._metadata()
            raise
        finally:
            self.closed = True
            for output in self.streams.values():
                output.close()
            self.index.close()
