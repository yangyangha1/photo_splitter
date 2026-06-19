param(
    [ValidateSet("opencv", "no-opencv", "all")]
    [string]$Variant = "opencv"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Push-Location (Join-Path $root "web_ui")
npm install
npm run build
Pop-Location

$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

$maxVersion = 0
Get-ChildItem -LiteralPath $dist -Filter "photo_splitter_demo_v*.exe" -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.BaseName -match "photo_splitter_demo_v(\d+)(?:_(?:opencv|no_opencv))?$") {
        $maxVersion = [Math]::Max($maxVersion, [int]$Matches[1])
    }
}

$nextVersion = $maxVersion + 1
$variants = if ($Variant -eq "all") { @("opencv", "no-opencv") } else { @($Variant) }

foreach ($item in $variants) {
    $suffix = if ($item -eq "opencv") { "opencv" } else { "no_opencv" }
    $appName = "photo_splitter_demo_v$nextVersion`_$suffix"
    $opencvArgs = if ($item -eq "opencv") { @("--hidden-import", "cv2") } else { @("--exclude-module", "cv2") }

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --onefile `
        --name $appName `
        --icon "assets\photo_splitter_icon.ico" `
        --add-data "assets\photo_splitter_icon.ico;assets" `
        --add-data "assets\photo_splitter_icon_preview.png;assets" `
        --add-data "photo_splitter\web_static;photo_splitter\web_static" `
        @opencvArgs `
        --hidden-import flask `
        --hidden-import webview `
        --exclude-module cupy `
        --exclude-module torch `
        --exclude-module matplotlib `
        --exclude-module pandas `
        --exclude-module scipy `
        --exclude-module skimage `
        --exclude-module IPython `
        --exclude-module pytest `
        --exclude-module yaml `
        "photo_splitter\web_app.py"

    $generatedSpec = Join-Path $root "$appName.spec"
    if (Test-Path -LiteralPath $generatedSpec) {
        Remove-Item -LiteralPath $generatedSpec -Force
    }

    Write-Host "Built dist\$appName.exe"
}
