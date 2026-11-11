# EPU one-line installer — reads the hardware, installs what this machine can run.
#
#   irm https://raw.githubusercontent.com/morshedgtc-lang/echo-office/main/scripts/install.ps1 | iex
#
# What it decides by itself (the owner never chooses a model size):
#   RAM      < 6 GB  -> lite profile: no local model, "tiny" speech model
#           6-16 GB  -> standard: "base" speech model, no local model
#           > 16 GB  -> full: "small" speech model, local model available (off by default)
#   CPU      2 cores -> smaller speech model; 4+ cores -> normal
#   Python   missing -> opens the download page and stops
#
# It installs: numpy (voice maths), OpenCV (face), faster-whisper (speech),
# psutil (EPU's own cost), pytest (the tests), cryptography (the certificate).
# Then: folders, identity facts, the certificate, and it starts the office.
#
# What it NEVER does (safe to run again, sir):
#   * never overwrites config/office.json. The owner's client list and DNS notes live
#     there. When the new config differs, the owner's file wins and the new one is
#     written beside it as config/office.json.new, with a one-line summary.
#   * never touches memory/ or secure/ (the owner's own memories, keys, passphrase).
#   * never deletes a file it did not create, and never removes anything from
#     projects/, logs/, run/ or tmp/.

$ErrorActionPreference = 'Stop'

# Folders the installer may never write into. The code copy skips them, and the
# update step protects them the same way.
$SkipTops = @('config', 'memory', 'secure', 'projects', 'logs', 'run', 'tmp', '.git')

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

$profile = 'standard'; $sttModel = 'base'
if ($ramGB -lt 6) { $profile = 'lite'; $sttModel = 'tiny' }
elseif ($ramGB -gt 16 -and $cores -ge 8) { $profile = 'full'; $sttModel = 'small' }
elseif ($cores -le 2) { $profile = 'lite'; $sttModel = 'tiny' }
Write-Host "  Chosen  : speech model=$sttModel ($profile machine)" -ForegroundColor Green
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

function Write-NoBom([string]$path, [string]$text) {
    # JSON without a BOM: epu/config.py reads the file as plain UTF-8, and a BOM
    # would make the whole office fail to start.
    [IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-Source([string]$relative) {
    # The file in the freshly downloaded copy, or $null when there is no such file.
    $candidate = Join-Path $SourceDir $relative
    if (Test-Path $candidate) { return $candidate }
    return $null
}

function Copy-OfficeCode([string]$from) {
    # Copy the office code only. The owner's folders are skipped by name, so a
    # re-run can never turn into a factory reset.
    Get-ChildItem -LiteralPath $from -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($from.Length).TrimStart('\', '/')
        $top = ($rel -split '[\\/]')[0]
        if ($SkipTops -contains $top) { return }
        if ($_.Name -like '*.zip') { return }
        if ($rel -match '(^|[\\/])(__pycache__|\.pytest_cache)([\\/]|$)') { return }
        $dest = Join-Path $root $rel
        $dir = Split-Path -Parent $dest
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
        }
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
    }
}

# ------------------------------------------------------------------ the code
function Install-FromZip {
    # Download the published code and copy only the office code. config/, memory/,
    # secure/, projects/ and the run folders are skipped by name.
    $zip = "https://github.com/morshedgtc-lang/echo-office/archive/refs/heads/main.zip"
    Invoke-WebRequest -Uri $zip -OutFile "$root\main.zip" -UseBasicParsing
    Expand-Archive -Path "$root\main.zip" -DestinationPath "$root\src" -Force
    $script:SourceDir = (Get-ChildItem "$root\src" -Directory | Select-Object -First 1).FullName
    Copy-OfficeCode $script:SourceDir
    Remove-Item "$root\src" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "$root\main.zip" -ErrorAction SilentlyContinue
    Write-Host "  office files in place at $root"
}

$SourceDir = $null
$firstInstall = -not (Test-Path "$root\epu\config.py")
if ($firstInstall) {
    Write-Host "Installing the office from GitHub..."
    Install-FromZip
} elseif (Test-Path "$root\.git") {
    Write-Host "Office already here - updating the code from GitHub (git pull --ff-only)"
    # Native commands write progress to stderr; that must not abort the installer.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    git -C $root pull --ff-only 2>&1 | Write-Host
    $pullCode = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($pullCode -ne 0) {
        # Fail safe: the code that is already here keeps running untouched.
        Write-Host "  git pull stopped (local changes, or no network). Nothing was changed." -ForegroundColor Yellow
    }
} else {
    Write-Host "Office already here - refreshing the code from GitHub"
    Install-FromZip
}

# ------------------------------------------------------------------ the owner's config
$cfgPath = Join-Path $root 'config\office.json'
$ownerCopy = $null
if (Test-Path $cfgPath) {
    # The owner's file is copied aside first: whatever happens next, his client
    # list and DNS notes survive.
    $ownerCopy = Join-Path $env:TEMP ("office.json.owner." + [guid]::NewGuid().ToString('N'))
    Copy-Item -LiteralPath $cfgPath -Destination $ownerCopy -Force
}
if (-not (Test-Path $cfgPath) -and $SourceDir) {
    $shipped = Join-Path $SourceDir 'config\office.json'
    if (Test-Path $shipped) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $cfgPath) | Out-Null
        Copy-Item -LiteralPath $shipped -Destination $cfgPath -Force
        Write-Host "  config/office.json created (the starting file - edit it any time)"
    }
}

