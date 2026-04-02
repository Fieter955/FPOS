@echo off
call conda activate ipos
echo.
echo  ================================
echo   iPos 5.0 berjalan!
echo   Buka: http://localhost:8000
echo   Ctrl+C untuk stop
echo  ================================
echo.
uvicorn main:app --host 0.0.0.0 --port 8000
