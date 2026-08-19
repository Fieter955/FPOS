import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from update_common import extract_update_archive, is_valid_version, version_tuple


class UpdateCommonTests(unittest.TestCase):
    def test_semver_comparison_helpers(self):
        self.assertEqual(version_tuple("v5.1.2"), (5, 1, 2))
        self.assertEqual(version_tuple("invalid"), (0, 0, 0))
        self.assertTrue(is_valid_version("5.1.2"))
        self.assertFalse(is_valid_version("5.1"))

    def test_extracts_flat_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "release.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("FPOS.exe", b"exe")
                bundle.writestr("_internal/runtime.dll", b"dll")
                bundle.writestr("frontend-dist/index.html", b"html")
            package = extract_update_archive(archive, root / "stage")
            self.assertEqual(package.name, "stage")
            self.assertTrue((package / "FPOS.exe").is_file())

    def test_extracts_nested_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "release.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("FPOS-5.1.0-Windows/FPOS.exe", b"exe")
                bundle.writestr("FPOS-5.1.0-Windows/_internal/runtime.dll", b"dll")
                bundle.writestr("FPOS-5.1.0-Windows/frontend-dist/index.html", b"html")
            package = extract_update_archive(archive, root / "stage")
            self.assertEqual(package.name, "FPOS-5.1.0-Windows")

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "release.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("FPOS.exe", b"exe")
                bundle.writestr("_internal/runtime.dll", b"dll")
                bundle.writestr("frontend-dist/index.html", b"html")
                bundle.writestr("../outside.txt", b"must not escape")
            with self.assertRaises(ValueError):
                extract_update_archive(archive, root / "stage")

    def test_rejects_symbolic_link_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "release.zip"
            info = zipfile.ZipInfo("_internal/link.dll")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("FPOS.exe", b"exe")
                bundle.writestr("frontend-dist/index.html", b"html")
                bundle.writestr(info, b"target")
            with self.assertRaises(ValueError):
                extract_update_archive(archive, root / "stage")


if __name__ == "__main__":
    unittest.main()
