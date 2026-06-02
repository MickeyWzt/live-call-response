$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Write-Host "Starting MFP Live Studio at http://127.0.0.1:8000"
Write-Host "Keep this window open while using the interface."

python -u "code/interface_backend.py" --host "127.0.0.1" --port 8000
