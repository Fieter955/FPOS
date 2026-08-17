param(
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path $PSScriptRoot).Path.TrimEnd("\\")
$separator = [System.IO.Path]::DirectorySeparatorChar
$repoPrefix = $repoRoot + $separator
$gitPrefix = (Join-Path $repoRoot ".git") + $separator

function Assert-SafeTarget {
    param([Parameter(Mandatory = $true)][string]$Path)

    $absolute = [System.IO.Path]::GetFullPath($Path)
    if ($absolute -eq $repoRoot -or -not $absolute.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Target di luar proyek atau terlalu luas: $absolute"
    }
    return $absolute
}

function Test-ContainsProtectedData {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    $files = if ($item.PSIsContainer) {
        Get-ChildItem -LiteralPath $item.FullName -Recurse -Force -File -ErrorAction SilentlyContinue
    } else {
        @($item)
    }

    foreach ($file in $files) {
        $relative = $file.FullName.Substring($repoPrefix.Length)
        if (
            $file.Name -match '^\.env($|\.)' -or
            $file.Name -match '\.db($|-wal$|-shm$)' -or
            $file.Name -match '\.sqlite3?$' -or
            $file.Name -match '\.key$' -or
            $relative -match '(^|\\)(backups|uploads)(\\|$)'
        ) {
            return $true
        }
    }
    return $false
}

$targets = @(
    "build",
    "dist",
    "backend\build",
    "backend\dist\FPOS",
    "backend\dist\FPOS-Updater.exe",
    "frontend-dist",
    "node_modules",
    "build-manifest.json",
    ".autostart_configured"
)

$releaseStages = @()
$generatedReleaseRoot = Join-Path $repoRoot "rilis\generated"
if (Test-Path -LiteralPath $generatedReleaseRoot) {
    $releaseStages = Get-ChildItem -LiteralPath $generatedReleaseRoot -Directory -Filter "FPOS-*-Windows-stage" -Force -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
}

$cacheDirectories = Get-ChildItem -LiteralPath $repoRoot -Recurse -Force -Directory -ErrorAction SilentlyContinue |
    Where-Object {
        ($_.Name -eq "__pycache__" -or $_.Name -eq ".pytest_cache") -and
        -not $_.FullName.StartsWith($gitPrefix, [System.StringComparison]::OrdinalIgnoreCase)
    } |
    Select-Object -ExpandProperty FullName

$allTargets = @($targets | ForEach-Object { Join-Path $repoRoot $_ }) + @($releaseStages) + @($cacheDirectories)
$allTargets = $allTargets | Sort-Object -Unique

foreach ($candidate in $allTargets) {
    $target = Assert-SafeTarget -Path $candidate
    if (-not (Test-Path -LiteralPath $target)) { continue }
    if (Test-ContainsProtectedData -Path $target) {
        Write-Warning "Dilewati karena mengandung data terlindungi: $target"
        continue
    }

    $relative = $target.Substring($repoPrefix.Length)
    if ($Execute) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Host "[deleted] $relative"
    } else {
        Write-Host "[preview] $relative"
    }
}

if (-not $Execute) {
    Write-Host "Tidak ada file yang dihapus. Jalankan kembali dengan -Execute untuk menerapkan."
}
