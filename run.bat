@echo off
REM Sobe a API + frontend no Windows, perfil "prod" (backend\alocacao.db, porta 8731).
REM Python nao esta no PATH -- aponta direto pro executavel do anaconda.
setlocal

set "PY=C:\Users\augusto.conto\AppData\Local\anaconda3\python.exe"
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo criando ambiente virtual em .venv ...
    "%PY%" -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt
)

set "ALOCACAO_DB=%CD%\backend\alocacao.db"
set "ALOCACAO_ENV=prod"
set "ALOCACAO_UPLOADS=%CD%\uploads"
set "ALOCACAO_EXPORTS=%CD%\exports"

echo perfil prod  -  banco %ALOCACAO_DB%  -  porta 8731
echo.
echo Acesse desta maquina:   http://localhost:8731
echo Acesse da rede local (um colega digita um destes IPs + :8731):
ipconfig | findstr /R /C:"IPv4"
echo.

".venv\Scripts\python.exe" -m uvicorn --app-dir backend app.main:app --host 0.0.0.0 --port 8731 --reload --reload-dir backend --reload-dir frontend

endlocal
