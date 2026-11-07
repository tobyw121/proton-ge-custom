# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit clock control for prepared processes and user-authorized debugger attachment."""
import fcntl
import json
import math
import os
import re
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import signal
import tempfile
from datetime import datetime
from collections import deque

from ge_wine_sessions import identity, records_directory

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


def attachment_record(pid, stamp):
    directory=records_directory().parent/'attached-speed'
    directory.mkdir(exist_ok=True,mode=0o700)
    return directory/f'{pid}-{stamp}.json'


def attached_directory(pid, stamp):
    path=attachment_record(pid,stamp)
    try:
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise ValueError('Ungültiger Speedhack-Anhängeeintrag.')
        data=json.loads(path.read_text())
        if data['pid']!=pid or data['start_ticks']!=stamp:
            raise ValueError('Speedhack-Prozesskennung stimmt nicht überein.')
        return data['directory']
    except FileNotFoundError:
        return None
    except (KeyError,TypeError) as error:
        raise ValueError('Ungültiger Speedhack-Anhängeeintrag.') from error



def debugger_summary(path):
    """Keep the complete log on disk, but never flood a message box with thread notices."""
    tail=deque(maxlen=3);important=deque(maxlen=4);hooks=None;context=0
    noise=re.compile(r'^\[(?:New LWP|New Thread|Thread .* exited|LWP .* exited)\b')
    failure=re.compile(r'error|failed|cannot|could not|couldn.t|not found|no symbol|undefined|permission|ptrace|GLIBC_|GE_SPEED_ERROR',re.I)
    with Path(path).open(encoding='utf-8',errors='replace') as output:
        for raw in output:
            line=raw.strip()
            match=re.search(r'GE_SPEED_HOOKS=(-?\d+)',line)
            if match:hooks=int(match[1])
            if not line or noise.match(line):continue
            # Keep contextual lines after a debugger error (its explanation often follows).
            if failure.search(line):context=2
            if context:
                important.append(line[:180]);context-=1
            tail.append(line[:180])
    return hooks,'\n'.join(important or tail)[:750]


def debugger_failure(hooks):
    if hooks==0:return 'Keine unterstützten clock_gettime-Importe in den geladenen Modulen gefunden.'
    if hooks==-1:return 'Zeitimporte konnten nicht vollständig angepasst oder Seitenschutzrechte nicht wiederhergestellt werden.'
    if hooks==-2:return 'Die Bibliothek unterstützt diese Prozessarchitektur nicht.'
    if hooks==-3:return 'Der private Speedhack-Steuerordner konnte im Zielprozess nicht eingerichtet werden.'
    if hooks==-4:return 'Im Zielprozess ist bereits eine Zeitbibliothek eingerichtet; erneutes Laden wurde abgelehnt.'
    return 'Der Debugger hat das Einklinken nicht bestätigt.'

