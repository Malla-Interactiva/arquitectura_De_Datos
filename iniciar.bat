@echo off
echo ============================================
echo   COMUNAS_NORM — Normalizador ETL
echo ============================================
echo.

:: Verificar que Python esta instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no esta instalado.
    echo Descargalo en: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Instalar dependencias si no estan
echo Instalando dependencias...
pip install flask --quiet

echo.
echo Iniciando servidor...
echo Abriendo http://localhost:5000 en el navegador...
echo.
echo Presiona Ctrl+C para detener el servidor.
echo ============================================
echo.

python app.py
pause