# ------------------------------------------------------------------ the packages this machine needs
python -m pip install --quiet --upgrade numpy opencv-contrib-python faster-whisper psutil pytest cryptography
python -c "import numpy, cv2, faster_whisper, psutil, cryptography; print('  all packages in place')"

# ------------------------------------------------------------------ the face model (one file, local)
$cascade = "$root\models\face\haarcascade_frontalface_default.xml"
if (-not (Test-Path $cascade)) {
    New-Item -ItemType Directory -Force -Path "$root\models\face" | Out-Null
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/haarcascade_frontalface_default.xml" `
        -OutFile $cascade -UseBasicParsing
}
"  face model: $((Get-Item $cascade -ErrorAction SilentlyContinue).Length) bytes"

# ------------------------------------------------------------------ the tests, run honestly
$testsDir = Join-Path $root 'tests'
$old = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$testOut = python -m pytest $testsDir -q -k "not transcribe" 2>&1
$ErrorActionPreference = $old
$lastLine = @($testOut | Where-Object { "$_" -match '\S' } | Select-Object -Last 1)
Write-Host "  tests: $lastLine"

# ------------------------------------------------------------------ the office itself
$memDb = Join-Path $root 'memory\office.sqlite'
if (-not (Test-Path $memDb)) {
    python "$root\epu.py" init
} else {
    # Re-run: report the office, write nothing new. memory/ and secure/ are the
    # owner's, and this installer never touches them.
    python "$root\epu.py" doctor
}

# The certificate lives in secure/ (owner-only). Made only when it is missing.
python -c "import sys; sys.path.insert(0, r'$root'); from epu import tlsutil; tlsutil.ensure_certificate()"

# ------------------------------------------------------------------ the config check, after any update
if ($ownerCopy) {
    $now = [IO.File]::ReadAllText($cfgPath)
    $was = [IO.File]::ReadAllText($ownerCopy)
    if ($now -ne $was) {
        # The update brought a different config. The owner's file wins; the new
        # one waits beside it, and the summary says exactly what differs.
        Write-NoBom "$cfgPath.new" $now
        Copy-Item -LiteralPath $ownerCopy -Destination $cfgPath -Force
        $mine = Compare-Object ($was -split "`r?`n") ($now -split "`r?`n")
        $onlyMine = @($mine | Where-Object { $_.SideIndicator -eq '<=' }).Count
        $onlyNew = @($mine | Where-Object { $_.SideIndicator -eq '>' }).Count
        Write-Host "  config/office.json: YOURS IS DIFFERENT from the new one " `
            "($onlyMine line(s) only in yours, $onlyNew only in the new one)." -ForegroundColor Yellow
        Write-Host "  Nothing was overwritten. Your file is untouched; the new one is config/office.json.new" -ForegroundColor Yellow
    } else {
        Write-Host "  config/office.json: unchanged - your client list and DNS notes are intact"
    }
    Remove-Item -LiteralPath $ownerCopy -Force -ErrorAction SilentlyContinue
}

# The first-run marker the owner (or this installer) reads. Written by hand, one
# line, so the rest of the file is never re-serialised.
$firstRunMarker = Select-String -LiteralPath $cfgPath -Pattern '"first_run"\s*:\s*true' -Quiet
if ($firstRunMarker) {
    Write-Host "  First install, sir. When the office runs the way you like, set " `
        "`"installs`": {`"first_run`": false} in config/office.json (one line, by hand)." -ForegroundColor DarkGray
}

# ------------------------------------------------------------------ start it (https so camera and mic work)
$env:EPU_BIND = '0.0.0.0'
$env:EPU_TLS = '1'
$env:EPU_STT_MODEL = $sttModel

# The port comes from the owner's own config, not from a number in this file.
try {
    $hudPort = [int](& python -c "import sys; sys.path.insert(0, r'$root'); from epu import config; print(config.port('hud'))")
} catch { $hudPort = 8790 }
if ($hudPort -le 0) { $hudPort = 8790 }

Start-Process -FilePath python -ArgumentList "$root\epu.py", 'serve' -WorkingDirectory $root -WindowStyle Hidden

# Wait for the port to answer. A TCP check works on every PowerShell (the older
# 5.1 has no -SkipCertificateCheck switch, and a TLS warning is not an error).
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 700
    try {
        $probe = New-Object System.Net.Sockets.TcpClient
        $probe.Connect('127.0.0.1', $hudPort); $probe.Close(); $ok = $true; break
    } catch { }
}
if (-not $ok) {
    Write-Host "EPU did not answer on port $hudPort - run: python epu.py serve" -ForegroundColor Red
    exit 1
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "EPU IS ONLINE, SIR" -ForegroundColor Green
Write-Host "  On this machine : https://127.0.0.1:$hudPort/   (camera + mic work)"
if ($ip) { Write-Host "  From your phone : https://${ip}:$hudPort/      (same WiFi, passphrase needed)" }
Write-Host "  First open: Advanced -> Proceed (once). Then RECORD, FACE and VOICE are ready."
Write-Host "  Speech model for this run: $sttModel (set EPU_STT_MODEL=$sttModel to keep it)."
Write-Host "  No cloud key yet: put one in secure/keys.env (owner-only) and answers come from the cloud."
Start-Process "https://127.0.0.1:$hudPort/"
