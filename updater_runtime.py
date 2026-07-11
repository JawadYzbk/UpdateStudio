from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk


BACKUP_DIR = ".update_backups"


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


def backup_paths(installation: Path, manifest: dict) -> Path:
    backup = installation / BACKUP_DIR / manifest["package_id"]
    if backup.exists():
        raise FileExistsError("This update was already applied. Roll it back before applying it again.")
    files_root = backup / "files"
    records = []
    try:
        backup.mkdir(parents=True)
        affected = sorted(set(manifest["included"] + manifest["deleted"]))
        for relative in affected:
            source = safe_path(installation, relative)
            existed = source.is_file()
            records.append({"path": relative, "existed": existed})
            if existed:
                destination = safe_path(files_root, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        (backup / "rollback.json").write_text(
            json.dumps({"package": manifest, "files": records}, ensure_ascii=False, indent=2),
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


def apply_update(installation: Path, payload: Path, manifest: dict) -> Path:
    installation = installation.resolve()
    if not installation.is_dir():
        raise NotADirectoryError("Choose a valid installation folder.")
    backup = backup_paths(installation, manifest)
    try:
        with zipfile.ZipFile(payload) as archive:
            archived = sorted(name for name in archive.namelist() if not name.endswith("/"))
            if archived != sorted(manifest["included"]):
                raise RuntimeError("Embedded update files failed verification.")
            for relative in archived:
                target = safe_path(installation, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".update_tmp")
                with archive.open(relative) as source, temporary.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                os.replace(temporary, target)
        for relative in manifest["deleted"]:
            target = safe_path(installation, relative)
            if target.is_file() or target.is_symlink():
                target.unlink()
        return backup
    except Exception:
        rollback_from(installation, backup)
        shutil.rmtree(backup, ignore_errors=True)
        raise


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
        self.geometry("720x500")
        self.minsize(680, 470)
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

        safety = ctk.CTkFrame(card, fg_color="#EFF6FF", corner_radius=12)
        safety.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 18))
        ctk.CTkLabel(safety, text="Protected update", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#1D4ED8").pack(anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(safety, text="Every affected file is backed up before changes begin. Failed updates restore automatically.", font=ctk.CTkFont("Segoe UI", 10), text_color="#475569", wraplength=580, justify="left").pack(anchor="w", padx=16, pady=(0, 12))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", padx=24)
        ctk.CTkButton(actions, text="Install update", command=self.install, height=44, corner_radius=10, fg_color="#2563EB", hover_color="#1D4ED8", font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(side="left")
        ctk.CTkButton(actions, text="Rollback latest", command=self.rollback, height=44, corner_radius=10, fg_color="transparent", hover_color="#FEF2F2", border_width=1, border_color="#CBD5E1", text_color="#B91C1C").pack(side="left", padx=10)
        ctk.CTkLabel(card, textvariable=self.status_var, height=34, corner_radius=9, fg_color="#F8FAFC", text_color="#475569", font=ctk.CTkFont("Segoe UI", 10)).grid(row=5, column=0, sticky="ew", padx=24, pady=(18, 24))

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose deployed application folder")
        if selected:
            self.installation_var.set(selected)

    def installation(self) -> Path:
        if not self.installation_var.get().strip():
            raise ValueError("Choose the deployed application folder first.")
        return Path(self.installation_var.get())

    def install(self) -> None:
        try:
            installation = self.installation()
            if not messagebox.askyesno("Install update", f"Update files in:\n{installation}\n\nContinue?"):
                return
            self.status_var.set("Backing up and installing...")
            self.update_idletasks()
            backup = apply_update(installation, self.payload, self.manifest)
            self.status_var.set(f"Installed successfully. Backup: {backup}")
            messagebox.showinfo("Update complete", "The update was installed successfully.")
        except Exception as error:
            self.status_var.set("Installation failed; original files were restored")
            messagebox.showerror("Update failed", str(error))

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
