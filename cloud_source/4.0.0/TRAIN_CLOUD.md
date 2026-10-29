# 2. Train a `.cloud`

**Use the current S2 Player recipe in `config/s2_player.json`.** The actual 4.0.0 training source is included in `training_source/cloudstudio/`. Commands below run from the extracted ZIP root.

## Input

The trainer takes a **Cloud project folder**, or its `project.json`. Keep its image files and SQLite database together. A YOLO YAML must first be imported into a Cloud project and its boxes given encasing masks.

- Each reviewed positive image needs every target labeled with `class_id: 0`, a pixel-coordinate `box: [left, top, right, bottom]`, and its instance `mask` in `cloud-rle-v1` format.
- A reviewed image with `annotations: []` is a negative. Unreviewed images are excluded.
- Provide reviewed training and validation images, with separate recording/session groups across splits.

Project/label API: [data.py](training_source/cloudstudio/data.py). Mask encoder: [masks.py](training_source/cloudstudio/masks.py).

## Fresh training

```powershell
python -m pip install -r requirements-training.txt
python train_cloud.py C:/datasets/players --output C:/runs/player-s2-001 --config config/s2_player.json --from-scratch --device cuda:0
```

Use `--device cpu` for CPU. CUDA needs a compatible CUDA-enabled Torch installation. The launcher explicitly selects `cloudvision_s2_v1`; fresh training starts with random weights.

The supplied recipe uses **300 epochs, batch 8, effective batch target 64, input 640, 96 channels, 64×64 masks, AdamW, learning rate 0.001, cosine scheduling and patience 75**. Change supported settings in the JSON or override flags, such as `--batch-size -1` for automatic batch sizing. `python train_cloud.py --help` lists the implemented controls.

## Python API

```python
from pathlib import Path
import sys

PACKAGE = Path.cwd()  # Extracted ZIP root; set explicitly in your application.
sys.path.insert(0, str(PACKAGE / "training_source"))
from cloudstudio.training import TrainingConfig, train
from cloudstudio.training_settings import resolve_settings

config = TrainingConfig(**resolve_settings("player", overrides={
    "architecture": "cloudvision_s2_v1",
    "device": "cuda:0",
    "init_model_path": None,
    "resume_path": None,
}))

result = train("C:/datasets/players", "C:/runs/player-s2-001", config,
               progress=lambda event: print(event, flush=True),
               should_stop=lambda: False)
print(result["best_cloud"], result["last_cloud"], result["checkpoint"])
```

Use `resolve_settings("player")` as shown: bare `TrainingConfig()` retains historical defaults. The API is synchronous and exposes progress and cancellation callbacks.

## Outputs and continuation

`best.cloud` is the validation-selected deployment model; `last.cloud` is the latest model. Both contain learned tensors and their required descriptor. Settings, metrics and optimizer/resume state remain separate in the run folder.

Resume the same experiment from **`.cloudtrain`**, letting its original recipe restore:

```powershell
python train_cloud.py C:/datasets/players --output C:/runs/player-s2-001 --resume C:/runs/player-s2-001/last.cloudtrain
```

Start a new experiment initialized from an existing S2 model, with a new optimizer:

```powershell
python train_cloud.py C:/datasets/players --output C:/runs/player-s2-002 --init-model C:/runs/player-s2-001/best.cloud --device cuda:0
```

That command inherits the parent's model dimensions. Resume requires compatible settings and unchanged dataset/labels/splits. Normal inference only needs the selected `.cloud` and runtime.

Source/API reference: [training.py](training_source/cloudstudio/training.py), [training_settings.py](training_source/cloudstudio/training_settings.py), [S2 Player recipe](config/s2_player.json).
