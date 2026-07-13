from __future__ import annotations

import json
import os
import fnmatch
import re
import ctypes
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

INSTALLER_TEXT = {
    "en": {
        "title": "Application Update", "heading": "Application update",
        "release": "Release {start} → {end}   •   {count} files",
        "choose": "Choose installation", "choose_help": "Select the root folder of the deployed application.",
        "browse": "Browse", "review": "Review", "commands_tab": "Commands", "log_tab": "Live log",
        "choose_review": "Choose the deployed application to review this update.",
        "commands_heading": "Commands to run",
        "commands_help": "Uncheck commands this deployment does not need. Laravel maintenance enter/exit always runs.",
        "no_optional": "No optional commands in this profile.", "live_heading": "Live deployment log",
        "waiting": "Waiting to start deployment...", "install": "Install update", "deploying": "Deploying...",
        "rollback": "Rollback latest", "storage_link": "Create storage link", "laravel_about": "Laravel about",
        "ready": "Ready to install", "env_check": "Environment validation", "readiness": "Deployment readiness",
        "services": "Required services", "running": "Running", "not_running": "Not running",
        "installed": "Installed", "package": "Package", "unknown": "Unknown",
        "changes": "{modified} modified • {added} added • {deleted} deleted",
        "migrations": "{count} migration(s) added: {names}", "dependencies": "Dependencies changed: {names}",
        "build_changed": "{path} changed", "review_title": "Review update", "commands": "Commands",
        "no_commands": "• No commands selected", "continue": "Continue with deployment?",
        "validating": "Validating and deploying...", "success_status": "Installed successfully. Log: {log}",
        "success_title": "Deployment complete", "success": "The application passed deployment checks.\n\nLog: {log}",
        "failed_status": "Deployment failed; application files were restored", "failed_title": "Update failed",
        "choose_first": "Choose the deployed application folder first.", "folder_title": "Choose deployed application folder",
        "storage_title": "Storage link", "storage_success": "Laravel storage link created.",
        "storage_failed": "Storage link failed", "about_title": "Laravel environment",
        "about_failed": "Laravel diagnostics failed", "rollback_title": "Rollback update",
        "rollback_question": "Restore backup:\n{name}\n\nContinue?", "rollback_done": "Rollback completed",
        "rollback_done_title": "Rollback complete", "rollback_success": "The previous files were restored.",
        "rollback_failed": "Rollback failed",
    },
    "ar": {
        "title": "تحديث التطبيق", "heading": "تحديث التطبيق",
        "release": "من {start} إلى {end}   •   عدد الملفات: {count}",
        "choose": "حدد مجلد التطبيق", "choose_help": "يمكنك اعتماد المسار المقترح أو اختيار مكان النسخة المثبتة.",
        "browse": "اختيار", "review": "مراجعة التحديث", "commands_tab": "خطوات النشر", "log_tab": "سجل التنفيذ",
        "choose_review": "حدد مجلد التطبيق لعرض تفاصيل التحديث قبل البدء.",
        "commands_heading": "الخطوات التي ستُنفّذ",
        "commands_help": "ألغِ أي خطوة لا يحتاجها هذا التحديث. وضع الصيانة يُفعّل ويُلغى تلقائياً.",
        "no_optional": "لا توجد خطوات اختيارية في هذا النمط.", "live_heading": "سجل التحديث المباشر",
        "waiting": "بانتظار بدء التحديث...", "install": "تثبيت التحديث", "deploying": "جارٍ التحديث...",
        "rollback": "استعادة النسخة السابقة", "storage_link": "إنشاء رابط ملفات التخزين", "laravel_about": "معلومات إطار العمل",
        "ready": "جاهز للتثبيت", "env_check": "فحص إعدادات البيئة", "readiness": "جاهزية التطبيق",
        "services": "الخدمات المطلوبة", "running": "تعمل", "not_running": "متوقفة",
        "installed": "الإصدار المثبت", "package": "الإصدار الجديد", "unknown": "غير معروف",
        "changes": "{modified} ملفاً معدلاً • {added} مضافاً • {deleted} محذوفاً",
        "migrations": "ملفات ترحيل جديدة ({count}):\n{names}", "dependencies": "ملفات الاعتماد المتغيرة:\n{names}",
        "build_changed": "سيتم استبدال ملفات الواجهة في {path}", "review_title": "تأكيد التحديث",
        "commands": "الخطوات المحددة", "no_commands": "• لا توجد خطوات إضافية محددة",
        "continue": "هل تريد بدء تحديث التطبيق؟", "validating": "جارٍ فحص التطبيق وتنفيذ التحديث...",
        "success_status": "اكتمل تحديث التطبيق بنجاح", "success_title": "اكتمل التحديث",
        "success": "تم تحديث التطبيق واجتاز فحص التشغيل.\n\nسجل التنفيذ: {log}",
        "failed_status": "تعذر إكمال التحديث وتمت استعادة ملفات التطبيق", "failed_title": "فشل التحديث",
        "choose_first": "حدد مجلد النسخة المثبتة أولاً.", "folder_title": "اختيار مجلد التطبيق",
        "storage_title": "رابط ملفات التخزين", "storage_success": "تم إنشاء رابط ملفات التخزين بنجاح.",
        "storage_failed": "تعذر إنشاء رابط التخزين", "about_title": "معلومات بيئة Laravel",
        "about_failed": "تعذر قراءة معلومات Laravel", "rollback_title": "استعادة النسخة السابقة",
        "rollback_question": "سيتم استعادة النسخة الاحتياطية:\n{name}\n\nهل تريد المتابعة؟",
        "rollback_done": "تمت استعادة النسخة السابقة", "rollback_done_title": "اكتملت الاستعادة",
        "rollback_success": "عادت ملفات التطبيق إلى حالتها السابقة.", "rollback_failed": "تعذرت استعادة النسخة السابقة",
    },
}

