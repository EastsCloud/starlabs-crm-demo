@echo off
setlocal
cd /d "%~dp0"

if exist ".env.local" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env.local") do (
    if not "%%A"=="" set "%%A=%%B"
  )
)

set "APP_ENV=development"
set "COOKIE_SECURE=0"
set "APP_ORIGIN=http://127.0.0.1:8000"

if not defined DATABASE_URL goto missing_database
if not defined REMEMBER_ENCRYPTION_KEY goto missing_remember_key

if /i "%~1"=="migrate" goto migrate

echo Starting Starlabs AMS at http://127.0.0.1:8000
echo If the database schema is out of date, stop with Ctrl+C and run: run.bat migrate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
if errorlevel 1 goto failed
goto end

:migrate
echo Updating the configured database schema. Make a backup before upgrading.
python -m alembic upgrade head
if errorlevel 1 goto failed
echo Database migration complete. Run run.bat to start the application.
goto end

:missing_database
echo [ERROR] DATABASE_URL is not configured.
echo Set it in Windows environment variables or in .env.local.
goto failed

:missing_remember_key
echo [ERROR] REMEMBER_ENCRYPTION_KEY is not configured.
echo Set it in Windows environment variables or in .env.local.
goto failed

:failed
echo.
echo Startup failed. Review the message above.
pause
endlocal
exit /b 1

:end
endlocal
exit /b 0
