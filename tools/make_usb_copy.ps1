<#
    Build a USB copy of the application, open it, and start it.

        .\tools\make_usb_copy.ps1

    Run this from anywhere inside a clone of the repository. It puts a folder
    named "WaveMaker_F25_new" on your Desktop containing only the files needed
    to RUN the application -- no tests, no git history, no development tooling
    -- opens it in Explorer so you can drag it to a USB stick, and launches the
    mock so you can see it working.

    About 560 KB and 58 files, so there is no need to zip anything.

    On the lab PC, drop the folder on the Desktop next to "WaveMaker Programs"
    (that is how the Open Studio 5000 button finds the .ACD project) and
    double-click "Open Wavemaker.cmd".

    -NoLaunch   build and open the folder, but do not start the application.
#>

param([switch]$NoLaunch)

$ErrorActionPreference = "Stop"

# --- find the repository root, from wherever this was run -------------------
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "main.py"))) {
    Write-Host "Could not find main.py. Run this from inside the repository." -ForegroundColor Red
    exit 1
}

$dest = Join-Path ([Environment]::GetFolderPath('Desktop')) "WaveMaker_F25_new"
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Path $dest | Out-Null

# --- only what the application needs in order to run ------------------------
$files = @(
    "main.py", "Model.py", "Motor.py", "View.py", "style.py",
    "Open Wavemaker.cmd", "Mock Wavemaker (no machine).cmd"
)
$folders = @("app", "modules", "operate", "preset_options", "feedback", "Presets")
$docs    = @("check_python.py", "LAB_TEST.md", "OPERATING.md")

foreach ($f in $files) {
    Copy-Item (Join-Path $root $f) -Destination $dest
}
foreach ($d in $folders) {
    Copy-Item (Join-Path $root $d) -Destination $dest -Recurse
}
New-Item -ItemType Directory -Path (Join-Path $dest "docs") | Out-Null
foreach ($d in $docs) {
    Copy-Item (Join-Path $root "docs\$d") -Destination (Join-Path $dest "docs")
}

# Compiled bytecode from the clone is not wanted on the lab machine.
Get-ChildItem $dest -Recurse -Force -Directory |
    Where-Object { $_.Name -eq "__pycache__" } |
    Remove-Item -Recurse -Force

$count = (Get-ChildItem $dest -Recurse -File).Count
$size  = [math]::Round(((Get-ChildItem $dest -Recurse -File | Measure-Object Length -Sum).Sum / 1KB), 0)

Write-Host ""
Write-Host "Built: $dest" -ForegroundColor Green
Write-Host "       $count files, $size KB - drag the folder straight onto the USB stick."
Write-Host ""

Start-Process explorer.exe -ArgumentList "`"$dest`""

if (-not $NoLaunch) {
    Write-Host "Starting the mock so you can see it (nothing physical will move)..."
    Start-Process -FilePath (Join-Path $dest "Mock Wavemaker (no machine).cmd") `
                  -WorkingDirectory $dest
}
