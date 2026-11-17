# VaporLock

VaporLock is a Windows vision, tracking and annotation application with a C++20
native engine and a Qt/Python interface. VaporLock 2.0.2 Community includes local
capture and inference, a dedicated .cloud Studio, and VaporLock Proving Grounds.
The Community client runs without a VaporLock account or key.

[Release notes and download guide](docs/RELEASE_NOTES.md) · [Donate](https://vaporunlocked.github.io/donations.github.io/)

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
- Local `.cloud` inference and a dedicated **.cloud Studio** tab for desktop
  capture, image and video imports, YOLO label drafts, optional SAM mask
  assistance, S1/S2 training recipes, checkpoints, and model evaluation.
- Tracking, target selection, exclusion zones, movement/output configuration,
  overlays, diagnostics, themes, games and personal profile workflows.
- Local annotation/training and explicit optional Ultralytics integration.

Hardware and backend dependencies determine what can run. The client ZIP
includes the SAM checkpoint for mask assistance. Detection models, saved
profiles and datasets can be added by the user. On first launch,
the bundled default uses DirectML and looks for `models\nanoparticle-003-c.onnx`.
Download it from [VaporLock Models](https://github.com/VaporUnlocked/VaporLock/releases/tag/Vaporlock-Models)
and place it in the `models` folder beside `VaporLock.exe` in the portable build
or `RunGui.bat` in a source checkout. The default annotation
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

Capture and inference FPS settings are independent ceilings. The sliders cover
common rates and the numeric fields accept higher values. Status & Diagnostics
shows the configured limits, fresh captures, completed inferences, and desktop
updates reported by DXGI. The source, model, and hardware determine the actual
rate.

CUDA Graph replay uses persistent GPU buffers for compatible ONNX models.
The engine can use standard CUDA inference when replay is unavailable, and
the CUDA Graph replay setting controls whether the engine attempts replay.
Preparing replay can add model startup time.

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
`.venv-cloud-dml` and `.venv-cloud-cuda`. Run `.\BuildCommunity.ps1 -CloudCPU -SkipNativeBuild`
to add CPU training to a source checkout, or use `-CloudCUDA` to add NVIDIA CUDA
training. Automatic training uses the CUDA runtime when installed and can train
on CPU with the CPU runtime. CUDA users need a compatible official PyTorch build
and driver. Do not mix CPU and CUDA/DML runtime DLLs
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

## Optional components

From a source checkout, run `.\tools\build_controller_host.ps1` to build the
Xbox One/Series-compatible controller host. The script verifies the pinned
HIDMaestro package, restores the bridge resources, and runs the driver-free
self-test. VaporLock finds the host in `build/controller-host`, and the release
packager includes a verified build from that location when present.

For first-time controller setup or a repair that needs to regenerate the driver
packages, install a matching [Windows SDK and Windows Driver Kit](https://learn.microsoft.com/en-us/windows-hardware/drivers/download-the-wdk)
in the default location. Then open **Movement > Transport**, select the
Xbox One-compatible bridge, and choose **Install controller component**.
Setup uses SignTool and Inf2Cat from the installed kits and asks for consent
before adding the local controller signing certificate to the machine trust stores.

VaporLock Proving Grounds source is included in `aim-testing-suite`. Build it
with that folder's `build.ps1`, then open **Proving Grounds** in the Control
Center. Industrial Yard includes three combat modes and eight weapons, and the
firing range retains its four drills. Proving Grounds uses
the retained CC0 SWAT built-in character and original procedural view geometry;
the former game-derived character/weapon asset is excluded. Custom user GLBs
remain supported. A missing optional controller component does not prevent the
main client opening; affected controller operations report missing dependencies.

Use the portable ZIP or build from the public source. Keep the original
workspace, private Git history, backups and operational records local.
Use the separately verified public export for publication.
