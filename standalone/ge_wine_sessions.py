# SPDX-License-Identifier: GPL-3.0-or-later
"""Linux process ownership for GE-Wine; never match Wine processes by name."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import sys
import threading
import uuid

TAG = "GE_WINE_SESSION_ID"


def identity(pid):
    try:
        path = Path('/proc') / str(pid)
        if path.stat().st_uid != os.getuid():
            return None
        fields = (path / 'stat').read_text().rsplit(') ', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError, ValueError):
        return None


def records_directory():
    base = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))
    path = base / 'ge-wine/sessions'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def marked_processes():
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or identity(int(path.name)) is None:
            continue
        try:
            for entry in (path / 'environ').read_bytes().split(b'\0'):
                if entry.startswith((TAG + '=').encode()):
                    value = entry.split(b'=', 1)[1].decode('ascii')
                    if len(value) == 32 and all(c in '0123456789abcdef' for c in value):
                        yield int(path.name), value
                    break
        except (OSError, UnicodeError):
            continue


def stop_children(session_id, known):
    targets = {pid for pid, token in marked_processes() if token == session_id}
    targets.update(int(pid) for pid, stamp in known.items() if identity(int(pid)) == stamp)
    for pid in targets:
        if pid <= 1 or pid == os.getpid():
            continue
        try:
            group = os.getpgid(pid)
            if group != os.getpgrp():
                os.killpg(group, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def stop_all():
    records = []
    tokens = set()
    for path in records_directory().glob('*.json'):
        try:
            data = json.loads(path.read_text())
            token = data['session']
            if token != path.stem or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
                continue
            records.append((path, data))
            tokens.add(token)
        except (OSError, ValueError, KeyError):
            continue
    active = {token for _, token in marked_processes()}
    active.update(data["session"] for _, data in records if identity(data.get("controller", 0)) == data.get("start")
                  and isinstance(data.get("controller"), int) and data["controller"] > 1)
    tokens.update(active)
    for token in tokens:
        known = next((data.get('children', {}) for _, data in records if data['session'] == token), {})
        stop_children(token, known)
    stopped = 0
    for path, data in records:
        pid = data.get('controller', 0)
        if isinstance(pid, int) and pid > 1 and pid != os.getpid() and identity(pid) == data.get('start'):
            try:
                os.kill(pid, signal.SIGTERM)
                stopped += 1
            except ProcessLookupError:
                pass
        path.unlink(missing_ok=True)
    return len(active)


class LaunchCancelled(Exception):
    pass


class Session:
    def __init__(self):
        self.token = uuid.uuid4().hex
        self.path = records_directory() / (self.token + '.json')
        self.cancelled = threading.Event()
        self.lock = threading.RLock()
        self.children = {}
        self.log = None
        self._save()

    def _save(self):
        data = dict(session=self.token, controller=os.getpid(), start=identity(os.getpid()), children=self.children)
        with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as output:
            json.dump(data, output)
        os.replace(output.name, self.path)

    def check(self):
        if self.cancelled.is_set():
            raise LaunchCancelled()

    def reset(self):
        self.cancelled.clear()

    def stop(self):
        self.cancelled.set()
        with self.lock:
            stop_children(self.token, self.children)

    def run(self, command, environment, directory, output=None):
        with self.lock:
            self.check()
            env = dict(environment, **{TAG: self.token})
            if self.log:
                log_directory = self.log.invocation(env)
                self.log.event('launch_requested', command=command, cwd=str(directory), backend_log_dir=log_directory)
            capture = bool(output or self.log)
            process = subprocess.Popen(command, env=env, cwd=directory, start_new_session=True,
                                       stdout=subprocess.PIPE if capture else None,
                                       stderr=subprocess.PIPE if capture else None,
                                       text=True, errors='replace', bufsize=1)
            stamp = identity(process.pid)
            if stamp is not None:
                self.children[str(process.pid)] = stamp
            self._save()
        readers = []; read_errors = []
        def consume(stream, source, destination):
            for line in stream:
                try:
                    if self.log and not read_errors:
                        line = self.log.console(source, line)
                    if not line:
                        continue
                    if output:
                        output(line)
                    else:
                        destination.write(line); destination.flush()
                except (OSError, ValueError) as error:
                    if not read_errors:
                        read_errors.append(error)
        try:
            if self.log:
                self.log.event('launch_started', pid=process.pid, start_ticks=stamp, command=command)
            if capture:
                for stream, source, destination in ((process.stdout, 'stdout', sys.stdout),
                                                     (process.stderr, 'stderr', sys.stderr)):
                    reader = threading.Thread(target=consume, args=(stream, source, destination), daemon=True)
                    reader.start(); readers.append(reader)
            result = process.wait()
            for reader in readers:
                reader.join()
            if read_errors:
                raise OSError('Aufzeichnung/Ausgabe fehlgeschlagen: ' + str(read_errors[0]))
            if self.log:
                self.log.event('launch_exited', pid=process.pid, start_ticks=stamp, exit_code=result)
            return result
        except BaseException:
            self.stop()
            process.wait()
            for reader in readers:
                reader.join()
            raise
        finally:
            for stream in (process.stdout, process.stderr):
                if stream:
                    stream.close()
            with self.lock:
                self.children.pop(str(process.pid), None)
                self._save()

    def close(self):
        self.path.unlink(missing_ok=True)
