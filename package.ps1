# Package File Picker into ONE Windows program: dist\FilePicker.exe
# (GUI + Qt + the Go engine inside; the target PC needs nothing installed).
#
# Easiest: double-click package.cmd.
# Or:      powershell -NoProfile -ExecutionPolicy Bypass -File .\package.ps1 [-OneFolder]
#
#   -OneFolder   build dist\FilePicker\FilePicker.exe + files instead of a single
#                .exe. Starts faster (nothing to unpack) - zip the folder to share it.
#
# Needs: Go 1.22+ and Python 3.9+ (from python.org). Everything else is
# installed into a private .venv-build folder, not into your Python.
# Keep this file plain ASCII (Windows PowerShell 5.1 misreads UTF-8 without BOM).

param([switch]$OneFolder)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$gui  = Join-Path $root "gui"

function Fail($message) {
    Write-Host ""
    Write-Host "PACKAGING FAILED: $message" -ForegroundColor Red
    exit 1
}
function Step($n, $text) { Write-Host ""; Write-Host "[$n/5] $text" -ForegroundColor Cyan }

# 1. Engine -------------------------------------------------------------------
Step 1 "Building the engine"
& (Join-Path $root "build.ps1")
if ($LASTEXITCODE -ne 0) { Fail "the engine build failed (see above)." }
$engineExe = Join-Path $gui "bin\picker.exe"

# 2. Python -------------------------------------------------------------------
Step 2 "Finding Python"
$python = $null
foreach ($candidate in @(@("py", "-3"), @("python"))) {
    if (Get-Command $candidate[0] -ErrorAction SilentlyContinue) {
        $exeArgs = @($candidate | Select-Object -Skip 1)
        $v = & $candidate[0] @exeArgs -c "import sys; print('%d.%d' % sys.version_info[:2])"
        if ($LASTEXITCODE -eq 0 -and $v) { $python = $candidate; break }
    }
}
if (-not $python) {
    Fail ("Python was not found.`n" +
          "  Install Python 3.9+ from https://www.python.org/downloads/ and tick`n" +
          "  'Add python.exe to PATH'. (The 'python' that opens the Microsoft Store`n" +
          "  is only a shortcut and does not count.)")
}
if ([version]$v -lt [version]"3.9") { Fail "Python $v is too old; 3.9 or newer is needed." }
Write-Host "Using Python $v"

# 3. Private build environment -------------------------------------------------
Step 3 "Preparing the build environment (.venv-build) - first time takes a few minutes"
$venv = Join-Path $root ".venv-build"
$vpy  = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    $exeArgs = @($python | Select-Object -Skip 1)
    & $python[0] @exeArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { Fail "could not create $venv" }
}
& $vpy -m pip install --quiet --disable-pip-version-check -r (Join-Path $gui "requirements.txt") pyinstaller
if ($LASTEXITCODE -ne 0) { Fail "installing PySide6 / PyInstaller failed (check your internet connection)." }

# 4. Bundle -------------------------------------------------------------------
$mode = if ($OneFolder) { "--onedir" } else { "--onefile" }
Step 4 "Bundling with PyInstaller ($mode)"
$dist = Join-Path $root "dist"
$work = Join-Path $root "build\pyinstaller"
# Absolute paths everywhere: PyInstaller resolves relative ones against --specpath.
$pyiArgs = @(
    "--noconfirm", "--clean", $mode, "--windowed",
    "--name", "FilePicker",
    "--icon", (Join-Path $gui "assets\icon.ico"),
    "--add-binary", "$engineExe;bin",                              # -> <bundle>\bin\picker.exe
    "--add-data", "$(Join-Path $gui 'assets\icon.png');assets",    # -> <bundle>\assets\icon.png
    "--paths", $gui,
    "--exclude-module", "tkinter",
    "--distpath", $dist, "--workpath", $work, "--specpath", $work,
    (Join-Path $gui "run_gui.py")
)
& $vpy -m PyInstaller @pyiArgs
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed (see above)." }

# 5. Verify -------------------------------------------------------------------
Step 5 "Checking the result"
$exe = if ($OneFolder) { Join-Path $dist "FilePicker\FilePicker.exe" } else { Join-Path $dist "FilePicker.exe" }
if (-not (Test-Path $exe)) { Fail "expected $exe but it was not created." }
# --check starts the bundled app without a window and exits 0 if it finds and runs its engine.
$p = Start-Process -FilePath $exe -ArgumentList "--check" -Wait -PassThru -WindowStyle Hidden
if ($p.ExitCode -ne 0) { Fail "the packaged app could not find or run its engine (exit $($p.ExitCode))." }

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Done: $exe ($sizeMb MB)" -ForegroundColor Green
if ($OneFolder) {
    Write-Host "Share the whole dist\FilePicker folder (zip it); run FilePicker.exe inside."
} else {
    Write-Host "Copy this single file anywhere and double-click it. Nothing else is needed."
}
