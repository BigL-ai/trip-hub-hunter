@echo off
REM Corrida diaria del Hub Hunter (la dispara el Programador de tareas de Windows).
REM El log queda en logs\hunt.log para ver qué pasó si algo falla.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> logs\hunt.log
.venv\Scripts\python.exe -m hunter.main >> logs\hunt.log 2>&1
