"""Shared, Torch-free training controls, presets, and strict JSON interchange.

The legacy values intentionally match TrainingConfig defaults for checkpoint
compatibility. The desktop and CLI select the explicit player preset for new
runs; neither the UI nor command help needs to import a training backend.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from pathlib import Path
from typing import Any

from .config import ARCHITECTURE, MASK_ARCHITECTURE, ModelConfig


@dataclass(frozen=True)
class TrainingSetting:
    name: str
    label: str
    group: str
    kind: str
    default: Any
    help: str
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1
    decimals: int = 3
    choices: tuple = ()
    widget_name: str | None = None

    def validate(self, value):
        if self.name == "device":
            if type(value) is not str or not re.fullmatch(r"auto|cpu|cuda(?::[0-9]+)?", value):
                raise ValueError("device must be auto, cpu, cuda, or cuda:N for a specific NVIDIA GPU.")
            return value
        if self.kind == "bool":
            if type(value) is not bool:
                raise ValueError(f"{self.name} must be true or false.")
        elif self.kind in ("int", "float"):
            if self.kind == "int" and type(value) is not int:
                raise ValueError(f"{self.name} must be an integer.")
            if self.kind == "float" and (type(value) not in (int, float) or not math.isfinite(value)):
                raise ValueError(f"{self.name} must be a finite number.")
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{self.name} must be at least {self.minimum:g}.")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"{self.name} must be at most {self.maximum:g}.")
        elif self.kind == "choice":
            valid = [item[1] if isinstance(item, tuple) else item for item in self.choices]
            if value not in valid or type(value) is not type(self.default):
                raise ValueError(f"{self.name} must be one of {valid}.")
        if self.name == "batch_size" and value == 0:
            raise ValueError("batch_size must be -1 for automatic selection, or a positive integer.")
        if self.name in ("adam_beta1", "adam_beta2") and value >= 1:
            raise ValueError(f"{self.name} must be less than 1.")
        return value


def _s(name, label, group, kind, default, help, minimum=None, maximum=None,
       step=1, decimals=3, choices=(), widget_name=None):
    return TrainingSetting(name, label, group, kind, default, help, minimum,
                           maximum, step, decimals, tuple(choices), widget_name)


SETTINGS = (
    _s("epochs", "Epochs", "Run and hardware", "int", 50,
       "Maximum full passes through the reviewed training images. More epochs allow more learning, but take longer and can overfit. Early stopping can end the run sooner.",
       1, 10000, widget_name="train_epochs"),
    _s("batch_size", "Images per batch", "Run and hardware", "int", 8,
       "Images processed together before accumulating gradients. Larger batches need more memory. -1 requests automatic sizing: CUDA uses a memory probe; CPU uses at most 4 images (limited by the available training images), and records that fallback. 0 is invalid.",
       -1, 256, widget_name="train_batch"),
    _s("nominal_batch_size", "Target effective batch", "Run and hardware", "int", 0,
       "Accumulates several image batches before an optimizer update, approximating this many images without keeping them all in memory. 0 updates after every batch. The actual batch and accumulation count are recorded in the run.",
       0, 65536),
    _s("device", "Training device", "Run and hardware", "choice", "auto",
       "Automatic uses NVIDIA CUDA when available and otherwise CPU. CUDA generally trains larger models faster. Selecting CUDA explicitly reports an error when it is unavailable. Enter cuda:0, cuda:1, and so on to choose a specific NVIDIA GPU.",
       choices=(("Automatic (CUDA if available)", "auto"), ("CPU", "cpu"), ("NVIDIA CUDA", "cuda")), widget_name="train_device"),
    _s("amp", "GPU mixed precision", "Run and hardware", "bool", True,
       "Uses supported reduced precision during CUDA training to lower memory use and often improve speed. CPU training stays FP32. It changes training arithmetic, not the FP32 tensor storage in the .cloud file.",
       widget_name="train_amp"),
    _s("cpu_threads", "CPU threads", "Run and hardware", "int", 4,
       "Maximum Torch CPU worker threads used by this training run. More threads are not always faster, especially with small models. Keep enough CPU capacity available for the rest of your computer.",
       1, 64, widget_name="train_threads"),
    _s("seed", "Random seed", "Run and hardware", "int", 42,
       "Controls initial random weights, image ordering, and repeatable training augmentations. The same seed helps compare settings fairly; reproducibility also depends on the device, software versions, and deterministic mode.",
       0, 4294967295, widget_name="train_seed"),
    _s("deterministic", "Deterministic operations", "Run and hardware", "bool", False,
       "Requests reproducible Torch operations and disables algorithm selection that varies between runs. It may reduce training speed or reject an operation without a deterministic implementation. It does not guarantee identical results across different hardware or software versions."),

    _s("architecture", "Model architecture", "Model and input", "choice", ARCHITECTURE,
       "S2 trains spatial foreground/background masks with holes and separate regions, using fine image features and nearby detector slots. S1 keeps the legacy fixed polygon model and compatible checkpoints. Changing architecture requires a new run from scratch.",
       choices=(("S2 spatial mask", MASK_ARCHITECTURE), ("S1 legacy polygon", ARCHITECTURE))),
    _s("input_size", "Input square (pixels)", "Model and input", "choice", 320,
       "Each image is resized with its aspect ratio preserved and padded to this square. Larger inputs preserve small-player detail but increase training memory and inference work. This is a trained model capacity and must match when improving an existing .cloud.",
       choices=range(64, 1025, 32), widget_name="train_input"),
    _s("vertices", "Encasing vertices", "Model and input", "choice", 32,
       "S1 legacy polygon only: number of learned points in each closed outline. More points can retain finer detail but enlarge the output head and add contour work. S2 learns a spatial mask instead and does not use this setting.",
       choices=(16, 32, 64), widget_name="train_vertices"),
    _s("channels", "Feature channels", "Model and input", "choice", 48,
       "Width of the shared image-feature network. The S2 Player recipe uses 96 channels to retain useful capacity while keeping its spatial head compact. 48 is an optional speed/memory tradeoff; smaller models may lose detection quality. Compare on held-out images.",
       choices=range(16, 129, 8), widget_name="train_channels"),
    _s("roi_size", "Encasing feature grid", "Model and input", "choice", 8,
       "S1 legacy polygon only: samples this many feature positions per axis in a guiding box. A larger grid adds spatial detail, weights and work. S2 derives its feature grid from the mask resolution, so this legacy control is inactive there.",
       choices=(("4 × 4", 4), ("8 × 8", 8), ("16 × 16", 16)), widget_name="train_roi"),
    _s("mask_size", "Mask resolution", "Model and input", "choice", 64,
       "S2 only: foreground/background output cells per axis inside each detected box. 64 means a 64×64 mask. Larger masks can preserve finer boundaries and holes but cost more per-target work and activation memory. The spatial head shares its weights across cells, so this does not multiply the stored parameters.",
       choices=(("32 × 32", 32), ("64 × 64", 64), ("128 × 128", 128))),

    _s("learning_rate", "Initial learning rate", "AdamW and learning rate", "float", .001,
       "AdamW step size after learning-rate warmup. Too large can make training unstable; too small can learn slowly. With cosine decay, this rate gradually falls toward initial rate × final factor.",
       1e-9, 1, .0001, 9, widget_name="train_lr"),
    _s("scheduler", "Learning-rate schedule", "AdamW and learning rate", "choice", "constant",
       "Constant keeps the learning rate fixed after warmup. Cosine smoothly lowers it across the planned run, often allowing larger early updates and smaller late refinements. This affects training only.",
       choices=(("Constant", "constant"), ("Cosine decay", "cosine"))),
    _s("lr_final_factor", "Final rate factor", "AdamW and learning rate", "float", .01,
       "Final cosine learning rate as a fraction of the initial rate. For initial 0.001 and factor 0.01, the final rate is 0.00001. Used only by the cosine schedule.",
       1e-6, 1, .01, 6),
    _s("lr_warmup_epochs", "Learning-rate warmup (epochs)", "AdamW and learning rate", "int", 0,
       "Ramps the optimizer learning rate up at the beginning of the run to reduce abrupt early updates. 0 disables this ramp. This is separate from teacher-box warmup, which controls the boxes guiding the encasing head.",
       0, 10000),
    _s("lr_warmup_start_factor", "Warmup starting factor", "AdamW and learning rate", "float", .1,
       "Starting learning rate as a fraction of the initial learning rate during optimizer warmup. A value of 0.1 starts at one tenth of the configured rate. Used only when learning-rate warmup is enabled.",
       1e-6, 1, .05, 6),
    _s("adam_beta1", "AdamW first-moment decay", "AdamW and learning rate", "float", .9,
       "How strongly AdamW averages recent gradient directions. A higher value smooths updates more but adapts more slowly to new gradients. This is the applicable AdamW counterpart to the momentum-like setting in your recipe.",
       0, .999999, .001, 6),
    _s("adam_beta2", "AdamW second-moment decay", "AdamW and learning rate", "float", .999,
       "How strongly AdamW averages squared gradients when scaling each parameter's update. Values close to 1 make that scaling change slowly. 0.999 is a common starting value.",
       0, .999999, .0001, 6),
    _s("adam_epsilon", "AdamW numerical epsilon", "AdamW and learning rate", "float", 1e-8,
       "Small number in AdamW's denominator that avoids division by zero. Usually leave this at 0.00000001; changing it can affect stability and the scale of updates for very small gradients.",
       1e-12, 1, 1e-8, 12),
    _s("weight_decay", "Weight decay", "AdamW and learning rate", "float", .0001,
       "Gradually shrinks weights to discourage unnecessarily large parameters and reduce overfitting. This is AdamW's decoupled weight decay. The player preset uses 0.0005.",
       0, 1, .0001, 7, widget_name="train_decay"),
    _s("weight_decay_bias", "Apply decay to biases", "AdamW and learning rate", "bool", True,
       "Also applies weight decay to bias parameters. The player preset leaves this off, using separate bias and weight parameter groups. Legacy mode keeps it on to preserve earlier training behavior."),
    _s("gradient_clip", "Gradient norm limit", "AdamW and learning rate", "float", 10.,
       "Limits the total gradient norm before each optimizer update to reduce extreme updates. It is applied after AMP unscaling and accumulation. A very low limit can slow learning.",
       .000001, 1000000, 1, 6),

    _s("objectness_weight", "Target-presence loss weight", "Box and encasing supervision", "float", 1.,
       "Scales the loss that learns whether a player center exists at each detector location. This model has one player class; it does not have a separate YOLO multiclass loss. This value is not interchangeable with YOLO's cls coefficient.",
       .000001, 1000, .1, 6),
    _s("focal_alpha", "Positive focal balance", "Box and encasing supervision", "float", .75,
       "Balances positive player-center examples against background in the presence loss. Positives use this weight and negatives use 1 minus this weight. Alter it cautiously and check false positives and missed players on validation images.",
       .000001, .999999, .05, 6),
    _s("focal_gamma", "Focal difficulty emphasis", "Box and encasing supervision", "float", 2.,
       "Reduces the influence of easy, already-correct presence predictions so harder examples matter more. 0 removes this difficulty weighting. Larger values do not automatically improve detection quality.",
       0, 10, .25, 5),
    _s("box_weight", "Box loss weight", "Box and encasing supervision", "float", 2.,
       "Scales the guiding-box loss, which combines box overlap and coordinate error. The box learns where the target is and guides feature sampling for the encasing. This loss has a different scale from YOLO's box or DFL losses.",
       .000001, 1000, .25, 6, widget_name="train_box_weight"),
    _s("contour_weight", "Encasing loss weight", "Box and encasing supervision", "float", 3.,
       "Scales the positive encasing objective: foreground/background silhouette loss for S2, or cyclic polygon loss for S1. Excessive weight can compete with detection learning. Separate background-ROI examples use their own weight below.",
       .000001, 1000, .25, 6, widget_name="train_contour_weight"),
    _s("contour_edge_weight", "Encasing edge emphasis", "Box and encasing supervision", "float", .15,
       "S1 legacy polygon only: contribution of edge error alongside point-position error. It encourages the local edge directions and lengths to match the annotation. 0 uses point error alone. S2 uses the mask boundary emphasis below instead.",
       0, 1000, .05, 6),
    _s("warmup_epochs", "Teacher-box warmup (epochs)", "Box and encasing supervision", "int", 3,
       "Initially guides the encasing head with reviewed ground-truth boxes while the detector is learning. After these epochs, guidance transitions toward predicted boxes. This does not warm up the optimizer learning rate.",
       0, 10000, widget_name="train_warmup"),
    _s("guidance_transition_epochs", "Predicted-box transition (epochs)", "Box and encasing supervision", "int", 5,
       "Number of epochs used to move from teacher boxes to the detector's own boxes after teacher warmup. The encasing head then trains under conditions closer to inference. Validation always uses predicted boxes.",
       1, 10000),

    _s("mask_bce_weight", "Foreground/background loss", "S2 mask supervision", "float", 1.,
       "S2 only: binary cross-entropy weight for every mask cell. Annotated foreground is positive; background around the silhouette, inside holes, and between separate pieces is negative. The whole bounding rectangle is never labeled foreground. This weight must stay positive.",
       .000001, 1000, .1, 6),
    _s("mask_dice_weight", "Mask overlap loss", "S2 mask supervision", "float", 1.,
       "S2 only: soft Dice overlap weight alongside pixel classification. It encourages the foreground shape to overlap the annotation and helps when background occupies much of the box. 0 disables this term; it is not a replacement for held-out mask IoU evaluation.",
       0, 1000, .1, 6),
    _s("mask_boundary_weight", "Mask boundary emphasis", "S2 mask supervision", "float", .2,
       "S2 only: extra pixel-classification loss around both exterior boundaries and interior hole boundaries. It emphasizes inaccurate edges without a smoothing penalty that fills holes. 0 disables the extra boundary term; every pixel still receives the base loss.",
       0, 1000, .05, 6),
    _s("background_rois_per_image", "Background boxes per image", "S2 mask supervision", "int", 2,
       "S2 only: maximum hard predicted background boxes used to teach the mask head an empty silhouette. A box must have zero overlap with every annotated target box. Unreviewed targets must never be omitted from a reviewed image. 0 disables these examples.",
       0, 16),
    _s("negative_mask_weight", "Background-mask loss weight", "S2 mask supervision", "float", .25,
       "S2 only: weight for teaching an empty mask in safe background proposals, including reviewed negative images. It is normalized per image separately from positive silhouettes. Too much weight can favor empty masks; validate recall and false positives together. 0 disables it.",
       0, 1000, .05, 6),
    _s("mask_target_samples", "Mask label samples per axis", "S2 mask supervision", "choice", 2,
       "S2 only: samples 1×1, 2×2, or 4×4 source annotation pixels per output cell to estimate foreground coverage. Sampling uses the original mask and actual guiding box, avoiding an earlier shrink to detector input size. More samples slow training, but add no deployment weights or inference work.",
       choices=(("1 × 1", 1), ("2 × 2", 2), ("4 × 4", 4))),

    _s("hsv_h", "Hue variation", "Color and geometry augmentation", "float", 0.,
       "Random hue shift as a fraction of the full hue circle. The player preset uses a small 0.005 shift to vary colors without large changes. 0 disables it. Applied to training images only.",
       0, .5, .001, 5),
    _s("hsv_s", "Saturation variation", "Color and geometry augmentation", "float", 0.,
       "Randomly increases or decreases color saturation by this fraction. For 0.15, the factor varies around 1 by up to 15%. Geometry is unchanged; validation images stay unaugmented.",
       0, 1, .01, 5),
    _s("hsv_v", "Value / brightness variation", "Color and geometry augmentation", "float", 0.,
       "Randomly varies HSV brightness by this fraction to cover modest lighting changes. 0.12 allows up to 12% variation. This is independent of the optional extra capture-effect brightness control.",
       0, 1, .01, 5),
    _s("translate", "Position variation", "Color and geometry augmentation", "float", 0.,
       "Attempts an image shift up to this fraction of image size. Every box and silhouette moves with it. Transforms that cut off a target or exceed detector assignment capacity are skipped. S2 mask labels are sampled through the same inverse transform from their original pixels.",
       0, .5, .01, 5),
    _s("scale", "Size variation", "Color and geometry augmentation", "float", 0.,
       "Attempts a zoom around 1 by this amount; 0.15 means roughly 0.85–1.15. Pixels, boxes and masks transform together. Samples that cut off targets or exceed detector assignment capacity are skipped, preserving complete labels.",
       0, .9, .01, 5),
    _s("fliplr", "Horizontal-flip probability", "Color and geometry augmentation", "float", 0.,
       "Probability of mirroring a training image and all its linked labels left to right. 0 disables it, matching the player preset. Mirroring may also mirror HUD text and asymmetric visual cues.",
       0, 1, .05, 5),

    _s("augmentation", "Additional capture effects", "Optional capture effects", "choice", "none",
       "None disables the extra brightness, contrast, blur, and JPEG effects below. Gameplay enables those controls. The independent hue, saturation, value, position, size, and flip controls above remain active at their own configured values.",
       choices=(("None", "none"), ("Gameplay capture variation", "gameplay")), widget_name="train_augmentation"),
    _s("gameplay_brightness", "Extra brightness variation", "Optional capture effects", "float", .15,
       "Additional brightness variation around 1 when Gameplay capture variation is selected. 0.15 allows up to 15% brighter or darker images. Set 0 to avoid stacking this effect with HSV value variation.",
       0, 1, .01, 5),
    _s("gameplay_contrast", "Contrast variation", "Optional capture effects", "float", .15,
       "Random contrast variation around 1 for Gameplay capture variation. 0.15 permits up to 15% change. 0 disables contrast variation.",
       0, 1, .01, 5),
    _s("gameplay_blur_probability", "Blur probability", "Optional capture effects", "float", .25,
       "Chance of applying mild Gaussian blur to a training image when Gameplay capture variation is selected. It can simulate some soft capture frames, but excessive blur may hide small players. 0 disables blur.",
       0, 1, .05, 5),
    _s("gameplay_blur_radius", "Maximum blur radius", "Optional capture effects", "float", .8,
       "Upper blur radius in pixels for the optional Gameplay blur effect. Larger values erase more detail. Used only when the blur probability is nonzero and Gameplay capture variation is selected.",
       0, 5, .1, 5),
    _s("gameplay_jpeg_probability", "JPEG-compression probability", "Optional capture effects", "float", .35,
       "Chance of simulating JPEG compression during Gameplay capture variation. This creates capture-like artifacts in the training input while leaving the original image file unchanged. 0 disables it.",
       0, 1, .05, 5),
    _s("gameplay_jpeg_min_quality", "Minimum JPEG quality", "Optional capture effects", "int", 75,
       "Lowest sampled JPEG quality for the optional compression effect. Lower values create stronger artifacts; the highest sampled quality is 95. Used only when Gameplay capture variation and its JPEG probability are enabled.",
       1, 95),

    _s("val_every", "Validate every (epochs)", "Validation and recovery", "int", 1,
       "Runs held-out validation at this interval, as well as the first and final epochs. More frequent checks give earlier feedback but take more time. Early-stop patience counts scored validation checks, not all training epochs.",
       1, 10000, widget_name="train_val_every"),
    _s("patience", "Early-stop patience (checks)", "Validation and recovery", "int", 15,
       "Stops after this many scored validation checks without improving the model-selection score. That score combines emitted-box F1 with encasing overlap; negative-only validation uses false positives per image. 0 disables early stopping.",
       0, 10000, widget_name="train_patience"),
    _s("validation_confidence", "Validation confidence", "Validation and recovery", "float", .35,
       "Confidence threshold used to score predicted detections during validation and select best.cloud. Changing it changes the selection criterion. Keep it fixed when comparing runs; it is separate from the live detection threshold.",
       0, 1, .05, 5),
    _s("validation_max_detections", "Validation detection limit", "Validation and recovery", "int", 32,
       "Maximum candidate detections processed per validation image. A higher cap handles crowded images but adds encasing work. This is a validation setting; the live pipeline controls its own inference cap. Supported range is 1–256.",
       1, 256),
    _s("save_period", "Numbered model interval", "Validation and recovery", "int", 0,
       "Also saves a numbered .cloud model every this many completed epochs. 0 disables numbered snapshots; best.cloud, last.cloud, and the latest separate recovery checkpoint are still saved. Smaller intervals use more disk space.",
       0, 10000),
    _s("allow_no_validation", "Allow training smoke test", "Validation and recovery", "bool", False,
       "Permits a deliberate training smoke test without a reviewed validation split. It cannot measure held-out detection quality or select a validated best model. Leave this off for player-model training."),
)

SETTING_BY_NAME = {setting.name: setting for setting in SETTINGS}
GROUPS = tuple(dict.fromkeys(setting.group for setting in SETTINGS))
PATH_KEYS = frozenset({"resume_path", "init_model_path"})
RUN_CONFIG_FIELDS = frozenset({
    "run_id", "training_config", "model_config", "dataset_fingerprint", "resolved_device",
    "amp_enabled", "amp_dtype", "batch_sizing", "accumulation_steps", "effective_batch_size",
    "schedule_total_updates", "schedule_updates_per_epoch", "train_images", "val_images",
    "initialization", "warnings", "synthetic", "purpose",
})
PRESETS = {
    "legacy": {},
    "player": {
        "architecture": MASK_ARCHITECTURE, "mask_size": 64,
        "epochs": 300, "batch_size": 8, "input_size": 640, "vertices": 64,
        "channels": 96, "roi_size": 8, "learning_rate": .001,
        "weight_decay": .0005, "patience": 75, "seed": 0, "amp": True,
        "scheduler": "cosine", "lr_final_factor": .01,
        "lr_warmup_epochs": 3, "lr_warmup_start_factor": .1,
        "adam_beta1": .937, "adam_beta2": .999, "nominal_batch_size": 64,
        "weight_decay_bias": False, "deterministic": True, "save_period": 10,
        "objectness_weight": 1., "box_weight": 2., "contour_weight": 3.,
        "contour_edge_weight": .15, "warmup_epochs": 3,
        "guidance_transition_epochs": 5, "val_every": 1,
        "validation_confidence": .35, "validation_max_detections": 32,
        "gradient_clip": 10., "cpu_threads": 4, "augmentation": "none",
        "hsv_h": .005, "hsv_s": .15, "hsv_v": .12,
        "translate": .03, "scale": .15, "fliplr": 0.,
    },
}


def validate_settings(values: dict, *, partial: bool = False) -> dict:
    """Validate values before changing controls or starting any work."""
    if not isinstance(values, dict):
        raise ValueError("Training settings must be a JSON object.")
    unknown = set(values) - set(SETTING_BY_NAME) - PATH_KEYS
    if unknown:
        raise ValueError("Unsupported Cloud training settings: " + ", ".join(sorted(unknown)) +
                         ". Use Cloud setting names; YOLO-only keys are not silently ignored.")
    result = dict(values)
    for name, value in values.items():
        if name in PATH_KEYS:
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a nonempty path string or null.")
        else:
            SETTING_BY_NAME[name].validate(value)
    if result.get("resume_path") and result.get("init_model_path"):
        raise ValueError("Choose either resume_path to continue an experiment or init_model_path for a new run.")
    model_fields = ("input_size", "vertices", "channels", "roi_size", "architecture", "mask_size")
    if not partial or all(name in result for name in model_fields):
        ModelConfig(**{name: result[name] for name in model_fields})
    return result


def resolve_settings(preset: str = "player", values: dict | None = None,
                     overrides: dict | None = None) -> dict:
    """Precedence: legacy defaults < named preset < JSON values < explicit edits."""
    if preset not in PRESETS:
        raise ValueError("Training preset must be player or legacy.")
    resolved = {setting.name: setting.default for setting in SETTINGS}
    resolved.update({name: None for name in PATH_KEYS})
    resolved.update(PRESETS[preset])
    for supplied in (values, overrides):
        if supplied is not None:
            resolved.update(validate_settings(supplied, partial=True))
    return validate_settings(resolved)


def read_settings_file(path: str | Path) -> tuple[dict, str | None]:
    """Accept a versioned exported recipe or a plain TrainingConfig JSON object."""
    path = Path(path).expanduser()
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Training settings file is missing or exceeds the 1 MiB limit.")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate training settings key: {key}.")
            result[key] = value
        return result
    try:
        document = json.loads(path.read_text("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Training settings must be valid UTF-8 JSON.") from exc
    if not isinstance(document, dict):
        raise ValueError("Training settings must be a JSON object.")
    preset = None
    if "training_config" in document:
        unknown = set(document) - RUN_CONFIG_FIELDS
        if unknown:
            raise ValueError("Unsupported fields in the run configuration: " + ", ".join(sorted(unknown)))
        if not {"run_id", "training_config", "model_config"} <= document.keys():
            raise ValueError("A run configuration must contain run_id, training_config, and model_config.")
        model = ModelConfig.from_dict(document["model_config"])
        training = validate_settings(document["training_config"], partial=True)
        # Older run files predate the new controls. Restore their original
        # behavior rather than filling omitted keys from today's player recipe.
        resolved = resolve_settings("legacy", training)
        capacities = ModelConfig(**{name: resolved[name] for name in
            ("input_size", "vertices", "channels", "roi_size", "architecture", "mask_size")})
        if capacities != model:
            raise ValueError("The run's training_config and model_config capacities do not match.")
        return training, "legacy"
    if "settings" in document or "format" in document:
        if set(document) - {"format", "version", "preset", "settings"}:
            raise ValueError("Unsupported fields in the training settings document.")
        if (document.get("format") != "cloudstudio-training-settings" or
                type(document.get("version")) is not int or document.get("version") != 1):
            raise ValueError("Unsupported training settings format or version.")
        preset = document.get("preset", "player")
        if preset not in PRESETS:
            raise ValueError("Training preset must be player or legacy.")
        document = document.get("settings")
        # An old exported recipe predates S2. Its named Player preset must not
        # silently change architecture when reopened in a newer application.
        if isinstance(document, dict) and "architecture" not in document:
            document = {**document, "architecture": ARCHITECTURE}
    return validate_settings(document, partial=True), preset


def save_settings_file(path: str | Path, values: dict, preset: str = "player") -> Path:
    """Save an atomic, explicit recipe outside the deployable model file."""
    from .data import write_json

    settings = resolve_settings(preset, values)
    destination = Path(path).expanduser()
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise ValueError("Choose a regular file for training settings.")
    write_json(destination, {"format": "cloudstudio-training-settings", "version": 1,
                             "preset": preset, "settings": settings})
    return destination


def add_training_arguments(parser) -> None:
    """Add complete CLI controls without importing Qt or Torch."""
    import argparse

    parser.add_argument("--preset", choices=tuple(PRESETS),
                        help="Starting recipe: player (default) or legacy. JSON values and explicit flags override it.")
    parser.add_argument("--config", help="Cloud training settings JSON; unknown and YOLO-only keys are rejected.")
    for setting in SETTINGS:
        flag = "--" + setting.name.replace("_", "-")
        kwargs = {"dest": setting.name, "default": argparse.SUPPRESS,
                  "help": setting.help.replace("%", "%%")}
        if setting.kind == "bool":
            kwargs["action"] = argparse.BooleanOptionalAction
        elif setting.kind == "choice":
            kwargs["type"] = type(setting.default)
            if setting.name != "device":
                kwargs["choices"] = [item[1] if isinstance(item, tuple) else item for item in setting.choices]
        else:
            kwargs["type"] = int if setting.kind == "int" else float
        parser.add_argument(flag, **kwargs)
    initialization = parser.add_mutually_exclusive_group()
    initialization.add_argument("--resume", dest="resume_path", default=argparse.SUPPRESS,
                                help="Resume a .cloudtrain experiment. Without --config or --preset, restores its original recipe automatically; explicit flags are checked for resume compatibility.")
    initialization.add_argument("--init-model", dest="init_model_path", default=argparse.SUPPRESS,
                                help="Improve an existing .cloud in a new run with a new optimizer. Unspecified model capacities are inherited from it; explicit JSON or CLI capacity conflicts are rejected by training.")
    initialization.add_argument("--from-scratch", action="store_true", default=argparse.SUPPRESS,
                                help="Explicitly clear any initialization paths supplied in the settings JSON.")


def settings_from_arguments(args) -> dict:
    supplied, file_preset = ({}, None) if not args.config else read_settings_file(args.config)
    namespace = vars(args)
    if not args.config and args.preset is None and namespace.get("resume_path"):
        from .training import _load_checkpoint
        supplied = _load_checkpoint(namespace["resume_path"])["config"]
        file_preset = "legacy"
    preset = args.preset or file_preset or "player"
    overrides = {name: namespace[name] for name in set(SETTING_BY_NAME) | PATH_KEYS if name in namespace}
    if namespace.get("from_scratch"):
        overrides.update({name: None for name in PATH_KEYS})
    elif "resume_path" in overrides:
        overrides["init_model_path"] = None
    elif "init_model_path" in overrides:
        overrides["resume_path"] = None
    resolved = resolve_settings(preset, supplied, overrides)
    if resolved.get("init_model_path") and not resolved.get("resume_path"):
        from .format import load_cloud
        explicit = set(supplied) | set(overrides)
        if args.config:
            # Old exported recipes gain a legacy architecture default in the
            # reader. That compatibility default is not an explicit request to
            # change the architecture of the selected Improve parent.
            document = json.loads(Path(args.config).expanduser().read_text("utf-8"))
            declared = document.get("training_config", document.get("settings", document))
            explicit = set(declared) | set(overrides)
        parent_config, _, _ = load_cloud(resolved["init_model_path"])
        inherited = {name: getattr(parent_config, name) for name in
            ("input_size", "vertices", "channels", "roi_size", "architecture", "mask_size")
            if name not in explicit}
        resolved = validate_settings({**resolved, **inherited})
    return resolved
