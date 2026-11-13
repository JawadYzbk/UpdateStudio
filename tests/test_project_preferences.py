import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from project_preferences import PreferencesStore, ProjectSettings, normalize_remote, project_key
from update_package_gui import (
    DEPLOYMENT_PRESETS,
    build_profile,
    commit_after,
    commits_in_range,
    installed_deployment,
    load_commits,
    parse_lines,
    previous_release_tag,
)


def make_repo(root: Path) -> tuple[Path, list[str]]:
    repo = root / "repo"
    repo.mkdir()

    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    run("init")
    run("config", "user.name", "Test")
    run("config", "user.email", "test@example.com")
    shas = []
    for index in range(4):
        (repo / f"file{index}.txt").write_text(str(index), encoding="utf-8")
        run("add", ".")
        run("commit", "-m", f"commit {index}")
        shas.append(run("rev-parse", "HEAD"))
        if index == 1:
            run("tag", "-a", "v1.0.0", "-m", "v1.0.0")
    return repo, shas


class ProjectPreferencesTest(unittest.TestCase):
    def test_settings_persist_across_store_instances_per_project(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "UpdateStudio" / "projects.json"
            first = ProjectSettings(
                destination="C:\\apps\\sandok",
                output_dir="D:\\releases",
                profile="Laravel + Reverb",
                version_variable="FRONTEND_VERSION",
                required_env=["APP_KEY", "REDIS_HOST"],
                language="ar",
                health_url="http://localhost/up",
                health_status=204,
                before_commands=["php artisan down"],
                after_commands=["php artisan queue:restart"],
                protected_paths=[{"path": "uploads/**", "mode": "never_overwrite"}],
                required_services=[{"name": "MySQL", "host": "127.0.0.1", "port": 3306}],
                exclude_build=False,
                complete_app=True,
                extract=False,
                create_exe=False,
                last_end_commit="abc123",
            )
            PreferencesStore(path).put(Path(temp) / "one", first, name="one")
            PreferencesStore(path).put(Path(temp) / "two", ProjectSettings(destination="E:\\other"), name="two")

            reloaded = PreferencesStore(path)
            self.assertEqual(reloaded.get(Path(temp) / "one").to_dict(), first.to_dict())
            self.assertEqual(reloaded.get(Path(temp) / "two").destination, "E:\\other")
            self.assertEqual(reloaded.get(Path(temp) / "two").profile, "Laravel")
            self.assertEqual(reloaded.last_project(), str(Path(temp) / "two"))
            self.assertEqual([name for name, _ in reloaded.recent()], ["two", "one"])

    def test_unknown_project_gets_defaults_and_is_not_stored(self):
        with tempfile.TemporaryDirectory() as temp:
            store = PreferencesStore(Path(temp) / "projects.json")
            settings = store.get(Path(temp) / "new")
            self.assertEqual(settings.profile, "Laravel")
            self.assertTrue(settings.exclude_build)
            self.assertFalse(store.has(Path(temp) / "new"))
            self.assertFalse((Path(temp) / "projects.json").exists())

    def test_project_key_ignores_slash_style_and_trailing_separator(self):
        self.assertEqual(project_key("C:\\Work\\App\\"), project_key("C:/Work/App"))

    def test_a_moved_repository_is_found_again_by_its_remote(self):
        with tempfile.TemporaryDirectory() as temp:
            store = PreferencesStore(Path(temp) / "projects.json")
            store.put(Path(temp) / "old-place", ProjectSettings(destination="C:\\live"), remote="git@github.com:Owner/App.git")
            moved = Path(temp) / "new-place"
            self.assertTrue(store.has(moved, "https://github.com/owner/app"))
            self.assertEqual(store.get(moved, "https://github.com/owner/app.git").destination, "C:\\live")

            store.put(moved, store.get(moved, "https://github.com/owner/app"), remote="https://github.com/owner/app")
            self.assertEqual(len(store.recent()), 1)
            self.assertEqual(store.recent()[0][1], str(moved))

    def test_remote_urls_normalize_to_one_form(self):
        forms = ["git@github.com:Owner/App.git", "https://github.com/owner/app", "ssh://git@github.com/Owner/App.git"]
        self.assertEqual({normalize_remote(form) for form in forms}, {"github.com/owner/app"})

    def test_damaged_file_is_kept_aside_and_replaced_by_an_empty_store(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "projects.json"
            path.write_text("{ not json", encoding="utf-8")
            store = PreferencesStore(path)
            self.assertEqual(store.recent(), [])
            self.assertTrue(path.with_suffix(".damaged.json").is_file())
            store.put(Path(temp) / "repo", ProjectSettings(destination="C:\\x"))
            self.assertEqual(PreferencesStore(path).get(Path(temp) / "repo").destination, "C:\\x")

    def test_invalid_stored_values_fall_back_to_defaults(self):
        settings = ProjectSettings.from_dict({
            "destination": 5,
            "health_status": "oops",
            "exclude_build": "yes",
            "protected_paths": [{"path": "a", "mode": "bogus"}, {"path": "b", "mode": "never_delete"}, "text"],
            "required_services": [{"name": "x", "host": "h", "port": "nope"}, {"name": "MySQL", "host": "h", "port": "3306"}],
            "unknown_key": 1,
        })
        self.assertEqual(settings.destination, "")
        self.assertEqual(settings.health_status, 200)
        self.assertTrue(settings.exclude_build)
        self.assertEqual(settings.protected_paths, [{"path": "b", "mode": "never_delete"}])
        self.assertEqual(settings.required_services, [{"name": "MySQL", "host": "h", "port": 3306}])

    def test_store_file_lives_where_asked_and_is_valid_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested" / "projects.json"
            PreferencesStore(path).put(Path(temp) / "repo", ProjectSettings())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            self.assertEqual(len(data["projects"]), 1)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_profile_is_built_from_structured_settings(self):
        settings = ProjectSettings(
            application="Sandok",
            profile="Laravel + Reverb",
            before_commands=["echo before"],
            after_commands=["echo after"],
            required_services=[{"name": "MySQL", "host": "127.0.0.1", "port": 3306}],
            destination="C:\\apps\\sandok",
            language="ar",
        )
        profile = build_profile(settings, "repo")
        self.assertEqual(profile["application"], "Sandok")
        self.assertEqual(profile["language"], "ar")
        self.assertEqual(profile["default_destination"], "C:\\apps\\sandok")
        self.assertEqual(profile["before_commands"], ["php artisan down", "echo before"])
        self.assertEqual(profile["after_commands"][-1], "echo after")
        self.assertEqual([service["name"] for service in profile["required_services"]], ["Reverb", "MySQL"])
        self.assertTrue(profile["laravel"])
        self.assertEqual(build_profile(ProjectSettings(), "fallback-name")["application"], "fallback-name")
        self.assertEqual(DEPLOYMENT_PRESETS["Laravel"]["after_commands"][-1], "php artisan up")

    def test_profile_rejects_bad_settings(self):
        for bad in (
            ProjectSettings(profile="Nope"),
            ProjectSettings(version_variable="BAD-NAME"),
            ProjectSettings(destination="relative/path"),
            ProjectSettings(health_status=42),
            ProjectSettings(required_services=[{"name": "x", "host": "h", "port": 70000}]),
        ):
            with self.subTest(settings=bad), self.assertRaises(ValueError):
                build_profile(bad, "repo")

    def test_multi_line_lists_split_on_lines_and_semicolons(self):
        self.assertEqual(parse_lines("a\n b ;c\n\n"), ["a", "b", "c"])


class GitHelpersTest(unittest.TestCase):
    def test_installed_commit_gives_the_next_start_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, shas = make_repo(Path(temp))
            self.assertEqual(commit_after(repo, shas[1], shas[3]), shas[2])
            self.assertEqual(commit_after(repo, shas[3], shas[3]), "")
            self.assertEqual(commit_after(repo, "deadbeef", shas[3]), "")
            self.assertEqual(commit_after(repo, "", shas[3]), "")

    def test_previous_release_tag_and_tags_in_commit_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, shas = make_repo(Path(temp))
            self.assertEqual(previous_release_tag(repo, shas[3]), "v1.0.0")
            self.assertEqual(previous_release_tag(repo, shas[1]), "")
            commits = {commit.sha: commit for commit in load_commits(repo)}
            self.assertEqual(commits[shas[1]].tags, ("v1.0.0",))
            self.assertIn("[v1.0.0]", commits[shas[1]].label)
            self.assertEqual(commits[shas[2]].tags, ())

    def test_range_lists_every_included_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, shas = make_repo(Path(temp))
            self.assertEqual([commit.sha for commit in commits_in_range(repo, shas[1], shas[3])], [shas[3], shas[2], shas[1]])
            self.assertEqual([commit.sha for commit in commits_in_range(repo, shas[0], shas[0])], [shas[0]])

    def test_installed_deployment_reads_the_updater_record(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(installed_deployment(temp))
            self.assertIsNone(installed_deployment(""))
            record = Path(temp) / ".update_studio"
            record.mkdir()
            (record / "deployment.json").write_text('{"version": "1.6.1", "commit": "abc"}', encoding="utf-8")
            self.assertEqual(installed_deployment(temp), {"version": "1.6.1", "commit": "abc"})
            (record / "deployment.json").write_text("garbage", encoding="utf-8")
            self.assertIsNone(installed_deployment(temp))


if __name__ == "__main__":
    unittest.main()
