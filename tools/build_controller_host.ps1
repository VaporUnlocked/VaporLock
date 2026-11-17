[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:DOTNET_GENERATE_ASPNET_CERTIFICATE = "false"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $ProjectRoot "build"
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $ProjectRoot "build\controller-host"
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd([char[]]'\/')
if (-not $OutputDirectory.StartsWith("$BuildRoot\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Controller build output must be below $BuildRoot."
}
$CacheRoot = Join-Path $BuildRoot "dependency-cache"
$ToolchainRoot = Join-Path $BuildRoot "toolchain"
$DependencyRoot = Join-Path $BuildRoot "controller-host-deps"
$HostProject = Join-Path $ProjectRoot "controller_host\VaporLockControllerHost.csproj"

$DotnetSdkVersion = "10.0.115"
$DotnetArchiveName = "dotnet-sdk-$DotnetSdkVersion-win-x64.zip"
$DotnetUrl = "https://builds.dotnet.microsoft.com/dotnet/Sdk/$DotnetSdkVersion/$DotnetArchiveName"
$DotnetSha512 = "f9547ed219c7bb08603adc1eea8bd92f21a02b684dd4088131f89cede677326bcfa84960a027d01e0c51091eaf27d4f41aa38a51ff15abc271c1025bc5df198e"

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
        Remove-GeneratedDirectory $Destination
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
}

function Remove-GeneratedDirectory {
    param([string]$Path)
    $directory = Get-Item -LiteralPath $Path
    if (-not $directory.PSIsContainer -or -not $directory.FullName.StartsWith(
        "$BuildRoot\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Only generated directories below $BuildRoot can be cleared."
    }
    for ($parent = $directory; $null -ne $parent; $parent = $parent.Parent) {
        if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "The controller builder refuses to clear a linked directory."
        }
    }
    Remove-Item -LiteralPath $directory.FullName -Recurse -Force
}

New-Item -ItemType Directory -Path $CacheRoot, $ToolchainRoot -Force | Out-Null

$DotnetRoot = Join-Path $ToolchainRoot "dotnet-$DotnetSdkVersion"
$Dotnet = Join-Path $DotnetRoot "dotnet.exe"
if (-not (Test-Path -LiteralPath (Join-Path $DotnetRoot "sdk\$DotnetSdkVersion\dotnet.dll"))) {
    $DotnetArchive = Join-Path $CacheRoot $DotnetArchiveName
    Get-VerifiedDownload $DotnetUrl $DotnetArchive "SHA512" $DotnetSha512
    Expand-ArchiveFresh $DotnetArchive $DotnetRoot
}
if (-not (Test-Path -LiteralPath $Dotnet)) {
    throw "The bundled .NET SDK could not be prepared."
}

$HidArchive = Join-Path $CacheRoot $HidMaestroArchiveName
Get-VerifiedDownload $HidMaestroUrl $HidArchive "SHA256" $HidMaestroSha256
Add-Type -AssemblyName System.IO.Compression.FileSystem
New-Item -ItemType Directory -Path $DependencyRoot -Force | Out-Null
$archive = [IO.Compression.ZipFile]::OpenRead($HidArchive)
try {
    foreach ($filename in @("HIDMaestro.Core.dll", "Microsoft.Windows.SDK.NET.dll", "WinRT.Runtime.dll", "LICENSE", "THIRD-PARTY-NOTICES.txt")) {
        $entry = $archive.Entries | Where-Object FullName -in @($filename, "HIDMaestroTest\$filename", "HIDMaestroTest/$filename") |
            Select-Object -First 1
        if ($null -eq $entry) {
            throw "The verified HIDMaestro package did not contain $filename."
        }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $DependencyRoot $filename), $true)
    }
}
finally {
    $archive.Dispose()
}
$CoreDll = Join-Path $DependencyRoot "HIDMaestro.Core.dll"
if ((Get-HashText $CoreDll "SHA256") -ne $HidMaestroCoreSha256) {
    throw "The HIDMaestro core assembly did not match its pinned integrity hash."
}

