import hashlib
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_launcher import launcher


class ProtonSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.environment_patch = patch.dict(os.environ, {"HOME": str(self.home)})
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.environment = {"PROTONPATH": str(launcher.pinned_proton_directory()),
                            "WINEPREFIX": str(self.home / "prefixes/icarus-ge10-34-clean")}

    def archive(self, name=None):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            entry = tarfile.TarInfo(name or launcher.PINNED_PROTON + "/proton")
            entry.size = 4
            entry.mode = 0o755
            archive.addfile(entry, io.BytesIO(b"test"))
        return output.getvalue()

    def responses(self, archive, digest=None):
        expected = digest or hashlib.sha512(archive).hexdigest()
        checksum = f"{expected}  {launcher.PINNED_PROTON}.tar.gz\n".encode()
        return [io.BytesIO(checksum), io.BytesIO(archive)]

    def test_verified_download_is_installed_and_reused_without_network(self):
        with patch.object(launcher, "urlopen", side_effect=self.responses(self.archive())) as request:
            launcher.ensure_proton(self.environment, lambda text: None)
        self.assertEqual(request.call_count, 2)
        self.assertEqual((launcher.pinned_proton_directory() / "proton").read_text(), "test")
        with patch.object(launcher, "urlopen", side_effect=AssertionError("unexpected download")):
            launcher.ensure_proton(self.environment, lambda text: None)

    def test_bad_checksum_does_not_install(self):
        with patch.object(launcher, "urlopen", side_effect=self.responses(self.archive(), "0" * 128)):
            with self.assertRaisesRegex(ValueError, "SHA512-Prüfung"):
                launcher.ensure_proton(self.environment, lambda text: None)
        self.assertFalse(launcher.pinned_proton_directory().exists())

    def test_escaping_archive_member_is_rejected(self):
        archive = self.archive(launcher.PINNED_PROTON + "/../../escape")
        with patch.object(launcher, "urlopen", side_effect=self.responses(archive)):
            with self.assertRaises((ValueError, tarfile.TarError)):
                launcher.ensure_proton(self.environment, lambda text: None)
        self.assertFalse((self.home / "escape").exists())
        self.assertFalse(launcher.pinned_proton_directory().exists())

    def test_existing_incomplete_installation_is_preserved(self):
        target = launcher.pinned_proton_directory()
        target.mkdir(parents=True)
        (target / "user-file").write_text("keep")
        with self.assertRaises(ValueError):
            launcher.ensure_proton(self.environment, lambda text: None)
        self.assertEqual((target / "user-file").read_text(), "keep")

    def test_phoenix_download_uses_ge10_archive_and_checksum(self):
        version = launcher.PHOENIX_PROTON
        archive = self.archive(version + "/proton")
        checksum = f"{hashlib.sha512(archive).hexdigest()}  {version}.tar.gz\n".encode()
        env = dict(self.environment, PROTONPATH=str(launcher.pinned_proton_directory(version)))
        with patch.object(launcher, "urlopen", side_effect=[io.BytesIO(checksum), io.BytesIO(archive)]) as request:
            launcher.ensure_proton(env, lambda text: None)
        self.assertEqual(request.call_args_list[0].args[0], launcher.PROTON_URLS[version].removesuffix(".tar.gz") + ".sha512sum")
        self.assertEqual(request.call_args_list[1].args[0], launcher.PROTON_URLS[version])
        self.assertTrue((launcher.pinned_proton_directory(version) / "proton").is_file())
        self.assertFalse(launcher.pinned_proton_directory().exists())
