# Lance l'application Devis BTP sur http://localhost:5000
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Creation de l'environnement virtuel..." -ForegroundColor Cyan
    py -3 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

Write-Host "Demarrage sur http://localhost:5000 (Ctrl+C pour arreter)" -ForegroundColor Green
.\.venv\Scripts\python.exe app.py
