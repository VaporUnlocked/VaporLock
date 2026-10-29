[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $ProjectRoot "build\controller-host"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$BuildRoot = Join-Path $ProjectRoot "build"
$CacheRoot = Join-Path $BuildRoot "dependency-cache"
$ToolchainRoot = Join-Path $BuildRoot "toolchain"
$DependencyRoot = Join-Path $BuildRoot "controller-host-deps"
$HostProject = Join-Path $ProjectRoot "controller_host\VaporLockControllerHost.csproj"

$DotnetSdkVersion = "10.0.100"
$DotnetArchiveName = "dotnet-sdk-$DotnetSdkVersion-win-x64.zip"
$DotnetUrl = "https://builds.dotnet.microsoft.com/dotnet/Sdk/$DotnetSdkVersion/$DotnetArchiveName"
$DotnetSha512 = "24b033418a3969effd49b4651ef7ebbffeb284773b99545d78dce61a82e57f38db7facdb013c609ba15573c072f0e093363ae470824a6847f3c6111078c1fb64"

$HidMaestroVersion = "1.7.3"
$HidMaestroArchiveName = "HIDMaestro-v$HidMaestroVersion.zip"
$HidMaestroUrl = "https://github.com/hifihedgehog/HIDMaestro/releases/download/v$HidMaestroVersion/$HidMaestroArchiveName"
$HidMaestroSha256 = "A337DDC70E90FF969DEAAAAD8C3F3F8B7A0EE5B61A6A9FF6183CA35950BD8503"
$HidMaestroCoreSha256 = "95C6D3EC0D41CD7B3ADDA80DA3FFBCD19C9466CC50E08A973C7745A16C7E0BDE"

function Get-HashText {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Algorithm)
    return (Get-FileHash -LiteralPath $Path -Algorithm $Algorithm).Hash.ToUpperInvariant()
}

function Get-VerifiedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Algorithm,
        [Parameter(Mandatory = $true)][string]$ExpectedHash
    )
    if ((Test-Path -LiteralPath $Destination) -and (Get-HashText $Destination $Algorithm) -eq $ExpectedHash) {
        return
    }
    $partial = "$Destination.part"
    Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
    Write-Host "Downloading $(Split-Path -Leaf $Destination)..."
    Invoke-WebRequest -Uri $Url -OutFile $partial -UseBasicParsing
    $actual = Get-HashText $partial $Algorithm
    if ($actual -ne $ExpectedHash) {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        throw "The downloaded dependency did not match its expected $Algorithm hash."
    }
    Move-Item -LiteralPath $partial -Destination $Destination -Force
}

function Expand-ArchiveFresh {
    param([Parameter(Mandatory = $true)][string]$Archive, [Parameter(Mandatory = $true)][string]$Destination)
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $Destination -Force
}

New-Item -ItemType Directory -Path $CacheRoot, $ToolchainRoot -Force | Out-Null

$DotnetRoot = Join-Path $ToolchainRoot "dotnet-$DotnetSdkVersion"
$Dotnet = Join-Path $DotnetRoot "dotnet.exe"
if (-not (Test-Path -LiteralPath $Dotnet)) {
    $DotnetArchive = Join-Path $CacheRoot $DotnetArchiveName
    Get-VerifiedDownload $DotnetUrl $DotnetArchive "SHA512" $DotnetSha512
    Expand-ArchiveFresh $DotnetArchive $DotnetRoot
}
if (-not (Test-Path -LiteralPath $Dotnet)) {
    throw "The bundled .NET SDK could not be prepared."
}

