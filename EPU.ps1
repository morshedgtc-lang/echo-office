# EPU launcher: starts the office hidden and opens the HUD.
# No console window ever appears. The second click opens the running office.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = if (Get-Command python -ErrorAction SilentlyContinue) { (Get-Command python).Source }
else { throw "Python 3.12 is not on PATH - install it from python.org" }

$port = 8790
$open = {
    Start-Process "$python" -ArgumentList "-c",
        "import webbrowser; webbrowser.open('http://127.0.0.1:$port/')"
}

# already running? just open the HUD, never start a twin
try {
    $null = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet `
        -WarningAction SilentlyContinue
} catch { }

function Test-Port([int]$p) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $p); $c.Close(); return $true
    } catch { return $false }
}

if (Test-Port $port) {
    & $open
    exit 0
}

Start-Process -FilePath $python -ArgumentList @('epu.py', 'serve') `
    -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 400
    if (Test-Port $port) { & $open; exit 0 }
}
Write-Warning "EPU did not answer on port $port within 12 seconds - run: python epu.py serve"
