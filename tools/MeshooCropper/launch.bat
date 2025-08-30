@echo off
:: Change to the current directory
cd /d "%~dp0"

:: Navigate to your virtual environment directory (update this to your venv path)
set VENV_PATH=venv\Scripts\activate

:: Check if the virtual environment activation script exists
if exist "%VENV_PATH%" (
    start cmd /k "%VENV_PATH% & python main.py"
) else (
    echo Virtual environment activation script not found at %VENV_PATH%.
    pause
)
