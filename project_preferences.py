"""Per-project preferences for Update Studio.

Settings live in a per-user file (``%APPDATA%\\UpdateStudio\\projects.json``), never inside the
target repository. Each project is keyed by its normalized repository path; when a repository
moves or is cloned again, its settings are found again through the git remote URL.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

STORE_VERSION = 1
PROTECTED_MODES = ("preserve_if_exists", "never_overwrite", "never_delete")
PROTECTED_DEFAULTS = [
    {"path": ".env", "mode": "preserve_if_exists"},
    {"path": "storage/app/**", "mode": "never_overwrite"},
    {"path": "storage/logs/**", "mode": "never_overwrite"},
    {"path": "public/uploads/**", "mode": "never_overwrite"},
    {"path": "public/storage/**", "mode": "never_overwrite"},
    {"path": "database/database.sqlite", "mode": "preserve_if_exists"},
    {"path": "config/local.php", "mode": "preserve_if_exists"},
]


def config_dir() -> Path:
    override = os.environ.get("UPDATE_STUDIO_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "UpdateStudio"


def project_key(repo: Path | str) -> str:
    """A stable key for a repository path: absolute, forward slashes, case-folded on Windows."""
    path = os.path.normpath(os.path.abspath(str(repo))).replace("\\", "/").rstrip("/")
    return path.lower() if os.name == "nt" else path


def normalize_remote(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    for prefix in ("https://", "http://", "ssh://git@", "ssh://", "git@"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    host, separator, path = url.partition(":")
    if separator and "/" not in host:
        url = f"{host}/{path}"
    return url.lower()


@dataclass
class ProjectSettings:
    application: str = ""
    output_dir: str = ""
    destination: str = ""
    profile: str = "Laravel"
    version_variable: str = "APP_VERSION"
    required_env: list[str] = field(default_factory=list)
    language: str = "en"
    health_url: str = ""
    health_status: int = 200
    before_commands: list[str] = field(default_factory=list)
    after_commands: list[str] = field(default_factory=list)
    protected_paths: list[dict] = field(default_factory=lambda: [dict(rule) for rule in PROTECTED_DEFAULTS])
    required_services: list[dict] = field(default_factory=list)
    exclude_build: bool = True
    complete_app: bool = False
    extract: bool = True
    create_exe: bool = True
    last_start_commit: str = ""
    last_end_commit: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> "ProjectSettings":
        """Build settings from stored data, ignoring unknown keys and values of the wrong type."""
        settings = cls()
        if not isinstance(data, dict):
            return settings
        for item in fields(cls):
            if item.name not in data:
                continue
            value = data[item.name]
            default = getattr(settings, item.name)
            if isinstance(default, bool):
                if isinstance(value, bool):
                    setattr(settings, item.name, value)
            elif isinstance(default, int):
                try:
                    setattr(settings, item.name, int(value))
                except (TypeError, ValueError):
                    pass
            elif isinstance(default, list):
                if isinstance(value, list):
                    setattr(settings, item.name, list(value))
            elif isinstance(value, str):
                setattr(settings, item.name, value)
        settings.protected_paths = [
            {"path": str(rule["path"]), "mode": rule["mode"]}
            for rule in settings.protected_paths
            if isinstance(rule, dict) and rule.get("path") and rule.get("mode") in PROTECTED_MODES
        ]
        services = []
        for service in settings.required_services:
            try:
                services.append({"name": str(service["name"]), "host": str(service["host"]), "port": int(service["port"])})
            except (KeyError, TypeError, ValueError):
                continue
        settings.required_services = services
        settings.required_env = [str(name) for name in settings.required_env if str(name).strip()]
        settings.before_commands = [str(command) for command in settings.before_commands if str(command).strip()]
        settings.after_commands = [str(command) for command in settings.after_commands if str(command).strip()]
        return settings

    def to_dict(self) -> dict:
        return asdict(self)


class PreferencesStore:
    """Reads and writes ``projects.json``. Every save is atomic, and a damaged file is kept aside."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_dir() / "projects.json"
        self.data = self._load()

    def _load(self) -> dict:
        empty = {"version": STORE_VERSION, "last_project": "", "projects": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return empty
        except (OSError, ValueError):
            try:
                self.path.replace(self.path.with_suffix(".damaged.json"))
            except OSError:
                pass
            return empty
        if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
            return empty
        data.setdefault("version", STORE_VERSION)
        data.setdefault("last_project", "")
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix="projects-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(self.data, file, ensure_ascii=False, indent=2)
            os.replace(temp, self.path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise

    def _find(self, repo: Path | str, remote: str = "") -> str | None:
        key = project_key(repo)
        if key in self.data["projects"]:
            return key
        wanted = normalize_remote(remote) if remote else ""
        if wanted:
            for other_key, entry in self.data["projects"].items():
                if entry.get("remote") and normalize_remote(entry["remote"]) == wanted:
                    return other_key
        return None

    def has(self, repo: Path | str, remote: str = "") -> bool:
        return self._find(repo, remote) is not None

    def get(self, repo: Path | str, remote: str = "") -> ProjectSettings:
        key = self._find(repo, remote)
        return ProjectSettings.from_dict(self.data["projects"][key].get("settings") if key else None)

    def put(self, repo: Path | str, settings: ProjectSettings, remote: str = "", name: str = "") -> None:
        key = project_key(repo)
        old_key = self._find(repo, remote)
        if old_key and old_key != key and not Path(self.data["projects"][old_key].get("repo", "")).is_dir():
            # The repository moved: carry its settings to the new path instead of keeping both.
            self.data["projects"].pop(old_key, None)
        self.data["projects"][key] = {
            "repo": str(repo),
            "remote": remote,
            "name": name or Path(str(repo)).name,
            "updated_at": datetime.now().isoformat(timespec="microseconds"),
            "settings": settings.to_dict(),
        }
        self.data["last_project"] = key
        self.save()

    def forget(self, repo: Path | str) -> None:
        if self.data["projects"].pop(project_key(repo), None) is not None:
            self.save()

    def last_project(self) -> str:
        entry = self.data["projects"].get(self.data.get("last_project", ""))
        return entry["repo"] if entry else ""

    def recent(self) -> list[tuple[str, str]]:
        """(name, repo path) of every known project, most recently used first."""
        entries = sorted(self.data["projects"].values(), key=lambda entry: entry.get("updated_at", ""), reverse=True)
        return [(entry.get("name") or Path(entry["repo"]).name, entry["repo"]) for entry in entries if entry.get("repo")]
