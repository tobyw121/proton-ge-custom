# SPDX-License-Identifier: GPL-3.0-or-later
"""Profile-aware freedesktop launchers, including installer-created .lnk files."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess


def desktop_directory():
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'user-dirs.dirs'
    try:
        for line in config.read_text().splitlines():
            match = re.fullmatch(r'\s*XDG_DESKTOP_DIR="([^"\n]*)"\s*', line)
            if match:
                value = match[1].replace('${HOME}', str(Path.home())).replace('$HOME', str(Path.home()))
                path = Path(value)
                if path.is_absolute():
                    return None if path == Path.home() else path
    except FileNotFoundError:
        pass
    return Path.home() / 'Desktop'


def exec_line(arguments):
    # First escape Exec argument quoting, then Desktop Entry value escaping.
    def quote(arg):
        arg = str(arg).replace('%', '%%').replace('\\', '\\\\')
        for character in ('"', '`', '$'):
            arg = arg.replace(character, '\\' + character)
        return '"' + arg + '"'
    value = 'ge-wine ' + ' '.join(quote(arg) for arg in arguments).replace('\\', '\\\\')
    return value.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def write_shortcut(name, arguments, key, report):
    desktop = desktop_directory()
    if desktop is None:
        report('Desktop ist in den Benutzereinstellungen deaktiviert; keine Verknüpfung erstellt.\n')
        return None
    desktop.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[^\w .-]', '_', name, flags=re.UNICODE).strip(' .')[:70] or 'Programm'
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    path = desktop / f'GE-Wine-{safe}-{digest}.desktop'
    content = ('[Desktop Entry]\nType=Application\n'
               f'Name={safe} (GE-Wine)\nExec={exec_line(arguments)}\n'
               'TryExec=ge-wine\nIcon=application-x-executable\nTerminal=false\n'
               'Categories=Game;\nStartupNotify=false\nX-GE-Wine=true\n')
    if path.exists() and 'X-GE-Wine=true\n' not in path.read_text():
        report(f'Vorhandene fremde Verknüpfung bleibt erhalten: {path}\n')
        return None
    path.write_text(content)
    path.chmod(0o755)
    gio = shutil.which('gio')
    if gio:
        try:
            subprocess.run([gio, 'set', str(path), 'metadata::trusted', 'true'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
    report(f'Desktop-Verknüpfung: {path}\n')
    return path


def shortcut_arguments(options, environment, executable, profile, arguments):
    command = ['--profile', profile, '--prefix', environment['WINEPREFIX'], '--gameid', options.gameid]
    if options.proton:
        command.extend(['--proton', environment['PROTONPATH']])
    for attribute, flag in [('diagnostics', '--diagnostics'), ('nvidia_offload', '--nvidia-offload'),
                            ('low_latency', '--low-latency'), ('no_prerequisites', '--no-prerequisites')]:
        if getattr(options, attribute, False):
            command.append(flag)
    return [*command, str(executable), *arguments]


def program_shortcut(options, environment, executable, profile, report, arguments=None, name=None):
    executable = Path(executable).expanduser().resolve()
    if name is None and profile == "icarus-online" and executable.name.casefold() in {"vfunlauncher.exe", "vlauncher.exe"}:
        name = "Icarus Online VFUN"
    if name is None and profile == "icarus-phoenix" and executable.name.casefold() == "launcher.exe":
        name = "Icarus Phoenix"
    arguments = options.arguments if arguments is None else arguments
    key = environment['WINEPREFIX'] + '\0' + str(executable) + '\0' + profile + '\0' + repr(arguments)
    return write_shortcut(name or executable.stem,
                          shortcut_arguments(options, environment, executable, profile, arguments), key, report)


def windows_links(prefix):
    root = Path(prefix) / 'drive_c'
    if not root.exists():
        root = Path(prefix) / 'pfx/drive_c'
    directories = [root / 'ProgramData/Microsoft/Windows/Start Menu/Programs']
    users = root / 'users'
    if users.is_dir():
        for user in users.iterdir():
            directories.extend([user / 'Desktop', user / 'AppData/Roaming/Microsoft/Windows/Start Menu/Programs'])
    links = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.rglob('*'):
            if path.is_file() and path.suffix.lower() == '.lnk':
                stat = path.stat()
                links[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return links


def installer(executable):
    path = Path(executable)
    return (path.suffix.casefold() == '.msi' or
            bool(re.search(r'setup|install|redist|update|unins', path.stem, re.I)) or
            any(part.casefold() == 'redist' for part in path.parts))


def installed_shortcuts(options, environment, executable, profile, before, report):
    prefix = Path(environment['WINEPREFIX'])
    root = prefix / 'drive_c'
    if not root.exists():
        root = prefix / 'pfx/drive_c'
    start = root / 'windows/system32/start.exe'
    created = set()
    after = windows_links(prefix)
    if start.is_file():
        for filename, stamp in after.items():
            link = Path(filename)
            if before.get(filename) == stamp or re.search(r'uninstall|unins|deinstall|remove|repair', link.stem, re.I):
                continue
            if link.stem.casefold() in created:
                continue
            program_shortcut(options, environment, start, profile, report,
                             ['/unix', str(link)], link.stem)
            created.add(link.stem.casefold())
    if profile == 'icarus-online':
        vfun = root / 'VFUN/VLauncher/VFUNLauncher.exe'
        if vfun.is_file():
            program_shortcut(options, environment, vfun, profile, report, [], 'Icarus Online VFUN')
            created.add('icarus-online')
    if installer(executable) and not created:
        report('Keine neue Windows-Verknüpfung erkannt. Installierte EXE wählen und '
               '„Desktop-Verknüpfung erstellen“ verwenden.\n')
