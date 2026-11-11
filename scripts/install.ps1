# EPU one-line installer — reads the hardware, installs what this machine can run.
#
#   irm https://raw.githubusercontent.com/morshedgtc-lang/echo-office/main/scripts/install.ps1 | iex
#
# What it decides by itself (the owner never chooses a model size):
#   RAM      < 6 GB  -> lite profile: no local model, "tiny" speech model
#           6-16 GB  -> standard: "base" speech model, no local model
#           > 16 GB  -> full: "small" speech model, local model available (off by default)
#   CPU      2 cores  -> fewer workers, smaller speech model
#          4+ cores  -> normal
#   GPU      present  -> face + speech may use it (documented, still optional)
#   Python   missing  -> opens the download page and stops
#
# It installs: numpy (voice maths), OpenCV (face), faster-whisper (speech),
# psutil (EPU's own cost), pytest (the tests), cryptography (the certificate).
# Then: folders, identity facts, the certificate, and it starts the office.

$ErrorActionPreference = 'Stop'

# ------------------------------------------------------------------ hardware
$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$cores = (Get-CimInstance Win32_Processor).NumberOfLogicalProcessors
$cpuName = (Get-CimInstance Win32_Processor).Name
$win = (Get-CimInstance Win32_OperatingSystem).Caption
Write-Host ""
Write-Host "EPU installer - reading this machine" -ForegroundColor Cyan
Write-Host "  Windows : $win"
Write-Host "  CPU     : $cpuName ($cores logical cores)"
Write-Host "  RAM     : $ramGB GB"

$profile = 'standard'; $sttModel = 'base'; $workers = 2
if ($ramGB -lt 6) { $profile = 'lite'; $sttModel = 'tiny'; $workers = 1 }
elseif ($ramGB -gt 16 -and $cores -ge 8) { $profile = 'full'; $sttModel = 'small'; $workers = 4 }
elseif ($cores -le 2) { $profile = 'lite'; $sttModel = 'tiny'; $workers = 1 }
Write-Host "  Chosen : profile=$profile  speech=$sttModel  workers=$workers" -ForegroundColor Green
Write-Host "  Local model stays OFF: the owner's PC must stay fast." -ForegroundColor DarkGray

# ------------------------------------------------------------------ python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Python is not on PATH. Install it (tick 'Add python.exe to PATH') and run again."
    Start-Process "https://www.python.org/downloads/"
    exit 1
}
python --version

# ------------------------------------------------------------------ where the office lives
$root = if ($env:EPU_ROOT) { $env:EPU_ROOT } else { "$HOME\Desktop\EPU" }
New-Item -ItemType Directory -Force -Path $root | Out-Null
Set-Location -LiteralPath $root

# ------------------------------------------------------------------ the code
if (-not (Test-Path "$root\epu\config.py")) {
    $zip = "https://github.com/morshedgtc-lang/echo-office/archive/refs/heads/main.zip"
    Write-Host "Downloading the office from GitHub..."
    Invoke-WebRequest -Uri $zip -OutFile "$root\main.zip" -UseBasicParsing
    Expand-Archive -Path "$root\main.zip" -DestinationPath "$root\src" -Force
    $inner = Get-ChildItem "$root\src" -Directory | Select-Object -First 1
    Copy-Item -Path "$($inner.FullName)\*" -Destination $root -Recurse -Force
    Remove-Item "$root\src" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "$root\main.zip" -ErrorAction SilentlyContinue
    Write-Host "  office files in place at $root"
} else {
    Write-Host "Office already here - updating from GitHub"
    if (Test-Path "$root\.git") {
        git -C $root pull --ff-only 2>$null
    } else {
        $zip = "https://github.com/morshedgtc-lang/echo-office/archive/refs/heads/main.zip"
        Invoke-WebRequest -Uri $zip -OutFile "$root\main.zip" -UseBasicParsing
        Expand-Archive -Path "$root\main.zip" -DestinationPath "$root\src" -Force
        $inner = Get-ChildItem "$root\src" -Directory | Select-Object -First 1
        Copy-Item -Path "$($inner.FullName)\*" -Destination $root -Recurse -Force
        Remove-Item "$root\src" -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item "$root\main.zip" -ErrorAction SilentlyContinue
    }
}

# ------------------------------------------------------------------ the packages this machine needs
python -m pip install --quiet --upgrade numpy opencv-contrib-python faster-whisper psutil pytest cryptography
python -c "import numpy, cv2, faster_whisper, psutil, cryptography; print('  all packages in place')"

# ------------------------------------------------------------------ the chosen profile
$env:EPU_STT_MODEL = $sttModel
"EPU_STT_MODEL=$sttModel" | Out-File -FilePath "$root\secure\stt.env" -Encoding ascii -Append
"EPU_PROFILE=$profile" | Out-File -FilePath "$root\secure\stt.env" -Encoding ascii -Append

# ------------------------------------------------------------------ the face model (one file, local)
$cascade = "$root\models\face\haarcascade_frontalface_default.xml"
if (-not (Test-Path $cascade)) {
    New-Item -ItemType Directory -Force -Path "$root\models\face" | Out-Null
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascases/haarcascade_frontalface_default.xml" `
        -OutFile $cascade -UseBasicParsing
    if (-not (Test-Path $cascade)) {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/haarcascade_frontalface_default.xml" `
            -OutFile $cascade -UseBasicParsing
    }
}
"  face model: $((Get-Item $cascade -ErrorAction SilentlyContinue).Length) bytes"

# ------------------------------------------------------------------ the office itself
python "$root\epu.py" init
python -c "import sys; sys.path.insert(0, r'$root'); from epu import tlsutil; tlsutil.ensure_certificate()"

# ------------------------------------------------------------------ start it (https so camera and mic work)
$env:EPU_BIND = '0.0.0.0'
$env:EPU_TLS = '1'
Start-Process -FilePath python -ArgumentList "$root\epu.py", 'serve' -WorkingDirectory $root -WindowStyle Hidden
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 700
    try {
        $r = Invoke-WebRequest -Uri "https://127.0.0.1:8790/api/status" -SkipCertificateCheck -TimeoutSec 5 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}
if (-not $ok) { Write-Host "EPU did not answer - run: python epu.py serve" -ForegroundColor Red; exit 1 }

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "EPU IS ONLINE, SIR" -ForegroundColor Green
Write-Host "  On this machine : https://127.0.0.1:8790/   (camera + mic work)"
if ($ip) { Write-Host "  From your phone : https://${ip}:8790/      (same WiFi, passphrase needed)" }
Write-Host "  First open: Advanced -> Proceed (once). Then RECORD, FACE and VOICE are ready."
Write-Host "  Household voice, sirs own name, and your orders - all offline on this machine."
Start-Process "https://127.0.0.1:8790/"
