from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

from project_preferences import PROTECTED_DEFAULTS, PROTECTED_MODES, PreferencesStore, ProjectSettings


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
INSTALLER_LANGUAGES = {"English": "en", "العربية": "ar"}

COMPLETE_PACKAGE_EXCLUDES = {".env", "config/local.php", "database/database.sqlite"}
COMPLETE_PACKAGE_EXCLUDED_DIRECTORIES = ("storage/", "public/storage/", "public/uploads/")

PROTECTED_MODE_LABELS = {
    "preserve_if_exists": "Keep the installed copy",
    "never_overwrite": "Never overwrite",
    "never_delete": "Never delete",
}

# `php artisan optimize` (Laravel 11+) caches config, events, routes and views, and also
# runs every package's registered optimize step, e.g. `filament:optimize` and
# `icons:cache`. `optimize:clear` removes those package caches too, so rebuilding them
# with config/route/view:cache alone left Blade Icons scanning its SVG sets on every
# request (about 0.8 s per page in SandokTa3adod).
DEPLOYMENT_PRESETS = {
    "Laravel": {
        "laravel": True,
        "before_commands": ["php artisan down"],
        "after_commands": [
            "composer install --no-dev --optimize-autoloader",
            "npm ci",
            "npm run build",
            "php artisan migrate --force",
            "php artisan optimize:clear",
            "php artisan optimize",
            "php artisan up",
        ],
    },
    "Laravel + Reverb": {
        "laravel": True,
        "before_commands": ["php artisan down"],
        "after_commands": [
            "composer install --no-dev --optimize-autoloader",
            "npm ci",
            "npm run build",
            "php artisan migrate --force",
            "php artisan optimize:clear",
            "php artisan optimize",
            "php artisan up",
        ],
        "required_services": [{"name": "Reverb", "host": "127.0.0.1", "port": 8080}],
    },
    "Node.js": {"before_commands": [], "after_commands": ["npm ci"]},
    "PHP Generic": {"before_commands": [], "after_commands": ["composer install --no-dev --optimize-autoloader"]},
    "Static Web App": {"before_commands": [], "after_commands": []},
    "Custom": {"before_commands": [], "after_commands": []},
}


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    subject: str
    tags: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        tags = f"[{', '.join(self.tags)}]  " if self.tags else ""
        return f"{self.sha[:8]}  {self.date}  {tags}{self.subject}"


def validate_version(value: str) -> str:
    value = value.strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError("Version must use SemVer, for example 2.7.0 or 2.7.0-beta.1.")
    return value


def validate_env_variable(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Version variable must be a valid environment name, for example APP_VERSION.")
    return value


def validate_destination(value: str) -> str:
    value = value.strip()
    if value and not os.path.isabs(value):
        raise ValueError("Default destination must be an absolute path, for example C:\\inetpub\\wwwroot\\app.")
    return value


def git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        creationflags=CREATE_NO_WINDOW,
    )
    return result.stdout


def find_repo(path: Path) -> Path:
    return Path(str(git(path, "rev-parse", "--show-toplevel")).strip())


def default_repo() -> Path:
    candidates = [Path.cwd()]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.append(Path(__file__).resolve().parent)
    for candidate in candidates:
        try:
            return find_repo(candidate)
        except (OSError, subprocess.SubprocessError):
            continue
    return Path.cwd()


def load_commits(repo: Path, limit: int = 500) -> list[Commit]:
    output = str(git(repo, "log", f"-n{limit}", "--date=short", "--decorate-refs=refs/tags", "--pretty=format:%H%x1f%ad%x1f%D%x1f%s"))
    commits = []
    for line in output.splitlines():
        if not line:
            continue
        sha, date, refs, subject = line.split("\x1f", 3)
        tags = tuple(ref.strip().removeprefix("tag: ") for ref in refs.split(",") if ref.strip().startswith("tag: "))
        commits.append(Commit(sha, date, subject, tags))
    return commits


