@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ====== FULLY AUTOMATIC SAFE GITHUB PUSH ======
REM - Stages all local changes
REM - Commits with a timestamped message (if needed)
REM - Fetches + rebases on the remote branch (if it exists)
REM - Pushes the current branch to origin

cd /d "C:\Users\User\Desktop\Talk-Leee" || exit /b 1

set "REPO_HTTPS=https://github.com/hishamkhan-10/Talk-Leee.git"
set "REPO_SSH=git@github.com:hishamkhan-10/Talk-Leee.git"
set "REMOTE_URL=%REPO_HTTPS%"

if /I "%~1"=="--ssh" (
  set "REMOTE_URL=%REPO_SSH%"
) else (
  if exist "%USERPROFILE%\.ssh\id_ed25519" set "REMOTE_URL=%REPO_SSH%"
  if exist "%USERPROFILE%\.ssh\id_rsa" set "REMOTE_URL=%REPO_SSH%"
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set "TS=%%i"
set "COMMIT_MESSAGE=Auto update from TRAE agent [%TS%]"

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo Initializing Git repository...
  git init || exit /b 1
)

where git-credential-manager-core.exe >nul 2>&1
if not errorlevel 1 (
  git config credential.helper manager-core >nul 2>&1
) else (
  where git-credential-manager.exe >nul 2>&1
  if not errorlevel 1 git config credential.helper manager >nul 2>&1
)

git remote get-url origin >nul 2>&1
if errorlevel 1 (
  echo Adding remote origin...
  git remote add origin "%REMOTE_URL%" || exit /b 1
) else (
  git remote set-url origin "%REMOTE_URL%" || exit /b 1
)

git add -A || exit /b 1

git diff --cached --quiet
if errorlevel 1 (
  git commit -m "%COMMIT_MESSAGE%"
  if errorlevel 1 exit /b 1
) else (
  echo No staged changes to commit.
)

for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BRANCH=%%b"
if "%BRANCH%"=="HEAD" (
  echo Detached HEAD detected. Aborting.
  exit /b 1
)

git ls-remote --exit-code --heads origin "%BRANCH%" >nul 2>&1
if not errorlevel 1 (
  git fetch origin "%BRANCH%" || exit /b 1
  git merge-base HEAD FETCH_HEAD >nul 2>&1
  if errorlevel 1 (
    set "FALLBACK_BRANCH=trae/auto-!TS!"
    echo Remote and local history do not share a common base.
    echo Pushing current HEAD to a new branch: !FALLBACK_BRANCH!
    git push origin "HEAD:refs/heads/!FALLBACK_BRANCH!"
    if errorlevel 1 (
      echo Push failed! Check remote or authentication.
      exit /b 1
    )
    echo.
    echo Push completed.
    exit /b 0
  )
  git rebase FETCH_HEAD
  if errorlevel 1 (
    echo Conflicts during rebase detected. Aborting rebase; resolve manually.
    git rebase --abort >nul 2>&1
    exit /b 1
  )
)

git push origin "%BRANCH%"
if errorlevel 1 (
  echo Push failed! Check remote or authentication.
  exit /b 1
)

echo.
echo Push completed.
exit /b 0
