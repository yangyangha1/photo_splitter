param(
    [ValidateSet("v1", "cpu", "all", "opencv", "no-opencv")]
    [string]$Variant = "v1",
    [string]$ReleaseVersion = "",
    [switch]$ReleaseV1
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

Push-Location (Join-Path $scriptDir "web_ui")
npm install
npm run build
Pop-Location

$dist = Join-Path $projectRoot "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

function Normalize-Variant {
    param([string]$Item)
    if ($Item -eq "opencv") { return "v1" }
    if ($Item -eq "no-opencv") { return "cpu" }
    return $Item
}

function Get-VariantSuffix {
    param([string]$Item)
    if ($Item -eq "v1") { return "opencv" }
    return "cpu"
}

function Get-ReleaseAppName {
    param(
        [string]$Item,
        [string]$Version
    )
    if ($Item -eq "v1") { return "photo_splitter_$Version" }
    return "photo_splitter_$Version`_cpu"
}

function Get-NextDemoVersion {
    $maxVersion = 0
    Get-ChildItem -LiteralPath $dist -Filter "photo_splitter_demo_v*.exe" -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.BaseName -match "photo_splitter_demo_v(\d+)(?:_(?:opencv|cpu|no_opencv))?$") {
            $maxVersion = [Math]::Max($maxVersion, [int]$Matches[1])
        }
    }
    return $maxVersion + 1
}

function Get-PyInstallerArgs {
    param(
        [string]$AppName,
        [string]$Item
    )

    $opencvArgs = if ($Item -eq "cpu") {
        @("--exclude-module", "cv2")
    } else {
        @("--hidden-import", "cv2")
    }

    $acceleratorExcludes = @(
        "--exclude-module", "cupy",
        "--exclude-module", "cupyx",
        "--exclude-module", "cupy_backends",
        "--exclude-module", "cuda",
        "--exclude-module", "nvidia"
    )

    return @(
        "--windowed",
        "--onefile",
        "--name", $AppName,
        "--icon", "photo_splitter\assets\photo_splitter_icon.ico",
        "--add-data", "photo_splitter\assets\photo_splitter_icon.ico;photo_splitter\assets",
        "--add-data", "photo_splitter\assets\photo_splitter_icon.svg;photo_splitter\assets",
        "--add-data", "photo_splitter\assets\photo_splitter_icon_preview.png;photo_splitter\assets",
        "--add-data", "photo_splitter\models;photo_splitter\models",
        "--add-data", "photo_splitter\web_ui\dist;photo_splitter\web_ui\dist"
    ) + $opencvArgs + @(
        "--hidden-import", "flask",
        "--hidden-import", "webview",
        "--hidden-import", "graphlib",
        "--exclude-module", "photo_splitter.cli",
        "--exclude-module", "webview.platforms.gtk",
        "--exclude-module", "webview.platforms.qt",
        "--exclude-module", "webview.platforms.cocoa",
        "--exclude-module", "webview.platforms.cef",
        "--exclude-module", "webview.platforms.android",
        "--exclude-module", "tkinter",
        "--exclude-module", "_tkinter",
        "--exclude-module", "tcl",
        "--exclude-module", "tk",
        "--exclude-module", "cryptography",
        "--exclude-module", "OpenSSL",
        "--exclude-module", "setuptools",
        "--exclude-module", "pkg_resources",
        "--exclude-module", "wheel"
    ) + $acceleratorExcludes + @(
        "--exclude-module", "torch",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pandas",
        "--exclude-module", "scipy",
        "--exclude-module", "skimage",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "PySide2",
        "--exclude-module", "PySide6",
        "--exclude-module", "gi",
        "--exclude-module", "IPython",
        "--exclude-module", "pytest",
        "--exclude-module", "yaml",
        "photo_splitter\web_app.py"
    )
}

function Wrap-WithStartupLauncher {
    param([string]$ExePath)

    $launcherScript = Join-Path $scriptDir "launcher\build_launcher.ps1"
    if (-not (Test-Path -LiteralPath $launcherScript)) {
        Write-Warning "Launcher build script was not found; keeping raw PyInstaller executable."
        return
    }

    $payloadPath = [System.IO.Path]::ChangeExtension($ExePath, ".payload.exe")
    if (Test-Path -LiteralPath $payloadPath) {
        Remove-Item -LiteralPath $payloadPath -Force
    }

    Move-Item -LiteralPath $ExePath -Destination $payloadPath -Force
    try {
        & $launcherScript -OutputPath $ExePath -PayloadPath $payloadPath
    } catch {
        if (Test-Path -LiteralPath $ExePath) {
            Remove-Item -LiteralPath $ExePath -Force
        }
        Move-Item -LiteralPath $payloadPath -Destination $ExePath -Force
        throw
    }

    Remove-Item -LiteralPath $payloadPath -Force
    Write-Host "Wrapped $ExePath with startup launcher"
}

$variants = if ($Variant -eq "all") {
    @("v1", "cpu")
} else {
    @(Normalize-Variant -Item $Variant)
}
$releaseVersionName = if ($ReleaseV1) { "v1" } elseif ($ReleaseVersion.Trim()) { $ReleaseVersion.Trim() } else { "" }
if ($releaseVersionName -and $releaseVersionName -notmatch "^v\d+$") {
    throw "ReleaseVersion must look like v2, v3, etc."
}
$nextVersion = Get-NextDemoVersion

foreach ($item in $variants) {
    $suffix = Get-VariantSuffix -Item $item
    $appName = if ($releaseVersionName) { Get-ReleaseAppName -Item $item -Version $releaseVersionName } else { "photo_splitter_demo_v$nextVersion`_$suffix" }
    $argsForVariant = Get-PyInstallerArgs -AppName $appName -Item $item
    $generatedSpec = Join-Path $projectRoot "$appName.spec"

    if (Test-Path -LiteralPath $generatedSpec) {
        Remove-Item -LiteralPath $generatedSpec -Force
    }

    python -m PyInstaller --noconfirm --clean @argsForVariant

    if (Test-Path -LiteralPath $generatedSpec) {
        Remove-Item -LiteralPath $generatedSpec -Force
    }

    $builtExe = Join-Path $dist "$appName.exe"
    if (Test-Path -LiteralPath $builtExe) {
        Wrap-WithStartupLauncher -ExePath $builtExe
    }

    Write-Host "Built dist\$appName.exe"
}
