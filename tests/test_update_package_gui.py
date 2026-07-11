import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from update_package_gui import changed_paths, create_package, find_repo
from updater_runtime import apply_update, rollback_from


class UpdatePackageGuiTest(unittest.TestCase):
    def test_current_repository_is_detected(self):
        repo = find_repo(Path(__file__).resolve().parent)

        self.assertEqual(repo, Path(__file__).resolve().parents[1])

    def test_inclusive_range_packages_full_files_and_reports_deletions(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            output = Path(temp) / "output"
            repo.mkdir()

            def run(*args: str) -> str:
                return subprocess.run(
                    ["git", *args], cwd=repo, check=True, capture_output=True, text=True
                ).stdout.strip()

            run("init")
            run("config", "user.name", "Test")
            run("config", "user.email", "test@example.com")
            (repo / "old.txt").write_text("old", encoding="utf-8")
            run("add", ".")
            run("commit", "-m", "first")
            (repo / "new.txt").write_text("complete file", encoding="utf-8")
            run("add", ".")
            run("commit", "-m", "second")
            start = run("rev-parse", "HEAD")
            (repo / "old.txt").unlink()
            run("add", "-A")
            run("commit", "-m", "third")
            end = run("rev-parse", "HEAD")

            included, deleted = changed_paths(repo, start, end)
            self.assertEqual(included, ["new.txt"])
            self.assertEqual(deleted, ["old.txt"])

            zip_path, folder, delete_manifest, count = create_package(
                repo, output, start, end, exclude_build=True, extract=True
            )
            self.assertTrue(zip_path.is_file())
            self.assertEqual(count, 1)
            self.assertEqual((folder / "new.txt").read_text(encoding="utf-8"), "complete file")
            self.assertEqual(delete_manifest.read_text(encoding="utf-8"), "old.txt\n")

    def test_updater_backs_up_applies_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation = root / "installation"
            installation.mkdir()
            (installation / "changed.txt").write_text("before", encoding="utf-8")
            (installation / "deleted.txt").write_text("restore me", encoding="utf-8")
            payload = root / "payload.zip"
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr("added.txt", "new")
                archive.writestr("changed.txt", "after")
            manifest = {
                "package_id": "test-update",
                "included": ["added.txt", "changed.txt"],
                "deleted": ["deleted.txt"],
            }

            backup = apply_update(installation, payload, manifest)
            self.assertEqual((installation / "changed.txt").read_text(encoding="utf-8"), "after")
            self.assertEqual((installation / "added.txt").read_text(encoding="utf-8"), "new")
            self.assertFalse((installation / "deleted.txt").exists())

            rollback_from(installation, backup)
            self.assertEqual((installation / "changed.txt").read_text(encoding="utf-8"), "before")
            self.assertFalse((installation / "added.txt").exists())
            self.assertEqual((installation / "deleted.txt").read_text(encoding="utf-8"), "restore me")

    def test_package_includes_ignored_working_build_when_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            output = Path(temp) / "output"
            repo.mkdir()

            def run(*args: str) -> str:
                return subprocess.run(
                    ["git", *args], cwd=repo, check=True, capture_output=True, text=True
                ).stdout.strip()

            run("init")
            run("config", "user.name", "Test")
            run("config", "user.email", "test@example.com")
            (repo / ".gitignore").write_text("/public/build\n", encoding="utf-8")
            (repo / "app.txt").write_text("app", encoding="utf-8")
            run("add", ".")
            run("commit", "-m", "release")
            commit = run("rev-parse", "HEAD")
            build = repo / "public" / "build"
            build.mkdir(parents=True)
            (build / "manifest.json").write_text("{}", encoding="utf-8")

            zip_path, _, _, _ = create_package(
                repo, output, commit, commit, exclude_build=False, extract=False
            )
            with zipfile.ZipFile(zip_path) as archive:
                self.assertIn("public/build/manifest.json", archive.namelist())


if __name__ == "__main__":
    unittest.main()
