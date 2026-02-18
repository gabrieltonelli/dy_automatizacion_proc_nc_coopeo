@echo off
setlocal ENABLEDELAYEDEXPANSION

rem === Configuración ===
set "SCRIPT=%~dp0solicitudes_nc_pipeline.py"
set "ARGS=--solo-descarga"
set "LOCKFILE=%~dp0solicitudes_nc.lock"
set "LOGFILE=%~dp0solicitudes_nc.log"

rem === Verifica el lock (otra instancia en ejecución) ===
if exist "%LOCKFILE%" (
    echo [INFO] Ya hay una ejecución en curso. Espera a que termine.
    timeout /t 5 >nul
    exit /b 1
)

rem === Crea lock ===
echo %date% %time% > "%LOCKFILE%"

echo -------------------------------------------------- >> "%LOGFILE%"
echo [%date% %time%] Iniciando pipeline >> "%LOGFILE%"
echo Iniciando proceso. Esta ventana puede tardar en cerrar...

rem === Ejecuta el script ===
python "%SCRIPT%" %ARGS%
set "EXITCODE=%ERRORLEVEL%"

rem === Limpieza del lock ===
del "%LOCKFILE%" 2>nul

if %EXITCODE% EQU 0 (
    echo Proceso finalizado correctamente.
    echo [%date% %time%] Finalizado OK >> "%LOGFILE%"
) else (
    echo Hubo un error. Codigo: %EXITCODE%
    echo [%date% %time%] Finalizado con error %EXITCODE% >> "%LOGFILE%"
)

pause
exit /b %EXITCODE%