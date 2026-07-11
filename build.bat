@echo off
setlocal
cd /d "%~dp0"

python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name UpdateStudio ^
  --distpath dist ^
  --add-data "updater_runtime.py;." ^
  update_package_gui.py

if errorlevel 1 exit /b 1
echo Built dist\UpdateStudio.exe
