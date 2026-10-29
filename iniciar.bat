@echo off
REM Arranca la aplicacion en Windows. Configuracion en el fichero .env (copiar de .env.example).
cd /d %~dp0
if not exist .env copy .env.example .env >nul
if not exist .venv (
  py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv
)
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt
.venv\Scripts\python scripts\descargar_vendor.py
echo.
echo Abriendo http://localhost:8000
start "" http://localhost:8000
.venv\Scripts\python -m prefact.web.app
