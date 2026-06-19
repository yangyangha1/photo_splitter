param(
    [ValidateSet("v1", "cpu", "cupy-cuda", "all", "opencv", "no-opencv", "gpu-lite")]
    [string]$Variant = "v1",
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
    if ($Item -eq "gpu-lite") { return "cupy-cuda" }
    return $Item
}

function Get-VariantSuffix {
    param([string]$Item)
    if ($Item -eq "v1") { return "opencv" }
    if ($Item -eq "cupy-cuda") { return "cupy_cuda" }
    return "cpu"
}

function Get-ReleaseAppName {
    param([string]$Item)
    if ($Item -eq "v1") { return "photo_splitter_v1" }
    if ($Item -eq "cupy-cuda") { return "photo_splitter_v1_cupy_cuda" }
    return "photo_splitter_v1_cpu"
}

function Get-NextDemoVersion {
    $maxVersion = 0
    Get-ChildItem -LiteralPath $dist -Filter "photo_splitter_demo_v*.exe" -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.BaseName -match "photo_splitter_demo_v(\d+)(?:_(?:opencv|cpu|cupy_cuda|no_opencv|gpu_lite))?$") {
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

    $cupyArgs = if ($Item -eq "cupy-cuda") {
        @(
            "--hidden-import", "cupy",
            "--hidden-import", "cupyx",
            "--hidden-import", "cupy.testing",
            "--hidden-import", "cupy.cuda.runtime",
            "--hidden-import", "cupy._core.syncdetect",
            "--hidden-import", "cupy_backends.cuda._softlink",
            "--hidden-import", "cuda.pathfinder",
            "--collect-submodules", "cupy._core",
            "--collect-submodules", "cupy.cuda",
            "--collect-submodules", "cupy_backends.cuda",
            "--collect-submodules", "cupy_backends.cuda.api",
            "--collect-data", "cupy",
            "--copy-metadata", "cupy-cuda12x",
            "--copy-metadata", "cuda-pathfinder",
            "--exclude-module", "nvidia"
        )
    } else {
        @(
            "--exclude-module", "cupy",
            "--exclude-module", "cupyx",
            "--exclude-module", "nvidia"
        )
    }

    return @(
        "--windowed",
        "--onefile",
        "--name", $AppName,
        "--icon", "photo_splitter\assets\photo_splitter_icon.ico",
        "--add-data", "photo_splitter\assets\photo_splitter_icon.ico;photo_splitter\assets",
        "--add-data", "photo_splitter\assets\photo_splitter_icon_preview.png;photo_splitter\assets",
        "--add-data", "photo_splitter\web_ui\dist;photo_splitter\web_ui\dist"
    ) + $opencvArgs + @(
            "--hidden-import", "flask",
            "--hidden-import", "webview",
            "--hidden-import", "graphlib",
            "--exclude-module", "tkinter",
        "--exclude-module", "_tkinter",
        "--exclude-module", "tcl",
        "--exclude-module", "tk",
        "--exclude-module", "cryptography",
        "--exclude-module", "OpenSSL",
        "--exclude-module", "setuptools",
        "--exclude-module", "pkg_resources",
        "--exclude-module", "wheel"
    ) + $cupyArgs + @(
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

function Add-CupyCudaBinaryFilter {
    param([string]$SpecPath)

    # CuPy 版依赖目标机器已有 CUDA 12 runtime，因此不把 pip 附带的完整 NVIDIA 运行库打进 EXE。
    $filterCodeBase64 = "CmRlZiBfa2VlcF9jdXB5X2N1ZGFfYmluYXJ5KGVudHJ5KToKICAgICIiIkN1UHkgcmVsZWFzZSBrZWVwcyBDVURBIGVudHJ5IHBvaW50cyBidXQgZG9lcyBub3QgYnVuZGxlIGZ1bGwgTlZJRElBIHJ1bnRpbWUgRExMcy4iIiIKICAgIHRhcmdldCA9IHN0cihlbnRyeVswXSkucmVwbGFjZSgnLycsICdcXCcpLmxvd2VyKCkKICAgIHNvdXJjZSA9IHN0cihlbnRyeVsxXSkucmVwbGFjZSgnLycsICdcXCcpLmxvd2VyKCkgaWYgbGVuKGVudHJ5KSA+IDEgZWxzZSAnJwogICAgZmlsZV9uYW1lID0gdGFyZ2V0LnJzcGxpdCgnXFwnLCAxKVstMV0KICAgIGN1ZGFfcnVudGltZV9wcmVmaXhlcyA9ICgKICAgICAgICAnY3VibGFzJywKICAgICAgICAnY3VkYXJ0JywKICAgICAgICAnY3VmZnQnLAogICAgICAgICdjdXJhbmQnLAogICAgICAgICdjdXNvbHZlcicsCiAgICAgICAgJ2N1c3BhcnNlJywKICAgICAgICAnY3VwdGknLAogICAgICAgICducHAnLAogICAgICAgICdudmZhdGJpbicsCiAgICAgICAgJ252aml0bGluaycsCiAgICAgICAgJ252cnRjJywKICAgICAgICAnbnZwdHhjb21waWxlcicsCiAgICApCiAgICBidW5kbGVkX252aWRpYV9ydW50aW1lID0gJ1xcc2l0ZS1wYWNrYWdlc1xcbnZpZGlhXFwnIGluIHNvdXJjZSBvciB0YXJnZXQuc3RhcnRzd2l0aCgnbnZpZGlhXFwnKQogICAgY3VkYV9ydW50aW1lX2RsbCA9IGZpbGVfbmFtZS5lbmRzd2l0aCgnLmRsbCcpIGFuZCBmaWxlX25hbWUuc3RhcnRzd2l0aChjdWRhX3J1bnRpbWVfcHJlZml4ZXMpCiAgICByZXR1cm4gbm90IChidW5kbGVkX252aWRpYV9ydW50aW1lIG9yIGN1ZGFfcnVudGltZV9kbGwpCgoKYS5iaW5hcmllcyA9IFRPQyhbZW50cnkgZm9yIGVudHJ5IGluIGEuYmluYXJpZXMgaWYgX2tlZXBfY3VweV9jdWRhX2JpbmFyeShlbnRyeSldKQo="
    $filterCode = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($filterCodeBase64))

    $specText = Get-Content -LiteralPath $SpecPath -Raw
    if ($specText -notmatch "(?m)^pyz = PYZ\(a\.pure\)") {
        throw "Could not locate PYZ section in $SpecPath"
    }

    $specText = $specText -replace "(?m)^pyz = PYZ\(a\.pure\)", "$filterCode`r`npyz = PYZ(a.pure)"
    Set-Content -LiteralPath $SpecPath -Value $specText -Encoding UTF8
}

$variants = if ($Variant -eq "all") {
    @("v1", "cpu", "cupy-cuda")
} else {
    @(Normalize-Variant -Item $Variant)
}
$nextVersion = Get-NextDemoVersion

foreach ($item in $variants) {
    $suffix = Get-VariantSuffix -Item $item
    $appName = if ($ReleaseV1) { Get-ReleaseAppName -Item $item } else { "photo_splitter_demo_v$nextVersion`_$suffix" }
    $argsForVariant = Get-PyInstallerArgs -AppName $appName -Item $item
    $generatedSpec = Join-Path $projectRoot "$appName.spec"

    if (Test-Path -LiteralPath $generatedSpec) {
        Remove-Item -LiteralPath $generatedSpec -Force
    }

    if ($item -eq "cupy-cuda") {
        pyi-makespec @argsForVariant
        Add-CupyCudaBinaryFilter -SpecPath $generatedSpec
        python -m PyInstaller --noconfirm --clean $generatedSpec
    } else {
        python -m PyInstaller --noconfirm --clean @argsForVariant
    }

    if (Test-Path -LiteralPath $generatedSpec) {
        Remove-Item -LiteralPath $generatedSpec -Force
    }

    Write-Host "Built dist\$appName.exe"
}
