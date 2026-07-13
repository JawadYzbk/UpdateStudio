# Update Studio

Update Studio creates deployment packages for locally hosted web applications
from an inclusive Git commit range. Its self-contained updater validates the
target application, runs a deployment recipe, and verifies application health.

<img width="982" height="712" alt="image" src="https://github.com/user-attachments/assets/c935952f-69a2-4925-9f3f-0c9cbee276d9" />


## Features

- Browse up to 500 recent commits and select an inclusive start/end range.
- Export exact committed file versions rather than dirty working-tree copies.
- Optionally include the complete current `public/build` directory, even when
  it is ignored by Git.
- Produce a verified ZIP, paste-ready extracted folder, and deletion manifest.
- Generate a self-contained Windows updater EXE with an embedded payload.
- Use Laravel, Laravel + Reverb, Node.js, generic PHP, static, or custom recipes.
- Preserve deployment-owned data such as `.env`, storage, uploads, and SQLite.
- Detect migrations and changed Composer/npm dependency files.
- Validate required `.env` names without transmitting their values.
- Run an HTTP health check and restore application files on failure.
- Record installed-version metadata and export timestamped deployment logs.
- Stream package generation and deployment command output in live log panels.
- Check Laravel readiness, its storage link, and configured local services.
- View and copy `php artisan about` output in a themed diagnostics modal.
- Prefill the client's application destination while keeping it editable.
- Generate the updater interface in English or Arabic.
- Let the operator choose the deployed application directory at install time.
- Back up every file that will be replaced or deleted before applying changes.
- Restore automatically if installation fails and support manual latest-update
  rollback.
- Reject paths that attempt to escape the selected installation directory.

## Download

Download `UpdateStudio.exe` from the latest GitHub release. Place it inside or
below the Git repository you want to package, then run it.

## Running from source

Requirements:

- Windows 10 or Windows 11
- Python 3.11+
- Git available on `PATH`

```powershell
git clone https://github.com/JawadYzbk/update-studio.git
cd update-studio
python -m pip install -r requirements.txt
python update_package_gui.py
```

You can also double-click `launch.bat`.

## Creating a package

1. Choose a Git repository.
2. Choose the output directory.
3. Select the first commit. This commit is included.
4. Select the end commit, normally `HEAD`.
5. Configure the outputs and deployment profile:
   - **Exclude build:** omit `public/build`.
   - **Extract files:** create a folder ready to paste into deployment.
   - **Updater EXE:** create a self-contained graphical updater.
6. Select **Generate release**.

The optional default app destination must be an absolute path. Generated
updaters open with that destination prefilled, so a client can install directly
or choose another folder. Installer language is selected per package; English
and deployment-focused Arabic are currently available.

Arabic installers embed [Noto Sans Arabic](https://notofonts.github.io/noto-docs/specimen/NotoSansArabic/)
and register it privately for the updater process. The font remains licensed
under the SIL Open Font License 1.1 included at `assets/fonts/NotoSansArabic-OFL.txt`.

When **Exclude build** is disabled, Update Studio appends every current file
under `public/build` to the package. This is intentional because build outputs
are commonly ignored by Git. The updater backs up the installed `public/build`,
removes it, then installs the packaged build so stale hashed assets cannot remain.

## Deployment and rollback behavior

The generated updater compares the installed deployment with the package,
validates the environment and Laravel readiness, then creates:

```text
<installation>/.update_backups/<package-id>/
```

The recipe enters maintenance mode when applicable, installs files, runs
dependency and migration commands, rebuilds caches, exits maintenance, and
performs the health check. Any failure restores application files. Database
migrations are never automatically reversed; the failure and deployment log
warn when manual database review may be required.

Before installation, the updater shows every recipe command as a checkbox.
Composer, migrations, npm install, and frontend build are selected only when
the detected package changes require them, and can still be overridden.

The application version must be valid SemVer (for example `2.7.0`). During
deployment it replaces an existing version value in `.env`. The variable name
is configurable in Studio and defaults to `APP_VERSION`. The updater does not
add the variable when absent and restores its old value on rollback.

Successful deployments write `.update_studio/deployment.json` and a timestamped
log below `.update_studio/logs/`. **Rollback latest** restores the newest backup.

## Building the Windows executable

```powershell
build.bat
```

The executable is written to `dist\UpdateStudio.exe`.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The tests cover packaging, dependency/migration analysis, protected local data,
environment checks, metadata/log creation, health-check rollback, and retry.

## Security model

- Files are sourced from the selected Git commit, except an explicitly included
  working-tree `public/build` directory.
- ZIP contents are verified against the selected file list.
- Installation and rollback paths are resolved and checked to remain within the
  chosen installation root.
- Existing files are backed up before the first deployment mutation.
- Package creation never modifies the source repository.

## License

[MIT](LICENSE)
