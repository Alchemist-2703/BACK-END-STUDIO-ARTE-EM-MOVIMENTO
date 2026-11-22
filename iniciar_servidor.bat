@echo off
echo ========================================================
echo INICIANDO SERVIDOR: Studio Arte em Movimento (FastAPI)
echo ========================================================

cd /d "%~dp0"

echo Acessivel em: http://127.0.0.1:8000
echo Documentacao Swagger: http://127.0.0.1:8000/docs
echo.

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pause
