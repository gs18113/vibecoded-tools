@echo off
rem Builds dist\PhotoOrphanCleaner.exe (single file, no console window).
rem Requires Python 3.10+ from python.org (with tcl/tk, the default).
setlocal
cd /d "%~dp0"
python -m pip install --upgrade -r requirements-build.txt || goto :error
python -m unittest discover -s tests || goto :error
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name PhotoOrphanCleaner PhotoOrphanCleaner.pyw || goto :error
echo.
echo Done: %~dp0dist\PhotoOrphanCleaner.exe
exit /b 0
:error
echo Build failed.
exit /b 1
