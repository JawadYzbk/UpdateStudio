# Update Studio

Update Studio creates deployment packages for locally hosted web applications
from an inclusive Git commit range. Its self-contained updater validates the
target application, runs a deployment recipe, and verifies application health.


## Features

- Browse up to 500 recent commits and select an inclusive start/end range.
- Export exact committed file versions rather than dirty working-tree copies.
- Optionally include the complete current `public/build` directory, even when
  it is ignored by Git.
- Create a complete application package with the current `vendor` and
  `node_modules`, while excluding local `.env` files, local config, uploads, and storage data.
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

Download `UpdateStudio.exe` from the latest GitHub release. Run it and pick the Git repository to package; it remembers your projects.

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

1. Pick the **Project** at the top (or **Open…** a Git repository). Its saved
   settings load automatically.
2. **Where is it installed?** Choose the install folder. When it holds an
   Update Studio install, the window shows what is installed (version, commit,
   date) next to what the package will contain, and the **From** commit is
   preselected right after the installed commit.
3. **What goes in the package?** Adjust the inclusive **From** / **To** commits,
   or use a shortcut: *After installed*, *After previous release*, *After last
   package*, *Latest commit only*. The summary lists the commits, changed files,
   new migrations and warnings. The version is read from the repository (see
   below).
4. **Output:** choose the folder and the switches:
   - **Updater EXE:** a self-contained graphical updater with backup + rollback.
   - **Extracted folder:** a paste-ready copy of the files.
   - **Include public/build:** ship the current frontend build, even if ignored.
   - **Complete app:** every committed file plus the current root `vendor` and
     `node_modules`, without local `.env`, config, uploads or storage data.
5. Select **Generate release**.

Rarely changed deployment settings live in **Project settings**: application
name, recipe, installer language, version `.env` variable, required `.env`
names, extra before/after commands, protected paths (a rule + path per row),
required services (name, host, port per row) and the health URL and status.

### Per-project preferences

Every setting above, plus the output folder, the switches and the last end
commit, is saved per project and restored when the project is opened. They are
stored per user, outside the repository, in `%APPDATA%\UpdateStudio\projects.json`
(a project is keyed by its repository path, and found again through its git
remote when the folder moves). Set `UPDATE_STUDIO_CONFIG_DIR` to use another
folder.

### Version

The version is taken from the repository for the selected end commit: a
`vX.Y.Z` tag on it, `X.Y.Z+N.gSHA` for commits after a tag, or `version` in
`package.json` / `composer.json`. The field is locked when one is found and
only typed by hand for repositories without any version.

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
