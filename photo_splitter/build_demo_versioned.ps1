param(
    [ValidateSet("v1", "cpu", "cupy-cuda", "all", "opencv", "no-opencv", "gpu-lite")]
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
    param(
        [string]$Item,
        [string]$Version
    )
    if ($Item -eq "v1") { return "photo_splitter_$Version" }
    if ($Item -eq "cupy-cuda") { return "photo_splitter_$Version`_cupy_cuda" }
    return "photo_splitter_$Version`_cpu"
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

    # CuPy release depends on the target machine's CUDA 12 runtime.
    $filterLines = @(
        'def _keep_cupy_cuda_payload(entry):',
        '    """Keep CuPy entry modules but do not bundle CUDA/NVIDIA runtime DLLs."""',
        '    target = str(entry[0]).replace("/", "\\").lower()',
        '    source = str(entry[1]).replace("/", "\\").lower() if len(entry) > 1 else ""',
        '    file_name = target.rsplit("\\", 1)[-1]',
        '    cuda_runtime_prefixes = (',
        '        "cublas",',
        '        "cudart",',
        '        "cufft",',
        '        "curand",',
        '        "cusolver",',
        '        "cusparse",',
        '        "cupti",',
        '        "npp",',
        '        "nvfatbin",',
        '        "nvjitlink",',
        '        "nvrtc",',
        '        "nvptxcompiler",',
        '    )',
        '    runtime_dirs = (',
        '        "\\site-packages\\nvidia\\",',
        '        "\\nvidia gpu computing toolkit\\cuda\\",',
        '    )',
        '    is_runtime_dll = file_name.endswith(".dll") and file_name.startswith(cuda_runtime_prefixes)',
        '    is_runtime_path = target.startswith("nvidia\\") or any(part in source for part in runtime_dirs)',
        '    return not (is_runtime_dll or is_runtime_path)',
        '',
        '',
        'a.binaries = [entry for entry in a.binaries if _keep_cupy_cuda_payload(entry)]',
        'a.datas = [entry for entry in a.datas if _keep_cupy_cuda_payload(entry)]',
        ''
    )
    $filterCode = ($filterLines -join "`r`n") + "`r`n"

    $specText = Get-Content -LiteralPath $SpecPath -Raw
    if ($specText -notmatch "(?m)^pyz = PYZ\(a\.pure\)") {
        throw "Could not locate PYZ section in $SpecPath"
    }

    $specText = $specText -replace "(?m)^pyz = PYZ\(a\.pure\)", "$filterCode`r`npyz = PYZ(a.pure)"
    Set-Content -LiteralPath $SpecPath -Value $specText -Encoding UTF8
}

function Invoke-WithHiddenCudaToolkit {
    param([scriptblock]$ScriptBlock)

    $savedPath = $env:PATH
    $savedCudaPath = $env:CUDA_PATH
    $savedCudaPathV128 = $env:CUDA_PATH_V12_8

    try {
        $env:PATH = (($env:PATH -split ";") | Where-Object {
                $_ -and ($_ -notmatch "\\NVIDIA GPU Computing Toolkit\\CUDA\\v[0-9.]+\\bin")
            }) -join ";"
        Remove-Item Env:CUDA_PATH -ErrorAction SilentlyContinue
        Remove-Item Env:CUDA_PATH_V12_8 -ErrorAction SilentlyContinue
        & $ScriptBlock
    } finally {
        $env:PATH = $savedPath
        if ($null -ne $savedCudaPath) { $env:CUDA_PATH = $savedCudaPath } else { Remove-Item Env:CUDA_PATH -ErrorAction SilentlyContinue }
        if ($null -ne $savedCudaPathV128) { $env:CUDA_PATH_V12_8 = $savedCudaPathV128 } else { Remove-Item Env:CUDA_PATH_V12_8 -ErrorAction SilentlyContinue }
    }
}

function Invoke-WithOptionalToolPath {
    param(
        [string]$ToolPath,
        [scriptblock]$ScriptBlock
    )

    $savedPath = $env:PATH
    try {
        if ($ToolPath -and (Test-Path -LiteralPath $ToolPath)) {
            $env:PATH = "$ToolPath;$env:PATH"
        }
        & $ScriptBlock
    } finally {
        $env:PATH = $savedPath
    }
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
    @("v1", "cpu", "cupy-cuda")
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

    if ($item -eq "cupy-cuda") {
        $cupyToolPath = Join-Path $projectRoot "build\cupy12_venv\Scripts"
        Invoke-WithHiddenCudaToolkit {
            Invoke-WithOptionalToolPath -ToolPath $cupyToolPath {
                pyi-makespec @argsForVariant
                Add-CupyCudaBinaryFilter -SpecPath $generatedSpec
                python -m PyInstaller --noconfirm --clean $generatedSpec
            }
        }
    } else {
        python -m PyInstaller --noconfirm --clean @argsForVariant
    }

    if (Test-Path -LiteralPath $generatedSpec) {
        Remove-Item -LiteralPath $generatedSpec -Force
    }

    $builtExe = Join-Path $dist "$appName.exe"
    if (Test-Path -LiteralPath $builtExe) {
        Wrap-WithStartupLauncher -ExePath $builtExe
    }

    Write-Host "Built dist\$appName.exe"
}
