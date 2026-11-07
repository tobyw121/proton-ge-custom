# SPDX-License-Identifier: GPL-3.0-or-later
"""macOS process inspection and checksum-pinned native Wine setup."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen


def support_directory():
    return Path.home()/'Library/Application Support/GE-Wine'


def process_identity(pid):
    import psutil
    try:
        process = psutil.Process(pid)
        if process.uids().real != os.getuid() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return format(process.create_time(),'.6f')
    except (psutil.Error,ValueError):
        return None


def process_rows():
    import psutil
    for process in psutil.process_iter(['pid','uids']):
        try:
            uids=process.info.get('uids')
            if uids is None or uids.real != os.getuid():
                continue
            stamp = process_identity(process.pid)
            if stamp is None:
                continue
            environment = process.environ()
            yield process,stamp,environment
        except (psutil.Error,OSError):
            continue


def registered_targets():
    """Fallback when macOS restricts another process's environment."""
    import psutil
    from ge_wine_sessions import records_directory, identity
    for path in records_directory().glob('*.json'):
        try:
            record=json.loads(path.read_text())
            token=record['session']
            if not re.fullmatch('[a-f0-9]{32}',token) or path.stem!=token:
                continue
            if identity(record['controller'])!=record['start']:
                continue
            for pid,stamp in record.get('children',{}).items():
                if identity(int(pid))!=stamp:
                    continue
                process=psutil.Process(int(pid))
                yield process,token
                for child in process.children(recursive=True):
                    if process_identity(child.pid) is not None:
                        yield child,token
        except (OSError,ValueError,KeyError,psutil.Error):
            continue


def marked_processes():
    seen=set()
    for process,stamp,environment in process_rows():
        token = environment.get('GE_WINE_SESSION_ID','')
        if re.fullmatch('[a-f0-9]{32}',token):
            seen.add(process.pid)
            yield process.pid,token
    for process,token in registered_targets():
        if process.pid not in seen:
            seen.add(process.pid)
            yield process.pid,token


def process_snapshot(token,prefixes):
    import psutil
    rows = {}
    def add(process,stamp,association):
        try:
            rows[(process.pid,stamp)] = dict(pid=process.pid,start_ticks=stamp,parent=process.ppid(),
                name=process.name(),command=process.cmdline(),executable=process.exe(),association=association)
        except psutil.Error:
            pass
    for process,stamp,environment in process_rows():
        tagged = environment.get('GE_WINE_SESSION_ID') == token
        if tagged or environment.get('WINEPREFIX') in prefixes:
            add(process,stamp,'session' if tagged else 'shared-prefix')
    for process,session in registered_targets():
        stamp=process_identity(process.pid)
        if session==token and stamp is not None and (process.pid,stamp) not in rows:
            add(process,stamp,'session')
    return rows


def open_log_writers(root):
    import psutil
    root = str(Path(root).resolve())+'/'
    writers = set()
    for process in psutil.process_iter(['pid','uids']):
        try:
            uids=process.info.get('uids')
            if uids is None or uids.real != os.getuid():
                continue
            for item in process.open_files():
                # macOS doesn't expose the descriptor's write flags; conservatively include readers.
                if item.path.startswith(root):
                    info = Path(item.path).stat(); writers.add((info.st_dev,info.st_ino))
        except (psutil.Error,OSError):
            continue
    return writers


def manifest():
    return json.loads(Path(__file__).with_name('macos_runtime.json').read_text())


