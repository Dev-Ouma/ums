import io
import tarfile
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from university.backup_services import safe_extract_tar


class BackupArchiveSecurityTests(SimpleTestCase):
    def test_restore_rejects_path_traversal_members(self):
        with TemporaryDirectory() as directory:
            archive_path = f"{directory}/malicious.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                payload = b"must not be written outside the restore root"
                member = tarfile.TarInfo("../../outside.txt")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))

            with self.assertRaisesRegex(ValueError, "unsafe path"):
                safe_extract_tar(archive_path, f"{directory}/restore")

    def test_restore_rejects_symbolic_links(self):
        with TemporaryDirectory() as directory:
            archive_path = f"{directory}/link.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                member = tarfile.TarInfo("media/link")
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
                archive.addfile(member)

            with self.assertRaisesRegex(ValueError, "unsafe link"):
                safe_extract_tar(archive_path, f"{directory}/restore")
