param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [Parameter(Mandatory = $true)]
    [string]$DownloadUrl,
    [string]$Notes = "",
    [string]$MinimumVersion = "0.0.0",
    [string]$CondaEnv = "base",
    [string]$OutputRoot = "rilis\generated"
)

$ErrorActionPreference = "Stop"
if ($Version -notmatch '^v?\d+\.\d+\.\d+$') {
    throw "Version harus memakai format semver sederhana, contoh 5.1.0"
}
if ($MinimumVersion -notmatch '^v?\d+\.\d+\.\d+$') {
    throw "MinimumVersion harus memakai format semver sederhana, contoh 5.0.0"
}

$repoRoot = (Resolve-Path ".").Path
$versionClean = $Version.TrimStart("v")
$outputPath = Join-Path $repoRoot $OutputRoot
$stage = Join-Path $outputPath "FPOS-$versionClean-Windows-stage"
$zipPath = Join-Path $outputPath "FPOS-$versionClean-Windows.zip"
$manifestPath = Join-Path $outputPath "version.json"
$repositoryManifestPath = Join-Path $repoRoot "version.json"

if (Test-Path -LiteralPath $stage) { throw "Folder staging sudah ada: $stage" }
if (Test-Path -LiteralPath $zipPath) { throw "ZIP tujuan sudah ada: $zipPath" }
New-Item -ItemType Directory -Path $stage -Force | Out-Null
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$pushed = $false

try {

    Push-Location (Join-Path $repoRoot "backend")
    $pushed = $true
    conda run -n $CondaEnv python -m PyInstaller --clean --noconfirm FPOS.spec
    if ($LASTEXITCODE -ne 0) { throw "Build FPOS gagal" }
    conda run -n $CondaEnv python -m PyInstaller --clean --noconfirm FPOS-Updater.spec
    if ($LASTEXITCODE -ne 0) { throw "Build FPOS-Updater gagal" }
    Pop-Location

    $mainDist = Join-Path $repoRoot "backend\dist\FPOS"
    Copy-Item -LiteralPath (Join-Path $mainDist "FPOS.exe") -Destination $stage
    Copy-Item -LiteralPath (Join-Path $mainDist "_internal") -Destination $stage -Recurse
    Copy-Item -LiteralPath (Join-Path $repoRoot "frontend-dist") -Destination $stage -Recurse
    Copy-Item -LiteralPath (Join-Path $repoRoot "backend\dist\FPOS-Updater.exe") -Destination $stage

    Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zipPath -CompressionLevel Optimal
    $hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = (Get-Item -LiteralPath $zipPath).Length
    $notesValue = if ($Notes) { $Notes } else { "Rilis FPOS $versionClean" }
    $manifest = [ordered]@{
        version = $versionClean
        minimum_version = $MinimumVersion.TrimStart("v")
        release_date = (Get-Date).ToString("yyyy-MM-dd")
        notes = $notesValue
        download_url = $DownloadUrl
        sha256 = $hash
        size_bytes = $size
        mandatory = $false
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 5
    $manifestJson | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    $manifestJson | Set-Content -LiteralPath $repositoryManifestPath -Encoding UTF8
    "$hash  $(Split-Path -Leaf $zipPath)" | Set-Content -LiteralPath "$zipPath.sha256.txt" -Encoding ASCII

    Write-Host "Rilis selesai: $zipPath"
    Write-Host "Manifest: $manifestPath"
}
finally {
    if ($pushed) { Pop-Location }
}