def remote_url(repo: Path) -> str:
    try:
        return str(git(repo, "remote", "get-url", "origin")).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def installed_deployment(destination: str | Path) -> dict | None:
    """The record the updater wrote at the last install (``.update_studio/deployment.json``)."""
    if not str(destination).strip():
        return None
    try:
        data = json.loads((Path(destination) / ".update_studio" / "deployment.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def resolve_commit(repo: Path, ref: str) -> str:
    """Full SHA of ``ref`` when it names a commit in the repository, otherwise ""."""
    if not ref:
        return ""
    try:
        return str(git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def commit_after(repo: Path, base: str, end: str) -> str:
    """First commit after ``base`` on the way to ``end``: the inclusive start of the next package.

    Returns "" when ``base`` is unknown, is not an ancestor of ``end``, or already is ``end``.
    """
    base = resolve_commit(repo, base)
    if not base:
        return ""
    try:
        output = str(git(repo, "rev-list", "--reverse", "--ancestry-path", "--first-parent", f"{base}..{end}"))
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = output.split()
    return lines[0] if lines else ""


def previous_release_tag(repo: Path, end: str) -> str:
    """The newest ``vX.Y.Z`` tag strictly before ``end`` (so a release package starts after it)."""
    try:
        return str(git(repo, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", f"{end}^")).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def commits_in_range(repo: Path, start: str, end: str) -> list[Commit]:
    """Every commit the inclusive range ``start..end`` packages, newest first."""
    parents = str(git(repo, "rev-list", "--parents", "-n1", start)).split()[1:]
    output = str(git(repo, "log", "--date=short", "--pretty=format:%H%x1f%ad%x1f%s", end, *(f"^{parent}" for parent in parents)))
    return [Commit(*line.split("\x1f", 2)) for line in output.splitlines() if line]


def is_ancestor(repo: Path, start: str, end: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", start, end],
        cwd=repo,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    ).returncode == 0


def parse_lines(value: str) -> list[str]:
    """One item per line; commas and semicolons also separate, for pasted lists."""
    return [item.strip() for item in re.split(r"[\n;]", value) if item.strip()]


def build_profile(settings: ProjectSettings, repo_name: str) -> dict:
    """The deployment profile embedded in the updater, from a project's saved settings."""
    if settings.profile not in DEPLOYMENT_PRESETS:
        raise ValueError(f"Unknown recipe: {settings.profile}")
    preset = DEPLOYMENT_PRESETS[settings.profile]
    profile = {key: value for key, value in preset.items() if key not in {"before_commands", "after_commands", "required_services"}}
    for rule in settings.protected_paths:
        if rule.get("mode") not in PROTECTED_MODES or not str(rule.get("path", "")).strip():
            raise ValueError(f"Invalid protected path rule: {rule}")
    for service in settings.required_services:
        if not str(service.get("name", "")).strip() or not str(service.get("host", "")).strip():
            raise ValueError("Every required service needs a name and a host.")
        if not 0 < int(service.get("port", 0)) < 65536:
            raise ValueError(f"Port of {service.get('name')} must be between 1 and 65535.")
    if not 100 <= int(settings.health_status) <= 599:
        raise ValueError("Expected health status must be an HTTP status code, for example 200.")
    profile.update({
        "name": settings.profile,
        "application": settings.application.strip() or repo_name,
        "before_commands": list(preset.get("before_commands", [])) + list(settings.before_commands),
        "after_commands": list(preset.get("after_commands", [])) + list(settings.after_commands),
        "required_env": list(settings.required_env),
        "health_url": settings.health_url.strip(),
        "health_status": int(settings.health_status),
        "protected_paths": [{"path": rule["path"].strip(), "mode": rule["mode"]} for rule in settings.protected_paths],
        "required_services": list(preset.get("required_services", [])) + [
            {"name": service["name"].strip(), "host": service["host"].strip(), "port": int(service["port"])}
            for service in settings.required_services
        ],
        "version_variable": validate_env_variable(settings.version_variable),
        "default_destination": validate_destination(settings.destination),
        "language": settings.language if settings.language in INSTALLER_LANGUAGES.values() else "en",
    })
    return profile


RELEASE_TAG_PATTERN = re.compile(r"^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")


def _semver_key(version: str) -> tuple:
    core, _, pre = version.partition("-")
    # A pre-release sorts before its release: 1.7.0-beta < 1.7.0.
    return (*map(int, core.split(".")), pre == "", pre)


def detect_version(repo: Path, commit: str) -> tuple[str, str]:
    """Return (version, source) for a commit from the repository's own versioning.

    1. A release tag on the commit itself (v1.7.0)            -> "1.7.0"
    2. Commits after the last release tag (3 after v1.7.0)    -> "1.7.0+3.g54c4ddf" (not a release)
    3. "version" in package.json / composer.json at the commit
    Returns ("", "") when the repository has no version, so it must be typed by hand.
    """
    tags = str(git(repo, "tag", "--points-at", commit)).split()
    releases = [match.group(1) for match in map(RELEASE_TAG_PATTERN.match, tags) if match]
    if releases:
        version = max(releases, key=_semver_key)
        return version, f"release tag v{version}"

    try:
        described = str(git(repo, "describe", "--tags", "--long", "--match", "v[0-9]*", commit)).strip()
    except subprocess.CalledProcessError:
        described = ""
    match = re.fullmatch(r"v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)-(\d+)-g([0-9a-f]+)", described)
    if match:
        base, count, sha = match.groups()
        return f"{base}+{count}.g{sha[:7]}", f"{count} commit(s) after v{base}, not a release"

    for manifest in ("package.json", "composer.json"):
        try:
            data = json.loads(str(git(repo, "show", f"{commit}:{manifest}")))
        except (subprocess.CalledProcessError, ValueError):
            continue
        version = str(data.get("version", "")).strip() if isinstance(data, dict) else ""
        if version and VERSION_PATTERN.fullmatch(version):
            return version, manifest
    return "", ""


def inclusive_base(repo: Path, start: str) -> str:
    parents = str(git(repo, "rev-list", "--parents", "-n1", start)).strip().split()
    if len(parents) > 1:
        return parents[1]
    result = subprocess.run(
        ["git", "hash-object", "-t", "tree", "--stdin"],
        cwd=repo,
        input=b"",
        check=True,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    return result.stdout.decode().strip()


def changed_paths(repo: Path, start: str, end: str) -> tuple[list[str], list[str]]:
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", start, end],
        cwd=repo,
        check=True,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    raw = bytes(git(repo, "diff", "--name-status", "-z", inclusive_base(repo, start), end, text=False))
    tokens = raw.decode("utf-8", errors="surrogateescape").rstrip("\0").split("\0") if raw else []
    included: list[str] = []
    deleted: list[str] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        old_path = tokens[index]
        index += 1
        if status[0] in "RC":
            new_path = tokens[index]
            index += 1
            included.append(new_path)
            if status[0] == "R":
                deleted.append(old_path)
        elif status[0] == "D":
            deleted.append(old_path)
        else:
            included.append(old_path)
    return sorted(set(included)), sorted(set(deleted))


def change_details(repo: Path, start: str, end: str) -> dict:
    raw = str(git(repo, "diff", "--name-status", inclusive_base(repo, start), end))
    statuses = [line.split("\t", 1)[0] for line in raw.splitlines() if line]
    return {
        "added": sum(status.startswith("A") for status in statuses),
        "modified": sum(status.startswith(("M", "R", "C")) for status in statuses),
        "deleted": sum(status.startswith("D") for status in statuses),
    }


def deployment_analysis(included: list[str], exclude_build: bool) -> tuple[list[str], list[str], list[str]]:
    migrations = [path for path in included if path.startswith("database/migrations/") and path.endswith(".php")]
    dependencies = [path for path in included if Path(path).name in {"composer.json", "composer.lock", "package.json", "package-lock.json"}]
    warnings = []
    if "composer.lock" in dependencies:
        warnings.append("composer.lock changed: run composer install")
    if "package-lock.json" in dependencies:
        warnings.append("package-lock.json changed: run npm ci")
    if exclude_build and any(Path(path).name in {"package.json", "package-lock.json"} for path in included):
        warnings.append("Frontend dependencies changed but public/build is excluded; assets may be outdated")
    return migrations, dependencies, warnings


def added_migrations(repo: Path, start: str, end: str) -> list[str]:
    output = str(git(repo, "diff", "--diff-filter=A", "--name-only", inclusive_base(repo, start), end))
    return sorted(path for path in output.splitlines() if path.startswith("database/migrations/") and path.endswith(".php"))


def complete_package_excluded(relative: str) -> bool:
    relative = relative.replace("\\", "/").removeprefix("./")
    local_env = relative.startswith(".env.") and relative != ".env.example"
    return local_env or relative in COMPLETE_PACKAGE_EXCLUDES or relative.startswith(COMPLETE_PACKAGE_EXCLUDED_DIRECTORIES)


def dependency_files(repo: Path) -> list[tuple[Path, str]]:
    return [
        (path, path.relative_to(repo).as_posix())
        for directory in (repo / "vendor", repo / "node_modules")
        if directory.is_dir()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    ]


def create_package(
    repo: Path,
    output_dir: Path,
    start: str,
    end: str,
    exclude_build: bool,
    extract: bool,
    complete_app: bool = False,
) -> tuple[Path, Path | None, Path | None, int]:
    included, deleted = changed_paths(repo, start, end)
    git_included = [path for path in included if not path.startswith("public/build/")]
    deleted = [path for path in deleted if not path.startswith("public/build/") and not complete_package_excluded(path)]
    build_files: list[Path] = []
    if not exclude_build:
        build_dir = repo / "public" / "build"
        if not build_dir.is_dir():
            raise FileNotFoundError("public/build does not exist. Run the frontend build or enable 'Exclude build'.")
        build_files = sorted(path for path in build_dir.rglob("*") if path.is_file())
        if not build_files:
            raise FileNotFoundError("public/build is empty. Run the frontend build or enable 'Exclude build'.")
    build_paths = [path.relative_to(repo).as_posix() for path in build_files]
    dependencies = dependency_files(repo) if complete_app else []
    dependency_paths = [relative for _, relative in dependencies]
    included = sorted(set(git_included + build_paths + dependency_paths))
    if not included and not deleted:
        raise ValueError("No changed files exist in the selected range.")

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"update-{start[:8]}-{end[:8]}-{stamp}"
    zip_path = output_dir / f"{name}.zip"
    folder_path = output_dir / name if extract else None
    deleted_path = output_dir / f"{name}.deleted.txt" if deleted else None

    if complete_app:
        tree_output = str(git(repo, "ls-tree", "-r", "--name-only", end))
        local_dependency_roots = tuple(f"{directory.name}/" for directory in (repo / "vendor", repo / "node_modules") if directory.is_dir())
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for filename in tree_output.splitlines():
                if complete_package_excluded(filename) or filename.startswith(("public/build/", *local_dependency_roots)):
                    continue
                data = bytes(git(repo, "show", f"{end}:{filename}", text=False))
                archive.writestr(filename, data)
        git_included = []
    elif git_included:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in git_included:
                data = bytes(git(repo, "show", f"{end}:{path}", text=False))
                archive.writestr(path, data)
    else:
        with zipfile.ZipFile(zip_path, "w"):
            pass
    if build_files or dependencies:
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            for file, relative in zip(build_files, build_paths):
                archive.write(file, relative)
            for file, relative in dependencies:
                archive.write(file, relative)

    if complete_app:
        with zipfile.ZipFile(zip_path) as archive:
            included = sorted(name for name in archive.namelist() if not name.endswith("/"))

    if folder_path:
        folder_path.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(folder_path)

    if deleted_path:
        deleted_path.write_text("\n".join(deleted) + "\n", encoding="utf-8")

    with zipfile.ZipFile(zip_path) as archive:
        archived = sorted(name for name in archive.namelist() if not name.endswith("/"))
    if archived != included:
        raise RuntimeError("Package verification failed: archived paths differ from the selected files.")

    return zip_path, folder_path, deleted_path, len(included)


def build_updater_exe(
    output_dir: Path,
    zip_path: Path,
    deleted_path: Path | None,
    start: str,
    end: str,
    profile: dict | None = None,
    version: str = "",
    changes: dict | None = None,
    migrations: list[str] | None = None,
    dependencies: list[str] | None = None,
    warnings: list[str] | None = None,
    progress=None,
) -> Path:
    progress = progress or (lambda _message: None)
    package_id = zip_path.stem
    with zipfile.ZipFile(zip_path) as archive:
        included = sorted(name for name in archive.namelist() if not name.endswith("/"))
    manifest = {
        "package_id": package_id,
        "title": "Application Update",
        "start": start,
        "end": end,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "included": included,
        "deleted": deleted_path.read_text(encoding="utf-8").splitlines() if deleted_path else [],
        "version": version,
        "profile": profile or {},
        "changes": changes or {},
        "migrations": migrations or [],
        "dependencies": dependencies or [],
        "warnings": warnings or [],
        "replace_directories": [directory for directory in ("public/build", "vendor", "node_modules") if any(path.startswith(f"{directory}/") for path in included)],
    }
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    updater_source = resource_root / "updater_runtime.py"
    arabic_font = resource_root / "assets" / "fonts" / "NotoSansArabic.ttf"
    arabic_semibold_font = resource_root / "assets" / "fonts" / "NotoSansArabic-SemiBold.ttf"
    arabic_font_license = resource_root / "assets" / "fonts" / "NotoSansArabic-OFL.txt"
    if profile and profile.get("language") == "ar" and not all(path.is_file() for path in (arabic_font, arabic_semibold_font, arabic_font_license)):
        raise FileNotFoundError("Arabic font asset is missing. Rebuild Update Studio with assets/fonts included.")
    python = shutil.which("python") if getattr(sys, "frozen", False) else sys.executable
    if not python or subprocess.run(
        [python, "-c", "import PyInstaller"],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    ).returncode:
        raise FileNotFoundError("Run 'python -m pip install pyinstaller' before creating an updater EXE.")

    with tempfile.TemporaryDirectory(prefix="update-exe-") as temp:
        progress("Preparing embedded payload and deployment manifest")
        staging = Path(temp)
        payload = staging / "payload.zip"
        manifest_path = staging / "update_manifest.json"
        shutil.copy2(zip_path, payload)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        name = f"SandokTa3adod-Updater-{start[:8]}-{end[:8]}"
        progress("Building self-contained updater EXE")
        process = subprocess.Popen(
            [
                python,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--onefile",
                "--windowed",
                "--name", name,
                "--distpath", str(output_dir),
                "--workpath", str(staging / "work"),
                "--specpath", str(staging / "spec"),
                "--add-data", f"{payload}{os.pathsep}.",
                "--add-data", f"{manifest_path}{os.pathsep}.",
                *( ["--add-data", f"{arabic_font}{os.pathsep}.", "--add-data", f"{arabic_semibold_font}{os.pathsep}.", "--add-data", f"{arabic_font_license}{os.pathsep}."] if profile and profile.get("language") == "ar" else [] ),
                str(updater_source),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        assert process.stdout is not None
        for line in process.stdout:
            if line.strip():
                progress(line.rstrip())
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, process.args)
    exe = output_dir / f"{name}.exe"
    if not exe.is_file():
        raise RuntimeError("PyInstaller finished without creating the updater EXE.")
    return exe


BG = "#FAFAFA"
CARD = "#FFFFFF"
BORDER = "#E4E4E7"
INK = "#09090B"
TEXT = "#27272A"
MUTED = "#71717A"
FAINT = "#A1A1AA"
ACCENT = "#18181B"
ACCENT_HOVER = "#27272A"
GOOD = "#16A34A"
WARN = "#B45309"
BAD = "#B91C1C"
PROJECT_SEPARATOR = "   —   "


def font(size: int, weight: str = "normal", family: str = "Segoe UI"):
    return ctk.CTkFont(family, size, weight)


def secondary_button(parent, text: str, command, width: int = 90, height: int = 34):
    return ctk.CTkButton(
        parent, text=text, command=command, width=width, height=height, corner_radius=9,
        fg_color="#E4E4E7", hover_color="#D4D4D8", text_color=TEXT, font=font(11),
    )


class RowEditor(ctk.CTkFrame):
    """Editable rows of small inputs (instead of `a:b; c:d` strings) with add and remove buttons."""

    def __init__(self, parent, columns: list[dict], add_text: str) -> None:
        super().__init__(parent, fg_color="transparent")
        self.columns = columns
        self.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=(4, 40))
        for index, column in enumerate(columns):
            header.grid_columnconfigure(index, weight=column.get("weight", 0), minsize=column.get("width", 80))
            ctk.CTkLabel(header, text=column["title"], font=font(10, "bold"), text_color=MUTED, anchor="w").grid(row=0, column=index, sticky="w", padx=4)
        self.body = ctk.CTkScrollableFrame(self, fg_color="#FAFAFA", corner_radius=10, height=220)
        self.body.grid(row=1, column=0, sticky="nsew", pady=(4, 8))
        self.body.grid_columnconfigure(0, weight=1)
        self.rows: list[tuple[ctk.CTkFrame, list]] = []
        secondary_button(self, f"+  {add_text}", lambda: self.add_row(), width=150).grid(row=2, column=0, sticky="w")

    def add_row(self, values: list | None = None) -> None:
        values = values or [column.get("default", "") for column in self.columns]
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.grid(row=len(self.rows), column=0, sticky="ew", pady=2)
        widgets = []
        for index, (column, value) in enumerate(zip(self.columns, values)):
            row.grid_columnconfigure(index, weight=column.get("weight", 0), minsize=column.get("width", 80))
            variable = tk.StringVar(value=str(value))
            if column.get("choices"):
                widget = ctk.CTkComboBox(row, variable=variable, values=column["choices"], state="readonly", width=column.get("width", 80), height=32)
            else:
                widget = ctk.CTkEntry(row, textvariable=variable, placeholder_text=column.get("placeholder", ""), width=column.get("width", 80), height=32)
            widget.grid(row=0, column=index, sticky="ew", padx=4)
            widgets.append(variable)
        entry = (row, widgets)
        ctk.CTkButton(row, text="✕", width=30, height=30, fg_color="transparent", hover_color="#FEE2E2", text_color=BAD, command=lambda: self._remove(entry)).grid(row=0, column=len(self.columns), padx=(4, 0))
        self.rows.append(entry)

    def _remove(self, entry) -> None:
        entry[0].destroy()
        self.rows.remove(entry)
        for index, (row, _) in enumerate(self.rows):
            row.grid_configure(row=index)

    def set_rows(self, rows: list[list]) -> None:
        for entry in list(self.rows):
            self._remove(entry)
        for values in rows:
            self.add_row(values)

    def values(self) -> list[list[str]]:
        result = []
        for _, variables in self.rows:
            values = [variable.get().strip() for variable in variables]
            if any(values):
                result.append(values)
        return result


def lines_box(parent, lines: list[str], height: int = 90, readonly: bool = False):
    box = ctk.CTkTextbox(parent, height=height, corner_radius=9, border_width=1, border_color="#D4D4D8", fg_color="#FAFAFA" if not readonly else "#F4F4F5", font=font(11, family="Consolas"), wrap="none")
    box.insert("1.0", "\n".join(lines))
    if readonly:
        box.configure(state="disabled", text_color=MUTED)
    return box


def set_box_lines(box, lines: list[str]) -> None:
    box.configure(state="normal")
    box.delete("1.0", "end")
    box.insert("1.0", "\n".join(lines) if lines else "(none)")
    box.configure(state="disabled")


class ProjectSettingsDialog(ctk.CTkToplevel):
    """Deployment settings that rarely change, saved per project."""

    def __init__(self, master, settings: ProjectSettings, repo_name: str, on_save) -> None:
        super().__init__(master)
        self.title(f"Project settings — {repo_name}")
        self.geometry("760x600")
        self.minsize(680, 520)
        self.configure(fg_color=BG)
        self.transient(master)
        self.settings = settings
        self.repo_name = repo_name
        self.on_save = on_save
        self.application_var = tk.StringVar(value=settings.application)
        self.profile_var = tk.StringVar(value=settings.profile if settings.profile in DEPLOYMENT_PRESETS else "Laravel")
        language_names = {code: name for name, code in INSTALLER_LANGUAGES.items()}
        self.language_var = tk.StringVar(value=language_names.get(settings.language, "English"))
        self.version_variable_var = tk.StringVar(value=settings.version_variable)
        self.health_url_var = tk.StringVar(value=settings.health_url)
        self.health_status_var = tk.StringVar(value=str(settings.health_status))
        self._build()
        self.profile_var.trace_add("write", lambda *_: self._show_recipe())
        self._show_recipe()
        self.after(50, self._focus)

    def _focus(self) -> None:
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _field(self, parent, row: int, title: str, widget_factory, helper: str = "") -> None:
        ctk.CTkLabel(parent, text=title, font=font(11, "bold"), text_color=TEXT).grid(row=row, column=0, sticky="nw", padx=(4, 16), pady=(10, 0))
        widget = widget_factory(parent)
        widget.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        if helper:
            ctk.CTkLabel(parent, text=helper, font=font(10), text_color=FAINT, anchor="w", justify="left", wraplength=480).grid(row=row + 1, column=1, sticky="w")

    def _build(self) -> None:
        tabs = ctk.CTkTabview(self, fg_color=CARD, segmented_button_selected_color=ACCENT, segmented_button_selected_hover_color=ACCENT_HOVER, corner_radius=14, border_width=1, border_color=BORDER)
        tabs.pack(fill="both", expand=True, padx=18, pady=(8, 0))

        general = tabs.add("General")
        general.grid_columnconfigure(1, weight=1)
        self._field(general, 0, "Application name", lambda p: ctk.CTkEntry(p, textvariable=self.application_var, placeholder_text=self.repo_name, height=34), "Shown in the installer and recorded at install time.")
        self._field(general, 2, "Recipe", lambda p: ctk.CTkComboBox(p, variable=self.profile_var, values=list(DEPLOYMENT_PRESETS), state="readonly", height=34), "The commands the updater runs; see the Commands tab.")
        self._field(general, 4, "Installer language", lambda p: ctk.CTkComboBox(p, variable=self.language_var, values=list(INSTALLER_LANGUAGES), state="readonly", height=34))
        self._field(general, 5, "Version .env variable", lambda p: ctk.CTkEntry(p, textvariable=self.version_variable_var, height=34), "The installer writes the new version into this .env variable when it exists.")
        self.required_env_box = lines_box(general, self.settings.required_env, height=110)
        self._field(general, 7, "Required .env names", lambda p: self.required_env_box, "One name per line. The installer refuses to continue while one is missing from .env.")

        commands = tabs.add("Commands")
        commands.grid_columnconfigure((0, 1), weight=1)
        commands.grid_rowconfigure((1, 3), weight=1)
        ctk.CTkLabel(commands, text="From the recipe (before)", font=font(11, "bold"), text_color=TEXT).grid(row=0, column=0, sticky="w", padx=4, pady=(8, 4))
        ctk.CTkLabel(commands, text="Your extra commands before, one per line", font=font(11, "bold"), text_color=TEXT).grid(row=0, column=1, sticky="w", padx=4, pady=(8, 4))
        self.recipe_before_box = lines_box(commands, [], readonly=True)
        self.recipe_before_box.grid(row=1, column=0, sticky="nsew", padx=4)
        self.before_box = lines_box(commands, self.settings.before_commands)
        self.before_box.grid(row=1, column=1, sticky="nsew", padx=4)
        ctk.CTkLabel(commands, text="From the recipe (after)", font=font(11, "bold"), text_color=TEXT).grid(row=2, column=0, sticky="w", padx=4, pady=(12, 4))
        ctk.CTkLabel(commands, text="Your extra commands after, one per line", font=font(11, "bold"), text_color=TEXT).grid(row=2, column=1, sticky="w", padx=4, pady=(12, 4))
        self.recipe_after_box = lines_box(commands, [], readonly=True)
        self.recipe_after_box.grid(row=3, column=0, sticky="nsew", padx=4)
        self.after_box = lines_box(commands, self.settings.after_commands)
        self.after_box.grid(row=3, column=1, sticky="nsew", padx=4)
        ctk.CTkLabel(commands, text="The installer lists every command as a checkbox, so each one can still be skipped at install time.", font=font(10), text_color=FAINT).grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=(8, 0))

        protected = tabs.add("Protected paths")
        protected.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(protected, text="Files the updater must not replace or remove. Use ** for a whole folder.", font=font(11), text_color=MUTED).grid(row=0, column=0, sticky="w", padx=4, pady=(8, 6))
        self.protected_editor = RowEditor(protected, [
            {"title": "Rule", "width": 190, "choices": list(PROTECTED_MODE_LABELS.values()), "default": PROTECTED_MODE_LABELS["never_overwrite"]},
            {"title": "Path", "width": 300, "weight": 1, "placeholder": "storage/app/**"},
        ], "Add path")
        self.protected_editor.grid(row=1, column=0, sticky="nsew")
        self.protected_editor.set_rows([[PROTECTED_MODE_LABELS[rule["mode"]], rule["path"]] for rule in self.settings.protected_paths])
        secondary_button(protected, "Reset to defaults", lambda: self.protected_editor.set_rows([[PROTECTED_MODE_LABELS[rule["mode"]], rule["path"]] for rule in PROTECTED_DEFAULTS]), width=140).grid(row=2, column=0, sticky="e", pady=(0, 4))

        checks = tabs.add("Services & health")
        checks.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(checks, text="Services the installer checks before installing (for example MySQL on 127.0.0.1:3306).", font=font(11), text_color=MUTED).grid(row=0, column=0, sticky="w", padx=4, pady=(8, 2))
        self.recipe_services_label = ctk.CTkLabel(checks, text="", font=font(10), text_color=FAINT)
        self.recipe_services_label.grid(row=1, column=0, sticky="w", padx=4)
        self.services_editor = RowEditor(checks, [
            {"title": "Name", "width": 160, "placeholder": "MySQL"},
            {"title": "Host", "width": 200, "weight": 1, "default": "127.0.0.1"},
            {"title": "Port", "width": 90, "placeholder": "3306"},
        ], "Add service")
        self.services_editor.body.configure(height=130)
        self.services_editor.grid(row=2, column=0, sticky="nsew")
        self.services_editor.set_rows([[service["name"], service["host"], service["port"]] for service in self.settings.required_services])
        health = ctk.CTkFrame(checks, fg_color="transparent")
        health.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        health.grid_columnconfigure(1, weight=1)
        self._field(health, 0, "Health URL", lambda p: ctk.CTkEntry(p, textvariable=self.health_url_var, placeholder_text="http://localhost/up", height=34), "Checked after installing. When it does not answer as expected, files are restored. Leave empty to skip.")
        self._field(health, 2, "Expected status", lambda p: ctk.CTkEntry(p, textvariable=self.health_status_var, width=90, height=34))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=14)
        ctk.CTkButton(actions, text="Save settings", command=self.save, height=38, fg_color=ACCENT, hover_color=ACCENT_HOVER, font=font(12, "bold")).pack(side="right")
        secondary_button(actions, "Cancel", self.destroy, height=38).pack(side="right", padx=10)

    def _show_recipe(self) -> None:
        preset = DEPLOYMENT_PRESETS.get(self.profile_var.get(), {})
        set_box_lines(self.recipe_before_box, preset.get("before_commands", []))
        set_box_lines(self.recipe_after_box, preset.get("after_commands", []))
        services = preset.get("required_services", [])
        self.recipe_services_label.configure(text="Always checked by the recipe: " + ", ".join(f"{s['name']} ({s['host']}:{s['port']})" for s in services) if services else "")

    def collect(self) -> ProjectSettings:
        modes = {label: mode for mode, label in PROTECTED_MODE_LABELS.items()}
        settings = ProjectSettings.from_dict(self.settings.to_dict())
        settings.application = self.application_var.get().strip()
        settings.profile = self.profile_var.get()
        settings.language = INSTALLER_LANGUAGES.get(self.language_var.get(), "en")
        settings.version_variable = self.version_variable_var.get().strip()
        settings.required_env = [name.strip() for name in re.split(r"[\n,;]", self.required_env_box.get("1.0", "end")) if name.strip()]
        settings.before_commands = parse_lines(self.before_box.get("1.0", "end"))
        settings.after_commands = parse_lines(self.after_box.get("1.0", "end"))
        settings.protected_paths = []
        for mode, path in self.protected_editor.values():
            if not path:
                raise ValueError("Every protected rule needs a path.")
            settings.protected_paths.append({"path": path.replace("\\", "/"), "mode": modes[mode]})
        settings.required_services = []
        for name, host, port in self.services_editor.values():
            if not port.isdigit():
                raise ValueError(f"Port of service '{name or host}' must be a number.")
            settings.required_services.append({"name": name, "host": host, "port": int(port)})
        status = self.health_status_var.get().strip()
        if not status.isdigit():
            raise ValueError("Expected status must be a number, for example 200.")
        settings.health_url = self.health_url_var.get().strip()
        settings.health_status = int(status)
        build_profile(settings, self.repo_name)
        return settings

    def save(self) -> None:
        try:
            settings = self.collect()
        except ValueError as error:
            messagebox.showwarning("Check the settings", str(error), parent=self)
            return
        self.on_save(settings)
        self.destroy()


class UpdatePackageApp(ctk.CTk):
    def __init__(self, store: PreferencesStore | None = None, repo: Path | None = None) -> None:
        super().__init__()
        self.title("Update Studio")
        self.geometry("1180x720")
        self.minsize(1000, 640)
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color=BG)
        self.store = store or PreferencesStore()
        self.repo: Path | None = None
        self.remote = ""
        self.settings = ProjectSettings()
        self.commits: list[Commit] = []
        self.installed: dict | None = None
        self.quick_starts: dict[str, str] = {}
        self.project_paths: dict[str, str] = {}
        self._loading = False
        self._busy = False
        self._save_job = None
        self._summary_job = None
        self._refresh_installed = False
        self._range_ok = False

        self.project_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        self.include_build_var = tk.BooleanVar(value=False)
        self.complete_app_var = tk.BooleanVar(value=False)
        self.extract_var = tk.BooleanVar(value=True)
        self.create_exe_var = tk.BooleanVar(value=True)
        self.version_var = tk.StringVar()
        self.version_hint_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Open a project to begin")
        self.version_entry = None
        self._build()

        self.end_var.trace_add("write", lambda *_: (self._sync_version(), self._schedule_summary()))
        self.start_var.trace_add("write", lambda *_: self._schedule_summary())
        self.destination_var.trace_add("write", lambda *_: (self._schedule_save(), self._schedule_summary(refresh_installed=True)))
        for variable in (self.output_var, self.complete_app_var, self.extract_var, self.create_exe_var):
            variable.trace_add("write", lambda *_: self._schedule_save())
        self.include_build_var.trace_add("write", lambda *_: (self._schedule_save(), self._schedule_summary()))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._refresh_projects()
        initial = repo or self._initial_repo()
        if initial:
            self.open_project(initial)

    # ----- layout -------------------------------------------------------------------------

    def _build(self) -> None:
        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=20, pady=14)
        shell.grid_columnconfigure(0, weight=11)
        shell.grid_columnconfigure(1, weight=9)
        shell.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        header.grid_columnconfigure(1, weight=1)
        title = ctk.CTkFrame(header, fg_color="transparent")
        title.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(title, text="Update Studio", font=font(24, "bold"), text_color=INK).pack(anchor="w")
        ctk.CTkLabel(title, text="Verified update packages and rollback-ready installers", font=font(11), text_color=MUTED).pack(anchor="w")
        picker = ctk.CTkFrame(header, fg_color="transparent")
        picker.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(picker, text="Project", font=font(11, "bold"), text_color=TEXT).pack(side="left", padx=(0, 8))
        self.project_combo = ctk.CTkComboBox(picker, variable=self.project_var, values=[], width=360, height=36, state="readonly", command=self._project_picked, font=font(11), dropdown_font=font(11))
        self.project_combo.pack(side="left")
        secondary_button(picker, "Open…", self.choose_repo, width=80, height=36).pack(side="left", padx=(8, 0))
        self.settings_button = ctk.CTkButton(picker, text="⚙  Project settings", command=self.open_settings, width=150, height=36, corner_radius=9, fg_color=INK, hover_color="#18181B", font=font(11, "bold"))
        self.settings_button.pack(side="left", padx=(8, 0))

        main = ctk.CTkFrame(shell, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        main.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        form = ctk.CTkScrollableFrame(main, fg_color="transparent", scrollbar_button_color="#D4D4D8", scrollbar_button_hover_color=FAINT)
        form.grid(row=0, column=0, sticky="nsew", padx=(2, 4), pady=(8, 0))
        form.grid_columnconfigure(0, weight=1)

        self._section(form, 0, "1", "Where is it installed?")
        installed = ctk.CTkFrame(form, fg_color="transparent")
        installed.grid(row=1, column=0, sticky="ew", padx=18)
        installed.grid_columnconfigure(0, weight=1)
        self.destination_entry = ctk.CTkEntry(installed, textvariable=self.destination_var, placeholder_text="Install folder, for example C:\\laragon\\www\\app", height=36, corner_radius=9, border_color="#D4D4D8", fg_color="#FAFAFA")
        self.destination_entry.grid(row=0, column=0, sticky="ew")
        secondary_button(installed, "Browse", self.choose_destination, width=80, height=36).grid(row=0, column=1, padx=(8, 0))
        self.installed_label = ctk.CTkLabel(installed, text="", font=font(11), text_color=MUTED, anchor="w", justify="left", wraplength=520)
        self.installed_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self._section(form, 2, "2", "What goes in the package?")
        range_box = ctk.CTkFrame(form, fg_color="transparent")
        range_box.grid(row=3, column=0, sticky="ew", padx=18)
        range_box.grid_columnconfigure(1, weight=1)
        self.start_combo = self._commit_row(range_box, 0, "From", self.start_var, "first commit included")
        self.end_combo = self._commit_row(range_box, 1, "To", self.end_var, "usually the latest")
        quick = ctk.CTkFrame(range_box, fg_color="transparent")
        quick.grid(row=2, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.quick_buttons = {}
        for key, text in (("installed", "After installed"), ("tag", "After previous release"), ("package", "After last package"), ("latest", "Latest commit only")):
            button = ctk.CTkButton(quick, text=text, command=lambda key=key: self._use_quick_start(key), height=28, width=40, corner_radius=8, fg_color="#F4F4F5", hover_color="#E4E4E7", text_color=ACCENT, text_color_disabled="#A1A1AA", font=font(10, "bold"))
            button.pack(side="left", padx=(0, 6))
            self.quick_buttons[key] = button
        ctk.CTkLabel(range_box, text="Version", font=font(11, "bold"), text_color=TEXT).grid(row=3, column=0, sticky="w", padx=(0, 14), pady=(12, 0))
        version_box = ctk.CTkFrame(range_box, fg_color="transparent")
        version_box.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(12, 0))
        self.version_entry = ctk.CTkEntry(version_box, textvariable=self.version_var, width=190, height=34, placeholder_text="e.g. 2.7.0", font=font(12, "bold"))
        self.version_entry.pack(side="left")
        ctk.CTkLabel(version_box, textvariable=self.version_hint_var, font=font(10), text_color=FAINT).pack(side="left", padx=10)

        self._section(form, 4, "3", "Output")
        output = ctk.CTkFrame(form, fg_color="transparent")
        output.grid(row=5, column=0, sticky="ew", padx=18)
        output.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(output, textvariable=self.output_var, height=36, corner_radius=9, border_color="#D4D4D8", fg_color="#FAFAFA").grid(row=0, column=0, sticky="ew")
        secondary_button(output, "Browse", self.choose_output, width=80, height=36).grid(row=0, column=1, padx=(8, 0))
        switches = ctk.CTkFrame(output, fg_color="#FAFAFA", corner_radius=12)
        switches.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        switches.grid_columnconfigure((0, 1), weight=1)
        self._switch(switches, 0, 0, "Updater EXE", "Installer with backup and rollback", self.create_exe_var)
        self._switch(switches, 0, 1, "Extracted folder", "Paste-ready copy of the files", self.extract_var)
        self._switch(switches, 1, 0, "Include public/build", "Ship the current frontend build", self.include_build_var)
        self._switch(switches, 1, 1, "Complete app", "Every file plus vendor and node_modules", self.complete_app_var)
        deployment = ctk.CTkFrame(form, fg_color="transparent")
        deployment.grid(row=6, column=0, sticky="ew", padx=18, pady=(12, 10))
        deployment.grid_columnconfigure(0, weight=1)
        self.deployment_label = ctk.CTkLabel(deployment, text="", font=font(10), text_color=MUTED, anchor="w", justify="left", wraplength=470)
        self.deployment_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(deployment, text="Edit", command=self.open_settings, width=50, height=26, fg_color="transparent", hover_color="#F4F4F5", text_color=ACCENT, font=font(10, "bold")).grid(row=0, column=1)

        actions = ctk.CTkFrame(main, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", padx=20, pady=(8, 16))
        self.generate_button = ctk.CTkButton(actions, text="Generate release", command=self.generate, height=44, corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_HOVER, font=font(13, "bold"))
        self.generate_button.pack(side="left")
        secondary_button(actions, "Refresh commits", self.refresh_commits, width=130, height=44).pack(side="left", padx=10)
        secondary_button(actions, "Open output folder", self.open_output, width=150, height=44).pack(side="left")

        side = ctk.CTkFrame(shell, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        side.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(3, weight=1)
        compare = ctk.CTkFrame(side, fg_color="#FAFAFA", corner_radius=12)
        compare.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        compare.grid_columnconfigure((1, 3), weight=1)
        for column, text in ((1, "Installed"), (3, "This package")):
            ctk.CTkLabel(compare, text=text, font=font(10, "bold"), text_color=MUTED).grid(row=0, column=column, sticky="w", padx=8, pady=(10, 0))
        ctk.CTkLabel(compare, text="→", font=font(18, "bold"), text_color=FAINT).grid(row=1, column=2, rowspan=2)
        self.compare_labels = {}
        for row, key in ((1, "version"), (2, "commit")):
            for column, side_name in ((1, "installed"), (3, "package")):
                label = ctk.CTkLabel(compare, text="—", font=font(16 if key == "version" else 10, "bold" if key == "version" else "normal", "Segoe UI" if key == "version" else "Consolas"), text_color=INK if key == "version" else MUTED, anchor="w")
                label.grid(row=row, column=column, sticky="w", padx=8, pady=(0, 10 if row == 2 else 0))
                self.compare_labels[(side_name, key)] = label
        self.summary_label = ctk.CTkLabel(side, text="", font=font(11, "bold"), text_color=TEXT, anchor="w", justify="left", wraplength=400)
        self.summary_label.grid(row=1, column=0, sticky="ew", padx=20)
        self.notice_label = ctk.CTkLabel(side, text="", font=font(10), text_color=WARN, anchor="w", justify="left", wraplength=400)
        self.notice_label.grid(row=2, column=0, sticky="ew", padx=20, pady=(2, 0))
        self.tabs = ctk.CTkTabview(side, fg_color="transparent", segmented_button_selected_color=ACCENT, segmented_button_selected_hover_color=ACCENT_HOVER, height=200)
        self.tabs.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 6))
        commits_tab = self.tabs.add("Commits")
        log_tab = self.tabs.add("Log")
        for tab in (commits_tab, log_tab):
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
        self.commit_list = ctk.CTkTextbox(commits_tab, corner_radius=10, border_width=0, fg_color="#FAFAFA", text_color=TEXT, font=font(10, family="Consolas"), wrap="none")
        self.commit_list.grid(row=0, column=0, sticky="nsew")
        self.commit_list.configure(state="disabled")
        self.result = ctk.CTkTextbox(log_tab, corner_radius=10, border_width=0, fg_color="#18181B", text_color="#D4D4D8", font=font(10, family="Consolas"), wrap="word")
        self.result.grid(row=0, column=0, sticky="nsew")
        self.result.insert("1.0", "Your ZIP, extracted folder, deletion manifest, and updater EXE will be listed here.")
        self.result.configure(state="disabled")
        self.status_chip = ctk.CTkLabel(side, textvariable=self.status_var, height=30, corner_radius=8, fg_color="#F4F4F5", text_color=ACCENT, font=font(11))
        self.status_chip.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 16))

    def _section(self, parent, row: int, number: str, title: str) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", padx=18, pady=(10 if row else 4, 6))
        ctk.CTkLabel(box, text=number, width=24, height=24, corner_radius=12, fg_color=ACCENT, text_color="#FFFFFF", font=font(11, "bold")).pack(side="left")
        ctk.CTkLabel(box, text=title, font=font(15, "bold"), text_color=INK).pack(side="left", padx=10)

    def _commit_row(self, parent, row: int, title: str, variable, helper: str):
        ctk.CTkLabel(parent, text=title, font=font(11, "bold"), text_color=TEXT).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
        combo = ctk.CTkComboBox(parent, variable=variable, values=[], height=36, corner_radius=9, border_color="#D4D4D8", fg_color="#FAFAFA", button_color="#E4E4E7", button_hover_color="#D4D4D8", text_color=TEXT, dropdown_font=font(10, family="Consolas"), font=font(10, family="Consolas"), state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=3)
        ctk.CTkLabel(parent, text=helper, font=font(9), text_color=FAINT, width=110, anchor="w").grid(row=row, column=2, sticky="w", padx=(8, 0))
        return combo

    def _switch(self, parent, row: int, column: int, title: str, helper: str, variable) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=column, sticky="nsew", padx=12, pady=5)
        ctk.CTkSwitch(box, text=title, variable=variable, font=font(11, "bold"), text_color=TEXT, progress_color=ACCENT).pack(anchor="w")
        ctk.CTkLabel(box, text=helper, font=font(9), text_color=FAINT, anchor="w").pack(anchor="w", padx=(48, 0))

    # ----- projects and preferences -------------------------------------------------------

    def _initial_repo(self) -> Path | None:
        last = self.store.last_project()
        if last and Path(last).is_dir():
            return Path(last)
        if getattr(sys, "frozen", False):
            try:
                return find_repo(Path(sys.executable).resolve().parent)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            return find_repo(Path.cwd())
        except (OSError, subprocess.SubprocessError):
            return None

    def _refresh_projects(self) -> None:
        self.project_paths = {}
        for name, path in self.store.recent():
            self.project_paths[f"{name}{PROJECT_SEPARATOR}{path}"] = path
        if self.repo and not any(Path(path) == self.repo for path in self.project_paths.values()):
            self.project_paths[f"{self.repo.name}{PROJECT_SEPARATOR}{self.repo}"] = str(self.repo)
        self.project_combo.configure(values=list(self.project_paths) or ["No projects yet — use Open…"])
        if self.repo:
            current = next((label for label, path in self.project_paths.items() if Path(path) == self.repo), "")
            self.project_var.set(current)

    def _project_picked(self, label: str) -> None:
        path = self.project_paths.get(label)
        if path and (not self.repo or Path(path) != self.repo):
            self.open_project(Path(path))

    def choose_repo(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.repo or Path.cwd(), title="Choose the Git repository to package")
        if selected:
            self.open_project(Path(selected))

    def open_project(self, path: Path) -> bool:
        try:
            repo = find_repo(Path(path))
        except (OSError, subprocess.SubprocessError) as error:
            messagebox.showerror("Not a Git repository", f"{path}\n\n{error}")
            self._refresh_projects()
            return False
        if self.repo:
            self.save_preferences()
        self.repo = repo
        self.remote = remote_url(repo)
        settings = self.store.get(repo, self.remote)
        settings.application = settings.application or repo.name
        settings.output_dir = settings.output_dir or str(repo.parent)
        if not settings.destination and installed_deployment(repo):
            # The application is installed in its own working folder (local development box).
            settings.destination = str(repo)
        self.settings = settings
        self._loading = True
        try:
            self.output_var.set(settings.output_dir)
            self.destination_var.set(settings.destination)
            self.include_build_var.set(not settings.exclude_build)
            self.complete_app_var.set(settings.complete_app)
            self.extract_var.set(settings.extract)
            self.create_exe_var.set(settings.create_exe)
        finally:
            self._loading = False
        self.title(f"Update Studio — {repo.name}")
        self.installed = installed_deployment(settings.destination)
        self._refresh_installed = False
        self.refresh_commits(default_range=True)
        self._show_deployment()
        self.save_preferences()
        self._refresh_projects()
        return True

    def _collect_settings(self) -> None:
        self.settings.output_dir = self.output_var.get().strip()
        self.settings.destination = self.destination_var.get().strip()
        self.settings.exclude_build = not self.include_build_var.get()
        self.settings.complete_app = self.complete_app_var.get()
        self.settings.extract = self.extract_var.get()
        self.settings.create_exe = self.create_exe_var.get()

    def save_preferences(self) -> None:
        if self._save_job:
            self.after_cancel(self._save_job)
            self._save_job = None
        if not self.repo:
            return
        self._collect_settings()
        try:
            self.store.put(self.repo, self.settings, self.remote, self.repo.name)
        except OSError as error:
            self.status_var.set(f"Could not save preferences: {error}")

    def _schedule_save(self) -> None:
        if self._loading or not self.repo:
            return
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(700, self.save_preferences)

    def open_settings(self) -> None:
        if not self.repo:
            messagebox.showinfo("Open a project", "Open a Git repository first.")
            return
        self._collect_settings()
        ProjectSettingsDialog(self, self.settings, self.repo.name, self._settings_saved)

    def _settings_saved(self, settings: ProjectSettings) -> None:
        self._collect_settings()
        for name in ("application", "profile", "language", "version_variable", "required_env", "before_commands", "after_commands", "protected_paths", "required_services", "health_url", "health_status"):
            setattr(self.settings, name, getattr(settings, name))
        self.save_preferences()
        self._show_deployment()
        self.status_var.set("Project settings saved")

    def _show_deployment(self) -> None:
        settings = self.settings
        language = next((name for name, code in INSTALLER_LANGUAGES.items() if code == settings.language), "English")
        health = f"health check {settings.health_url}" if settings.health_url else "no health check"
        extra = len(settings.before_commands) + len(settings.after_commands)
        services = len(settings.required_services) + len(DEPLOYMENT_PRESETS.get(settings.profile, {}).get("required_services", []))
        self.deployment_label.configure(text=(
            f"Deployment: {settings.profile} recipe{f' + {extra} extra command(s)' if extra else ''} · {language} installer · "
            f"{len(settings.protected_paths)} protected path(s) · {services} service check(s) · {health}"
        ))

    def _on_close(self) -> None:
        self.save_preferences()
        self.destroy()

    # ----- folders ------------------------------------------------------------------------

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get() or None, title="Where should packages be written?")
        if selected:
            self.output_var.set(os.path.normpath(selected))

    def choose_destination(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.destination_var.get() or None, title="Where is the application installed?")
        if selected:
            self.destination_var.set(os.path.normpath(selected))

    def open_output(self) -> None:
        folder = self.output_var.get().strip()
        if folder and Path(folder).is_dir():
            os.startfile(folder)  # type: ignore[attr-defined]

    # ----- commits and range --------------------------------------------------------------

    def _label_for(self, sha: str) -> str:
        return next((commit.label for commit in self.commits if commit.sha == sha), "")

    def _selected(self, variable) -> str:
        return next((commit.sha for commit in self.commits if commit.label == variable.get()), "")

    def _selected_end(self) -> str | None:
        return self._selected(self.end_var) or None

    def refresh_commits(self, default_range: bool = False) -> None:
        if not self.repo:
            return
        start, end = self._selected(self.start_var), self._selected(self.end_var)
        try:
            self.commits = load_commits(self.repo)
        except (OSError, subprocess.SubprocessError) as error:
            messagebox.showerror("Git error", str(error))
            return
        labels = [commit.label for commit in self.commits]
        self.start_combo.configure(values=labels)
        self.end_combo.configure(values=labels)
        if not labels:
            self.start_var.set("")
            self.end_var.set("")
            self.status_var.set("This repository has no commits")
            return
        if default_range or not end:
            self.end_var.set(labels[0])
            self.start_var.set(self._label_for(self._default_start(self.commits[0].sha)) or labels[min(1, len(labels) - 1)])
        else:
            self.end_var.set(self._label_for(end) or labels[0])
            self.start_var.set(self._label_for(start) or labels[min(1, len(labels) - 1)])
        self.status_var.set(f"Loaded {len(labels)} commits")

    def _quick_start_candidates(self, end: str) -> dict[str, str]:
        installed = resolve_commit(self.repo, (self.installed or {}).get("commit", ""))
        tag = previous_release_tag(self.repo, end)
        return {
            "installed": end if installed == end else commit_after(self.repo, installed, end),
            "tag": commit_after(self.repo, tag, end) if tag else "",
            "package": commit_after(self.repo, self.settings.last_end_commit, end),
            "latest": end,
            "_tag": tag,
        }

    def _default_start(self, end: str) -> str:
        """Start right after what is installed, else right after the last package made."""
        candidates = self._quick_start_candidates(end)
        for key in ("installed", "package"):
            if candidates[key] and self._label_for(candidates[key]):
                return candidates[key]
        return ""

    def _use_quick_start(self, key: str) -> None:
        sha = self.quick_starts.get(key, "")
        label = self._label_for(sha)
        if label:
            self.start_var.set(label)
        elif sha:
            messagebox.showinfo("Commit not listed", "That commit is older than the 500 commits shown.")

    def _schedule_summary(self, refresh_installed: bool = False) -> None:
        if refresh_installed:
            self._refresh_installed = True
        if self._summary_job:
            self.after_cancel(self._summary_job)
        self._summary_job = self.after(150, self._update_summary)

    def _update_summary(self) -> None:
        self._summary_job = None
        if not self.repo:
            return
        if self._refresh_installed:
            self._refresh_installed = False
            self.installed = installed_deployment(self.destination_var.get())
        self._show_installed()
        start, end = self._selected(self.start_var), self._selected(self.end_var)
        if not start or not end:
            return
        try:
            self.quick_starts = self._quick_start_candidates(end)
        except (OSError, subprocess.SubprocessError):
            self.quick_starts = {}
        tag = self.quick_starts.get("_tag", "")
        self.quick_buttons["tag"].configure(text=f"After {tag}" if tag else "After previous release")
        for key, button in self.quick_buttons.items():
            button.configure(state="normal" if self.quick_starts.get(key) else "disabled")

        end_commit = next(commit for commit in self.commits if commit.sha == end)
        self.compare_labels[("package", "version")].configure(text=self.version_var.get() or "?")
        self.compare_labels[("package", "commit")].configure(text=f"{end[:8]}  {end_commit.date}")
        if not is_ancestor(self.repo, start, end):
            self._range_ok = False
            self.summary_label.configure(text="“From” must be the same commit as “To” or an older one.", text_color=BAD)
            self.notice_label.configure(text="")
            set_box_lines(self.commit_list, [])
            return
        self._range_ok = True
        try:
            commits = commits_in_range(self.repo, start, end)
            changes = change_details(self.repo, start, end)
            included, _ = changed_paths(self.repo, start, end)
            _, _, warnings = deployment_analysis(included, not self.include_build_var.get())
            migrations = added_migrations(self.repo, start, end)
        except (OSError, subprocess.SubprocessError) as error:
            self.summary_label.configure(text=f"Could not read the range: {error}", text_color=BAD)
            return
        files = changes["added"] + changes["modified"] + changes["deleted"]
        parts = [f"{len(commits)} commit(s)", f"{files} file(s): {changes['added']} added, {changes['modified']} changed, {changes['deleted']} deleted"]
        if migrations:
            parts.append(f"{len(migrations)} new migration(s)")
        self.summary_label.configure(text="  ·  ".join(parts), text_color=TEXT)

        notices = []
        installed = resolve_commit(self.repo, (self.installed or {}).get("commit", ""))
        if installed:
            expected = self.quick_starts.get("installed", "")
            if installed == end:
                notices.append("This commit is already installed.")
            elif not is_ancestor(self.repo, installed, end):
                notices.append("The installed commit is not part of this history; check the range.")
            elif start != expected and installed != start and is_ancestor(self.repo, installed, start):
                missing = len(str(git(self.repo, "rev-list", f"{installed}..{start}")).split()) - 1
                notices.append(f"Gap: {missing} commit(s) after the installed one are not in this package. Use “After installed”.")
            elif start != expected:
                notices.append("This package starts before the installed commit; files already installed are sent again.")
        elif (self.installed or {}).get("commit"):
            notices.append("The installed commit is not in this repository.")
        notices.extend(warnings)
        self.notice_label.configure(text="\n".join(f"⚠  {notice}" for notice in notices))
        set_box_lines(self.commit_list, [f"{commit.sha[:8]}  {commit.date}  {commit.subject}" for commit in commits])

    def _show_installed(self) -> None:
        installed = self.installed
        destination = self.destination_var.get().strip()
        if installed:
            version = installed.get("version") or "?"
            commit = str(installed.get("commit", ""))[:8]
            when = str(installed.get("installed_at", ""))[:16].replace("T", " ")
            self.installed_label.configure(text=f"✓  {installed.get('application', '')} {version} installed on {when} (commit {commit})", text_color=GOOD)
            self.compare_labels[("installed", "version")].configure(text=version)
            self.compare_labels[("installed", "commit")].configure(text=f"{commit}  {when[:10]}")
        else:
            if not destination:
                text = "Set the install folder to compare with what is installed. It is also the installer's default folder."
            elif not Path(destination).is_dir():
                text = "This folder does not exist on this computer; it is still the installer's default folder."
            else:
                text = "No Update Studio install is recorded in this folder yet."
            self.installed_label.configure(text=text, text_color=MUTED)
            self.compare_labels[("installed", "version")].configure(text="—")
            self.compare_labels[("installed", "commit")].configure(text="")

    def _sync_version(self) -> None:
        """Take the version from the repository (tag / package.json) for the selected end commit.

        When the repository has its own version the field is locked, so a package can never
        claim a version the code does not have. Only repositories without one are typed by hand.
        """
        end = self._selected_end()
        if not end or self.version_entry is None or not self.repo:
            return
        try:
            version, source = detect_version(self.repo, end)
        except (OSError, subprocess.SubprocessError):
            version, source = "", ""
        if version:
            self.version_entry.configure(state="normal")
            self.version_var.set(version)
            self.version_entry.configure(state="disabled")
            self.version_hint_var.set(f"Automatic: {source}")
        else:
            if str(self.version_entry.cget("state")) == "disabled":
                self.version_entry.configure(state="normal")
                self.version_var.set("")
            self.version_hint_var.set("No version in the repository; type it")

    # ----- generation ---------------------------------------------------------------------

    def generate(self) -> None:
        if self._busy or not self.repo:
            return
        start, end = self._selected(self.start_var), self._selected(self.end_var)
        if not start or not end:
            messagebox.showwarning("Select commits", "Choose both a start and end commit.")
            return
        if not is_ancestor(self.repo, start, end):
            messagebox.showwarning("Check the range", "“From” must be the same commit as “To” or an older one.")
            return
        self._collect_settings()
        if not self.settings.output_dir:
            messagebox.showwarning("Choose an output folder", "Choose where the package should be written.")
            return
        try:
            profile = build_profile(self.settings, self.repo.name)
            version = validate_version(self.version_var.get())
        except (KeyError, ValueError) as error:
            messagebox.showwarning("Check the settings", str(error))
            return
        self.save_preferences()
        self._busy = True
        self.status_var.set("Generating...")
        self.generate_button.configure(state="disabled", text="Generating...")
        self.tabs.set("Log")
        self._reset_log()
        self._append_log("Generation started")
        settings = self.settings
        options = (settings.exclude_build, settings.complete_app, settings.extract, settings.create_exe, settings.output_dir, profile, version)
        threading.Thread(target=self._generate, args=(self.repo, start, end, options), daemon=True).start()

    def _generate(self, repo: Path, start: str, end: str, options: tuple) -> None:
        try:
            exclude_build, complete_app, extract, create_exe, output_dir, profile, version = options
            self._append_log(f"Analyzing commits {start[:8]} → {end[:8]}")
            self._append_log("Creating and verifying package archive")
            zip_path, folder_path, deleted_path, count = create_package(repo, Path(output_dir), start, end, exclude_build, extract, complete_app)
            self._append_log(f"Verified package archive ({count} files)")
            included, _ = changed_paths(repo, start, end)
            _, dependencies, warnings = deployment_analysis(included, exclude_build)
            migrations = added_migrations(repo, start, end)
            changes = change_details(repo, start, end)
            self._append_log(f"Detected {len(migrations)} new migrations and {len(dependencies)} dependency manifests")
            lines = [f"ZIP: {zip_path}", f"Files: {count}"]
            if folder_path:
                lines.append(f"Folder: {folder_path}")
            if deleted_path:
                lines.append(f"Delete manifest: {deleted_path}")
            if create_exe:
                exe = build_updater_exe(
                    Path(output_dir), zip_path, deleted_path, start, end,
                    profile, version, changes, migrations, dependencies, warnings,
                    self._append_log,
                )
                self._append_log("Updater EXE created")
                lines.append(f"Auto-updater: {exe}")
            if migrations:
                lines.append(f"Migrations: {len(migrations)}")
            lines.extend(f"Warning: {warning}" for warning in warnings)
            self.after(0, self._show_success, "\n".join(lines), repo, start, end)
        except Exception as error:
            self.after(0, self._show_error, str(error))

    def _show_success(self, result: str, repo: Path, start: str, end: str) -> None:
        if self.repo == repo:
            self.settings.last_start_commit = start
            self.settings.last_end_commit = end
            self.save_preferences()
        self._append_log_line("Generation completed")
        self._append_log_line("")
        self._append_log_line(result)
        self._busy = False
        self.status_var.set("Package created and verified")
        self.generate_button.configure(state="normal", text="Generate release")
        self._schedule_summary()
        messagebox.showinfo("Done", "Update package created successfully.")

    def _reset_log(self) -> None:
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.configure(state="disabled")

    def _append_log(self, message: str) -> None:
        self.after(0, self._append_log_line, message)

    def _append_log_line(self, message: str) -> None:
        self.result.configure(state="normal")
        self.result.insert("end", message + "\n")
        self.result.see("end")
        self.result.configure(state="disabled")

    def _show_error(self, error: str) -> None:
        self._busy = False
        self._append_log_line(f"FAILED: {error}")
        self.status_var.set("Failed")
        self.generate_button.configure(state="normal", text="Generate release")
        messagebox.showerror("Package generation failed", error)


if __name__ == "__main__":
    UpdatePackageApp().mainloop()