ARABIC_CHECKS = {
    "PHP executable found": "تم العثور على مفسّر: \u200ePHP\u200e",
    "artisan found": "ملف التشغيل موجود: \u200eartisan\u200e",
    "vendor/autoload.php exists": "مكتبات التطبيق مثبتة عبر: \u200eComposer\u200e",
    "APP_KEY configured": "مفتاح التطبيق مضبوط: \u200eAPP_KEY\u200e",
    "storage writable": "المجلد قابل للكتابة: \u200estorage\u200e",
    "bootstrap/cache writable": "مجلد التخزين المؤقت قابل للكتابة: \u200ebootstrap/cache\u200e",
    "Laravel storage link valid": "رابط ملفات التخزين سليم",
}

ARABIC_WARNINGS = {
    "composer.lock changed: run composer install": "تغيرت مكتبات الخادم. شغّل الأمر عند الحاجة: \u200ecomposer install\u200e",
    "package-lock.json changed: run npm ci": "تغيرت مكتبات الواجهة. شغّل الأمر عند بناء الواجهة محلياً: \u200enpm ci\u200e",
    "Frontend dependencies changed but public/build is excluded; assets may be outdated": "قد تظهر واجهة قديمة لأن مجلد البناء غير مضمن: \u200epublic/build\u200e",
}


def installer_text(language: str, key: str, **values) -> str:
    return INSTALLER_TEXT.get(language, INSTALLER_TEXT["en"])[key].format(**values)


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


def register_arabic_font() -> str:
    if os.name != "nt":
        return "Segoe UI"
    regular = next((path for path in [resource_path("NotoSansArabic.ttf"), resource_path("assets/fonts/NotoSansArabic.ttf")] if path.is_file()), None)
    semibold = next((path for path in [resource_path("NotoSansArabic-SemiBold.ttf"), resource_path("assets/fonts/NotoSansArabic-SemiBold.ttf")] if path.is_file()), None)
    if regular and ctypes.windll.gdi32.AddFontResourceExW(str(regular), 0x10, 0):
        if semibold:
            ctypes.windll.gdi32.AddFontResourceExW(str(semibold), 0x10, 0)
        return "Noto Sans Arabic"
    return "Segoe UI"


def ltr(value) -> str:
    return f"\u200e{value}\u200e"


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


