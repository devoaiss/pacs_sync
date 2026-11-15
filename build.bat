@echo off
setlocal

echo ========================================
echo PACS Sync Build
echo ========================================
echo.

set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo ERROR: Virtual environment not found.
    echo Expected:
    echo %PYTHON%
    pause
    exit /b 1
)

echo Using Python:
"%PYTHON%" --version

echo.
echo Cleaning previous build...

if exist "%PROJECT_DIR%build" (
    rmdir /s /q "%PROJECT_DIR%build"
)

if exist "%PROJECT_DIR%dist" (
    rmdir /s /q "%PROJECT_DIR%dist"
)

if exist "%PROJECT_DIR%pacs_uploader.spec" (
    del /q "%PROJECT_DIR%pacs_uploader.spec"
)

echo.
echo Building EXE...

"%PYTHON%" -m PyInstaller ^
    --onefile ^
    --name pacs_uploader ^
    --collect-submodules applications ^
    --collect-submodules core ^
    --collect-submodules util ^
    "%PROJECT_DIR%main.py"

if errorlevel 1 (
    echo.
    echo ========================================
    echo BUILD FAILED
    echo ========================================
    pause
    exit /b 1
)

echo.
echo ========================================
echo BUILD SUCCESSFUL
echo ========================================
echo.
echo EXE:
echo %PROJECT_DIR%dist\pacs_uploader.exe
echo.

pause