@echo off
rem Builds a standalone "Book Ninja.exe" (no Python needed on the target PC).
rem Put ffmpeg.exe and ffprobe.exe in this folder first and they'll be bundled in.
cd /d "%~dp0"
py -3 -m pip install --upgrade pyinstaller || goto :err
py -3 -m PyInstaller --noconfirm --clean --windowed --name "Book Ninja" book_ninja.pyw || goto :err
py -3 -m PyInstaller --noconfirm --clean --console --name "bookninja" bookninja_cli.py || goto :err
copy /y "dist\bookninja\bookninja.exe" "dist\Book Ninja\" >nul
xcopy /e /i /y "dist\bookninja\_internal" "dist\Book Ninja\_internal" >nul
if exist ffmpeg.exe  copy /y ffmpeg.exe  "dist\Book Ninja\" >nul
if exist ffprobe.exe copy /y ffprobe.exe "dist\Book Ninja\" >nul
echo.
echo Built: dist\Book Ninja\Book Ninja.exe
if not exist "dist\Book Ninja\ffmpeg.exe" echo NOTE: copy ffmpeg.exe and ffprobe.exe into that folder before using it.
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