def attach_speed(pid, expected_stamp=None, libraries=None):
    """Explicit debugger attachment to an owned x86-64 ELF process. No privilege bypass."""
    pid=int(pid)
    if pid<=1 or pid==os.getpid():raise ValueError('GE-Memory selbst oder System-PID 1 kann nicht als Speedhack-Ziel verwendet werden.')
    stamp=identity(pid)
    if stamp is None or expected_stamp is not None and stamp!=str(expected_stamp):
        raise ProcessLookupError('Zielprozess beendet, fremder Benutzer oder PID wiederverwendet.')
    try:
        return SpeedControl(pid,expected_stamp=stamp)
    except ValueError:
        pass
    debugger=shutil.which('gdb')
    if not debugger:raise ValueError('Zum Einklinken wird GDB benötigt. Vollständiges GE-Wine-Paket installieren.')
    with Path(f'/proc/{pid}/exe').open('rb') as executable:header=executable.read(20)
    if header[:6]!=b'\x7fELF\x02\x01' or header[18:20]!=b'\x3e\x00':
        raise ValueError('Nachträgliches Einklinken unterstützt derzeit nur Linux-x86-64-Prozesse; 32-Bit-Prozesse benötigen den vorbereiteten Start.')
    # Enforce the same kernel permission check as a debugger before creating any attachment files.
    descriptor=os.open(f'/proc/{pid}/mem',os.O_RDWR|os.O_CLOEXEC);os.close(descriptor)
    source=Path(libraries or os.environ.get('GE_WINE_SPEED_LIBRARY_DIR',Path(__file__).with_name('speed')))/'lib64/libge_speed.so'
    if not source.is_file():raise ValueError('Speedhack-Bibliothek fehlt. Vollständiges GE-Wine-Paket installieren.')
    host_directory=Path(tempfile.mkdtemp(prefix='ge-memory-attach-',dir=f'/proc/{pid}/root/tmp'))
    remote_directory='/tmp/'+host_directory.name
    library=host_directory/'libge_speed.so'
    shutil.copyfile(source,library);library.chmod(0o500)
    script=host_directory/'attach.gdb'
    commands=['python',
              f'with open("/proc/{pid}/stat") as stream: ge_stamp=stream.read().rsplit(") ",1)[1].split()[19]',
              f'if ge_stamp != {stamp!r}: raise gdb.GdbError("Zielprozesskennung geändert")','end',
              'set pagination off','set confirm off','set auto-load off','set debuginfod enabled off',
              'set unwindonsignal on',
              'set $h=((void*(*)(const char*,int))dlopen)("'+remote_directory+'/libge_speed.so",2)',
              'if $h == 0','printf "GE_SPEED_ERROR=dlopen fehlgeschlagen\\n"','call (char*)dlerror()','detach','quit 1','end',
              'set $f=((void*(*)(void*,const char*))dlsym)($h,"ge_speed_attach")',
              'if $f == 0','printf "GE_SPEED_ERROR=dlsym fehlgeschlagen\\n"','call (char*)dlerror()','detach','quit 1','end',
              'set $hooks=((int(*)(const char*))$f)("'+remote_directory+'")',
              'printf "GE_SPEED_HOOKS=%d\\n",$hooks','detach']
    script.write_text('\n'.join(commands)+'\n');script.chmod(0o600)
    try:
        if identity(pid)!=stamp:raise ProcessLookupError('Zielprozess wurde beendet.')
        with tempfile.NamedTemporaryFile(mode='w',prefix=f'debugger-{pid}-{stamp}-',suffix='.log',
                                         dir=attachment_record(pid,stamp).parent,delete=False,encoding='utf-8') as output:
            log_path=Path(output.name)
            output.write(f'# GE-Memory attachment pid={pid} start_ticks={stamp} sysroot=/proc/{pid}/root\n')
            output.flush()
            process=subprocess.Popen([debugger,'-nx','-nh','-q','-batch',
                                      '-iex','set auto-load off','-iex','set debuginfod enabled off',
                                      '-iex','set print thread-events off',
                                      '-iex',f'set sysroot /proc/{pid}/root',
                                      '-p',str(pid),'-x',str(script)],stdout=output,stderr=subprocess.STDOUT)
            timed_out=False
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                timed_out=True
                process.send_signal(signal.SIGINT)
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait()
        hooks,details=debugger_summary(log_path)
        reference=f'Vollständiges Protokoll: {log_path}'
        if timed_out:
            raise ValueError('Einklinken hat das Zeitlimit überschritten. Zielprozess prüfen.\n'+details+'\n'+reference)
        if process.returncode or hooks is None or hooks<=0:
            reason=debugger_failure(hooks) if hooks is not None and hooks<=0 else 'Der Debugger wurde nach dem Verbinden mit einem Fehler beendet.'
            raise ValueError(reason+'\n'+details+'\n'+reference)
        controller=SpeedControl(pid,directory=host_directory,expected_stamp=stamp)
        data=dict(pid=pid,start_ticks=stamp,directory=str(host_directory),remote_directory=remote_directory,
                  hooks=hooks,debugger_log=str(log_path),method='gdb-dlopen-elf-import-rebinding')
        target=attachment_record(pid,stamp)
        with tempfile.NamedTemporaryFile(mode='w',dir=target.parent,delete=False) as out:
            temporary=Path(out.name);json.dump(data,out)
        os.replace(temporary,target)
        return controller
    finally:
        script.unlink(missing_ok=True)
        # Never unload a library still referenced by patched imports. Disabling requests factor 1.



class SpeedControl:
    def __init__(self, pid, directory=None, expected_stamp=None):
        self.pid = int(pid)
        self.stamp = identity(self.pid)
        if self.stamp is None or expected_stamp is not None and self.stamp != str(expected_stamp):
            raise ProcessLookupError('Zielprozess wurde beendet oder PID wiederverwendet.')
        if directory is None:
            entry = next((item for item in Path(f'/proc/{self.pid}/environ').read_bytes().split(b'\0')
                          if item.startswith(b'GE_WINE_SPEED_DIR=')), None)
            directory = os.fsdecode(entry.split(b'=', 1)[1]) if entry is not None else attached_directory(self.pid,self.stamp)
            if directory is None:
                raise ValueError('Speedhack noch nicht verbunden. In GE-Memory „Speedhack aktiv“ einschalten.')
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
