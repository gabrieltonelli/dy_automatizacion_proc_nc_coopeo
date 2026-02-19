@echo off
setlocal
echo ==================================================
echo Inciando Pipeline Notas de Credito (Don Yeyo)
echo ==================================================
python main.py %*
if %ERRORLEVEL% EQU 0 (
    echo [OK] Proceso finalizado correctamente.
) else (
    echo [ERROR] El proceso fallo con codigo: %ERRORLEVEL%
)
pause
