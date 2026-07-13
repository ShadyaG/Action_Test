@echo off
setlocal EnableDelayedExpansion

:: 1. Universal Date Format (prevents locale bugs between different Windows machines)
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd'"') do set "DATE=%%i"

:: 2. Detect GitHub Actions automation
if "%GITHUB_ACTIONS%"=="true" (
    set "CI_MODE=true"
) else (
    set "CI_MODE=false"
)

:: 3. Configure paths (Uses repo files for CI, Q: drive for local manual runs)
if "%CI_MODE%"=="true" (
    set "PYTHON64=Q:\tiles\60_QA\conda\envs\nxgm-py3\python.exe"
    set "DEFAULT_PATH=Q:\tiles\35_sandbox\QA_VT\_results"
    set "SCRIPT_PATH=.\QA_VT_batch.py"
) else (
    set "PYTHON64=Q:\tiles\60_QA\conda\envs\nxgm-py3\python.exe"
    set "DEFAULT_PATH=Q:\tiles\35_sandbox\QA_VT\_results"
    set "SCRIPT_PATH=Q:\tiles\35_sandbox\QA_VT\QA_VT_batch.py"
)

set "DEFAULT_STATS=stats_%DATE%.csv"
set "DEFAULT_GEOM=geom_issues_%DATE%.geojson"
set "DEFAULT_DASHBOARD=qa_report_%DATE%.html"

set "OUT_PATH=%DEFAULT_PATH%"
set "STATS_FILE=%DEFAULT_STATS%"
set "GEOM_FILE=%DEFAULT_GEOM%"
set "DASHBOARD_FILE=%DEFAULT_DASHBOARD%"

:: 4. Skip user prompts if running in GitHub Actions
if "%CI_MODE%"=="true" goto run_script

echo -------------------------------------
echo QA VT - Configuration (Local Mode)
echo -------------------------------------
echo.
set /p "USER_PATH=Enter output path [%DEFAULT_PATH%]: "
if not "%USER_PATH%"=="" set "OUT_PATH=%USER_PATH%"

set /p "USER_STATS=Enter stats file name [%DEFAULT_STATS%]: "
if not "%USER_STATS%"=="" set "STATS_FILE=%USER_STATS%"

set /p "USER_GEOM=Enter geometry issues file name [%DEFAULT_GEOM%]: "
if not "%USER_GEOM%"=="" set "GEOM_FILE=%USER_GEOM%"

set /p "USER_DASHBOARD=Enter dashboard file name [%DEFAULT_DASHBOARD%]: "
if not "%USER_DASHBOARD%"=="" set "DASHBOARD_FILE=%USER_DASHBOARD%"

:run_script
echo.
echo -------------------------------------
echo Configuration
echo -------------------------------------
echo Output path: !OUT_PATH!
echo Stats file: !STATS_FILE!
echo Geoms file: !GEOM_FILE!
echo Dashboard file: !DASHBOARD_FILE!
echo -------------------------------------
echo.

if not exist "!OUT_PATH!" (
    echo Creating folder: !OUT_PATH!
    mkdir "!OUT_PATH!"
)

echo Running QA_VT_batch.py...
echo.

"%PYTHON64%" "%SCRIPT_PATH%" ^
    --path "!OUT_PATH!" ^
    --stats "!STATS_FILE!" ^
    --geoms "!GEOM_FILE!" ^
    --dashboard "!DASHBOARD_FILE!"

echo.
echo -------------------------------------
echo Done.
echo -------------------------------------

:: 5. Skip the pause command if running in GitHub Actions
if "%CI_MODE%"=="false" pause

endlocal