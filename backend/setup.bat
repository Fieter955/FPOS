@echo off
echo ========================================
echo    iPos 5.0 - Setup (Jalankan 1x saja)
echo ========================================
echo.
call conda activate ipos 2>nul || (
  echo Membuat environment baru...
  call conda create -n ipos python=3.11 -y
  call conda activate ipos
)
echo.
echo Installing dependencies...
pip install -r requirements.txt
pip install python-dateutil
echo.
echo =========================================
echo  Setup selesai!
echo.
echo  Selanjutnya:
echo  1. Edit .env dan isi GROQ_API_KEY (opsional)
echo  2. Jalankan run.bat
echo  3. Buka http://localhost:8000
echo  4. Login: admin / admin123
echo =========================================
pause
