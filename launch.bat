@echo off
cd /d "%~dp0"
pythonw update_package_gui.py
if errorlevel 1 python update_package_gui.py
