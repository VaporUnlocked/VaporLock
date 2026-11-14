# VaporLock 2.0.0 Community

## What's new

- **Capture and preview.** The lossless preview can read native capture frames
  through shared memory. Capture and inference run independently, with separate
  rate limits and live diagnostics. Enable **Require 1:1 model input** for a
  compatible ONNX model to feed a model-sized native crop through the pipeline
  without resizing. A 640 × 640 model uses a 640 × 640 crop at native pixel
  density. The capture dimensions must match the model input.
- **.cloud Studio.** Import desktop captures, images, video and YOLO labels.
  Label manually or use SAM mask assistance. Manage dataset splits, train with
  S1 or S2 recipes, resume from checkpoints, evaluate on validation or test
  splits, inspect a prediction on one image, and export the ONNX graph pair.
- **VaporLock Proving Grounds.** Industrial Yard has eight weapons and three
  modes, Clear the Yard, Hold the Line, and Free Practice. The Firing Range
  keeps all four original drills. Open Proving Grounds from VaporLock or run
  the standalone Windows build.

## Downloads

| Asset | Use |
| --- | --- |
| Community Windows x64 ZIP | Portable VaporLock, including .cloud Studio and Proving Grounds |
| .cloud CUDA add-on ZIP | Optional .cloud runtime for compatible NVIDIA GPUs |
| Proving Grounds Windows x64 ZIP | Standalone yard and range with its own local settings |
| Community Source ZIP | Source, build files, tests and notices |
| SHA256SUMS.txt | SHA-256 hashes of the downloadable archives |

## Get started

1. Extract the Community ZIP into a fresh writable folder, then open
   `VaporLock/VaporLock.exe`. Windows x64 is required. Use CPU for a baseline or
   DirectML on a supported GPU with current drivers.
2. For detection, choose a compatible model in Model Library. The DirectML
   default expects `models/nanoparticle-003-c.onnx`. Download that model from
   [VaporLock Models](https://github.com/VaporUnlocked/VaporLock/releases/tag/Vaporlock-Models)
   or use your own compatible model. The default annotation configuration
   references `nanoparticle-003.onnx` from the same release.
3. To migrate, keep your previous installation as a backup and copy your own
   models and saved profiles into the new installation. Review the selected
   model, capture region and backend before starting a session.
4. For optional .cloud CUDA, extract the add-on into the same parent folder
   so the two `VaporLock` folders merge. Select CUDA or Automatic in .cloud
   Studio. Native ONNX CUDA setup is separate and available through Model
   Library. TensorRT RTX has its own GPU, driver and model requirements.

For standalone Proving Grounds, extract its ZIP and open
`VaporLock Proving Grounds.exe`. It requires Windows x64 and a working OpenGL
3.3-capable graphics driver. Custom character GLBs, reports and settings live
beside the executable.

The Windows virtual controller output host is excluded from the portable
client. See [LICENSE](../LICENSE) and [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)
for the Community source terms and bundled components.
