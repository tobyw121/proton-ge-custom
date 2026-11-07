# SPDX-License-Identifier: GPL-3.0-or-later
"""Disk-backed log deduplication; only known timestamp prefixes are ignored."""
from datetime import datetime
from collections import OrderedDict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import threading

ANSI = re.compile(rb'\x1b\[[0-?]*[ -/]*[@-~]')
ISO = re.compile(rb'^\[(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]\s*')
WINE = re.compile(rb'^(\d+\.\d+):(?=[0-9a-fA-F]{4,16}:)')
CHANNEL = re.compile(rb'^(\[(?:stdout|stderr|ge-wine)\] )')


def now():
    return datetime.now().astimezone().isoformat(timespec='microseconds')


def canonical(line, console=False, structured=False):
    """Retain payload, PID/TID, numbers and whitespace. Never scrub addresses."""
    line = line.rstrip(b'\r\n')
    if structured:
        try:
            event = json.loads(line)
            if isinstance(event, dict) and 'event' in event:
                stamp = event.pop('time', None)
                return json.dumps(event, sort_keys=True, ensure_ascii=False).encode(), stamp
        except (ValueError, UnicodeError):
            pass
    value = ANSI.sub(b'', line)
    runtime_stamp = None
    match = ISO.match(value)
    if match:
        runtime_stamp = match[1].decode('ascii'); value = value[match.end():]
    prefix = b''
    if console:
        match = CHANNEL.match(value)
        if match:
            prefix = match[1]; value = value[match.end():]
    # Console messages may themselves carry a runtime timestamp.
    match = ISO.match(value) or WINE.match(value)
    if match:
        runtime_stamp = match[1].decode('ascii'); value = value[match.end():]
    return prefix + value, runtime_stamp


