@echo off
rem Double-click launcher for Word Report Studio.
rem Uses pythonw.exe so no black console window appears.
cd /d "%~dp0"
start "" "C:\Users\MOI\AppData\Local\Programs\Python\Python312\pythonw.exe" run_gui.py
