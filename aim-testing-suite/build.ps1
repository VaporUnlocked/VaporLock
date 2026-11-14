param([string]$ExecutableName = 'VaporLock Proving Grounds', [int]$Parallel = 2, [string]$CharacterAsset)
$ErrorActionPreference = 'Stop'
if ($Parallel -lt 1 -or $Parallel -gt 32) { throw 'Parallel must be between 1 and 32.' }
$suiteRoot = $PSScriptRoot
# Install CMake 3.25+ and Visual Studio C++ Desktop Build Tools first.
# CMake selects the installed Visual Studio generator; no private tool paths.
$configure = @('-S',$suiteRoot,'-B',(Join-Path $suiteRoot 'build'),'-A','x64',"-DSUITE_EXECUTABLE_NAME=$ExecutableName")
if ($CharacterAsset) { $configure += "-DSUITE_CHARACTER_ASSET=$((Resolve-Path -LiteralPath $CharacterAsset).Path)" }
& cmake @configure
if ($LASTEXITCODE) { throw 'Suite configuration failed.' }
& cmake --build (Join-Path $suiteRoot 'build') --config Release --parallel $Parallel
if ($LASTEXITCODE) { throw 'Suite build failed.' }
& ctest --test-dir (Join-Path $suiteRoot 'build') -C Release --output-on-failure
if ($LASTEXITCODE) { throw 'Suite tests failed.' }
