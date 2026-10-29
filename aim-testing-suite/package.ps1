param([switch]$SkipBuild, [string]$SourceExecutable = 'Aim Testing Suite.exe')
$ErrorActionPreference = 'Stop'
$suiteRoot = $PSScriptRoot
if ([IO.Path]::GetFileName($SourceExecutable) -ne $SourceExecutable -or !$SourceExecutable.EndsWith('.exe', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Provide only the local executable filename.'
}
if (!$SkipBuild) {
    if ($SourceExecutable -ne 'Aim Testing Suite.exe') { throw 'A staged executable requires -SkipBuild.' }
    & (Join-Path $suiteRoot 'build.ps1')
}
$source = Join-Path $suiteRoot $SourceExecutable
if (!(Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Build the executable before packaging.' }
$release = Join-Path $suiteRoot 'release'
if (Test-Path -LiteralPath $release) {
    if ((Get-Item -LiteralPath $release -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Release location must not be linked.' }
} else { New-Item -ItemType Directory -Path $release | Out-Null }
$destination = Join-Path $release 'Aim Testing Suite.exe'
if ((Test-Path -LiteralPath $destination) -and ((Get-Item -LiteralPath $destination -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Release executable must not be linked.' }
Copy-Item -LiteralPath $source -Destination $destination -Force
$sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
if ($sourceHash -ne (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash) { throw 'Package copy did not match the build.' }
$details = Get-Item -LiteralPath $destination
$manifest = [ordered]@{
    executable = $details.Name
    version = $details.VersionInfo.ProductVersion
    sha256 = $sourceHash
    bytes = $details.Length
    platform = 'Windows x64'
    distribution_files = @('Aim Testing Suite.exe')
    embedded = @('SWAT.glb', 'Interface.ttf', 'licenses and portable guides', 'VL icon')
    creates_models_beside_executable = $true
    custom_model_format = '.glb'
    requires_runtime_installer = $false
    requires_asset_download = $false
}
$reports = Join-Path $suiteRoot 'reports'
if (!(Test-Path -LiteralPath $reports)) { New-Item -ItemType Directory -Path $reports | Out-Null }
if ((Get-Item -LiteralPath $reports -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reports location must not be linked.' }
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $reports 'portable-package.json') -Encoding utf8
Write-Output "Portable EXE ready: $($details.Name), $($details.Length) bytes, SHA-256 $sourceHash"
