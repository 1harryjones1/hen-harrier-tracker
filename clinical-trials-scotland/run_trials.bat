@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py run_trials.py %*) || (python run_trials.py %*)
echo.
pause
