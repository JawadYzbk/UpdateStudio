from __future__ import annotations

import os
import json
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


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    subject: str

    @property
    def label(self) -> str:
        return f"{self.sha[:8]}  {self.date}  {self.subject}"


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
    output = str(git(repo, "log", f"-n{limit}", "--date=short", "--pretty=format:%H%x1f%ad%x1f%s"))
    return [Commit(*line.split("\x1f", 2)) for line in output.splitlines() if line]


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


def create_package(
    repo: Path,
    output_dir: Path,
    start: str,
    end: str,
    exclude_build: bool,
    extract: bool,
) -> tuple[Path, Path | None, Path | None, int]:
    included, deleted = changed_paths(repo, start, end)
    git_included = [path for path in included if not path.startswith("public/build/")]
    deleted = [path for path in deleted if not path.startswith("public/build/")]
    build_files: list[Path] = []
    if not exclude_build:
        build_dir = repo / "public" / "build"
        if not build_dir.is_dir():
            raise FileNotFoundError("public/build does not exist. Run the frontend build or enable 'Exclude build'.")
        build_files = sorted(path for path in build_dir.rglob("*") if path.is_file())
        if not build_files:
            raise FileNotFoundError("public/build is empty. Run the frontend build or enable 'Exclude build'.")
    build_paths = [path.relative_to(repo).as_posix() for path in build_files]
    included = sorted(set(git_included + build_paths))
    if not included and not deleted:
        raise ValueError("No changed files exist in the selected range.")

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"update-{start[:8]}-{end[:8]}-{stamp}"
    zip_path = output_dir / f"{name}.zip"
    folder_path = output_dir / name if extract else None
    deleted_path = output_dir / f"{name}.deleted.txt" if deleted else None

    if git_included:
        git(repo, "archive", "--format=zip", f"--output={zip_path}", end, "--", *git_included)
    else:
        with zipfile.ZipFile(zip_path, "w"):
            pass
    if build_files:
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            for file, relative in zip(build_files, build_paths):
                archive.write(file, relative)

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
) -> Path:
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
    }
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    updater_source = resource_root / "updater_runtime.py"
    python = shutil.which("python") if getattr(sys, "frozen", False) else sys.executable
    if not python or subprocess.run(
        [python, "-c", "import PyInstaller"],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    ).returncode:
        raise FileNotFoundError("Run 'python -m pip install pyinstaller' before creating an updater EXE.")

    with tempfile.TemporaryDirectory(prefix="update-exe-") as temp:
        staging = Path(temp)
        payload = staging / "payload.zip"
        manifest_path = staging / "update_manifest.json"
        shutil.copy2(zip_path, payload)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        name = f"SandokTa3adod-Updater-{start[:8]}-{end[:8]}"
        subprocess.run(
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
                str(updater_source),
            ],
            check=True,
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
    exe = output_dir / f"{name}.exe"
    if not exe.is_file():
        raise RuntimeError("PyInstaller finished without creating the updater EXE.")
    return exe


class UpdatePackageApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Update Studio")
        self.geometry("980x680")
        self.minsize(900, 600)
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color="#F3F6FA")
        self.repo = default_repo()
        self.commits: list[Commit] = []

        self.repo_var = tk.StringVar(value=str(self.repo))
        self.output_var = tk.StringVar(value=str(self.repo.parent))
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        self.exclude_build_var = tk.BooleanVar(value=True)
        self.extract_var = tk.BooleanVar(value=True)
        self.create_exe_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")
        self._build()
        self.refresh_commits()

    def _build(self) -> None:
        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=24, pady=18)
        shell.grid_columnconfigure(0, weight=5)
        shell.grid_columnconfigure(1, weight=3)
        shell.grid_rowconfigure(1, weight=1)

        heading = ctk.CTkFrame(shell, fg_color="transparent")
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(heading, text="Update Studio", font=ctk.CTkFont("Segoe UI", 28, "bold"), text_color="#172B4D").pack(anchor="w")
        ctk.CTkLabel(
            heading,
            text="Create verified releases and rollback-ready Windows installers.",
            font=ctk.CTkFont("Segoe UI", 13),
            text_color="#64748B",
        ).pack(anchor="w", pady=(4, 0))

        main = ctk.CTkFrame(
            shell,
            fg_color="#FFFFFF",
            corner_radius=16,
            border_width=1,
            border_color="#E2E8F0",
        )
        main.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        form = ctk.CTkScrollableFrame(
            main,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color="#CBD5E1",
            scrollbar_button_hover_color="#94A3B8",
        )
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(10, 0))
        form.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(form, text="Package configuration", font=ctk.CTkFont("Segoe UI", 17, "bold"), text_color="#1E293B").grid(row=0, column=0, sticky="w", padx=18, pady=(4, 3))
        ctk.CTkLabel(form, text="Select the source repository and inclusive commit range.", font=ctk.CTkFont("Segoe UI", 11), text_color="#64748B").grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))

        self._folder_field(form, 2, "Repository", self.repo_var, self.choose_repo, readonly=True)
        self._folder_field(form, 3, "Output folder", self.output_var, self.choose_output)

        ctk.CTkFrame(form, height=1, fg_color="#E8EDF3").grid(row=4, column=0, sticky="ew", padx=18, pady=10)
        self.start_combo = self._commit_field(form, 5, "Start commit", "Included in the package", self.start_var)
        self.end_combo = self._commit_field(form, 6, "End commit", "Usually the current HEAD", self.end_var)

        options = ctk.CTkFrame(form, fg_color="#F8FAFC", corner_radius=12)
        options.grid(row=7, column=0, sticky="ew", padx=18, pady=(10, 8))
        options.grid_columnconfigure((0, 1, 2), weight=1)
        self._switch(options, 0, "Exclude build", "Skip build assets", self.exclude_build_var)
        self._switch(options, 1, "Extract files", "Paste-ready folder", self.extract_var)
        self._switch(options, 2, "Updater EXE", "Backup + rollback", self.create_exe_var)

        actions = ctk.CTkFrame(main, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", padx=22, pady=(10, 18))
        self.generate_button = ctk.CTkButton(actions, text="Generate release", command=self.generate, height=44, corner_radius=10, fg_color="#2563EB", hover_color="#1D4ED8", font=ctk.CTkFont("Segoe UI", 13, "bold"))
        self.generate_button.pack(side="left")
        ctk.CTkButton(actions, text="Refresh commits", command=self.refresh_commits, height=44, corner_radius=10, fg_color="transparent", hover_color="#EFF6FF", border_width=1, border_color="#CBD5E1", text_color="#334155").pack(side="left", padx=10)

        side = ctk.CTkFrame(shell, fg_color="#0F172A", corner_radius=16)
        side.grid(row=1, column=1, sticky="nsew", padx=(12, 0))
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(side, text="Release output", font=ctk.CTkFont("Segoe UI", 17, "bold"), text_color="#F8FAFC").grid(row=0, column=0, sticky="w", padx=22, pady=(22, 4))
        ctk.CTkLabel(side, text="Generated artifacts appear here.", font=ctk.CTkFont("Segoe UI", 11), text_color="#94A3B8").grid(row=1, column=0, sticky="w", padx=22, pady=(0, 16))
        self.status_chip = ctk.CTkLabel(side, textvariable=self.status_var, height=30, corner_radius=8, fg_color="#1E293B", text_color="#BFDBFE", font=ctk.CTkFont("Segoe UI", 11))
        self.status_chip.grid(row=2, column=0, sticky="ew", padx=22)
        self.result = ctk.CTkTextbox(side, corner_radius=10, border_width=0, fg_color="#111C2F", text_color="#CBD5E1", font=ctk.CTkFont("Consolas", 10), wrap="word")
        self.result.grid(row=3, column=0, sticky="nsew", padx=22, pady=18)
        self.result.insert("1.0", "Your ZIP, extracted folder, deletion manifest, and updater EXE will be listed here.")
        self.result.configure(state="disabled")
        ctk.CTkLabel(side, text="Backups are stored inside .update_backups", font=ctk.CTkFont("Segoe UI", 10), text_color="#64748B").grid(row=4, column=0, sticky="w", padx=22, pady=(0, 20))

    def _folder_field(self, parent, row: int, title: str, variable, command, readonly: bool = False) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", padx=22, pady=3)
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text=title, font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#334155").grid(row=0, column=0, sticky="w", pady=(0, 4))
        entry = ctk.CTkEntry(box, textvariable=variable, height=38, corner_radius=9, border_color="#CBD5E1", fg_color="#F8FAFC")
        entry.grid(row=1, column=0, sticky="ew")
        if readonly:
            entry.configure(state="readonly")
        ctk.CTkButton(box, text="Browse", width=84, height=38, corner_radius=9, fg_color="#E2E8F0", hover_color="#CBD5E1", text_color="#334155", command=command).grid(row=1, column=1, padx=(10, 0))

    def _commit_field(self, parent, row: int, title: str, helper: str, variable):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", padx=22, pady=4)
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text=title, font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#334155").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(box, text=helper, font=ctk.CTkFont("Segoe UI", 9), text_color="#94A3B8").grid(row=0, column=1, sticky="e")
        combo = ctk.CTkComboBox(box, variable=variable, values=[], height=38, corner_radius=9, border_color="#CBD5E1", fg_color="#F8FAFC", button_color="#E2E8F0", button_hover_color="#CBD5E1", text_color="#334155", dropdown_font=ctk.CTkFont("Consolas", 10), font=ctk.CTkFont("Consolas", 10), state="readonly")
        combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        return combo

    def _switch(self, parent, column: int, title: str, helper: str, variable) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=0, column=column, sticky="nsew", padx=12, pady=9)
        ctk.CTkSwitch(box, text=title, variable=variable, width=130, font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#334155", progress_color="#2563EB").pack(anchor="w")
        ctk.CTkLabel(box, text=helper, width=120, wraplength=120, justify="left", font=ctk.CTkFont("Segoe UI", 9), text_color="#94A3B8").pack(anchor="w", padx=(44, 0), pady=(2, 0))

    def choose_repo(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.repo)
        if not selected:
            return
        try:
            self.repo = find_repo(Path(selected))
            self.repo_var.set(str(self.repo))
            self.refresh_commits()
        except Exception as error:
            messagebox.showerror("Invalid repository", str(error))

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get())
        if selected:
            self.output_var.set(selected)

    def refresh_commits(self) -> None:
        try:
            self.commits = load_commits(self.repo)
            labels = [commit.label for commit in self.commits]
            self.start_combo.configure(values=labels)
            self.end_combo.configure(values=labels)
            if labels:
                self.end_var.set(labels[0])
                self.start_var.set(labels[min(1, len(labels) - 1)])
            self.status_var.set(f"Loaded {len(labels)} commits")
        except Exception as error:
            messagebox.showerror("Git error", str(error))

    def generate(self) -> None:
        labels = [commit.label for commit in self.commits]
        if self.start_var.get() not in labels or self.end_var.get() not in labels:
            messagebox.showwarning("Select commits", "Choose both a start and end commit.")
            return
        start_index = labels.index(self.start_var.get())
        end_index = labels.index(self.end_var.get())
        self.status_var.set("Generating...")
        self.generate_button.configure(state="disabled", text="Generating...")
        options = (self.exclude_build_var.get(), self.extract_var.get(), self.create_exe_var.get(), self.output_var.get())
        threading.Thread(target=self._generate, args=(start_index, end_index, options), daemon=True).start()

    def _generate(self, start_index: int, end_index: int, options: tuple[bool, bool, bool, str]) -> None:
        try:
            exclude_build, extract, create_exe, output_dir = options
            start = self.commits[start_index].sha
            end = self.commits[end_index].sha
            package = create_package(
                self.repo,
                Path(output_dir),
                start,
                end,
                exclude_build,
                extract,
            )
            zip_path, folder_path, deleted_path, count = package
            lines = [f"ZIP: {zip_path}", f"Files: {count}"]
            if folder_path:
                lines.append(f"Folder: {folder_path}")
            if deleted_path:
                lines.append(f"Delete manifest: {deleted_path}")
            if create_exe:
                exe = build_updater_exe(Path(output_dir), zip_path, deleted_path, start, end)
                lines.append(f"Auto-updater: {exe}")
            self.after(0, self._show_success, "\n".join(lines))
        except Exception as error:
            self.after(0, self._show_error, str(error))

    def _show_success(self, result: str) -> None:
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", result)
        self.result.configure(state="disabled")
        self.status_var.set("Package created and verified")
        self.generate_button.configure(state="normal", text="Generate release")
        messagebox.showinfo("Done", "Update package created successfully.")

    def _show_error(self, error: str) -> None:
        self.status_var.set("Failed")
        self.generate_button.configure(state="normal", text="Generate release")
        messagebox.showerror("Package generation failed", error)


if __name__ == "__main__":
    UpdatePackageApp().mainloop()
