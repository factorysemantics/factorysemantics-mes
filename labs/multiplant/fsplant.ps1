<#
.SYNOPSIS
    Run several independent MES plants side by side on one machine.

.DESCRIPTION
    Each plant is a completely separate MES: its own database, its own OPC UA
    server, its own dashboard on its own port. Nothing is shared. There is no
    multi-tenant code path anywhere in the MES, and that is the point --
    isolation here is by construction, not by a WHERE clause somebody might
    forget.

    Every difference between the two plants is an environment variable. MES-TWIN
    reads all configuration from MES_* variables, so "run a second plant" needs
    no product code at all:

        MES_DATABASE_URL   which database        -> data isolation
        MES_API_PORT       which dashboard       -> two UIs at once
        MES_OPC_ENDPOINT   which OPC UA server   -> two machine layers at once
        MES_TAG_MAP_FILE   which machines exist  -> different plants entirely
        MES_REPLAY_DIR     which line data       -> different physics

.EXAMPLE
    .\fsplant.ps1 all init
    .\fsplant.ps1 all start
    .\fsplant.ps1 all status
    .\fsplant.ps1 all stop
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('bottling', 'machining', 'all')]
    [string]$Plant,

    [Parameter(Mandatory, Position = 1)]
    [ValidateSet('init', 'start', 'stop', 'status')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'

$Root   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Mes    = Join-Path $Root '.venv\Scripts\fsmes.exe'
$DataDir = Join-Path $PSScriptRoot '.data'

if (-not (Test-Path $Python)) { throw "No venv at $Python. Run: python -m venv .venv; .venv\Scripts\pip install -e .[dev]" }

# --------------------------------------------------------------------------
# The plant registry. Adding a third plant is a block here plus a tag map and
# generated line data -- no product code. If a new plant ever needs a change
# under src/, that is a tenant literal and a bug.
# --------------------------------------------------------------------------
$PLANTS = [ordered]@{
    bottling = @{
        Label   = 'ACME Beverages / Kansas City -- 6-station bottling line'
        ApiPort = 8010
        OpcPort = 4841
        TagMap  = 'config\tag_map_kepsim.json'
        Replay  = 'labs\kepsim\out'
        Init    = 'labs\multiplant\bottling\init.py'
    }
    machining = @{
        Label   = 'Northgate Machining / Cell A -- 3-station machining cell'
        ApiPort = 8020
        OpcPort = 4842
        TagMap  = 'labs\multiplant\machining\tag_map.json'
        Replay  = 'labs\multiplant\machining\out'
        Init    = 'labs\multiplant\machining\seed.py'
    }
}

function Get-Targets {
    if ($Plant -eq 'all') { return $PLANTS.Keys } else { return @($Plant) }
}

function Set-PlantEnv([string]$name) {
    $p = $PLANTS[$name]
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $db = (Join-Path $DataDir "$name.db") -replace '\\', '/'

    $env:MES_DATABASE_URL = "sqlite:///$db"
    $env:MES_API_PORT     = $p.ApiPort
    $env:MES_API_HOST     = '127.0.0.1'
    $env:MES_OPC_ENDPOINT = "opc.tcp://127.0.0.1:$($p.OpcPort)/mes-twin/$name"
    $env:MES_TAG_MAP_FILE = $p.TagMap
    $env:MES_REPLAY_DIR   = $p.Replay
    $env:MES_LOG_DIR      = "logs\$name"
    # No ERP in the lab: both plants would otherwise poll the same mock ERP and
    # fight over the same orders, which would look like a bug in the MES.
    $env:MES_ERP_MODE     = 'off'
    # Stable per-plant key so a restart does not sign everyone out, and so the
    # two plants can never accept each other's session tokens.
    $env:MES_SECRET_KEY   = "lab-$name-do-not-use-in-production"
}

function Start-Plant([string]$name) {
    $p = $PLANTS[$name]
    Set-PlantEnv $name
    $pidFile = Join-Path $DataDir "$name.pids"

    if (Test-Path $pidFile) {
        $alive = Get-Content $pidFile | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
        if ($alive) { Write-Host "  $name is already running (PIDs $($alive -join ', ')). Stop it first." -ForegroundColor Yellow; return }
    }

    $procs = @()
    # Order matters: the OPC server must be listening before the agent tries to
    # subscribe. The agent retries forever anyway, but starting it first means a
    # confusing burst of connection errors in the log.
    $procs += Start-Process -FilePath $Mes -ArgumentList 'run-opc-sim', '--replay' -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 2500
    $procs += Start-Process -FilePath $Mes -ArgumentList 'run-opc-agent' -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    $procs += Start-Process -FilePath $Mes -ArgumentList 'run-api' -WorkingDirectory $Root -WindowStyle Hidden -PassThru

    $procs.Id | Set-Content $pidFile
    Write-Host "  $name started -> http://127.0.0.1:$($p.ApiPort)/dashboard  (PIDs $($procs.Id -join ', '))" -ForegroundColor Green
}

function Stop-Plant([string]$name) {
    $pidFile = Join-Path $DataDir "$name.pids"
    if (-not (Test-Path $pidFile)) { Write-Host "  $name is not running." -ForegroundColor DarkGray; return }
    $stopped = 0
    foreach ($processId in Get-Content $pidFile) {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($proc) { Stop-Process -Id $processId -Force; $stopped++ }
    }
    Remove-Item $pidFile -Force
    Write-Host "  $name stopped ($stopped process(es))." -ForegroundColor Green
}

function Initialize-Plant([string]$name) {
    $p = $PLANTS[$name]
    Set-PlantEnv $name
    Write-Host "  $name : creating schema..." -NoNewline
    & $Mes init-db | Out-Null
    Write-Host " seeding..."
    # No 2>&1 here: in Windows PowerShell that wraps a native exe's stderr in
    # ErrorRecords and turns a clean exit 0 into a failure.
    & $Python (Join-Path $Root $p.Init) | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    if ($LASTEXITCODE -ne 0) { throw "$name seed failed (exit $LASTEXITCODE)" }

    # Accounts. `fsmes seed` (the demo plant) creates these, but `seed-kepsim` and
    # the machining seed do not — they build a plant, not a user list. Without
    # this the dashboard is unreachable and it looks like the plant failed.
    foreach ($u in @(
        @{ Code = 'SCOTT'; Name = 'Scott K';   Password = 'operator'; Role = 'operator' },
        @{ Code = 'ADMIN'; Name = 'Lab Admin'; Password = 'admin';    Role = 'admin' }
    )) {
        & $Mes add-user $u.Code $u.Name $u.Password --role $u.Role 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Host "    account $($u.Code) ($($u.Role))" -ForegroundColor DarkGray }
    }
}

