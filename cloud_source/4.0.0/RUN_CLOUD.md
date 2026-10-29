# 1. Run a `.cloud`

**Cloud Studio 4.0.0 / current S2 architecture: `cloudvision_s2_v1`.** The required runtime source is included in `runtime_sdk/cloud_sdk/`. Commands below run from this ZIP's extracted root, using Python 3.11–3.14.

## Command line

```powershell
python -m pip install -r requirements-runtime.txt
python run_cloud.py C:/models/player.cloud C:/frames/frame.png --backend numpy --device cpu
```

For NVIDIA GPU execution, install a CUDA-enabled `torch>=2.6,<3` build in the same environment using the [PyTorch installer](https://pytorch.org/get-started/locally/), then run:

```powershell
python run_cloud.py C:/models/player.cloud C:/frames/frame.png --backend torch --device cuda --confidence 0.35
```

The command prints detection JSON. Supply your trained S2 `.cloud`; this ZIP contains the implementation, not pretrained player weights.

## Python API

```python
from pathlib import Path
import sys
from PIL import Image

# Set this to the extracted package location when integrating elsewhere.
PACKAGE = Path.cwd()
sys.path.insert(0, str(PACKAGE / "runtime_sdk"))
from cloud_sdk import CloudRuntime, MASK_ARCHITECTURE, decode_mask

runtime = CloudRuntime("C:/models/player.cloud", backend="torch", device="cuda")
assert runtime.config.architecture == MASK_ARCHITECTURE

with Image.open("C:/frames/frame.png") as image:
    result = runtime.predict(image, confidence=0.35, nms_iou=0.5,
                             max_detections=32, debug=True)

for detection in result["detections"]:
    box = detection["primary_box"]          # [left, top, right, bottom]
    score = detection["confidence"]
    crop = decode_mask(detection["mask"], full=False)  # Boolean foreground
    x, y = detection["mask"]["origin"]     # Place crop at source-image (x, y)
```

For live frames, construct `CloudRuntime` once and call `predict` repeatedly. It accepts a Pillow image; use `Image.fromarray(rgb_uint8)` for RGB capture arrays and convert BGR/BGRA captures to RGB first.

Outputs use the oriented source image's pixel coordinates. `debug=True` supplies the main boxes. `mask` is the exact emitted encasing; `wrapper` is an approximate preview. Empty `detections` is valid. The model is single-class: `0 = player`.

Source/API reference: [runtime.py](runtime_sdk/cloud_sdk/runtime.py), [masks.py](runtime_sdk/cloud_sdk/masks.py), [format.py](runtime_sdk/cloud_sdk/format.py).