if ((Test-Path -LiteralPath $OutputDirectory) -and -not $Clean) {
    throw "Controller output already exists. Use -Clean to rebuild a generated directory under build."
}

$env:DOTNET_MULTILEVEL_LOOKUP = "0"
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = "1"
$resourceMap = Join-Path $ProjectRoot "controller_host\hidmaestro_integration\EmbeddedResources.props"
$resources = Join-Path $DependencyRoot "resources"
& $Dotnet run --file (Join-Path $PSScriptRoot "restore_controller_resources.cs") -- $CoreDll $resourceMap $resources
if ($LASTEXITCODE -ne 0) {
    throw "The required resources could not be restored from the verified HIDMaestro assembly."
}
$PublishDirectory = Join-Path $BuildRoot "controller-host-$([Guid]::NewGuid().ToString('N'))"
try {
    & $Dotnet publish $HostProject `
        --configuration Release `
        --runtime win-x64 `
        --self-contained true `
        --output $PublishDirectory `
        "-p:HIDMaestroDependencyRoot=$DependencyRoot" `
        "-p:PublishReadyToRun=false"
    if ($LASTEXITCODE -ne 0) {
        throw "The Xbox One-compatible controller host failed to build."
    }

    Get-ChildItem -LiteralPath $PublishDirectory -Filter "*.pdb" -File | Remove-Item -Force
    Copy-Item -LiteralPath (Join-Path $DotnetRoot "LICENSE.txt") -Destination (Join-Path $PublishDirectory "DOTNET-LICENSE.txt")
    Copy-Item -LiteralPath (Join-Path $DotnetRoot "ThirdPartyNotices.txt") -Destination (Join-Path $PublishDirectory "DOTNET-THIRD-PARTY-NOTICES.txt")
    foreach ($notice in @("LICENSE", "THIRD-PARTY-NOTICES.txt")) {
        $source = Join-Path $DependencyRoot $notice
        if (Test-Path -LiteralPath $source) {
            $name = if ($notice -eq "LICENSE") { "HIDMAESTRO-LICENSE.txt" } else { "HIDMAESTRO-$notice" }
            Copy-Item -LiteralPath $source -Destination (Join-Path $PublishDirectory $name) -Force
        }
    }

    $HostExecutable = Join-Path $PublishDirectory "VaporLockControllerHost.exe"
    if (-not (Test-Path -LiteralPath $HostExecutable)) {
        throw "The controller host executable is missing after publish."
    }
    & $HostExecutable --self-test
    if ($LASTEXITCODE -ne 0) {
        throw "The controller host failed its pre-package self-test."
    }

    $files = @(Get-ChildItem -LiteralPath $PublishDirectory -Recurse -File | ForEach-Object {
        @{
            path = $_.FullName.Substring($PublishDirectory.Length + 1).Replace('\', '/')
            sha256 = (Get-HashText $_.FullName "SHA256").ToLowerInvariant()
        }
    })
    $receipt = @{
        schema_version = 1
        version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "product_version.txt") -Raw).Trim()
        files = $files
    } | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText((Join-Path $PublishDirectory "CONTROLLER_BUILD.json"), $receipt + [Environment]::NewLine)
    if (Test-Path -LiteralPath $OutputDirectory) {
        if (-not $Clean -or -not (Test-Path -LiteralPath (Join-Path $OutputDirectory "CONTROLLER_BUILD.json"))) {
            throw "The controller builder will only replace a previous verified controller build."
        }
        Remove-GeneratedDirectory $OutputDirectory
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $OutputDirectory) -Force | Out-Null
    Move-Item -LiteralPath $PublishDirectory -Destination $OutputDirectory
}
finally {
    if (Test-Path -LiteralPath $PublishDirectory) { Remove-GeneratedDirectory $PublishDirectory }
}

Write-Host "Xbox One-compatible controller host is ready at $OutputDirectory"
