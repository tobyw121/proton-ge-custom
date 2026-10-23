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

    def test_backup_before_major_change_keeps_original_files_and_symlinks(self):
        prefix = Path(self.environment["WINEPREFIX"])
        prefix.mkdir(parents=True)
        (prefix / "version").write_text("GE-Proton11-11")
        (prefix / "savegame").write_text("keep my game")
        (prefix / "pfx").symlink_to(".")
        launcher.backup_before_upgrade(self.environment, lambda text: None)
        backups = list((prefix.parent / ".ge-wine-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "savegame").read_text(), "keep my game")
        self.assertTrue((backups[0] / "pfx").is_symlink())
        self.assertEqual((prefix / "version").read_text(), "GE-Proton11-11")
        launcher.backup_before_upgrade(self.environment, lambda text: None)
        self.assertEqual(len(list((prefix.parent / ".ge-wine-backups").iterdir())), 1)

    def test_existing_ge10_prefix_is_not_migrated_or_backed_up(self):
        prefix = Path(self.environment["WINEPREFIX"])
        prefix.mkdir(parents=True)
        (prefix / "version").write_text("GE-Proton10-34")
        launcher.backup_before_upgrade(self.environment, lambda text: None)
        self.assertFalse((prefix.parent / ".ge-wine-backups").exists())
