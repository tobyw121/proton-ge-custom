# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit, launch-time clock control; never inject into an existing process."""
import fcntl
import json
import math
import os
import re
from pathlib import Path
import shutil
import stat
import struct
import tempfile
from datetime import datetime

from ge_wine_sessions import identity

MAGIC = b'GESPD01\0'
SIZE = 64
MIN_FACTOR, MAX_FACTOR = .05, 20.0


def validate_factor(value):
    try:
        factor = float(str(value).replace(',', '.'))
    except (ValueError, TypeError) as error:
        raise ValueError('Geschwindigkeit als Zahl zwischen 0,05 und 20 eingeben.') from error
    if not math.isfinite(factor) or not MIN_FACTOR <= factor <= MAX_FACTOR:
        raise ValueError('Geschwindigkeit muss zwischen 0,05 und 20 liegen; 1 = normal.')
    return factor


class SpeedSession:
    def __init__(self, environment, libraries=None):
        libraries = Path(libraries or os.environ.get('GE_WINE_SPEED_LIBRARY_DIR', Path(__file__).with_name('speed')))
        source = libraries / 'lib64/libge_speed.so'
        if not source.is_file():
            raise ValueError('Speedhack-Bibliothek fehlt. Vollständiges GE-Wine-Paket installieren oder build_speed.sh ausführen.')
        self.path = Path(tempfile.mkdtemp(prefix=f'ge-wine-speed-{os.getuid()}-', dir='/tmp'))
        try:
            for architecture, destinations in (('lib64', ('lib64', 'lib/x86_64-linux-gnu')),
                                               ('lib32', ('lib32', 'lib/i386-linux-gnu'))):
                library = libraries / architecture / 'libge_speed.so'
                if not library.is_file():
                    continue
                for destination in destinations:
                    directory = self.path / destination
                    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    shutil.copyfile(library, directory / library.name)
                    (directory / library.name).chmod(0o500)
            environment['GE_WINE_SPEED_DIR'] = str(self.path)
            preload = str(self.path / '$LIB/libge_speed.so')
            previous = ':'.join(item for item in re.split(r'[:\s]+', environment.get('LD_PRELOAD', ''))
                                if item and Path(item).name != 'libge_speed.so')
            environment['LD_PRELOAD'] = preload + (':' + previous if previous else '')
            previous = environment.get('PRESSURE_VESSEL_FILESYSTEMS_RW', '')
            environment['PRESSURE_VESSEL_FILESYSTEMS_RW'] = ':'.join(filter(None, (previous, str(self.path))))
        except BaseException:
            shutil.rmtree(self.path)
            raise

    def close(self, archive=None):
        if not self.path.exists():
            return
        # Closing the launch restores normal speed, even for a surviving background process.
        for process in Path('/proc').iterdir():
            if not process.name.isdigit() or identity(int(process.name)) is None:
                continue
            try:
                marker = ('GE_WINE_SPEED_DIR=' + str(self.path)).encode()
                if marker not in (process / 'environ').read_bytes().split(b'\0'):
                    continue
                SpeedControl(int(process.name), directory=self.path).set(1)
            except (OSError, ValueError):
                continue
        commands = self.path / 'commands.jsonl'
        try:
            if archive is not None and commands.exists():
                target = Path(archive) / 'speed-commands.jsonl'
                shutil.copyfile(commands, target)
                target.chmod(0o600)
        finally:
            # Mapped pages remain alive until process exit. The final request is 1x.
            shutil.rmtree(self.path)


class SpeedControl:
    def __init__(self, pid, directory=None, expected_stamp=None):
        self.pid = int(pid)
        self.stamp = identity(self.pid)
        if self.stamp is None or expected_stamp is not None and self.stamp != str(expected_stamp):
            raise ProcessLookupError('Zielprozess wurde beendet oder PID wiederverwendet.')
        if directory is None:
            entry = next((item for item in Path(f'/proc/{self.pid}/environ').read_bytes().split(b'\0')
                          if item.startswith(b'GE_WINE_SPEED_DIR=')), None)
            if entry is None:
                raise ValueError('Programm neu über GE-Wine mit „Speedhack-Steuerung“ oder --speed-control starten.')
            directory = os.fsdecode(entry.split(b'=', 1)[1])
        self.directory = Path(directory)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Ungültiger Speedhack-Steuerordner.')
        # Pressure-vessel may expose a different PID inside its PID namespace.
        namespace_pids = [self.pid]
        try:
            for line in Path(f'/proc/{self.pid}/status').read_text().splitlines():
                if line.startswith('NSpid:'):
                    namespace_pids = [int(value) for value in line.split()[1:]] or namespace_pids
                    break
        except (OSError, ValueError):
            pass
        self.namespace_pid = self.pid
        for candidate in reversed(namespace_pids):
            candidate_path = self.directory / f'{candidate}-{self.stamp}.control'
            if candidate_path.exists():
                self.namespace_pid = candidate
                break
        self.path = self.directory / f'{self.namespace_pid}-{self.stamp}.control'
        self.read()

    def _open(self):
        if identity(self.pid) != self.stamp:
            raise ProcessLookupError('Zielprozess wurde beendet oder PID wiederverwendet.')
        descriptor = os.open(self.path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size != SIZE or info.st_mode & 0o077:
            os.close(descriptor)
            raise ValueError('Ungültige Speedhack-Steuerdatei.')
        return descriptor

    def read(self):
        descriptor = self._open()
        try:
            data = os.pread(descriptor, SIZE, 0)
        finally:
            os.close(descriptor)
        if len(data) != SIZE or data[:8] != MAGIC:
            raise ValueError('Speedhack-Bibliothek noch nicht bereit.')
        pid, stamp, request, requested, applied, actual, calls = struct.unpack_from('<QQQdQdQ', data, 8)
        if pid != self.namespace_pid or str(stamp) != self.stamp:
            raise ValueError('Speedhack-Prozesskennung stimmt nicht überein.')
        return dict(pid=self.pid, namespace_pid=pid, requested=requested, applied=actual, pending=bool(request & 1 or request != applied), calls=calls)

    def set(self, value):
        factor = validate_factor(value)
        descriptor = self._open()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self.read()
            sequence = struct.unpack('<Q', os.pread(descriptor, 8, 24))[0]
            sequence = sequence + 1 if not sequence & 1 else sequence + 2
            for offset, raw in ((24, struct.pack('<Q', sequence)), (32, struct.pack('<d', factor)),
                                (24, struct.pack('<Q', sequence+1))):
                if os.pwrite(descriptor, raw, offset) != 8:
                    raise OSError('Geschwindigkeitsanforderung unvollständig geschrieben.')
            event = dict(time=datetime.now().astimezone().isoformat(), pid=self.pid, namespace_pid=self.namespace_pid,
                         start_ticks=self.stamp, factor=factor, request=sequence+1)
            fd = os.open(self.directory / 'commands.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, 'w') as output:
                output.write(json.dumps(event) + '\n')
        finally:
            os.close(descriptor)
        return self.read()
