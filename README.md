# VaporLock

VaporLock is a Windows vision, tracking and annotation application with a C++20
native engine and a Qt/Python interface. This is an unfinished community source
snapshot of the 0.2.1 work, provided as-is. It is not a newly validated binary
release. Publisher licensing, accounts, paid-feature gates, telemetry, forced
updates, hosted model catalogs and installer distribution are retired.
All former client capabilities are available without a VaporLock account or key.

Use only in lawful, authorized environments. Movement output changes input;
follow the rules of your environment. No ban avoidance, invisibility, model
accuracy, frame rate or compatibility is promised.

## License

Original VaporLock material is provided under [LICENSE](LICENSE): free use,
study, modification and sharing for noncommercial purposes. Selling the code,
paid forks or otherwise monetizing it or derivatives is not permitted. Because
of that condition this is **source-available**, not OSI-defined open source.
Third-party code and assets retain their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Optional Ultralytics
integrations need a license compatibility review before combined redistribution.
A noncommercial label cannot remove AGPL obligations.

## Included capabilities

- DXGI/GDI capture; ONNX CPU, DirectML and CUDA backend source; TensorRT RTX source.
- Local `.cloud` inference, project/annotation/training/results workspace and
  optional SAM mask assistance.
- Tracking, target selection, exclusion zones, movement/output configuration,
  overlays, diagnostics, themes, games and personal profile workflows.
- Local annotation/training and explicit optional Ultralytics integration.

Hardware/backend dependencies determine what can run. No model weights, private
profiles, datasets or external account credentials are included. On first launch,
the bundled default uses DirectML and looks for `models\nanoparticle-003-c.onnx`.
Download it from [VaporLock Models](https://github.com/VaporUnlocked/VaporLock/releases/tag/Vaporlock-Models)
and place it in the `models` folder beside `RunGui.bat`. The default annotation
configuration also references `models\nanoparticle-003.onnx` from the same release.

## Build from source

Requires Windows x64, Python 3.12 x64, CMake 3.24+, and an installed Visual Studio
C++20 toolchain with a Windows SDK. Dependencies are downloaded from their own
publishers only when you choose installation or a feature's setup action.
Use a short checkout path such as `C:\src\VaporLock`: Qt's dependency files can
exceed Windows path-length limits in deeply nested checkout/virtualenv folders.

```powershell
.\BuildCommunity.ps1 -InstallDependencies -CloudDML -NativeDirectML
.\RunGui.bat
```

`BuildCommunity.ps1` restores the pinned ONNX Runtime 1.29.0 Windows SDK and
builds the native CPU/CUDA executable. CPU runs with its adjacent ONNX Runtime
DLL. Install or refresh CUDA from Model Library to use the NVIDIA provider
and its runtime libraries. The build reuses a verified CUDA download cache
when it already contains the matching ONNX Runtime package.

`-CloudDML` installs the `.cloud` Python worker. `-NativeDirectML` also builds
`build/windows-release/Release/dml/VaporLockDml.exe` from the pinned
Microsoft.ML.OnnxRuntime.DirectML and Microsoft.AI.DirectML packages. Use
`-SkipNativeBuild` to prepare Python only or `-SyntheticOnly` for a base
executable intended for test signals.

For an offline native build, point the build at the extracted SDKs:

```powershell
.\BuildCommunity.ps1 -OnnxRoot C:\SDKs\onnxruntime -DirectMlOrtRoot C:\SDKs\onnxruntime-directml -DirectMlRoot C:\SDKs\directml
```

The CMake cache variables `ONNXRUNTIME_ROOT`, `ONNXRUNTIME_DML_ROOT`, `VAPORLOCK_DIRECTML_ROOT`,
`VAPORLOCK_TENSORRT_RTX_ROOT`, `CUDAToolkit_ROOT` and
`VAPORLOCK_CUDA_RUNTIME_ROOT` configure external SDKs. To build TensorRT RTX,
use Model Library's TensorRT RTX setup to stage the SDK, then run the build
with its SDK path and a compatible local CUDA Toolkit:

```powershell
.\BuildCommunity.ps1 -TensorRtRoot .\build\dependencies\TensorRT-RTX-1.6.1.120 -CudaToolkit 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2'
```

Use the CUDA Toolkit path installed on your system. If you want DirectML in
the same build, also pass `-NativeDirectML` or the two extracted DirectML roots.

`.cloud` worker dependencies are isolated in `.venv-cloud-cpu`,
`.venv-cloud-dml` and `.venv-cloud-cuda`. CUDA users must select a compatible
official PyTorch CUDA build and driver. Do not mix CPU and CUDA/DML runtime DLLs
in one folder. Ultralytics and other optional training integrations have their
own setup and license requirements.

After editing `.cloud` implementation files or UI browser resources, pass
`-RefreshManifests` to `BuildCommunity.ps1` or run:

```powershell
.\.venv\Scripts\python.exe tools\refresh_public_manifests.py
```

To obtain the optional SAM mask-assistance checkpoint from its original
publisher (not included in this source archive), run
`.\.venv\Scripts\python.exe tools\fetch_sam_weights.py` after reviewing
`assets/cloud-annotation/LICENSE.txt`. The helper verifies the expected size
and SHA-256, and never loads the downloaded checkpoint.

The refresh command regenerates local safety inventories. No original publisher key or
historical official-build authorization is required for your changes.

## Optional components and known build gaps

The Xbox-compatible controller integration retains normal Windows signing,
consent, trust verification and owned removal safeguards. Its source references
external HIDMaestro and Windows tooling resources. Those binaries are excluded
from this source export; the existing dependency fetch script does not reproduce
every embedded resource. The controller component is not a turnkey clean-source
build until those inputs are supplied under their upstream licenses. Do not
disable Windows protections or claim an install succeeded when resources are absent.

The aim-testing-suite source is included separately. The community suite uses
the retained CC0 SWAT built-in character and original procedural view geometry;
the former game-derived character/weapon asset is excluded. Custom user GLBs
remain supported. A missing optional controller component does not prevent the
main client opening; affected controller operations report missing dependencies.

This snapshot is not an installer or hosted service. Administrator, key bot,
licensing server, web/API, release catalog and publishing tools are not part of
it. Never upload the original workspace, its Git history, private backups,
operational records or credentials. Use the separately verified public export.
No public hosting or support service is promised.
