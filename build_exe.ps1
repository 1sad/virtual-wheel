# Build the standalone one-file Virtual Wheel executable.
# The vJoy driver is an external prerequisite and is not bundled.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = $PSScriptRoot
Set-Location $root

$arch = python -c "import struct; print(struct.calcsize('P') * 8)"
if ($arch -ne "64") {
    throw "A 64-bit Python is required to build the 64-bit Virtual Wheel executable."
}

Write-Host "Installing/updating build dependencies..."
python -m pip install --upgrade -r "$root\requirements.txt"

Write-Host "Building dist\VirtualWheel.exe ..."
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --noupx `
    --name "VirtualWheel" `
    --icon "$root\assets\virtual_wheel.ico" `
    --hidden-import "XInput" `
    --hidden-import "pystray._win32" `
    --hidden-import "PIL.Image" `
    --hidden-import "PIL.ImageDraw" `
    --hidden-import "PIL.ImageTk" `
    --distpath "$root\dist" `
    --workpath "$root\build" `
    --specpath "$root" `
    "$root\virtual_wheel_vjoy.py"

$exe = Join-Path $root "dist\VirtualWheel.exe"
if (-not (Test-Path $exe)) {
    throw "PyInstaller did not produce $exe"
}

$checker = Join-Path (Split-Path $root -Parent) "repo-guard\check-release.ps1"
if (Test-Path $checker) {
    Write-Host ""
    Write-Host "Scanning dist for leaked personal data..."
    & $checker -Path "$root\dist"
    if ($LASTEXITCODE -ne 0) {
        throw "Artifact scan failed. Do not publish $exe until the findings are fixed."
    }
} else {
    Write-Warning "repo-guard check-release.ps1 not found, skipping artifact scan."
}

$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built: $exe ($size MB)"
Write-Host "Requires the BrunnerInnovation vJoy driver at C:\Program Files\vJoy\x64\vJoyInterface.dll"
Write-Host "Settings are stored beside the EXE (fallback: %APPDATA%\VirtualWheel if read-only)"
