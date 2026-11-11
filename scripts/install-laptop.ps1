# EPU installer for the owner's laptop — one file, double-click.
#
# What it does, in order:
#   1. finds Python 3.10+ on PATH (prints the download link when it is missing)
#   2. installs the packages EPU needs (numpy, OpenCV, faster-whisper, pytest, psutil)
#   3. creates the office folders and seeds the owner's identity facts
#   4. starts the office on https://0.0.0.0:8790 so the laptop's camera and mic work
#   5. opens https://127.0.0.1:8790/ and prints the phone/laptop-friendly instructions
#
# Nothing here asks the owner a question or opens a console window.
# Usage: right-click -> Run with PowerShell (or double-click EPU-LAPTOP.cmd)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$Root = $PSScriptRoot

function Step($name, [scriptblock]$block) {
    Write-Host ""
    Write-Host "== $name" -ForegroundColor Cyan
    try { & $block } catch {
        Write-Host "   FAILED: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "   Fix it and run this file again, sir. Nothing is half-done." -ForegroundColor Red
        exit 1
    }
}

Step "Python" {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        Write-Host "   Python is not on PATH. Install it from https://www.python.org/downloads/"
        Write-Host "   IMPORTANT: tick 'Add python.exe to PATH' on the first screen."
        Start-Process "https://www.python.org/downloads/"
        exit 1
    }
    python --version
}

Step "Packages EPU needs" {
    # 8 GB laptop CPU: numpy (voice maths), OpenCV (face), faster-whisper (speech),
    # psutil (EPU's own cost), pytest (the tests).
    python -m pip install --quiet --upgrade numpy opencv-contrib-python faster-whisper psutil pytest
    python -c "import numpy, cv2, faster_whisper, psutil; print('   all packages in place')"
}

Step "The face model" {
    New-Item -ItemType Directory -Force -Path "$Root\models\face" | Out-Null
    $cascade = "$Root\models\face\haarcascade_frontalface_default.xml"
    if (-not (Test-Path $cascade)) {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/$([IO.Path]::GetFileName($cascade))" `
            -OutFile $cascade -UseBasicParsing
    }
    "   face model ready: $((Get-Item $cascade).Length) bytes"
}

Step "Folders and the owner's identity" {
    python "$Root\epu.py" init
}

Step "The certificate for camera and mic" {
    python -c "import sys; sys.path.insert(0, r'$Root'); from epu import tlsutil; print('   certificate:', tlsutil.ensure_certificate())"
    Write-Host "   The laptop browser shows ONE warning on first open: Advanced -> Proceed." -ForegroundColor Yellow
}

Step "Starting EPU" {
    $env:EPU_BIND = '0.0.0.0'
    $env:EPU_TLS = '1'
    Start-Process -FilePath python -ArgumentList "$Root\epu.py", 'serve' `
        -WorkingDirectory $Root -WindowStyle Hidden
    Start-Sleep -Seconds 6
    $ok = $false
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "https://127.0.0.1:8790/api/status" -SkipCertificateCheck -TimeoutSec 5 -UseBasicParsing
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $ok) { throw "EPU did not answer on https://127.0.0.1:8790 within 20 s" }
    "   EPU is online."
}

Write-Host ""
Write-Host "EPU IS READY, SIR" -ForegroundColor Green
Write-Host "  On this laptop : https://127.0.0.1:8790/         (camera + mic work)"
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress
if ($ip) { Write-Host "  On your phone  : https://${ip}:8790/        (same WiFi, passphrase needed)" }
Write-Host "  First open: Advanced -> Proceed (one time), then RECORD / FACE / VOICE all work."
Start-Process "https://127.0.0.1:8790/"