class LogIndex:
    """Store fingerprints and counters in SQLite, not an unbounded RAM set."""
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.pending = 0
        self.cache = OrderedDict()
        self.guard = os.open(self.root/'.dedup.lock',os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,0o600)
        try:
            fcntl.flock(self.guard,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(self.guard)
            raise ValueError('Diese Aufzeichnung ist bereits geöffnet oder wird verarbeitet.') from error
        path = self.root / 'dedup.sqlite3'
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            os.close(fd)
            self.connection = sqlite3.connect(path, check_same_thread=False)
            self.connection.execute('PRAGMA synchronous=NORMAL')
            self.connection.execute('PRAGMA cache_size=-2048')
            self.connection.execute('''CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, file TEXT NOT NULL, digest BLOB NOT NULL,
                line INTEGER NOT NULL, count INTEGER NOT NULL, first_seen TEXT, last_seen TEXT,
                first_runtime_timestamp TEXT, last_runtime_timestamp TEXT, UNIQUE(file,digest))''')
            self.connection.execute('''CREATE TABLE IF NOT EXISTS files (
                file TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER)''')
        except (sqlite3.Error,OSError) as error:
            if hasattr(self,'connection'):
                self.connection.close()
            os.close(self.guard)
            raise OSError('Log-Index konnte nicht geöffnet werden: ' + str(error)) from error

    def execute(self, query, parameters=()):
        try:
            return self.connection.execute(query, parameters)
        except sqlite3.Error as error:
            raise OSError('Log-Index fehlgeschlagen: ' + str(error)) from error

    def flush_cached(self, key, value):
        if value[0]:
            self.execute('UPDATE messages SET count=count+?,last_seen=?,last_runtime_timestamp=? WHERE file=? AND digest=?',
                         (*value,*key))
            value[0] = 0

    def commit(self):
        try:
            for key,value in self.cache.items():
                self.flush_cached(key,value)
            self.connection.commit(); self.pending = 0
        except sqlite3.Error as error:
            raise OSError('Log-Index konnte nicht gespeichert werden: ' + str(error)) from error

    def record(self, file, payload, line, structured=False):
        key, runtime_stamp = canonical(payload, console=file=='console.log', structured=structured)
        digest = hashlib.sha256(key).digest()
        seen = now()
        with self.lock:
            cache_key = (file,digest)
            cached = self.cache.get(cache_key)
            if cached is not None:
                cached[0] += 1; cached[1:] = [seen,runtime_stamp]
                self.cache.move_to_end(cache_key)
                unique = False
            else:
                row = self.execute('''INSERT INTO messages
                    (file,digest,line,count,first_seen,last_seen,first_runtime_timestamp,last_runtime_timestamp)
                    VALUES (?,?,?,1,?,?,?,?) ON CONFLICT(file,digest) DO UPDATE SET
                    count=count+1,last_seen=excluded.last_seen,last_runtime_timestamp=excluded.last_runtime_timestamp
                    RETURNING count''', (file,digest,line,seen,seen,runtime_stamp,runtime_stamp)).fetchone()
                unique = row[0] == 1
                self.cache[cache_key] = [0,seen,runtime_stamp]
                if len(self.cache)>2048:
                    old_key,old_value = self.cache.popitem(last=False)
                    self.flush_cached(old_key,old_value)
            self.pending += 1
            if self.pending >= 1024:
                self.commit()
            return unique

    def checkpoint(self, file):
        info = (self.root / file).stat()
        self.execute('INSERT OR REPLACE INTO files VALUES (?,?,?)',(file,info.st_size,info.st_mtime_ns))
        self.commit()

    def compact(self, path):
        """Only call after checking writers; atomic replace preserves the original on error."""
        path = Path(path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return
        file = str(path.relative_to(self.root))
        previous = self.execute('SELECT size,mtime_ns FROM files WHERE file=?',(file,)).fetchone()
        if previous == (info.st_size, info.st_mtime_ns):
            return
        # A replaced/modified file is a new generation, rather than a second reading of old messages.
        self.commit()
        for key in [key for key in self.cache if key[0]==file]:
            del self.cache[key]
        self.execute('DELETE FROM messages WHERE file=?',(file,))
        temporary = None
        try:
            with path.open('rb') as source, tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                kept = 0
                for raw in source:
                    if self.record(file, raw, kept+1, structured=file=='events.jsonl'):
                        output.write(raw); kept += 1
            current = path.stat()
            if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns) != (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns):
                raise OSError(f'Logdatei wurde während der Kompaktierung verändert: {path}')
            os.replace(temporary, path)
            self.checkpoint(file)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def export(self, pending=()):
        self.commit()
        target = self.root / 'repetitions.jsonl'
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=self.root,encoding='utf-8',delete=False) as output:
                temporary = Path(output.name)
                rows = self.execute('''SELECT id,file,line,count,first_seen,last_seen,
                    first_runtime_timestamp,last_runtime_timestamp FROM messages WHERE count>1 ORDER BY id''')
                keys = ('id','file','line','count','first_seen','last_seen','first_runtime_timestamp','last_runtime_timestamp')
                for row in rows:
                    output.write(json.dumps(dict(zip(keys,row)),ensure_ascii=False)+'\n')
            os.replace(temporary,target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        totals = self.execute('SELECT COUNT(*),COALESCE(SUM(count),0) FROM messages').fetchone()
        return dict(version=1, unique_lines=totals[0], total_lines=totals[1],
                    suppressed_lines=totals[1]-totals[0], pending=list(pending),
                    repetitions_file='repetitions.jsonl', index_file='dedup.sqlite3')

    def close(self):
        try:
            self.commit()
        finally:
            self.connection.close()
            os.close(self.guard)


def open_writers(root):
    """Best-effort /proc check, including writers which outlive the launcher."""
    root = str(Path(root).resolve()) + '/'
    writers = set()
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            for fd in (process/'fd').iterdir():
                try:
                    if not os.readlink(fd).startswith(root):
                        continue
                    flags = next(line.split()[1] for line in (process/'fdinfo'/fd.name).read_text().splitlines() if line.startswith('flags:'))
                    if int(flags,8) & os.O_ACCMODE:
                        info = fd.stat(); writers.add((info.st_dev,info.st_ino))
                except (OSError,ValueError,StopIteration):
                    continue
        except OSError:
            continue
    return writers


def compact_files(index, paths):
    writers = open_writers(index.root)
    pending = []
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            continue
        info = path.stat()
        if (info.st_dev,info.st_ino) in writers:
            pending.append(str(path.relative_to(index.root)))
            continue
        index.compact(path)
    return pending


def compact_recording(directory):
    """Upgrade a finished legacy recording, or retry files which still had writers."""
    root = Path(directory).expanduser().resolve()
    metadata_path = root/'session.json'
    if not metadata_path.is_file() or metadata_path.is_symlink():
        raise ValueError('Einen datierten GE-Wine-Logordner mit session.json angeben.')
    metadata = json.loads(metadata_path.read_text())
    if metadata.get('schema') != 1 or not isinstance(metadata.get('session'),str):
        raise ValueError('Kein unterstützter GE-Wine-Logordner.')
    writers = open_writers(root)
    for name in ('console.log','events.jsonl','dedup.sqlite3'):
        path = root/name
        if path.exists():
            info = path.stat()
            if (info.st_dev,info.st_ino) in writers:
                raise ValueError('Diese Aufzeichnung ist noch geöffnet. Zuerst die GE-Wine-Sitzung beenden.')
    index = LogIndex(root)
    try:
        paths = [path for path in root.rglob('*.log') if not path.is_symlink()]
        if (root/'events.jsonl').is_file():
            paths.append(root/'events.jsonl')
        pending = compact_files(index, paths)
        summary = index.export(pending)
        metadata['deduplication'] = summary
        with tempfile.NamedTemporaryFile(mode='w',dir=root,encoding='utf-8',delete=False) as output:
            temporary = Path(output.name)
            try:
                json.dump(metadata,output,ensure_ascii=False,indent=2)
                output.flush()
                os.replace(temporary,metadata_path)
            finally:
                temporary.unlink(missing_ok=True)
        return summary
    finally:
        index.close()
