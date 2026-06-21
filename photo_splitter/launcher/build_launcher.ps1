param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [string]$PayloadPath = ""
)

$ErrorActionPreference = "Stop"

$launcherDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$packageDir = Split-Path -Parent $launcherDir
$projectRoot = Split-Path -Parent $packageDir
$sourcePath = Join-Path $launcherDir "PhotoSplitterLauncher.cs"
$iconPath = Join-Path $packageDir "assets\photo_splitter_icon.ico"
$iconPreviewPath = Join-Path $packageDir "assets\photo_splitter_icon_preview.png"

if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "Launcher source was not found: $sourcePath"
}

$outputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
$outputDir = Split-Path -Parent $outputFullPath
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$compilerOptions = New-Object System.Collections.Generic.List[string]
$compilerOptions.Add("/target:winexe")
$compilerOptions.Add("/optimize+")
$compilerOptions.Add("/platform:anycpu")
if (Test-Path -LiteralPath $iconPath) {
    $compilerOptions.Add("/win32icon:`"$iconPath`"")
}
if (Test-Path -LiteralPath $iconPreviewPath) {
    $compilerOptions.Add("/resource:`"$iconPreviewPath`",PhotoSplitterIconPreview")
}
if ($PayloadPath.Trim()) {
    $payloadFullPath = [System.IO.Path]::GetFullPath($PayloadPath)
    if (-not (Test-Path -LiteralPath $payloadFullPath)) {
        throw "Launcher payload was not found: $payloadFullPath"
    }
    $compilerOptions.Add("/resource:`"$payloadFullPath`",PhotoSplitterPayload")
}

if (Test-Path -LiteralPath $outputFullPath) {
    Remove-Item -LiteralPath $outputFullPath -Force
}

$sourceText = Get-Content -LiteralPath $sourcePath -Raw -Encoding UTF8
$references = @("System.dll", "System.Core.dll", "System.Drawing.dll", "System.Windows.Forms.dll")

# Windows PowerShell 自带 CodeDom 编译器，不需要安装 dotnet SDK。
Add-Type -AssemblyName Microsoft.CSharp
$provider = New-Object Microsoft.CSharp.CSharpCodeProvider
$parameters = New-Object System.CodeDom.Compiler.CompilerParameters
$parameters.GenerateExecutable = $true
$parameters.GenerateInMemory = $false
$parameters.OutputAssembly = $outputFullPath
$parameters.CompilerOptions = [string]::Join(" ", $compilerOptions.ToArray())
foreach ($reference in $references) {
    [void]$parameters.ReferencedAssemblies.Add($reference)
}

$result = $provider.CompileAssemblyFromSource($parameters, $sourceText)
if ($result.Errors.HasErrors) {
    $messages = @()
    foreach ($errorItem in $result.Errors) {
        $messages += "$($errorItem.FileName):$($errorItem.Line):$($errorItem.Column) $($errorItem.ErrorNumber) $($errorItem.ErrorText)"
    }
    throw "Launcher compile failed:`n$($messages -join "`n")"
}

if (-not (Test-Path -LiteralPath $outputFullPath)) {
    throw "Launcher compiler finished but did not create: $outputFullPath"
}

Write-Host "Built launcher $outputFullPath"
