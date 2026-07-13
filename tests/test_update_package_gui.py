import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from update_package_gui import changed_paths, create_package, deployment_analysis, find_repo, validate_env_variable, validate_version
from updater_runtime import (
    DeploymentError,
    apply_update,
    command_selected_by_default,
    deploy_update,
    rollback_from,
    validate_environment,
)


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

    def test_build_directory_is_replaced_and_restored_as_a_unit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation = root / "installation"
            old_build = installation / "public" / "build"
            old_build.mkdir(parents=True)
            (old_build / "old-hash.js").write_text("old", encoding="utf-8")
            payload = root / "payload.zip"
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr("public/build/new-hash.js", "new")
                archive.writestr("public/build/manifest.json", "{}")
            manifest = {
                "package_id": "build-replacement",
                "included": ["public/build/manifest.json", "public/build/new-hash.js"],
                "deleted": [],
                "replace_directories": ["public/build"],
            }

            backup = apply_update(installation, payload, manifest)

            self.assertFalse((old_build / "old-hash.js").exists())
            self.assertEqual((old_build / "new-hash.js").read_text(encoding="utf-8"), "new")
            rollback_from(installation, backup)
            self.assertEqual((old_build / "old-hash.js").read_text(encoding="utf-8"), "old")
            self.assertFalse((old_build / "new-hash.js").exists())

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

    def test_web_deployment_protects_local_data_and_writes_metadata_and_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation = root / "app"
            installation.mkdir()
            (installation / ".env").write_text('APP_KEY=local-secret\nFRONTEND_VERSION="1.0.0"\nEMPTY=\n', encoding="utf-8")
            (installation / "uploads").mkdir()
            (installation / "uploads" / "photo.jpg").write_text("local", encoding="utf-8")
            payload = root / "payload.zip"
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr(".env", "APP_KEY=package-secret\n")
                archive.writestr("app.txt", "deployed")
                archive.writestr("uploads/photo.jpg", "package")
            manifest = {
                "package_id": "web-1",
                "title": "Test App",
                "version": "2.0.0",
                "end": "abcdef123456",
                "included": [".env", "app.txt", "uploads/photo.jpg"],
                "deleted": [],
                "profile": {
                    "application": "Test App",
                    "version_variable": "FRONTEND_VERSION",
                    "required_env": ["APP_KEY"],
                    "before_commands": [],
                    "after_commands": [],
                    "protected_paths": [
                        {"path": ".env", "mode": "preserve_if_exists"},
                        {"path": "uploads/**", "mode": "never_overwrite"},
                    ],
                },
            }

            progress = []
            backup, log = deploy_update(installation, payload, manifest, progress.append)

            self.assertTrue(backup.is_dir())
            self.assertTrue(log.is_file())
            self.assertTrue(any("Installing: app.txt" in line for line in progress))
            self.assertEqual((installation / ".env").read_text(encoding="utf-8"), 'APP_KEY=local-secret\nFRONTEND_VERSION="2.0.0"\nEMPTY=\n')
            self.assertEqual((installation / "uploads" / "photo.jpg").read_text(encoding="utf-8"), "local")
            self.assertEqual((installation / "app.txt").read_text(encoding="utf-8"), "deployed")
            metadata = (installation / ".update_studio" / "deployment.json").read_text(encoding="utf-8")
            self.assertIn('"version": "2.0.0"', metadata)
            self.assertEqual(validate_environment(installation, manifest), [])

            rollback_from(installation, backup)
            self.assertFalse((installation / ".update_studio" / "deployment.json").exists())
            self.assertIn('FRONTEND_VERSION="1.0.0"', (installation / ".env").read_text(encoding="utf-8"))

    def test_application_version_requires_semver(self):
        self.assertEqual(validate_version(" 2.7.0-beta.1 "), "2.7.0-beta.1")
        for invalid in ("", "2.7", "v2.7.0", "2.07.0", "2.7.0 unsafe"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_version(invalid)

    def test_version_environment_variable_name_is_validated(self):
        self.assertEqual(validate_env_variable(" FRONTEND_VERSION "), "FRONTEND_VERSION")
        for invalid in ("", "APP-VERSION", "1APP_VERSION", "APP VERSION", "APP_VERSION="):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_env_variable(invalid)

    def test_failed_health_check_rolls_back_files_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation = root / "app"
            installation.mkdir()
            (installation / "app.txt").write_text("before", encoding="utf-8")
            payload = root / "payload.zip"
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr("app.txt", "after")
            manifest = {
                "package_id": "health-failure",
                "included": ["app.txt"],
                "deleted": [],
                "profile": {
                    "before_commands": [],
                    "after_commands": [],
                    "health_url": "http://127.0.0.1:1/health",
                },
            }

            with self.assertRaises(DeploymentError):
                deploy_update(installation, payload, manifest)

            self.assertEqual((installation / "app.txt").read_text(encoding="utf-8"), "before")
            self.assertFalse((installation / ".update_backups" / "health-failure").exists())

    def test_dependency_and_migration_analysis_warns_about_excluded_assets(self):
        migrations, dependencies, warnings = deployment_analysis(
            ["composer.lock", "package.json", "database/migrations/2026_01_01_create_items.php"],
            exclude_build=True,
        )

        self.assertEqual(migrations, ["database/migrations/2026_01_01_create_items.php"])
        self.assertEqual(dependencies, ["composer.lock", "package.json"])
        self.assertTrue(any("public/build" in warning for warning in warnings))

    def test_deployment_commands_default_only_when_relevant(self):
        manifest = {
            "dependencies": ["composer.lock", "package-lock.json"],
            "migrations": ["database/migrations/new.php"],
            "included": ["composer.lock", "package-lock.json"],
        }

        self.assertTrue(command_selected_by_default("composer install --no-dev", manifest))
        self.assertTrue(command_selected_by_default("npm ci", manifest))
        self.assertTrue(command_selected_by_default("npm run build", manifest))
        self.assertTrue(command_selected_by_default("php artisan migrate --force", manifest))

        manifest["included"].append("public/build/manifest.json")
        self.assertFalse(command_selected_by_default("npm ci", manifest))
        self.assertFalse(command_selected_by_default("npm run build", manifest))
        self.assertTrue(command_selected_by_default("php artisan config:cache", manifest))


if __name__ == "__main__":
    unittest.main()
