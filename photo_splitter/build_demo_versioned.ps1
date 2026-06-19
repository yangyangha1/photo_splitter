param(
    [ValidateSet("opencv", "no-opencv", "gpu-lite", "all")]
    [string]$Variant = "opencv"
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

$maxVersion = 0
Get-ChildItem -LiteralPath $dist -Filter "photo_splitter_demo_v*.exe" -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.BaseName -match "photo_splitter_demo_v(\d+)(?:_(?:opencv|no_opencv|gpu_lite))?$") {
        $maxVersion = [Math]::Max($maxVersion, [int]$Matches[1])
    }
}

$nextVersion = $maxVersion + 1
$variants = if ($Variant -eq "all") { @("opencv", "no-opencv", "gpu-lite") } else { @($Variant) }

function Get-VariantSuffix {
    param([string]$Item)
    if ($Item -eq "opencv") { return "opencv" }
    if ($Item -eq "gpu-lite") { return "gpu_lite" }
    return "no_opencv"
}

function Get-PyInstallerArgs {
    param(
        [string]$AppName,
        [string]$Item
    )

    $opencvArgs = if ($Item -eq "no-opencv") {
        @("--exclude-module", "cv2")
    } else {
        @("--hidden-import", "cv2")
    }

    $cupyArgs = if ($Item -eq "gpu-lite") {
        @(
            "--hidden-import", "cupy",
            "--hidden-import", "cupy.cuda.runtime",
            "--hidden-import", "cuda.pathfinder",
            "--copy-metadata", "cupy-cuda13x",
            "--copy-metadata", "cuda-pathfinder",
            "--exclude-module", "nvidia",
            "--exclude-module", "cupyx",
            "--exclude-module", "cupy.testing"
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

function Add-GpuLiteBinaryFilter {
    param([string]$SpecPath)

    # GPU 轻量版只保留 CuPy 调用入口，不把完整 CUDA/NVIDIA 运行库打进 EXE。
    $filterCode = @'

def _keep_gpu_lite_binary(entry):
    """GPU 轻量版只保留 CuPy 调用入口，不随 EXE 捆绑完整 CUDA/NVIDIA 运行库。"""
    target = str(entry[0]).replace('/', '\\').lower()
    source = str(entry[1]).replace('/', '\\').lower() if len(entry) > 1 else ''
    file_name = target.rsplit('\\', 1)[-1]
    cuda_runtime_prefixes = (
        'cublas',
        'cudart',
        'cufft',
        'curand',
        'cusolver',
        'cusparse',
        'cupti',
        'npp',
        'nvfatbin',
        'nvjitlink',
        'nvrtc',
        'nvptxcompiler',
    )
    bundled_nvidia_runtime = '\\site-packages\\nvidia\\' in source or target.startswith('nvidia\\')
    cuda_runtime_dll = file_name.endswith('.dll') and file_name.startswith(cuda_runtime_prefixes)
    return not (bundled_nvidia_runtime or cuda_runtime_dll)


a.binaries = TOC([entry for entry in a.binaries if _keep_gpu_lite_binary(entry)])
'@

    $specText = Get-Content -LiteralPath $SpecPath -Raw
    if ($specText -notmatch "(?m)^pyz = PYZ\(a\.pure\)") {
        throw "Could not locate PYZ section in $SpecPath"
    }

    $specText = $specText -replace "(?m)^pyz = PYZ\(a\.pure\)", "$filterCode`r`npyz = PYZ(a.pure)"
    Set-Content -LiteralPath $SpecPath -Value $specText -Encoding UTF8
}

foreach ($item in $variants) {
    $suffix = Get-VariantSuffix -Item $item
    $appName = "photo_splitter_demo_v$nextVersion`_$suffix"
    $argsForVariant = Get-PyInstallerArgs -AppName $appName -Item $item
    $generatedSpec = Join-Path $projectRoot "$appName.spec"

    if (Test-Path -LiteralPath $generatedSpec) {
        Remove-Item -LiteralPath $generatedSpec -Force
    }

    if ($item -eq "gpu-lite") {
        pyi-makespec @argsForVariant
        Add-GpuLiteBinaryFilter -SpecPath $generatedSpec
        python -m PyInstaller --noconfirm --clean $generatedSpec
    } else {
        python -m PyInstaller --noconfirm --clean @argsForVariant
    }

    if (Test-Path -LiteralPath $generatedSpec) {
        Remove-Item -LiteralPath $generatedSpec -Force
    }

    Write-Host "Built dist\$appName.exe"
}
