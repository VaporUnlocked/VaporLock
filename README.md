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
profiles, datasets or external account credentials are included. The client
creates new user settings; existing private settings are not build seeds.

## Build from source

Requires Windows x64, Python 3.12 x64, CMake 3.24+, and an installed Visual Studio
C++20 toolchain with a Windows SDK. Dependencies are downloaded from their own
publishers only when you choose installation or a feature's setup action.
Use a short checkout path such as `C:\src\VaporLock`: Qt's dependency files can
exceed Windows path-length limits in deeply nested checkout/virtualenv folders.

```powershell
.\BuildCommunity.ps1 -InstallDependencies -CloudCPU -CloudDML
.\RunGui.bat
```

That command prepares source dependencies and compiles available native targets.
To prepare Python without compiling C++, add `-SkipNativeBuild`. A native engine
without an ONNX SDK can exercise its synthetic testing path but cannot perform
real ONNX inference. Supply the ONNX Runtime/DirectML SDKs explicitly to build
those providers; there are no SDK binaries in this source export:

```powershell
.\.venv\Scripts\python.exe tools\build_public_client.py --onnx-root C:\SDKs\onnxruntime --dml-root C:\SDKs\directml
```

The CMake cache variables `ONNXRUNTIME_ROOT`, `ONNXRUNTIME_DML_ROOT`,
`VAPORLOCK_TENSORRT_RTX_ROOT`, `CUDAToolkit_ROOT` and
`VAPORLOCK_CUDA_RUNTIME_ROOT` configure external SDKs. TensorRT RTX needs its
compatible CUDA toolkit, compiler and supported NVIDIA GPU. Provider support
is retained in source; a CPU-only build does not certify GPU execution.

`.cloud` worker dependencies are isolated in `.venv-cloud-cpu`,
`.venv-cloud-dml` and `.venv-cloud-cuda`. CUDA users must select a compatible
official PyTorch CUDA build and driver. Do not mix CPU and CUDA/DML runtime DLLs
in one folder. Ultralytics and other optional training integrations have their
own setup and license requirements.

After editing `.cloud` implementation files or UI browser resources, run:

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
