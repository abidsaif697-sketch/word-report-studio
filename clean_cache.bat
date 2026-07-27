@echo off
rem Double-click launcher to clean cache and temporary files.
cd /d "%~dp0"
echo Running clean_cache.py...
"C:\Users\MOI\AppData\Local\Programs\Python\Python312\python.exe" clean_cache.py
pause