$HidArchive = Join-Path $CacheRoot $HidMaestroArchiveName
Get-VerifiedDownload $HidMaestroUrl $HidArchive "SHA256" $HidMaestroSha256
$CoreDll = Join-Path $DependencyRoot "HIDMaestro.Core.dll"
if (-not (Test-Path -LiteralPath $CoreDll) -or (Get-HashText $CoreDll "SHA256") -ne $HidMaestroCoreSha256) {
    $ExtractRoot = Join-Path $BuildRoot "hidmaestro-extract"
    Expand-ArchiveFresh $HidArchive $ExtractRoot
    $CoreSource = Get-ChildItem -LiteralPath $ExtractRoot -Recurse -File -Filter "HIDMaestro.Core.dll" |
        Sort-Object { $_.FullName.Length } |
        Select-Object -First 1
    $SdkSource = Get-ChildItem -LiteralPath $ExtractRoot -Recurse -File -Filter "Microsoft.Windows.SDK.NET.dll" |
        Sort-Object { $_.FullName.Length } |
        Select-Object -First 1
    $WinRtSource = Get-ChildItem -LiteralPath $ExtractRoot -Recurse -File -Filter "WinRT.Runtime.dll" |
        Sort-Object { $_.FullName.Length } |
        Select-Object -First 1
    if (-not $CoreSource -or -not $SdkSource -or -not $WinRtSource) {
        throw "The verified HIDMaestro package did not contain the required host assemblies."
    }
    if ((Get-HashText $CoreSource.FullName "SHA256") -ne $HidMaestroCoreSha256) {
        throw "The HIDMaestro core assembly did not match its pinned integrity hash."
    }
    if (Test-Path -LiteralPath $DependencyRoot) {
        Remove-Item -LiteralPath $DependencyRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $DependencyRoot -Force | Out-Null
    Copy-Item -LiteralPath $CoreSource.FullName -Destination $CoreDll -Force
    Copy-Item -LiteralPath $SdkSource.FullName -Destination (Join-Path $DependencyRoot "Microsoft.Windows.SDK.NET.dll") -Force
    Copy-Item -LiteralPath $WinRtSource.FullName -Destination (Join-Path $DependencyRoot "WinRT.Runtime.dll") -Force
    foreach ($notice in @("LICENSE", "THIRD-PARTY-NOTICES.txt")) {
        $noticeSource = Get-ChildItem -LiteralPath $ExtractRoot -Recurse -File -Filter $notice |
            Sort-Object { $_.FullName.Length } |
            Select-Object -First 1
        if ($noticeSource) {
            Copy-Item -LiteralPath $noticeSource.FullName -Destination (Join-Path $DependencyRoot $notice) -Force
        }
    }
}

if ($Clean -and (Test-Path -LiteralPath $OutputDirectory)) {
    Remove-Item -LiteralPath $OutputDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$env:DOTNET_MULTILEVEL_LOOKUP = "0"
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = "1"
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
& $Dotnet run --project (Join-Path $ProjectRoot "tests\controller_host\ControllerHostTests.csproj") --configuration Release
if ($LASTEXITCODE -ne 0) {
    throw "The driver-free controller stream checks failed."
}
& $Dotnet publish $HostProject `
    --configuration Release `
    --runtime win-x64 `
    --self-contained true `
    --output $OutputDirectory `
    "-p:HIDMaestroDependencyRoot=$DependencyRoot" `
    "-p:PublishReadyToRun=false"
if ($LASTEXITCODE -ne 0) {
    throw "The Xbox One-compatible controller host failed to build."
}

Get-ChildItem -LiteralPath $OutputDirectory -Filter "*.pdb" -File -ErrorAction SilentlyContinue | Remove-Item -Force
foreach ($notice in @("LICENSE", "THIRD-PARTY-NOTICES.txt")) {
    $source = Join-Path $DependencyRoot $notice
    if (Test-Path -LiteralPath $source) {
        $name = if ($notice -eq "LICENSE") { "HIDMAESTRO-LICENSE.txt" } else { "HIDMAESTRO-$notice" }
        Copy-Item -LiteralPath $source -Destination (Join-Path $OutputDirectory $name) -Force
    }
}

$HostExecutable = Join-Path $OutputDirectory "VaporLockControllerHost.exe"
if (-not (Test-Path -LiteralPath $HostExecutable)) {
    throw "The controller host executable is missing after publish."
}
& $HostExecutable --self-test --profile vapor-lock-xbox-one-windows
if ($LASTEXITCODE -ne 0) {
    throw "The controller host failed its pre-package self-test."
}

Write-Host "Xbox One-compatible controller host is ready at $OutputDirectory"
