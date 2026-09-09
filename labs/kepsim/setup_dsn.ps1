# Create the 32-bit System DSN that Kepware's Advanced Simulator reads the CSVs through.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File setup_dsn.ps1
#
# Must run ELEVATED (System DSNs are machine-wide).
#
# Why 32-bit + System, not the defaults:
#   - the KEPServerEX runtime is a 32-bit process and cannot see 64-bit DSNs
#   - it runs as a Windows service, so it cannot see per-user DSNs
# Getting either wrong produces "data source not found" with no other clue.

$ErrorActionPreference = 'Stop'
$dsnName = 'KepSimCSV'
$dataDir = Join-Path $PSScriptRoot 'out'

if (-not (Test-Path (Join-Path $dataDir 'LD.csv'))) {
    throw "No CSVs in $dataDir - run generate_line.py first."
}

# Driver name is localized (this machine also carries pt-BR/de-DE variants), so
# detect rather than hardcode; prefer the plain English name when present.
$textDrivers = @(Get-OdbcDriver -Platform 32-bit | Where-Object { $_.Name -match '\*\.txt' })
if ($textDrivers.Count -eq 0) { throw "No 32-bit text ODBC driver found." }
$driver = ($textDrivers | Where-Object { $_.Name -eq 'Microsoft Text Driver (*.txt; *.csv)' } |
           Select-Object -First 1)
if (-not $driver) { $driver = $textDrivers[0] }
Write-Output "Using driver: $($driver.Name)"

$existing = Get-OdbcDsn -Name $dsnName -Platform 32-bit -ErrorAction SilentlyContinue
if ($existing) {
    Write-Output "DSN '$dsnName' already exists - removing so settings are re-applied cleanly."
    Remove-OdbcDsn -Name $dsnName -DsnType 'System' -Platform 32-bit
}

Add-OdbcDsn -Name $dsnName -DriverName $driver.Name -DsnType 'System' -Platform 32-bit `
            -SetPropertyValue @("DefaultDir=$dataDir", "Extensions=asc,csv,tab,txt")

$check = Get-OdbcDsn -Name $dsnName -Platform 32-bit
Write-Output "Created System DSN '$($check.Name)' (32-bit) -> $dataDir"
Write-Output "Tables it exposes: $((Get-ChildItem $dataDir -Filter *.csv | ForEach-Object { $_.BaseName }) -join ', ')"
