[CmdletBinding()]
param([switch]$InstallDependencies, [switch]$CloudCPU, [switch]$CloudDML, [switch]$CloudCUDA, [switch]$SkipNativeBuild,
      [ValidatePattern('^https://download\.pytorch\.org/whl/cu[0-9]+$')][string]$TorchCUDAIndex = 'https://download.pytorch.org/whl/cu130')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
function Checked([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Build command failed: $exe" }
}
if ($InstallDependencies) {
    if (-not (Test-Path -LiteralPath $python)) { Checked 'py.exe' @('-3.12','-m','venv',(Join-Path $root '.venv')) }
    Checked $python @('-I','-m','pip','install','-r',(Join-Path $root 'requirements-community.txt'))
}
if (-not (Test-Path -LiteralPath $python)) { throw 'Run with -InstallDependencies after installing Python 3.12 x64.' }
foreach ($kind in @('cpu','dml','cuda')) {
    $requested = ($kind -eq 'cpu' -and ($CloudCPU -or $CloudDML -or $CloudCUDA)) -or ($kind -eq 'dml' -and $CloudDML) -or ($kind -eq 'cuda' -and $CloudCUDA)
    if (-not $requested) { continue }
    $venv = Join-Path $root ('.venv-cloud-' + $kind)
    $workerPython = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $workerPython)) { Checked $python @('-I','-m','venv',$venv) }
    Checked $workerPython @('-I','-m','pip','install','numpy>=1.26,<3','Pillow>=10,<14','PyYAML>=6,<7','packaging','onnx','opencv-python-headless')
    if ($kind -eq 'dml') { Checked $workerPython @('-I','-m','pip','install','onnxruntime-directml') }
    elseif ($kind -eq 'cpu') {
        Checked $workerPython @('-I','-m','pip','install','torch','torchvision','--index-url','https://download.pytorch.org/whl/cpu')
        Checked $workerPython @('-I','-m','pip','install','onnxruntime')
    } else { Checked $workerPython @('-I','-m','pip','install','torch','torchvision','--index-url',$TorchCUDAIndex) }
}
Checked $python @('-I','-B',(Join-Path $root 'tools\refresh_public_manifests.py'))
if (-not $SkipNativeBuild) { Checked $python @('-I','-B',(Join-Path $root 'tools\build_public_client.py')) }
Write-Host 'Source preparation finished. Run RunGui.bat. Optional driver/test-suite dependencies are documented in README.md.'
