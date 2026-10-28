@echo off
REM Abre o editor do gabarito (fecha quando esta janela for fechada)
cd /d "%~dp0.."
python editor\servidor.py
pause
