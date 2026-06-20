$ErrorActionPreference = "Stop"

$packageDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $packageDir
Set-Location $projectRoot

$pythonExe = "python"
$localPython = Join-Path $env:LocalAppData "Programs\Python\Python313\python.exe"
if (Test-Path -LiteralPath $localPython) {
    $pythonExe = $localPython
}

$logFile = Join-Path $projectRoot "gui_startup.log"
$webUiDir = Join-Path $packageDir "web_ui"
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) {
    $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
}

Set-Content -LiteralPath $logFile -Encoding UTF8 -Value @(
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Starting Vue GUI with `"$pythonExe`"",
    "Working directory: `"$projectRoot`""
)

function Invoke-LoggedNative {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = (Get-Location).Path,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $stdoutPath = Join-Path $env:TEMP ("photo_splitter_stdout_{0}.log" -f ([guid]::NewGuid().ToString("N")))
    $stderrPath = Join-Path $env:TEMP ("photo_splitter_stderr_{0}.log" -f ([guid]::NewGuid().ToString("N")))
    try {
        $process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $Arguments `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        if (Test-Path -LiteralPath $stdoutPath) {
            Get-Content -LiteralPath $stdoutPath -Encoding UTF8 -ErrorAction SilentlyContinue | Add-Content -LiteralPath $logFile -Encoding UTF8
        }
        if (Test-Path -LiteralPath $stderrPath) {
            Get-Content -LiteralPath $stderrPath -Encoding UTF8 -ErrorAction SilentlyContinue | Add-Content -LiteralPath $logFile -Encoding UTF8
        }
        $exitCode = [int]$process.ExitCode
    } finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }

    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode."
    }
}

try {
    if (-not $npmCommand) {
        throw "npm was not found. Please install Node.js first."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $webUiDir "package.json"))) {
        throw "Vue UI package.json was not found: $webUiDir"
    }

    Add-Content -LiteralPath $logFile -Encoding UTF8 -Value "Building Vue UI before startup..."
    Push-Location $webUiDir
    try {
        if (-not (Test-Path -LiteralPath "node_modules")) {
            Add-Content -LiteralPath $logFile -Encoding UTF8 -Value "Installing Vue UI dependencies..."
            Invoke-LoggedNative -FilePath $npmCommand.Source -Arguments @("install") -WorkingDirectory $webUiDir -Description "Vue UI dependency install"
        }

        Invoke-LoggedNative -FilePath $npmCommand.Source -Arguments @("run", "build") -WorkingDirectory $webUiDir -Description "Vue UI build"
    } finally {
        Pop-Location
    }

    Add-Content -LiteralPath $logFile -Encoding UTF8 -Value "Vue UI build completed."
    Invoke-LoggedNative -FilePath $pythonExe -Arguments @("-m", "photo_splitter.web_app") -WorkingDirectory $projectRoot -Description "Vue GUI"
    exit 0
} catch {
    Add-Content -LiteralPath $logFile -Encoding UTF8 -Value ("Startup failed: " + $_.Exception.Message)
    throw
}
