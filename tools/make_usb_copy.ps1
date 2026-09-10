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
    -To <path>  also sync the build onto an already-prepared copy, e.g. a USB
                stick at D:\WaveMaker_F25_new. Copies and overwrites, but by
                default deletes nothing.
    -Purge      with -To, also delete stale CODE left over from an older
                version. Logs, presets, notes and any other non-code file on
                the destination are always kept, with or without this flag.

#>

param(
    [switch]$NoLaunch,
    [string]$To,
    [switch]$Purge
)

# Directories the application writes into as it runs. A log from a real trial
# at the machine cannot be reproduced, so a sync must never delete these.
$KeepDirs = @("logs", "analytics", "__pycache__")

# Extensions -Purge is allowed to delete. Code only: a stale module can break
# the application, whereas a stray .txt or .csv is far more likely to be data
# somebody wants to keep.
$PurgeExtensions = @(".py", ".pyc", ".cmd")

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
$folders = @("app", "modules", "operate", "preset_options", "feedback", "wave", "Presets")
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

# --- optionally sync onto an existing copy (USB stick, lab PC) --------------
if ($To) {
    if (-not (Test-Path $To)) {
        Write-Host "Destination not found: $To" -ForegroundColor Red
        exit 1
    }
    $To = (Resolve-Path $To).Path.TrimEnd([char]92)
    Write-Host "Syncing to $To ..." -ForegroundColor Cyan

    # Copy and overwrite. This never deletes anything.
    robocopy $dest $To /E /XD $KeepDirs /NFL /NDL /NJH /NP /R:2 /W:2 | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Write-Host "Sync FAILED (robocopy $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }

    # Whatever is on the destination that the build does not contain.
    $extras = @()
    foreach ($item in (Get-ChildItem $To -Recurse -File -Force)) {
        $rel  = $item.FullName.Substring($To.Length).TrimStart([char]92)
        $segs = $rel.Split([char]92)   # 92 = backslash
        $skip = $false
        foreach ($s in $segs) { if ($KeepDirs -contains $s) { $skip = $true } }
        if ($skip) { continue }
        if (-not (Test-Path (Join-Path $dest $rel))) { $extras += $item }
    }

    # Only code can go stale in a way that breaks the application. Anything
    # else on the stick is the operator's -- a trial log, a preset they wrote --
    # and is never deleted, whatever the flags say.
    $stale = @($extras | Where-Object { $PurgeExtensions -contains $_.Extension.ToLower() })
    $data  = @($extras | Where-Object { $PurgeExtensions -notcontains $_.Extension.ToLower() })

    if ($data.Count) {
        Write-Host ""
        Write-Host "Keeping (not from the build, so assumed to be yours):" -ForegroundColor Cyan
        $data | ForEach-Object { Write-Host ("  " + $_.FullName.Substring($To.Length).TrimStart([char]92)) }
    }
    if ($stale.Count) {
        Write-Host ""
        if ($Purge) {
            Write-Host "Deleting stale code no longer in the build:" -ForegroundColor Yellow
            foreach ($f in $stale) {
                Write-Host ("  " + $f.FullName.Substring($To.Length).TrimStart([char]92))
                Remove-Item $f.FullName -Force
            }
        } else {
            Write-Host "Stale code present; -Purge would delete it:" -ForegroundColor Yellow
            $stale | ForEach-Object { Write-Host ("  " + $_.FullName.Substring($To.Length).TrimStart([char]92)) }
        }
    }

    # Bytecode from this machine is the wrong Python for the lab PC.
    Get-ChildItem $To -Recurse -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "__pycache__" } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "Synced to $To" -ForegroundColor Green
    Write-Host ""
}

Start-Process explorer.exe -ArgumentList "`"$dest`""

if (-not $NoLaunch) {
    Write-Host "Starting the mock so you can see it (nothing physical will move)..."
    Start-Process -FilePath (Join-Path $dest "Mock Wavemaker (no machine).cmd") `
                  -WorkingDirectory $dest
}

# robocopy leaves a non-zero code behind even when it succeeded (1 = copied,
# 2 = extras, 3 = both). Do not let that look like a failure to the caller.
exit 0
