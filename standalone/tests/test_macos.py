import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ge_wine
import ge_wine_macos as mac
import ge_wine_macos_runtime as runtime


class MacTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.home=self.root/'home';self.home.mkdir()
        self.exe=self.root/'Windows Game $(touch nope).exe';self.exe.touch()
        self.wine=self.root/'fake wine'
        self.wine.write_text(f'#!{sys.executable}\nimport json,os,sys\nprint(json.dumps(dict(args=sys.argv[1:],prefix=os.environ["WINEPREFIX"])))\nsys.exit(int(os.environ.get("MAC_TEST_EXIT","0")))\n')
        self.wine.chmod(0o755)
        self.environment=dict(os.environ,HOME=str(self.home),XDG_STATE_HOME=str(self.root/'state'))
        self.options=argparse.Namespace(executable=str(self.exe),prefix=str(self.root/'prefix'),wine=str(self.wine),
                                        arguments=['literal $value'],profile='standard',full_trace=False,no_shortcuts=True)

    def run_mac(self,*args):
        return subprocess.run([sys.executable,mac.__file__,*map(str,args)],env=self.environment,text=True,capture_output=True,timeout=20)

    def test_cli_uses_native_wine_and_preserves_arguments_and_exit_code(self):
        result=self.run_mac('--wine',self.wine,'--no-shortcuts','--prefix',self.root/'prefix',self.exe,'a b','$(touch nope)')
        self.assertEqual(result.returncode,0,result.stderr)
        record=json.loads(result.stdout)
        self.assertEqual(record['args'],[str(self.exe),'a b','$(touch nope)'])
        self.assertFalse((self.root/'nope').exists())
        folder,=(self.root/'prefix/logs').iterdir()
        metadata=json.loads((folder/'session.json').read_text())
        self.assertEqual(metadata['macos_runtime']['source'],'custom')
        self.assertEqual(metadata['command'][0],str(self.wine))
        self.assertTrue((folder/'repetitions.jsonl').exists())
        self.environment['MAC_TEST_EXIT']='37'
        result=self.run_mac('--wine',self.wine,'--no-shortcuts',self.exe)
        self.assertEqual(result.returncode,37,result.stderr)

    def test_msi_routing_and_macos_prefix_defaults(self):
        self.options.executable=str(self.root/'Setup.msi');Path(self.options.executable).touch()
        self.options.prefix=None;self.options.profile='icarus-phoenix'
        with patch.dict(os.environ,HOME=str(self.home)):
            _,profile,env,args=mac.prepare(self.options)
        self.assertEqual(args[:2],['msiexec','/i'])
        self.assertEqual(profile,'icarus-phoenix')
        self.assertIn('Library/Application Support/GE-Wine/prefixes/icarus-phoenix',env['WINEPREFIX'])
        self.assertNotIn('PROTONPATH',env)

    def test_linux_proton_is_rejected_as_macos_wine(self):
        self.wine.write_bytes(b'\x7fELFtest')
        with self.assertRaisesRegex(ValueError,'Linux-Wine'):
            runtime.wine_binary(self.wine)

    def test_core_entry_dispatches_to_native_macos_frontend(self):
        with patch.object(mac,'main',return_value=7) as native,patch.object(sys,'platform','darwin'):
            self.assertEqual(ge_wine.main(['--version']),7)
        native.assert_called_once_with(['--version'])

    def make_archive(self,malicious=False):
        payload=io.BytesIO()
        with tarfile.open(fileobj=payload,mode='w:xz') as archive:
            body=b'#!/bin/sh\nexit 0\n'
            member=tarfile.TarInfo('../escaped' if malicious else 'Wine Stable.app/Contents/Resources/wine/bin/wine')
            member.size=len(body);member.mode=0o755
            archive.addfile(member,io.BytesIO(body))
        data=payload.getvalue()
        return data,dict(wine=dict(version='test',url='https://example.invalid/wine.tar.xz',sha256=hashlib.sha256(data).hexdigest()))

    def test_verified_wine_archive_is_installed_and_reused(self):
        data,spec=self.make_archive()
        with patch.object(runtime,'manifest',return_value=spec),patch.object(runtime,'urlopen',return_value=io.BytesIO(data)):
            binary=runtime.ensure_wine(lambda text:None,root=self.root/'runtime')
        self.assertTrue(binary.is_file())
        with patch.object(runtime,'manifest',return_value=spec),patch.object(runtime,'urlopen',side_effect=AssertionError('unexpected download')):
            self.assertEqual(runtime.ensure_wine(lambda text:None,root=self.root/'runtime'),binary)

    def test_bad_checksum_or_escaping_archive_never_installs_runtime(self):
        for malicious in (False,True):
            data,spec=self.make_archive(malicious)
            if not malicious:spec['wine']['sha256']='0'*64
            root=self.root/str(malicious)
            with patch.object(runtime,'manifest',return_value=spec),patch.object(runtime,'urlopen',return_value=io.BytesIO(data)):
                with self.assertRaises((ValueError,tarfile.TarError)):
                    runtime.ensure_wine(lambda text:None,root=root)
            self.assertFalse((root/'wine-test').exists())
        self.assertFalse((self.root/'escaped').exists())

    def test_gstreamer_dependency_uses_standard_mac_installer(self):
        package=self.root/'gstreamer.pkg'
        with patch.object(runtime.platform,'machine',return_value='x86_64'),patch.object(Path,'is_dir',return_value=False),\
             patch.object(runtime,'download',return_value=package),patch.object(runtime.subprocess,'Popen') as opened:
            with self.assertRaisesRegex(ValueError,'GStreamer.framework'):
                runtime.require_dependencies(lambda text:None,interactive=True)
        opened.assert_called_once_with(['/usr/bin/open',str(package)])

    def test_missing_rosetta_has_actionable_error_without_silent_install(self):
        with patch.object(runtime.platform,'machine',return_value='arm64'),\
             patch.object(runtime.subprocess,'run',return_value=argparse.Namespace(returncode=1)),\
             patch.object(runtime,'download',side_effect=AssertionError('unexpected download')):
            with self.assertRaisesRegex(ValueError,'Rosetta 2'):
                runtime.require_dependencies(lambda text:None)

    def test_native_process_identity_and_owned_session_tracking(self):
        try:
            import psutil
        except ImportError:
            self.skipTest('psutil is installed by macOS/Linux CI')
        self.assertIsNotNone(runtime.process_identity(os.getpid()))
        self.assertEqual(runtime.process_identity(os.getpid()),runtime.process_identity(os.getpid()))
        token='d'*32
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],env=dict(self.environment,GE_WINE_SESSION_ID=token))
        from ge_wine_sessions import Session,identity
        with patch.dict(os.environ,XDG_STATE_HOME=str(self.root/'state')):
            session=Session()
            session.children[str(process.pid)]=identity(process.pid);session._save()
            try:
                # Registry fallback must work even if the OS hides process environments.
                with patch.object(runtime,'process_rows',return_value=iter(())):
                    self.assertIn((process.pid,session.token),list(runtime.marked_processes()))
            finally:
                process.terminate();process.wait(timeout=5);session.close()

    def test_desktop_app_shortcut_escapes_arguments_and_keeps_profile(self):
        with patch.dict(os.environ,HOME=str(self.home)):
            self.options.no_shortcuts=False
            mac.write_shortcut(self.options,{'WINEPREFIX':self.options.prefix},self.exe,'icarus-online',lambda text:None)
        shortcut,=(self.home/'Desktop').glob('*.app')
        info=plistlib.loads((shortcut/'Contents/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleExecutable'],'launch')
        script=(shortcut/'Contents/MacOS/launch').read_text()
        argv=__import__('shlex').split(script.splitlines()[1])[1:]
        self.assertIn('icarus-online',argv)
        self.assertIn(str(self.exe),argv)
        self.assertIn('literal $value',argv)
        self.assertFalse((self.root/'nope').exists())


if __name__=='__main__':unittest.main()
