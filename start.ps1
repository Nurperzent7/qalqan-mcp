# Runs the Qalqan Platform MCP server and restarts it if it exits.
# Log: logs\server.log
$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Force -Path logs | Out-Null
$env:PYTHONIOENCODING = 'utf-8'

while ($true) {
    "$(Get-Date -Format s) starting server" | Out-File -Append -Encoding utf8 logs\server.log
    & .\.venv\Scripts\python.exe server.py *>> logs\server.log
    "$(Get-Date -Format s) server exited (code $LASTEXITCODE), restarting in 10 s" | Out-File -Append -Encoding utf8 logs\server.log
    Start-Sleep -Seconds 10
}
