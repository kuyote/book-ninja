@echo off
rem Opens Book Ninja. Drag an .m4b onto this file to open it directly.
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw book_ninja.pyw %* & exit /b)
where py >nul 2>nul && (start "" pyw -3 book_ninja.pyw %* & exit /b)
echo Python 3 was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
pause
