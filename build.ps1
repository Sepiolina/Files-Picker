# Build the File Picker engine for Windows into gui\bin\picker.exe.
#
# Easiest: double-click build.cmd (it runs this script with the right settings).
# Or from PowerShell:   powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
#
# Works with Windows PowerShell 5.1 and PowerShell 7+.
# Keep this file plain ASCII: Windows PowerShell 5.1 misreads UTF-8 without a BOM.

$ErrorActionPreference = "Stop"
$MinGo = [version]"1.22"

function Fail($message) {
    Write-Host ""
    Write-Host "BUILD FAILED: $message" -ForegroundColor Red
    exit 1
}

# 1. Is Go installed and on PATH?
$goCmd = Get-Command go -ErrorAction SilentlyContinue
if (-not $goCmd) {
    Fail ("Go was not found.`n" +
          "  Install it from https://go.dev/dl/ (Windows .msi), then open a NEW terminal`n" +
          "  so PATH is refreshed, and run this script again.")
}

# 2. Is it new enough? ("go1.24.7" -> 1.24.7)
$goVersionText = & go env GOVERSION
if ($LASTEXITCODE -ne 0 -or -not $goVersionText) {
    Fail "Go is installed but 'go env GOVERSION' failed. Try reinstalling Go."
}
$goVersion = [version](($goVersionText -replace '^go', '') -replace '[^0-9.].*$', '')
if ($goVersion -lt $MinGo) {
    Fail "Go $goVersion is too old; version $MinGo or newer is needed. Update from https://go.dev/dl/"
}
Write-Host "Using Go $goVersion ($($goCmd.Source))"

# 3. Build. Native programs don't throw in PowerShell, so check the exit code.
$root   = $PSScriptRoot
$engine = Join-Path $root "engine"
$outDir = Join-Path $root "gui\bin"
$out    = Join-Path $outDir "picker.exe"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Push-Location $engine
try {
    & go build -trimpath "-ldflags=-s -w" -o $out .\cmd\picker
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) { Fail "go build exited with code $code (see the messages above)." }
if (-not (Test-Path $out)) { Fail "go build reported success but $out was not created." }

# 4. Smoke test: the engine should report its version.
$version = & $out version
if ($LASTEXITCODE -ne 0) { Fail "the built engine does not run: $out" }

Write-Host ""
Write-Host "Built $out (engine $version)" -ForegroundColor Green
Write-Host "Start the app with:  python gui\run_gui.py"
