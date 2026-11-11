# EPU launcher - the one PowerShell helper for the office.
#
#   scripts\epu-start.ps1              start the office hidden, then open the HUD
#   scripts\epu-start.ps1 -Lan         also open it to the owner's phone on this WiFi (https)
#   scripts\epu-start.ps1 -Firewall    open the Windows port for that (asks for admin once)
#
# One office only: when port 8790 already answers, this just opens the HUD and never
# starts a twin. No console window is left behind.
#
# EPU.cmd is the double-click version of the first line.

param([switch]$Lan, [switch]$Firewall)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot          # scripts\ -> the office root
Set-Location -LiteralPath $root

$python = if (Get-Command python -ErrorAction SilentlyContinue) { (Get-Command python).Source }
else { throw "Python 3.12 is not on PATH - install it from python.org" }

$port = 8790
$scheme = if ($Lan) { 'https' } else { 'http' }

function Test-Port([int]$p) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $p); $c.Close(); return $true
    } catch { return $false }
}

function Open-Hud([string]$url) {
    # A second python process opens the browser: no console window flashes up.
    Start-Process "$python" -ArgumentList "-c",
        "import webbrowser; webbrowser.open('$url')"
}

function Show-LanUrl {
    # The phone address, printed only when the office is open to the LAN.
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
           Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
           Select-Object -First 1).IPAddress
    if ($ip) {
        Write-Host "On your phone (same WiFi): ${scheme}://${ip}:${port}/  " `
            "- accept the certificate once (Advanced -> Proceed)."
    }
}

# ---------------------------------------------------------------- the firewall rule
if ($Firewall) {
    # One click, one admin prompt (that is Windows, not EPU). Private profile only:
    # the office is never opened to a public network.
    Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile', '-Command',
        "New-NetFirewallRule -DisplayName 'EPU office 8790' -Direction Inbound " +
        "-Protocol TCP -LocalPort 8790 -Action Allow -Profile Private | Out-Null; " +
        "Write-Host 'EPU port 8790 is open on your home network (private profile only).'"
    return
}

# ---------------------------------------------------------------- start + open
if (Test-Port $port) {
    Open-Hud "${scheme}://127.0.0.1:${port}/"
    if ($Lan) { Show-LanUrl }
    Write-Host "EPU is already running, sir. The HUD is open."
    exit 0
}

$serveArgs = @('epu.py', 'serve')
if ($Lan) { $serveArgs += @('--lan', '--tls') }
Start-Process -FilePath $python -ArgumentList $serveArgs -WorkingDirectory $root -WindowStyle Hidden

for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 400
    if (Test-Port $port) {
        Open-Hud "${scheme}://127.0.0.1:${port}/"
        if ($Lan) { Show-LanUrl }
        exit 0
    }
}
Write-Warning "EPU did not answer on port $port within 12 seconds - run: python epu.py serve"
