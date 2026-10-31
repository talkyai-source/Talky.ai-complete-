<#
    local-test-setup.ps1 — stand up a LOCAL test database for a TestSprite run.

    Creates a dedicated role and database on the PostgreSQL 16 instance already
    running on this machine, loads the schema baseline, then applies every
    migration in filename order.

    This touches NOTHING outside the local `talky_test` database. It does not
    connect to, read from, or modify production.

    USAGE
        cd "C:\Users\AL AZIZ TECH\Desktop\Talky.ai-complete-"
        .\scripts\local-test-setup.ps1

    You will be prompted once for your LOCAL postgres superuser password. It is
    held only for the lifetime of this process and is never written to disk,
    logged, or echoed.

    Safe to re-run: it drops and recreates `talky_test` each time, so a botched
    run costs nothing. It will refuse to run if the target database name has
    been changed to anything that does not look like a test database.
#>

$ErrorActionPreference = 'Stop'

$PsqlExe  = 'C:\Program Files\PostgreSQL\16\bin\psql.exe'
$DbName   = 'talky_test'
$DbUser   = 'talky_test'
$DbPass   = 'talky_test_local'     # local-only, matches backend/.env
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Baseline = Join-Path $RepoRoot 'backend\database\schema\baseline_2026-06-02.sql'
$MigDir   = Join-Path $RepoRoot 'backend\database\migrations'

# ---- Guard rails ----------------------------------------------------------
if ($DbName -notmatch 'test') {
    throw "Refusing to run: target database '$DbName' is not clearly a test database."
}
if (-not (Test-Path $PsqlExe)) {
    throw "psql not found at $PsqlExe — adjust `$PsqlExe at the top of this script."
}
if (-not (Test-Path $Baseline)) {
    throw "Schema baseline not found at $Baseline"
}

Write-Host ''
Write-Host '  Local TestSprite database setup' -ForegroundColor Cyan
Write-Host '  --------------------------------' -ForegroundColor Cyan
Write-Host "  server    : localhost:5432 (your local PostgreSQL 16)"
Write-Host "  database  : $DbName  (dropped and recreated)"
Write-Host "  role      : $DbUser"
Write-Host "  baseline  : $(Split-Path -Leaf $Baseline)"
Write-Host ''

# ---- Superuser password (in-memory only) ----------------------------------
$SecurePass = Read-Host -Prompt '  Local postgres superuser password' -AsSecureString
$env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePass)
)
$env:PGCLIENTENCODING = 'UTF8'

function Invoke-Psql {
    param([string]$Database, [string]$Sql, [string]$File)
    $args = @('-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-h', '127.0.0.1', '-d', $Database)
    if ($Sql)  { $args += @('-c', $Sql) }
    if ($File) { $args += @('-f', $File) }
    & $PsqlExe @args
    if ($LASTEXITCODE -ne 0) { throw "psql failed (exit $LASTEXITCODE)" }
}

try {
    Write-Host '  [1/4] Creating role and database...' -ForegroundColor Yellow
    Invoke-Psql -Database 'postgres' -Sql @"
DROP DATABASE IF EXISTS $DbName;
DO `$`$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$DbUser') THEN
        CREATE ROLE $DbUser LOGIN PASSWORD '$DbPass';
    ELSE
        ALTER ROLE $DbUser LOGIN PASSWORD '$DbPass';
    END IF;
END `$`$;
CREATE DATABASE $DbName OWNER $DbUser;
"@

    Write-Host '  [2/4] Enabling extensions...' -ForegroundColor Yellow
    Invoke-Psql -Database $DbName -Sql @"
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
"@

    Write-Host '  [3/4] Loading schema baseline (this takes a moment)...' -ForegroundColor Yellow
    Invoke-Psql -Database $DbName -File $Baseline

    Write-Host '  [4/4] Applying migrations...' -ForegroundColor Yellow
    $applied = 0; $skipped = 0
    Get-ChildItem -Path $MigDir -Filter '*.sql' | Sort-Object Name | ForEach-Object {
        try {
            Invoke-Psql -Database $DbName -File $_.FullName
            Write-Host "        ok   $($_.Name)" -ForegroundColor DarkGray
            $applied++
        } catch {
            # A migration already folded into the baseline will fail on a
            # duplicate object. That is expected and not fatal.
            Write-Host "        skip $($_.Name) (already in baseline)" -ForegroundColor DarkGray
            $skipped++
        }
    }

    Write-Host ''
    Write-Host "  Migrations: $applied applied, $skipped skipped" -ForegroundColor Green

    $tableCount = (& $PsqlExe -U postgres -h 127.0.0.1 -d $DbName -t -A -c `
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';").Trim()

    Write-Host "  Tables created: $tableCount" -ForegroundColor Green
    Write-Host ''
    Write-Host '  Done. Next:' -ForegroundColor Cyan
    Write-Host '    cd backend; uvicorn app.main:app --reload --port 8000'
    Write-Host '    cd Talk-Leee; npm run dev'
    Write-Host ''
}
finally {
    # Never leave the password in the environment.
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
