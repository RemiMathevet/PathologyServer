@echo off
title FoetoPath Slide Viewer
echo.
echo  ==========================================
echo   FoetoPath MRXS Slide Viewer
echo  ==========================================
echo.

REM Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python non trouve. Installez Python 3.10+
    pause
    exit /b 1
)

REM Install dependencies if needed
pip show flask >nul 2>&1
if errorlevel 1 (
    echo Installation des dependances...
    pip install -r requirements.txt
    echo.
)

REM Launch
set PORT=5000
if not "%1"=="" set PORT=%1

echo Demarrage sur http://127.0.0.1:%PORT%
echo.
start http://127.0.0.1:%PORT%
python app.py --port %PORT% --debug
pause
