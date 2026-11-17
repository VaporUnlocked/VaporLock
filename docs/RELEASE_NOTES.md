# VaporLock Community release notes

## 2.0.2

- **Capture > Source & rate > Window** captures a selected app at its native
  dimensions or with a centered native crop. Capture follows the window when
  moved, pauses when minimized, and stops when the window closes.
- **Capture > Preview > Capture privacy masks** covers selected areas with
  opaque rectangles before preview, inference, or dataset capture. Masks
  are saved with each profile and use coordinates relative to the final
  frame. Review mask placement whenever you change the source or crop.
- **Model Library > Runtime repair** checks the shipped native engines and
  elevated mouse helper against the installed manifest. Choose the matching
  Community Windows ZIP to restore missing or changed files.

- **Movement > Transport** offers **Elevate mouse output** alongside Relative
  Mouse. Windows asks for administrator approval for a movement-only helper.
  Prepare it before starting capture. Approval expires when the session stops
  or when the helper waits two minutes for a session to connect.
- Administrator mouse output checks the selected native engine for matching
  protocol support before requesting approval or starting capture.
- Elevated Relative Mouse reads the configured activation, compensation and
  quick-release controls through its helper. Movement Diagnostics shows
  whether the activation input is held, released or unavailable.

## 2.0.1

- Target assistance slows as it approaches the measured aim point, and relative
  steps stay within the remaining correction. Automatic scene compensation
  pauses during target assistance when set to automatic activation. New default profiles start with
  compensation disabled.
- Use **Targeting > Aim point** for the detection-box preset, offsets, and visual
  picker. The transparent skeleton shows the detection box, aim point, and
  targeting zones at one screen pixel per capture pixel. Turn on **Fit whole
  capture** to see the full capture area. **Targeting > Class rules** contains
  per-class overrides.
- **Capture** owns source, rate, performance, and preview controls. **Movement**
  owns response, calibration, and motion compensation. Live desktop video is
  available from Capture and Overview and fits the source frame's aspect ratio.
- **Dataset health** is available from Training Data and .cloud Studio. Check for
  duplicates and split leakage, missing labels or reviews, tiny targets, and
  underrepresented classes before training.
- **Movement > Control > Quick release** pauses aim assist, motion compensation,
  and snap actions while held. Mouse Button 5 is the default.
- **Training Data > Review > Analyze review queue** moves uncertain detections,
  disagreements between two ONNX models, and rare classes up the review queue.
- **.cloud Studio > Results** shows recall and mean matched box overlap for
  separate target sizes and visibility groups. Tag visibility in **Annotate**.
- **Capture > Source & rate** supports Windows video capture devices such as
  capture cards and cameras. Use the full native frame or the centered crop.
  Local video files play through inference and preview in Observe mode.
- The optional Xbox-compatible controller host builds from the public source
  and can be included in the Windows portable package. First-time driver setup
  uses a matching Windows SDK and Windows Driver Kit in the default location.
- **Movement > Diagnostics** reports Windows mouse and virtual mouse output
  rejections. The virtual mouse reconnects after a rejected report. Both
  transports retry from fresh observations after a short pause.
- The main footer includes a Donate link.

## 2.0.0

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
