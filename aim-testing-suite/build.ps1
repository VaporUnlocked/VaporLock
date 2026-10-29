param([string]$ExecutableName = 'Aim Testing Suite', [int]$Parallel = 2)
$ErrorActionPreference = 'Stop'
if ($Parallel -lt 1 -or $Parallel -gt 32) { throw 'Parallel must be between 1 and 32.' }
$suiteRoot = $PSScriptRoot
# Install CMake 3.25+ and Visual Studio C++ Desktop Build Tools first.
# CMake selects the installed Visual Studio generator; no private tool paths.
& cmake -S $suiteRoot -B (Join-Path $suiteRoot 'build') -A x64 "-DSUITE_EXECUTABLE_NAME=$ExecutableName"
if ($LASTEXITCODE) { throw 'Suite configuration failed.' }
& cmake --build (Join-Path $suiteRoot 'build') --config Release --parallel $Parallel
if ($LASTEXITCODE) { throw 'Suite build failed.' }
& ctest --test-dir (Join-Path $suiteRoot 'build') -C Release --output-on-failure
if ($LASTEXITCODE) { throw 'Suite tests failed.' }