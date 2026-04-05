@echo off
title Mesin Server iPos 5.0
color 0A

:: 1. Pastikan posisi ada di folder backend
cd /d "%~dp0"

echo ====================================================
echo    MENYALAKAN GUDANG iPOS 5.0 (JALUR BYPASS)...
echo    MOHON JANGAN TUTUP KOTAK HITAM INI
echo ====================================================
echo.

:: 2. Buka Chrome 5 detik kemudian (Kita pakai port 8000 agar bersih dari hantu)
start cmd /c "timeout /t 5 /nobreak > NUL & start chrome --app=http://127.0.0.1:8000"

:: 3. TEMBAK LANGSUNG KE MESIN UVICORN DI DALAM FOLDER CONDA ANDA!
C:\conda\miniconda3\envs\ipos\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8000

pause