function Show-Status([string]$name) {
    $p = $PLANTS[$name]
    $pidFile = Join-Path $DataDir "$name.pids"
    $running = $false
    if (Test-Path $pidFile) {
        $alive = @(Get-Content $pidFile | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        $running = $alive.Count -gt 0
    }
    $health = 'unreachable'
    if ($running) {
        try {
            $r = Invoke-WebRequest "http://127.0.0.1:$($p.ApiPort)/health" -UseBasicParsing -TimeoutSec 4
            if ($r.StatusCode -eq 200) { $health = 'healthy' }
        } catch { $health = 'not answering yet' }
    }
    $colour = if ($health -eq 'healthy') { 'Green' } elseif ($running) { 'Yellow' } else { 'DarkGray' }
    Write-Host ("  {0,-10} {1,-14} api :{2}  opc :{3}  {4}" -f $name, $health, $p.ApiPort, $p.OpcPort, $p.Label) -ForegroundColor $colour
}

Write-Host ""
Write-Host "Multi-plant MES lab -- $Action" -ForegroundColor Cyan

# Every MES_* path in the registry is relative to the repo root, because that is
# how MES-TWIN's own defaults are written. Child processes inherit this location,
# so resolving it here is what makes `config\tag_map_kepsim.json` mean anything.
Push-Location $Root
try {
    foreach ($name in Get-Targets) {
        switch ($Action) {
            'init'   { Initialize-Plant $name }
            'start'  { Start-Plant $name }
            'stop'   { Stop-Plant $name }
            'status' { Show-Status $name }
        }
    }
}
finally { Pop-Location }
if ($Action -eq 'start') {
    Write-Host ""
    Write-Host "  Sign in with SCOTT / operator  (or ADMIN / admin)" -ForegroundColor Cyan
    Write-Host "  Give the agent ~30s to book the first production, then check 'status'." -ForegroundColor DarkGray
}
Write-Host ""
