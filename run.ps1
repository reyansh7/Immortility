Write-Host "Activating virtual environment..." -ForegroundColor Cyan
& .\venv\Scripts\Activate.ps1
Write-Host "Running Immortility..." -ForegroundColor Green
python main.py
