from __future__ import annotations

import json
import os
import fnmatch
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk


BACKUP_DIR = ".update_backups"
STUDIO_DIR = ".update_studio"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class DeploymentError(RuntimeError):
    def __init__(self, message: str, database_applied: bool = False) -> None:
        super().__init__(message)
        self.database_applied = database_applied


class DeploymentLog:
    def __init__(self, installation: Path, progress=None) -> None:
        self.lines: list[str] = []
        self.progress = progress or (lambda _line: None)
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        self.path = installation / STUDIO_DIR / "logs" / f"deployment-{stamp}.log"

    def add(self, message: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {message}"
        self.lines.append(line)
        self.progress(line)

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        return self.path


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / name


def safe_path(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    root = root.resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Unsafe package path: {relative}")
    return target


def load_manifest() -> dict:
    return json.loads(resource_path("update_manifest.json").read_text(encoding="utf-8"))


def profile_for(manifest: dict) -> dict:
    return manifest.get("profile") or {}


def protected_mode(relative: str, manifest: dict) -> str | None:
    relative = relative.replace("\\", "/")
    for rule in profile_for(manifest).get("protected_paths", []):
        pattern = rule["path"].replace("\\", "/")
        if pattern.startswith("./"):
            pattern = pattern[2:]
        if fnmatch.fnmatchcase(relative, pattern) or (
            pattern.endswith("/**") and (relative == pattern[:-3] or relative.startswith(pattern[:-2]))
        ):
            return rule["mode"]
    return None


def affected_paths(installation: Path, manifest: dict) -> tuple[list[str], list[str]]:
    included = []
    for relative in manifest["included"]:
        mode = protected_mode(relative, manifest)
        if mode == "never_overwrite" or (mode == "preserve_if_exists" and safe_path(installation, relative).exists()):
            continue
        included.append(relative)
    deleted = [relative for relative in manifest["deleted"] if protected_mode(relative, manifest) not in {"never_delete", "never_overwrite", "preserve_if_exists"}]
    return included, deleted


def replacement_directories(installation: Path, manifest: dict) -> list[tuple[str, Path]]:
    directories = []
    for relative in manifest.get("replace_directories", []):
        target = safe_path(installation, relative)
        if target == installation.resolve():
            raise ValueError("The installation root cannot be replaced.")
        directories.append((relative, target))
    return directories


def read_env_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    names = set()
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() and value.strip().strip("'\""):
            names.add(name.strip())
    return names


def validate_environment(installation: Path, manifest: dict) -> list[str]:
    required = profile_for(manifest).get("required_env", [])
    present = read_env_names(installation / ".env")
    return [name for name in required if name not in present]


def update_env_version(installation: Path, version: str, variable: str = "APP_VERSION") -> bool:
    if not version or "\n" in version or "\r" in version:
        return False
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
        raise ValueError(f"Invalid version environment variable: {variable}")
    path = installation / ".env"
    if not path.is_file():
        return False
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    lines = text.splitlines(keepends=True)
    changed = False
    for index, line in enumerate(lines):
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        content = line[:-len(ending)] if ending else line
        match = re.match(rf"^(\s*(?:export\s+)?{re.escape(variable)}\s*=\s*)(.*)$", content)
        if not match:
            continue
        old_value = match.group(2).strip()
        quote = old_value[0] if len(old_value) >= 2 and old_value[0] == old_value[-1] and old_value[0] in "'\"" else ""
        lines[index] = f"{match.group(1)}{quote}{version}{quote}{ending}"
        changed = True
    if changed:
        temporary = path.with_name(path.name + ".update_tmp")
        temporary.write_bytes((b"\xef\xbb\xbf" if bom else b"") + "".join(lines).encode("utf-8"))
        os.replace(temporary, path)
    return changed


def run_command(command: str, installation: Path, log: DeploymentLog) -> None:
    log.add(f"Running: {command}")
    process = subprocess.Popen(
        command,
        cwd=installation,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    output = []
    assert process.stdout is not None
    for line in process.stdout:
        if line.strip():
            output.append(line.rstrip())
            log.add(line.rstrip())
    if process.wait():
        raise DeploymentError(f"Command failed ({process.returncode}): {command}\n" + "\n".join(output))


def health_check(profile: dict, log: DeploymentLog) -> None:
    url = profile.get("health_url", "").strip()
    if not url:
        return
    expected = int(profile.get("health_status", 200))
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except Exception as error:
        raise DeploymentError(f"Health check failed: {error}") from error
    elapsed = round((time.monotonic() - started) * 1000)
    log.add(f"Health check: HTTP {status} ({elapsed} ms)")
    if status != expected:
        raise DeploymentError(f"Health check failed: HTTP {status}; expected {expected}.")


def deployment_readiness(installation: Path, manifest: dict) -> list[tuple[bool, str]]:
    profile = profile_for(manifest)
    if not profile.get("laravel"):
        return []
    php = shutil.which("php")
    env = read_env_names(installation / ".env")
    checks = [
        (bool(php), "PHP executable found"),
        ((installation / "artisan").is_file(), "artisan found"),
        ((installation / "vendor" / "autoload.php").is_file(), "vendor/autoload.php exists"),
        ("APP_KEY" in env, "APP_KEY configured"),
        (os.access(installation / "storage", os.W_OK), "storage writable"),
        (os.access(installation / "bootstrap" / "cache", os.W_OK), "bootstrap/cache writable"),
    ]
    storage_link = installation / "public" / "storage"
    checks.append((storage_link.is_symlink() and storage_link.resolve() == (installation / "storage" / "app" / "public").resolve(), "Laravel storage link valid"))
    return checks


def service_statuses(manifest: dict) -> list[tuple[bool, str]]:
    statuses = []
    for service in profile_for(manifest).get("required_services", []):
        try:
            with socket.create_connection((service.get("host", "127.0.0.1"), int(service["port"])), timeout=1):
                statuses.append((True, service["name"]))
        except OSError:
            statuses.append((False, service["name"]))
    return statuses


def deployment_summary(installation: Path, manifest: dict) -> str:
    metadata_path = installation / STUDIO_DIR / "deployment.json"
    installed = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    changes = manifest.get("changes", {})
    lines = [
        f"Installed: {installed.get('version', 'Unknown')} ({installed.get('commit', 'unknown')[:8]})",
        f"Package: {manifest.get('version', 'Unknown')} ({manifest.get('end', 'unknown')[:8]})",
        "",
        f"{changes.get('modified', 0)} modified • {changes.get('added', 0)} added • {len(manifest.get('deleted', []))} deleted",
    ]
    migrations = manifest.get("migrations", [])
    if migrations:
        lines.append(f"{len(migrations)} migration(s) added: {', '.join(Path(path).name for path in migrations)}")
    dependencies = manifest.get("dependencies", [])
    if dependencies:
        lines.append("Dependencies changed: " + ", ".join(dependencies))
    if any(path.startswith("public/build/") for path in manifest.get("included", [])):
        lines.append("public/build changed")
    lines.extend(manifest.get("warnings", []))
    return "\n".join(lines)


def command_selected_by_default(command: str, manifest: dict) -> bool:
    command = command.lower()
    dependencies = {Path(path).name for path in manifest.get("dependencies", [])}
    if command.startswith("composer install"):
        return bool(dependencies & {"composer.json", "composer.lock"})
    if "artisan migrate" in command:
        return bool(manifest.get("migrations"))
    if command.startswith(("npm ci", "npm install")):
        build_included = any(path.startswith("public/build/") for path in manifest.get("included", []))
        return bool(dependencies & {"package.json", "package-lock.json"}) and not build_included
    if command.startswith("npm run build"):
        build_included = any(path.startswith("public/build/") for path in manifest.get("included", []))
        return bool(dependencies & {"package.json", "package-lock.json"}) and not build_included
    return True


def backup_paths(installation: Path, manifest: dict, progress=None) -> Path:
    progress = progress or (lambda _message: None)
    backup = installation / BACKUP_DIR / manifest["package_id"]
    if backup.exists():
        raise FileExistsError("This update was already applied. Roll it back before applying it again.")
    files_root = backup / "files"
    directories_root = backup / "directories"
    records = []
    directories = []
    try:
        backup.mkdir(parents=True)
        for relative, source in replacement_directories(installation, manifest):
            existed = source.is_dir()
            directories.append({"path": relative, "existed": existed})
            if existed:
                progress(f"Backing up directory: {relative}")
                destination = safe_path(directories_root, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, destination, symlinks=True)
        included, deleted = affected_paths(installation, manifest)
        metadata = [f"{STUDIO_DIR}/deployment.json"]
        if manifest.get("version"):
            metadata.append(".env")
        affected = sorted(set(included + deleted + metadata))
        for relative in affected:
            source = safe_path(installation, relative)
            existed = source.is_file()
            records.append({"path": relative, "existed": existed})
            if existed:
                progress(f"Backing up: {relative}")
                destination = safe_path(files_root, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        (backup / "rollback.json").write_text(
            json.dumps({"package": manifest, "files": records, "directories": directories}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(backup, ignore_errors=True)
        raise
    return backup


def rollback_from(installation: Path, backup: Path) -> None:
    rollback = json.loads((backup / "rollback.json").read_text(encoding="utf-8"))
    files_root = backup / "files"
    for record in rollback["files"]:
        target = safe_path(installation, record["path"])
        if record["existed"]:
            source = safe_path(files_root, record["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
    for record in rollback.get("directories", []):
        target = safe_path(installation, record["path"])
        if target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if record["existed"]:
            source = safe_path(backup / "directories", record["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, symlinks=True)


def apply_update(installation: Path, payload: Path, manifest: dict, progress=None) -> Path:
    progress = progress or (lambda _message: None)
    installation = installation.resolve()
    if not installation.is_dir():
        raise NotADirectoryError("Choose a valid installation folder.")
    backup = backup_paths(installation, manifest, progress)
    try:
        included, deleted = affected_paths(installation, manifest)
        for relative, target in replacement_directories(installation, manifest):
            if target.exists():
                progress(f"Removing old directory: {relative}")
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
        with zipfile.ZipFile(payload) as archive:
            archived = sorted(name for name in archive.namelist() if not name.endswith("/"))
            if archived != sorted(manifest["included"]):
                raise RuntimeError("Embedded update files failed verification.")
            for relative in included:
                progress(f"Installing: {relative}")
                target = safe_path(installation, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".update_tmp")
                with archive.open(relative) as source, temporary.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                os.replace(temporary, target)
        for relative in deleted:
            progress(f"Deleting: {relative}")
            target = safe_path(installation, relative)
            if target.is_file() or target.is_symlink():
                target.unlink()
        return backup
    except Exception:
        progress("File installation failed; restoring backup")
        rollback_from(installation, backup)
        progress("Original files restored")
        shutil.rmtree(backup, ignore_errors=True)
        raise


def write_deployment_metadata(installation: Path, manifest: dict) -> Path:
    profile = profile_for(manifest)
    path = installation / STUDIO_DIR / "deployment.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "application": profile.get("application") or manifest.get("title", "Application"),
                "version": manifest.get("version", ""),
                "commit": manifest.get("end", ""),
                "package_id": manifest["package_id"],
                "installed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def deploy_update(installation: Path, payload: Path, manifest: dict, progress=None) -> tuple[Path, Path]:
    installation = installation.resolve()
    log = DeploymentLog(installation, progress)
    backup: Path | None = None
    database_applied = False
    maintenance_enabled = False
    try:
        missing = validate_environment(installation, manifest)
        if missing:
            raise DeploymentError("Missing required environment variables: " + ", ".join(missing))
        log.add("Environment validation passed")
        for command in profile_for(manifest).get("before_commands", []):
            run_command(command, installation, log)
            maintenance_enabled = maintenance_enabled or "artisan down" in command.lower()
        log.add("Backing up affected files and installing payload")
        backup = apply_update(installation, payload, manifest, log.add)
        included, deleted = affected_paths(installation, manifest)
        log.add(f"{len(included)} files installed")
        log.add(f"{len(deleted)} files deleted")
        version_variable = profile_for(manifest).get("version_variable", "APP_VERSION")
        if update_env_version(installation, manifest.get("version", ""), version_variable):
            log.add(f"Updated {version_variable} to {manifest['version']}")
        else:
            log.add(f"{version_variable} is not present in .env; skipped")
        for command in profile_for(manifest).get("after_commands", []):
            if "artisan migrate" in command.lower():
                database_applied = True
            run_command(command, installation, log)
            if "artisan up" in command.lower():
                maintenance_enabled = False
            if "artisan migrate" in command.lower():
                log.add(f"{len(manifest.get('migrations', []))} migrations applied")
        health_check(profile_for(manifest), log)
        write_deployment_metadata(installation, manifest)
        log.add("Deployment successful")
        return backup, log.save()
    except Exception as error:
        if backup and backup.exists():
            rollback_from(installation, backup)
            log.add("Files rolled back successfully")
            shutil.rmtree(backup, ignore_errors=True)
        if database_applied:
            log.add("Database migrations were already applied; manual database review may be required")
        if maintenance_enabled:
            try:
                run_command("php artisan up", installation, log)
            except Exception as recovery_error:
                log.add(f"Could not disable maintenance mode: {recovery_error}")
        log.add(f"Deployment failed: {error}")
        log.save()
        if isinstance(error, DeploymentError):
            error.database_applied = database_applied
            if database_applied:
                error.args = (f"{error}\n\nFiles rolled back successfully. Database migrations were already applied; manual database review may be required.",)
            raise
        raise DeploymentError(str(error), database_applied) from error


def latest_backup(installation: Path) -> Path:
    root = installation / BACKUP_DIR
    backups = sorted(
        (path for path in root.iterdir() if (path / "rollback.json").is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if root.is_dir() else []
    if not backups:
        raise FileNotFoundError("No rollback backup was found in this installation.")
    return backups[0]


class UpdaterApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.manifest = load_manifest()
        self.payload = resource_path("payload.zip")
        self.installation_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready to install")
        self.title(self.manifest.get("title", "Application Update"))
        self.geometry("780x760")
        self.minsize(720, 680)
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color="#F3F6FA")
        self._build()

    def _build(self) -> None:
        root = ctk.CTkFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=30, pady=28)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(root, text="Application update", font=ctk.CTkFont("Segoe UI", 27, "bold"), text_color="#172B4D").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            root,
            text=f"Release {self.manifest['start'][:8]}  →  {self.manifest['end'][:8]}   •   {len(self.manifest['included'])} files",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color="#64748B",
        ).grid(row=1, column=0, sticky="w", pady=(4, 20))

        card = ctk.CTkFrame(root, fg_color="#FFFFFF", corner_radius=16, border_width=1, border_color="#E2E8F0")
        card.grid(row=2, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="Choose installation", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color="#1E293B").grid(row=0, column=0, sticky="w", padx=24, pady=(24, 4))
        ctk.CTkLabel(card, text="Select the root folder of the deployed application.", font=ctk.CTkFont("Segoe UI", 11), text_color="#64748B").grid(row=1, column=0, sticky="w", padx=24)

        folder = ctk.CTkFrame(card, fg_color="transparent")
        folder.grid(row=2, column=0, sticky="ew", padx=24, pady=(18, 16))
        folder.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(folder, textvariable=self.installation_var, height=44, corner_radius=10, border_color="#CBD5E1", fg_color="#F8FAFC", placeholder_text="C:\\path\\to\\application").grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(folder, text="Browse", command=self.choose_folder, width=90, height=44, corner_radius=10, fg_color="#E2E8F0", hover_color="#CBD5E1", text_color="#334155").grid(row=0, column=1, padx=(10, 0))

        self.details = ctk.CTkTabview(card, height=290, fg_color="#F8FAFC", segmented_button_selected_color="#2563EB")
        self.details.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 14))
        review_tab = self.details.add("Review")
        commands_tab = self.details.add("Commands")
        log_tab = self.details.add("Live log")

        self.preview = ctk.CTkTextbox(review_tab, corner_radius=10, fg_color="#F8FAFC", text_color="#334155", font=ctk.CTkFont("Consolas", 10))
        self.preview.pack(fill="both", expand=True, padx=4, pady=4)
        self.preview.insert("1.0", "Choose the deployed application to review this update.")
        self.preview.configure(state="disabled")

        command_card = ctk.CTkFrame(commands_tab, fg_color="transparent")
        command_card.pack(fill="both", expand=True)
        ctk.CTkLabel(command_card, text="Commands to run", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#334155").pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(command_card, text="Uncheck commands this deployment does not need. Laravel maintenance enter/exit always runs.", font=ctk.CTkFont("Segoe UI", 9), text_color="#64748B").pack(anchor="w", padx=14, pady=(0, 5))
        command_list = ctk.CTkScrollableFrame(command_card, fg_color="transparent")
        command_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.command_vars: dict[tuple[str, int], tk.BooleanVar] = {}
        selectable = 0
        for phase in ("before_commands", "after_commands"):
            for index, command in enumerate(profile_for(self.manifest).get(phase, [])):
                if command.lower() in {"php artisan down", "php artisan up"}:
                    continue
                variable = tk.BooleanVar(value=command_selected_by_default(command, self.manifest))
                self.command_vars[(phase, index)] = variable
                ctk.CTkCheckBox(command_list, text=command, variable=variable, height=24, checkbox_width=18, checkbox_height=18, font=ctk.CTkFont("Consolas", 10)).pack(anchor="w", padx=4, pady=2)
                selectable += 1
        if not selectable:
            ctk.CTkLabel(command_list, text="No optional commands in this profile.", text_color="#94A3B8", font=ctk.CTkFont("Segoe UI", 10)).pack(anchor="w", padx=4, pady=4)

        log_card = ctk.CTkFrame(log_tab, fg_color="#111C2F", corner_radius=12)
        log_card.pack(fill="both", expand=True, padx=4, pady=4)
        ctk.CTkLabel(log_card, text="Live deployment log", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#BFDBFE").pack(anchor="w", padx=14, pady=(10, 2))
        self.live_log = ctk.CTkTextbox(log_card, fg_color="transparent", text_color="#CBD5E1", font=ctk.CTkFont("Consolas", 9), wrap="word")
        self.live_log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.live_log.insert("1.0", "Waiting to start deployment...\n")
        self.live_log.configure(state="disabled")

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", padx=24)
        self.install_button = ctk.CTkButton(actions, text="Install update", command=self.install, height=44, corner_radius=10, fg_color="#2563EB", hover_color="#1D4ED8", font=ctk.CTkFont("Segoe UI", 12, "bold"))
        self.install_button.pack(side="left")
        ctk.CTkButton(actions, text="Rollback latest", command=self.rollback, height=44, corner_radius=10, fg_color="transparent", hover_color="#FEF2F2", border_width=1, border_color="#CBD5E1", text_color="#B91C1C").pack(side="left", padx=10)
        if profile_for(self.manifest).get("laravel"):
            ctk.CTkButton(actions, text="Create storage link", command=self.create_storage_link, height=44, corner_radius=10, fg_color="transparent", border_width=1, border_color="#CBD5E1", text_color="#334155").pack(side="left")
            ctk.CTkButton(actions, text="Laravel about", command=self.laravel_about, height=44, corner_radius=10, fg_color="transparent", border_width=1, border_color="#CBD5E1", text_color="#334155").pack(side="left", padx=(10, 0))
        ctk.CTkLabel(card, textvariable=self.status_var, height=34, corner_radius=9, fg_color="#F8FAFC", text_color="#475569", font=ctk.CTkFont("Segoe UI", 10)).grid(row=5, column=0, sticky="ew", padx=24, pady=(18, 24))

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose deployed application folder")
        if selected:
            self.installation_var.set(selected)
            self.show_preview()

    def installation(self) -> Path:
        if not self.installation_var.get().strip():
            raise ValueError("Choose the deployed application folder first.")
        return Path(self.installation_var.get())

    def selected_manifest(self) -> dict:
        manifest = dict(self.manifest)
        profile = dict(profile_for(self.manifest))
        for phase in ("before_commands", "after_commands"):
            profile[phase] = [
                command for index, command in enumerate(profile.get(phase, []))
                if command.lower() in {"php artisan down", "php artisan up"}
                or self.command_vars[(phase, index)].get()
            ]
        manifest["profile"] = profile
        return manifest

    def show_preview(self) -> None:
        try:
            installation = self.installation().resolve()
            lines = [deployment_summary(installation, self.manifest), "", "Environment validation"]
            missing = set(validate_environment(installation, self.manifest))
            for name in profile_for(self.manifest).get("required_env", []):
                lines.append(f"{'✗' if name in missing else '✓'} {name}")
            readiness = deployment_readiness(installation, self.manifest)
            if readiness:
                lines.extend(["", "Deployment readiness"])
                lines.extend(f"{'✓' if ok else '✗'} {label}" for ok, label in readiness)
            services = service_statuses(self.manifest)
            if services:
                lines.extend(["", "Required services"])
                lines.extend(f"{'Running' if ok else 'Not running'}  {name}" for ok, name in services)
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", "\n".join(lines))
            self.preview.configure(state="disabled")
        except Exception as error:
            self.status_var.set(str(error))

    def create_storage_link(self) -> None:
        try:
            installation = self.installation().resolve()
            log = DeploymentLog(installation)
            run_command("php artisan storage:link", installation, log)
            log.save()
            self.show_preview()
            messagebox.showinfo("Storage link", "Laravel storage link created.")
        except Exception as error:
            messagebox.showerror("Storage link failed", str(error))

    def laravel_about(self) -> None:
        try:
            installation = self.installation().resolve()
            result = subprocess.run(
                ["php", "artisan", "about"], cwd=installation, check=True,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            messagebox.showinfo("Laravel environment", result.stdout.strip())
        except Exception as error:
            messagebox.showerror("Laravel diagnostics failed", str(error))

    def _reset_live_log(self) -> None:
        self.live_log.configure(state="normal")
        self.live_log.delete("1.0", "end")
        self.live_log.configure(state="disabled")

    def _queue_live_log(self, line: str) -> None:
        self.after(0, self._append_live_log, line)

    def _append_live_log(self, line: str) -> None:
        self.live_log.configure(state="normal")
        self.live_log.insert("end", line + "\n")
        self.live_log.see("end")
        self.live_log.configure(state="disabled")

    def install(self) -> None:
        try:
            installation = self.installation()
            self.show_preview()
            manifest = self.selected_manifest()
            commands = profile_for(manifest).get("before_commands", []) + profile_for(manifest).get("after_commands", [])
            summary = deployment_summary(installation, manifest)
            command_summary = "\n".join(f"• {command}" for command in commands) or "• No commands selected"
            if not messagebox.askyesno("Review update", f"{summary}\n\nCommands:\n{command_summary}\n\nContinue with deployment?"):
                return
            self.status_var.set("Validating and deploying...")
            self.install_button.configure(state="disabled", text="Deploying...")
            self._reset_live_log()
            self.details.set("Live log")
            threading.Thread(target=self._install_worker, args=(installation, manifest), daemon=True).start()
        except Exception as error:
            self._install_failed(str(error))

    def _install_worker(self, installation: Path, manifest: dict) -> None:
        try:
            backup, log = deploy_update(installation, self.payload, manifest, self._queue_live_log)
            self.after(0, self._install_succeeded, backup, log)
        except Exception as error:
            self.after(0, self._install_failed, str(error))

    def _install_succeeded(self, _backup: Path, log: Path) -> None:
        self.status_var.set(f"Installed successfully. Log: {log}")
        self.install_button.configure(state="normal", text="Install update")
        self.show_preview()
        messagebox.showinfo("Deployment complete", f"The application passed deployment checks.\n\nLog: {log}")

    def _install_failed(self, error: str) -> None:
        self._append_live_log(f"FAILED: {error}")
        self.status_var.set("Deployment failed; application files were restored")
        self.install_button.configure(state="normal", text="Install update")
        messagebox.showerror("Update failed", error)

    def rollback(self) -> None:
        try:
            installation = self.installation()
            backup = latest_backup(installation)
            if not messagebox.askyesno("Rollback update", f"Restore backup:\n{backup.name}\n\nContinue?"):
                return
            rollback_from(installation, backup)
            shutil.rmtree(backup)
            self.status_var.set("Rollback completed")
            messagebox.showinfo("Rollback complete", "The previous files were restored.")
        except Exception as error:
            self.status_var.set("Rollback failed")
            messagebox.showerror("Rollback failed", str(error))


if __name__ == "__main__":
    UpdaterApp().mainloop()