def download(component,destination,report,cancelled=None):
    expected = component['sha256']
    if not re.fullmatch('[a-f0-9]{64}',expected) or not component['url'].startswith('https://'):
        raise ValueError('Ungültige macOS-Laufzeitquelle oder Prüfsumme.')
    destination = Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.is_file():
        with destination.open('rb') as existing:
            if hashlib.file_digest(existing,'sha256').hexdigest() == expected:
                return destination
    # Python.org/packaged Python use a bundled CA set when available.
    import ssl
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent,delete=False) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256(); size=0
            with urlopen(component['url'],timeout=60,context=context) as response:
                while chunk := response.read(1024*1024):
                    if cancelled and cancelled.is_set():
                        from ge_wine_sessions import LaunchCancelled
                        raise LaunchCancelled()
                    size += len(chunk)
                    if size > 2*1024**3:
                        raise ValueError('Laufzeitdownload unerwartet groß.')
                    digest.update(chunk);output.write(chunk)
                    if size % (32*1024**2)==0:
                        report(f'Mac-Laufzeit: {size//1024**2} MiB geladen …\n')
            if digest.hexdigest()!=expected:
                raise ValueError('SHA256-Prüfung fehlgeschlagen. Download wird nicht verwendet.')
        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def wine_binary(value):
    path = Path(value).expanduser().resolve()
    if path.suffix.lower()=='.app':
        path = path/'Contents/Resources/wine/bin/wine'
    if not path.is_file() or not os.access(path,os.X_OK):
        raise ValueError(f'Keine ausführbare native Wine-Datei: {path}')
    with path.open('rb') as source:
        magic = source.read(4)
    if magic == b'\x7fELF':
        raise ValueError('Linux-Wine/GE-Proton läuft nicht auf macOS. Native Wine.app auswählen.')
    return path


def ensure_wine(report,cancelled=None,explicit=None,root=None):
    if explicit:
        return wine_binary(explicit)
    spec = manifest()['wine']
    root = Path(root or support_directory()/'runtimes').expanduser().resolve()
    destination = root/('wine-'+spec['version'])
    binary = destination/'Wine Stable.app/Contents/Resources/wine/bin/wine'
    if binary.is_file():
        return wine_binary(binary)
    root.mkdir(parents=True,exist_ok=True)
    import fcntl
    with (root/'.runtime.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if binary.is_file():
            return wine_binary(binary)
        archive = download(spec,root/'downloads'/('wine-'+spec['version']+'.tar.xz'),report,cancelled)
        report('Wine für macOS: SHA256 geprüft; entpacke …\n')
        with tempfile.TemporaryDirectory(dir=root,prefix='.wine-extract-') as temporary:
            extracted = Path(temporary)/'contents';extracted.mkdir()
            with tarfile.open(archive,'r:*') as source:
                members=source.getmembers()
                if sum(member.size for member in members)>8*1024**3:
                    raise ValueError('Wine-Archiv entpackt unerwartet groß.')
                for member in members:
                    if cancelled and cancelled.is_set():
                        from ge_wine_sessions import LaunchCancelled
                        raise LaunchCancelled()
                    source.extract(member,extracted,filter='data')
            wine_binary(extracted/'Wine Stable.app/Contents/Resources/wine/bin/wine')
            if destination.exists():
                raise ValueError(f'Unvollständige vorhandene Mac-Laufzeit: {destination}')
            extracted.rename(destination)
    return wine_binary(binary)


def require_dependencies(report,cancelled=None,interactive=False):
    if platform.machine()=='arm64':
        result = subprocess.run(['/usr/bin/arch','-x86_64','/usr/bin/true'],capture_output=True)
        if result.returncode:
            raise ValueError('Rosetta 2 fehlt. Zuerst über Apple installieren; Wine läuft als Intel-Prozess.')
    if Path('/Library/Frameworks/GStreamer.framework/Versions/1.0').is_dir():
        return
    spec = manifest()['gstreamer']
    package = download(spec,support_directory()/'downloads'/('gstreamer-'+spec['version']+'.pkg'),report,cancelled)
    if interactive:
        subprocess.Popen(['/usr/bin/open',str(package)])
    raise ValueError(f'GStreamer.framework wird benötigt. Geprüften macOS-Installer öffnen und installieren: {package}. Danach erneut starten.')