def deployment_summary(installation: Path, manifest: dict, language: str = "en") -> str:
    metadata_path = installation / STUDIO_DIR / "deployment.json"
    installed = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    changes = manifest.get("changes", {})
    unknown = installer_text(language, "unknown")
    installed_commit = installed.get("commit", "")
    package_commit = manifest.get("end", "")
    display = ltr if language == "ar" else str
    installed_details = f"{installed.get('version') or unknown} ({installed_commit[:8] or unknown})"
    package_details = f"{manifest.get('version') or unknown} ({package_commit[:8] or unknown})"
    lines = [
        f"{installer_text(language, 'installed')}: {display(installed_details)}",
        f"{installer_text(language, 'package')}: {display(package_details)}",
        "",
        installer_text(language, "changes", modified=changes.get("modified", 0), added=changes.get("added", 0), deleted=len(manifest.get("deleted", []))),
    ]
    migrations = manifest.get("migrations", [])
    if migrations:
        names = [Path(path).name for path in migrations]
        migration_names = "\n".join(f"• {display(name)}" for name in names) if language == "ar" else ", ".join(names)
        lines.append(installer_text(language, "migrations", count=len(migrations), names=migration_names))
    dependencies = manifest.get("dependencies", [])
    if dependencies:
        dependency_names = "\n".join(f"• {display(name)}" for name in dependencies) if language == "ar" else ", ".join(dependencies)
        lines.append(installer_text(language, "dependencies", names=dependency_names))
    if any(path.startswith("public/build/") for path in manifest.get("included", [])):
        lines.append(installer_text(language, "build_changed", path=display("public/build")))
    warnings = manifest.get("warnings", [])
    if language == "ar":
        warnings = [ARABIC_WARNINGS.get(warning, warning) for warning in warnings]
    lines.extend(warnings)
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
        self.profile = profile_for(self.manifest)
        self.language = self.profile.get("language", "en")
        if self.language not in INSTALLER_TEXT:
            self.language = "en"
        self.font_family = register_arabic_font() if self.language == "ar" else "Segoe UI"
        self.payload = resource_path("payload.zip")
        self.installation_var = tk.StringVar(value=self.profile.get("default_destination", ""))
        self.status_var = tk.StringVar(value=self.t("ready"))
        self.title(self.t("title"))
        self.geometry("780x720" if self.language == "ar" else "780x760")
        self.minsize(720, 640 if self.language == "ar" else 680)
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color="#F3F6FA")
        self._build()

    def t(self, key: str, **values) -> str:
        return installer_text(self.language, key, **values)

    def font(self, size: int, weight: str = "normal"):
        if self.language == "ar" and self.font_family == "Noto Sans Arabic" and weight == "bold":
            return ctk.CTkFont("Noto Sans Arabic SemBd", size + 2)
        return ctk.CTkFont(self.font_family, size + (2 if self.language == "ar" else 0), weight)

    def _build(self) -> None:
        compact = self.language == "ar"
        root = ctk.CTkFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=22 if compact else 30, pady=14 if compact else 28)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)
        anchor = "e" if self.language == "ar" else "w"
        ctk.CTkLabel(root, text=self.t("heading"), font=self.font(23 if compact else 27, "bold"), text_color="#172B4D").grid(row=0, column=0, sticky=anchor)
        ctk.CTkLabel(
            root,
            text=self.t("release", start=ltr(self.manifest["start"][:8]), end=ltr(self.manifest["end"][:8]), count=ltr(len(self.manifest["included"]))),
            font=self.font(12),
            text_color="#64748B",
        ).grid(row=1, column=0, sticky=anchor, pady=(2 if compact else 4, 10 if compact else 20))

        card = ctk.CTkFrame(root, fg_color="#FFFFFF", corner_radius=16, border_width=1, border_color="#E2E8F0")
        card.grid(row=2, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(card, text=self.t("choose"), font=self.font(16, "bold"), text_color="#1E293B").grid(row=0, column=0, sticky=anchor, padx=24, pady=(14 if compact else 24, 2 if compact else 4))
        ctk.CTkLabel(card, text=self.t("choose_help"), font=self.font(11), text_color="#64748B").grid(row=1, column=0, sticky=anchor, padx=24)

        folder = ctk.CTkFrame(card, fg_color="transparent")
        folder.grid(row=2, column=0, sticky="ew", padx=24, pady=(10 if compact else 18, 10 if compact else 16))
        folder.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(folder, textvariable=self.installation_var, height=44, corner_radius=10, border_color="#CBD5E1", fg_color="#F8FAFC", placeholder_text="C:\\path\\to\\application", justify="left").grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(folder, text=self.t("browse"), command=self.choose_folder, width=90, height=44, corner_radius=10, fg_color="#E2E8F0", hover_color="#CBD5E1", text_color="#334155", font=self.font(11)).grid(row=0, column=1, padx=(10, 0))

        self.details = ctk.CTkTabview(card, height=220 if compact else 290, fg_color="#F8FAFC", segmented_button_selected_color="#2563EB")
        self.details._segmented_button.configure(font=self.font(11, "bold"))
        self.details.grid(row=3, column=0, sticky="nsew", padx=24, pady=(0, 10 if compact else 14))
        self.review_tab_name = self.t("review")
        self.commands_tab_name = self.t("commands_tab")
        self.log_tab_name = self.t("log_tab")
        review_tab = self.details.add(self.review_tab_name)
        commands_tab = self.details.add(self.commands_tab_name)
        log_tab = self.details.add(self.log_tab_name)

        self.preview = ctk.CTkTextbox(review_tab, corner_radius=10, fg_color="#F8FAFC", text_color="#334155", font=self.font(11) if self.language == "ar" else ctk.CTkFont("Consolas", 10), wrap="word")
        self.preview.pack(fill="both", expand=True, padx=4, pady=4)
        self.preview.tag_config("rtl", justify="right", rmargin=8)
        self.preview.insert("1.0", self.t("choose_review"), "rtl" if self.language == "ar" else None)
        self.preview.configure(state="disabled")

        command_card = ctk.CTkFrame(commands_tab, fg_color="transparent")
        command_card.pack(fill="both", expand=True)
        ctk.CTkLabel(command_card, text=self.t("commands_heading"), font=self.font(11, "bold"), text_color="#334155").pack(anchor=anchor, padx=14, pady=(10, 2))
        ctk.CTkLabel(command_card, text=self.t("commands_help"), font=self.font(9), text_color="#64748B").pack(anchor=anchor, padx=14, pady=(0, 5))
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
            ctk.CTkLabel(command_list, text=self.t("no_optional"), text_color="#94A3B8", font=self.font(10)).pack(anchor=anchor, padx=4, pady=4)

        log_card = ctk.CTkFrame(log_tab, fg_color="#111C2F", corner_radius=12)
        log_card.pack(fill="both", expand=True, padx=4, pady=4)
        ctk.CTkLabel(log_card, text=self.t("live_heading"), font=self.font(11, "bold"), text_color="#BFDBFE").pack(anchor=anchor, padx=14, pady=(10, 2))
        self.live_log = ctk.CTkTextbox(log_card, fg_color="transparent", text_color="#CBD5E1", font=ctk.CTkFont("Consolas", 9), wrap="word")
        self.live_log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.live_log.insert("1.0", self.t("waiting") + "\n")
        self.live_log.configure(state="disabled")

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", padx=24)
        self.install_button = ctk.CTkButton(actions, text=self.t("install"), command=self.install, height=44, corner_radius=10, fg_color="#2563EB", hover_color="#1D4ED8", font=self.font(12, "bold"))
        self.install_button.pack(side="left")
        ctk.CTkButton(actions, text=self.t("rollback"), command=self.rollback, height=44, corner_radius=10, fg_color="transparent", hover_color="#FEF2F2", border_width=1, border_color="#CBD5E1", text_color="#B91C1C", font=self.font(11)).pack(side="left", padx=10)
        if profile_for(self.manifest).get("laravel"):
            ctk.CTkButton(actions, text=self.t("storage_link"), command=self.create_storage_link, height=44, corner_radius=10, fg_color="transparent", border_width=1, border_color="#CBD5E1", text_color="#334155", font=self.font(10)).pack(side="left")
            ctk.CTkButton(actions, text=self.t("laravel_about"), command=self.laravel_about, height=44, corner_radius=10, fg_color="transparent", border_width=1, border_color="#CBD5E1", text_color="#334155", font=self.font(10)).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(card, textvariable=self.status_var, height=34, corner_radius=9, fg_color="#F8FAFC", text_color="#475569", font=self.font(10)).grid(row=5, column=0, sticky="ew", padx=24, pady=(10 if compact else 18, 14 if compact else 24))

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(title=self.t("folder_title"), initialdir=self.installation_var.get() or None)
        if selected:
            self.installation_var.set(selected)
            self.show_preview()

    def installation(self) -> Path:
        if not self.installation_var.get().strip():
            raise ValueError(self.t("choose_first"))
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
            lines = [deployment_summary(installation, self.manifest, self.language), "", self.t("env_check")]
            missing = set(validate_environment(installation, self.manifest))
            for name in profile_for(self.manifest).get("required_env", []):
                lines.append(f"{'✗' if name in missing else '✓'} {ltr(name) if self.language == 'ar' else name}")
            readiness = deployment_readiness(installation, self.manifest)
            if readiness:
                lines.extend(["", self.t("readiness")])
                lines.extend(f"{'✓' if ok else '✗'} {ARABIC_CHECKS.get(label, label) if self.language == 'ar' else label}" for ok, label in readiness)
            services = service_statuses(self.manifest)
            if services:
                lines.extend(["", self.t("services")])
                lines.extend(f"{self.t('running') if ok else self.t('not_running')}  {ltr(name) if self.language == 'ar' else name}" for ok, name in services)
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", "\n".join(lines), "rtl" if self.language == "ar" else None)
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
            messagebox.showinfo(self.t("storage_title"), self.t("storage_success"))
        except Exception as error:
            messagebox.showerror(self.t("storage_failed"), str(error))

    def laravel_about(self) -> None:
        try:
            installation = self.installation().resolve()
            result = subprocess.run(
                ["php", "artisan", "about"], cwd=installation, check=True,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            messagebox.showinfo(self.t("about_title"), result.stdout.strip())
        except Exception as error:
            messagebox.showerror(self.t("about_failed"), str(error))

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
            summary = deployment_summary(installation, manifest, self.language)
            command_summary = "\n".join(f"• {ltr(command) if self.language == 'ar' else command}" for command in commands) or self.t("no_commands")
            if not messagebox.askyesno(self.t("review_title"), f"{summary}\n\n{self.t('commands')}:\n{command_summary}\n\n{self.t('continue')}"):
                return
            self.status_var.set(self.t("validating"))
            self.install_button.configure(state="disabled", text=self.t("deploying"))
            self._reset_live_log()
            self.details.set(self.log_tab_name)
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
        display_log = ltr(log) if self.language == "ar" else log
        self.status_var.set(self.t("success_status", log=display_log))
        self.install_button.configure(state="normal", text=self.t("install"))
        self.show_preview()
        messagebox.showinfo(self.t("success_title"), self.t("success", log=display_log))

    def _install_failed(self, error: str) -> None:
        self._append_live_log(f"FAILED: {error}")
        self.status_var.set(self.t("failed_status"))
        self.install_button.configure(state="normal", text=self.t("install"))
        messagebox.showerror(self.t("failed_title"), error)

    def rollback(self) -> None:
        try:
            installation = self.installation()
            backup = latest_backup(installation)
            backup_name = ltr(backup.name) if self.language == "ar" else backup.name
            if not messagebox.askyesno(self.t("rollback_title"), self.t("rollback_question", name=backup_name)):
                return
            rollback_from(installation, backup)
            shutil.rmtree(backup)
            self.status_var.set(self.t("rollback_done"))
            messagebox.showinfo(self.t("rollback_done_title"), self.t("rollback_success"))
        except Exception as error:
            self.status_var.set(self.t("rollback_failed"))
            messagebox.showerror(self.t("rollback_failed"), str(error))


if __name__ == "__main__":
    UpdaterApp().mainloop()
