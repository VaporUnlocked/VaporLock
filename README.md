# VaporLock

VaporLock is a Windows vision, tracking and annotation application with a C++20
engine and a native Qt desktop interface. Community builds run without a
VaporLock account or license key. Older releases remain available on the
[releases page](https://github.com/VaporUnlocked/VaporLock/releases).

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

## Desktop workflow

- **Capture** puts the model, inference backend, display and frame rate next to
  the preview. Start in Observe only to run capture with output disabled.
- **Workspace** reviews YOLO images, suggests boxes with a local ONNX model,
  and saves negative labels. It also creates `.cloud` projects, imports and
  reviews annotations, and runs fresh, initialized, or resumed training.
- **Settings** groups the full profile settings by task. Search covers all
  groups. Open legacy JSON profiles and use Save as to make a separate copy.
  Capture settings take effect at the next session start.
- **Diagnostics** shows the selected backend, process messages and any global
  shortcuts that are available only while the window has focus.

DXGI/GDI capture, native CPU, DirectML, CUDA and TensorRT RTX backends depend
on the installed SDKs and hardware. Model weights and datasets are user inputs.

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

`-CloudDML` installs the `.cloud` Python worker. `-NativeDirectML` restores
the pinned Microsoft.ML.OnnxRuntime.DirectML and
Microsoft.AI.DirectML NuGet packages into `build/dependencies` and produces
`build/windows-release/Release/dml/VaporLockDml.exe` with its matching DLLs.
To prepare Python without compiling C++, use `-SkipNativeBuild` in place of
`-NativeDirectML`. A native engine built without an ONNX SDK can exercise its
synthetic testing path. For real ONNX inference, choose a configured backend.
For an offline build, supply both extracted NuGet packages:

```powershell
.\.venv\Scripts\python.exe tools\build_public_client.py --dml-root C:\SDKs\onnxruntime-directml --directml-root C:\SDKs\directml
```

Use `--onnx-root` for the separate native CPU/CUDA ONNX Runtime SDK.
The CMake cache variables `ONNXRUNTIME_ROOT`, `ONNXRUNTIME_DML_ROOT`, `VAPORLOCK_DIRECTML_ROOT`,
`VAPORLOCK_TENSORRT_RTX_ROOT`, `CUDAToolkit_ROOT` and
`VAPORLOCK_CUDA_RUNTIME_ROOT` configure external SDKs. TensorRT RTX needs its
compatible CUDA toolkit, compiler and supported NVIDIA GPU. Provider support
is retained in source; a CPU-only build does not certify GPU execution.

`.cloud` worker dependencies are isolated in `.venv-cloud-cpu`,
`.venv-cloud-dml` and `.venv-cloud-cuda`. CUDA users must select a compatible
official PyTorch CUDA build and driver. Do not mix CPU and CUDA/DML runtime DLLs
in one folder. Ultralytics and other optional training integrations have their
own setup and license requirements.

After editing `.cloud` implementation files, refresh the worker source manifest:

```powershell
.\.venv\Scripts\python.exe tools\refresh_public_manifests.py
```

To obtain the optional SAM mask-assistance checkpoint from its original
publisher (not included in this source archive), run
`.\.venv\Scripts\python.exe tools\fetch_sam_weights.py` after reviewing
`assets/cloud-annotation/LICENSE.txt`. The helper verifies the expected size
and SHA-256, and never loads the downloaded checkpoint.

The manifest records hashes for the local `.cloud` worker source.

The desktop currently exposes local dataset and `.cloud` workflows. The
optional Ultralytics worker is available from its command line with
`.\.venv\Scripts\python.exe ui\training_worker.py --help`. The legacy
local API, hosted dataset screens, and Xbox bridge launcher are not wired
into the replacement desktop yet.

## Optional components and known build gaps

The optional Xbox-compatible controller component references upstream
HIDMaestro and Windows tooling resources. Building it requires those inputs
under their upstream licenses. The desktop's other capture and output paths
can be built independently.

The aim-testing-suite source is included separately. The community suite uses
the retained CC0 SWAT built-in character and original procedural view geometry;
the former game-derived character/weapon asset is excluded. Custom user GLBs
remain supported. A missing optional controller component does not prevent the
main client opening; affected controller operations report missing dependencies.
