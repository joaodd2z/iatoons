@echo off
chcp 65001 >nul
title IATOONS - Fabrica (produzir e montar agenda)
cd /d "%~dp0"
echo.
echo  IATOONS: produzindo os episodios novos de todas as contas ativas e montando a agenda de 2 dias.
echo  Pode deixar rodando a noite. Se fechar, na proxima vez ele continua de onde parou.
echo.
".venv\Scripts\python.exe" fabrica.py tudo --dias 2
echo.
pause
