# Runs the Qalqan Platform MCP server and the qalqan-hub Cloudflare tunnel,
# restarting either one if it exits.
# Logs: logs\server.log, logs\tunnel.log
$ErrorActionPreference = 'Continue'
# Windows PowerShell 5.1 redirects (*>>) as UTF-16 unless told otherwise.
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Force -Path logs | Out-Null
$env:PYTHONIOENCODING = 'utf-8'

# The tunnel token lives in .env as TUNNEL_TOKEN; cloudflared reads it from the environment.
$line = Get-Content -Encoding UTF8 .env | Where-Object { $_ -match '^TUNNEL_TOKEN=' } | Select-Object -First 1
$token = if ($line) { ($line -replace '^TUNNEL_TOKEN=', '').Trim("'`" ") } else { '' }
# Tolerate a pasted "cloudflared.exe service install <token>" command: keep only the token.
if ($token) { $token = ($token -split '\s+')[-1] }

if ($token) {
    $cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
    if (-not $cloudflared) { $cloudflared = 'C:\Program Files (x86)\cloudflared\cloudflared.exe' }
    Start-Job -Name tunnel -ArgumentList $cloudflared, $PSScriptRoot, $token -ScriptBlock {
        param($exe, $dir, $tok)
        $PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
        $env:TUNNEL_TOKEN = $tok
        while ($true) {
            "$(Get-Date -Format s) starting tunnel" | Out-File -Append -Encoding utf8 "$dir\logs\tunnel.log"
            & $exe tunnel --no-autoupdate --config "$dir\cloudflared.yml" run *>> "$dir\logs\tunnel.log"
            Start-Sleep -Seconds 10
        }
    } | Out-Null
} else {
    "$(Get-Date -Format s) TUNNEL_TOKEN is empty in .env, tunnel not started" | Out-File -Append -Encoding utf8 logs\tunnel.log
}

while ($true) {
    "$(Get-Date -Format s) starting server" | Out-File -Append -Encoding utf8 logs\server.log
    & .\.venv\Scripts\python.exe server.py *>> logs\server.log
    "$(Get-Date -Format s) server exited (code $LASTEXITCODE), restarting in 10 s" | Out-File -Append -Encoding utf8 logs\server.log
    Start-Sleep -Seconds 10
}
