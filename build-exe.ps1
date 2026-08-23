# build-exe.ps1  —  run this on Windows to produce DesignStudio.exe
# Usage: right-click → "Run with PowerShell"   OR   from a VS Developer PowerShell:
#   .\build-exe.ps1
#
# Prerequisites:
#   • Visual Studio 2022 with "Desktop development with C++" and ".NET desktop development"
#   • CMake 3.20+ on PATH  (ships with VS 2022; tick "CMake tools for Windows" in installer)
#   • .NET 8 SDK           (ships with VS 2022)
#
# Output: publish\DesignStudio.exe   (~80-120 MB, fully self-contained, no install needed)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

Write-Host ""
Write-Host "=== DesignStudio — single-file build ===" -ForegroundColor Cyan
Write-Host ""

# ── 1. Build C++ core ─────────────────────────────────────────────────────────
Write-Host "[1/3] Building C++ core (designcore.dll)..." -ForegroundColor Yellow

$coreDir  = Join-Path $root "core"
$buildDir = Join-Path $coreDir "build"

cmake -B $buildDir -S $coreDir -G "Visual Studio 17 2022" -A x64 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed" }

cmake --build $buildDir --config Release -j
if ($LASTEXITCODE -ne 0) { throw "CMake build failed" }

$dll = Join-Path $buildDir "Release\designcore.dll"
if (-not (Test-Path $dll)) { throw "designcore.dll not found at $dll" }
Write-Host "    designcore.dll built OK  ($([math]::Round((Get-Item $dll).Length/1KB)) KB)" -ForegroundColor Green

# ── 2. Publish single-file exe ────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/3] Publishing single-file exe..." -ForegroundColor Yellow

$proj    = Join-Path $root "app\DesignStudio\DesignStudio.csproj"
$outDir  = Join-Path $root "publish"

dotnet publish $proj `
    --configuration Release `
    --runtime win-x64 `
    --self-contained true `
    -p:PublishSingleFile=true `
    -p:IncludeNativeLibrariesForSelfExtract=true `
    -p:EnableCompressionInSingleFile=true `
    -p:PublishReadyToRun=true `
    --output $outDir `
    --nologo

if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed" }

# ── 3. Verify output ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[3/3] Verifying output..." -ForegroundColor Yellow

$exe = Join-Path $outDir "DesignStudio.exe"
if (-not (Test-Path $exe)) { throw "DesignStudio.exe not found in publish\" }

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  BUILD COMPLETE" -ForegroundColor Green
Write-Host ""
Write-Host "  $exe" -ForegroundColor White
Write-Host "  Size: $sizeMb MB" -ForegroundColor White
Write-Host ""
Write-Host "  Double-click DesignStudio.exe to launch." -ForegroundColor Green
Write-Host "  No installation required." -ForegroundColor Green
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""

# Open the publish folder in Explorer
Start-Process explorer.exe $outDir
