"""Shared product paths, settings, and portable profile files for VaporLock."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from tempfile import NamedTemporaryFile

from annotation_provider import BACKENDS as ANNOTATION_BACKENDS
from local_control_api import DEFAULT_PORT


MAX_PROFILE_BYTES = 1_048_576
MAX_SAFE_INTEGER = 2**53 - 1
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?")


@dataclass(frozen=True, slots=True)
class ProductPaths:
    root: Path
    component_root: Path
    asset_root: Path

    @classmethod
    def discover(cls) -> ProductPaths:
        if getattr(sys, "frozen", False):
            component = Path(sys.executable).resolve().parent
            parent = component.parent
            root = parent if (parent / "engine").is_dir() or (
                parent / "information" / "alpha-manifest.json"
            ).is_file() else component
            asset_root = Path(getattr(sys, "_MEIPASS", component)) / "assets"
            return cls(root, component, asset_root)
        return cls.from_root(Path(__file__).resolve().parents[1])

    @classmethod
    def from_root(cls, root: Path) -> ProductPaths:
        root = Path(root).resolve()
        return cls(root, root, root / "assets")

    @property
    def profiles(self) -> Path:
        return self.root / "profiles"

    @property
    def icon(self) -> Path:
        path = self.asset_root / "VaporLock.ico"
        return path if path.is_file() else self.root / "assets" / "VaporLock.ico"

    @property
    def version(self) -> str:
        try:
            from product_build import PRODUCT_VERSION
        except ImportError:
            PRODUCT_VERSION = ""
        if PRODUCT_VERSION:
            return _release_version(PRODUCT_VERSION)
        for path in (self.root / "information" / "product_version.txt", self.root / "product_version.txt"):
            if path.is_file():
                return _release_version(path.read_text(encoding="utf-8"))
        return "development"

    def resolve(self, value: str | Path) -> Path:
        candidate = Path(value).expanduser()
        return (self.root / candidate).resolve()

    def portable(self, value: str) -> str:
        if not value:
            return ""
        candidate = self.resolve(value)
        try:
            return str(candidate.relative_to(self.root))
        except ValueError:
            return str(candidate)


def _release_version(value: str) -> str:
    value = value.strip()
    if not _VERSION.fullmatch(value):
        raise ValueError("Invalid VaporLock release version.")
    return value


@dataclass
class Settings:
    profile_name: str = "Default"
    model_path: str = ""
    cloud_project: str = ""
    cloud_output: str = ""
    cloud_run_name: str = "cloud-training"
    cloud_mode: str = "fresh"
    cloud_initial_model: str = ""
    cloud_checkpoint: str = ""
    cloud_epochs: int = 300
    cloud_batch: int = 8
    cloud_patience: int = 75
    cloud_device: str = "auto"
    cloud_annotation_device: str = "cpu"
    cloud_import_split: str = "train"
    provider: str = "auto"
    class_filter: str = ""
    model_class_aliases: list[dict] = field(default_factory=list)
    exclusion_zones: list[dict] = field(default_factory=list)
    confidence: float = 0.55
    nms_iou: float = 0.45
    nms_enabled: bool = True
    nms_class_agnostic: bool = False
    model_output_format: str = "auto"
    model_coordinate_units: str = "auto"
    max_detections: int = 300
    resize_mode: str = "letterbox"
    monitor: int = 1
    region_x: int = 0
    region_y: int = 0
    region_width: int = 640
    region_height: int = 640
    capture_fps: int = 30
    inference_fps: int = 30
    frame_skip: int = 0
    ort_intra_op_threads: int = 0
    inference_dml_device: int = -1
    inference_cuda_device: int = -1
    inference_trt_device: int = -1
    dataset_annotation_batch: int = 1
    ort_inter_op_threads: int = 0
    cuda_graph_replay: bool = True
    cuda_preprocess: bool = True
    match_iou: float = 0.30
    max_missed: int = 18
    track_retention_ms: int = -1
    smoothing: float = 0.35
    box_smoothing: float = 0.25
    anchor_smoothing: float = 0.65
    anchor_settle_radius: float = 2.0
    predict_lost: bool = True
    prediction_lead_ms: int = 50
    prediction_limit_ms: int = 500
    prediction_max_speed: float = 2000.0
    prediction_max_acceleration: float = 12000.0
    prediction_max_distance: float = 80.0
    prediction_noise_floor: float = 1.0
    strategy: int = 0
    acquisition_radius: int = 420
    near_radius: int = 180
    snap_radius: int = 48
    retention_radius: int = 540
    fov_shape: str = "circle"
    switching: float = 0.18
    lock_persistence_ms: int = 900
    lost_target_timeout: int = 12
    minimum_track_age: int = 2
    snap_bypasses_confirmation: bool = False
    near_candidate_wait_ms: int = 150
    lost_target_timeout_ms: int = -1
    anchor_mode: str = "center"
    anchor_x: float = 0.0
    anchor_y: float = 0.0
    watchlist: str = ""
    occlusion_handling: bool = True
    occlusion_grace_frames: int = 12
    occlusion_grace_ms: int = -1
    occlusion_reacquire_radius: int = 180
    class_policies: list[dict] = field(default_factory=list)
    movement_enabled: bool = False
    activation_mode: str = "hold"
    activation_key: str = "right"
    activation_source: str = "keyboard"
    controller_slot: int = 0
    controller_binding: str = "right_trigger"
    controller_trigger_press: int = 30
    controller_trigger_release: int = 30
    manual_target_lock_enabled: bool = False
    manual_lock_source: str = "keyboard"
    manual_lock_key: str = "middle"
    manual_lock_controller_binding: str = "left_shoulder"
    assistance_x: float = 1.0
    assistance_y: float = 1.0
    controller_output_deadzone: float = 0.0
    acquire_confidence: float = 0.0
    retain_confidence: float = 0.0
    gain: float = 0.18
    max_step: int = 24
    deadzone: int = 8
    movement_smoothing: float = 0.35
    output_rate: int = 120
    output_max_observation_age_ms: int = 150
    input_units_per_pixel_x: float = 1.0
    input_units_per_pixel_y: float = 1.0
    max_speed: float = 1440.0
    crosshair_enabled: bool = False
    crosshair_calibration: dict = field(default_factory=dict)
    aim_reference: str = "capture-center"
    aim_reference_x: float = 0.0
    aim_reference_y: float = 0.0
    # A named preset describes measured image drift in pixels per second.
    # The native engine applies the inverse correction only while output is active.
    motion_compensation_enabled: bool = False
    motion_compensation_activation_mode: str = "hold"
    motion_compensation_activation_key: str = "left"
    motion_compensation_mode: str = "preset"
    motion_compensation_library: str = "profiles/motion_compensation.txt"
    motion_compensation_preset: str = ""
    motion_compensation_vertical: float = 0.0
    motion_compensation_horizontal: float = 0.0
    motion_compensation_strength: float = 1.0
    motion_compensation_auto_min_tracks: int = 3
    motion_compensation_auto_consensus: float = 24.0
    motion_compensation_auto_smoothing: float = 0.75
    motion_compensation_auto_max_rate: float = 240.0
    event_action_enabled: bool = False
    event_action_requires_activation: bool = True
    event_action_mode: str = "click"
    event_action_button: str = "left"
    event_action_cooldown_ms: int = 1000
    output_transport: str = "mouse"
    virtual_mouse_opt_in: bool = False
    controller_output_opt_in: bool = False
    virtual_controller_mode: str = "slot0-proxy"
    virtual_controller_identity: str = "vapor-lock-xbox-one-windows"
    virtual_controller_stick: str = "right"
    virtual_controller_strength: float = 1.0
    virtual_controller_manual_priority: bool = True
    hardware_serial_port: str = ""
    hardware_serial_baud: int = 115200
    kmbox_net_address: str = ""
    kmbox_net_port: int = 0
    kmbox_net_device_code: str = ""
    kmbox_a_library_path: str = ""
    kmbox_a_vendor_id: int = 0x6688
    kmbox_a_product_id: int = 0x2021
    invert_x: bool = False
    invert_y: bool = False
    response_curve: str = "linear"
    preview_enabled: bool = True
    preview_fps: int = 5
    preview_quality: int = 80
    overlay_enabled: bool = True
    overlay_boxes: bool = True
    overlay_motion: bool = True
    overlay_fov: bool = True
    overlay_segmentation_masks: bool = False
    segmentation_mask_limit: int = 1
    segmentation_mask_threshold: float = 0.50
    desktop_overlay_enabled: bool = False
    desktop_overlay_opacity: int = 88
    desktop_overlay_labels: bool = True
    desktop_overlay_status: bool = True
    desktop_overlay_motion: bool = False
    desktop_overlay_visible: bool = True
    desktop_overlay_layout: str = "standard"
    desktop_overlay_scale: float = 1.0
    desktop_overlay_capture_bounds: bool = False
    desktop_overlay_palette: str = "vapor"
    desktop_overlay_track_color: str = "#7dbcf5"
    desktop_overlay_selected_color: str = "#e887c5"
    desktop_overlay_fov_color: str = "#a481f1"
    desktop_overlay_text_color: str = "#dfe9f2"
    hotkey_start_stop: str = "Ctrl+Alt+S"
    hotkey_toggle_overlay: str = "Ctrl+Alt+O"
    hotkey_emergency_stop: str = "Ctrl+Alt+X"
    hotkey_next_profile: str = "Ctrl+Alt+P"
    hotkey_toggle_target_lock: str = "Ctrl+Alt+L"
    hotkey_save_screenshot: str = "Ctrl+Alt+F12"
    hotkey_calibration: str = "Ctrl+Alt+C"
    hotkey_profile_1: str = ""
    hotkey_profile_1_target: str = ""
    hotkey_profile_2: str = ""
    hotkey_profile_2_target: str = ""
    hotkey_profile_3: str = ""
    hotkey_profile_3_target: str = ""
    local_api_enabled: bool = False
    local_api_lan_enabled: bool = False
    local_api_port: int = DEFAULT_PORT
    dataset_enabled: bool = False
    dataset_capture_confidence_enabled: bool = False
    dataset_directory: str = ""
    dataset_min_confidence: float = 0.50
    dataset_interval_ms: int = 1000
    dataset_max_images: int = 0
    dataset_max_mib: int = 0
    dataset_capture_requires_activation: bool = True
    dataset_jpeg_quality: int = 92
    dataset_annotation_model: str = ""
    dataset_annotation_provider: str = "auto"
    dataset_annotation_passes: int = 2
    dataset_annotation_confidence: float = 0.55
    dataset_annotation_iou: float = 0.85
    dataset_annotation_nms_iou: float = 0.45
    dataset_annotation_additive: bool = False
    inference_cpu_placement: dict = field(default_factory=dict)
    dataset_annotation_replace: bool = False
    dataset_annotation_delete_unannotated: bool = False
    dataset_effects_enabled: bool = False
    dataset_effects_extra_copies: bool = False
    dataset_effects_copies: int = 1
    dataset_effects_strength: str = "medium"
    dataset_effects_blur: bool = True
    dataset_effects_colour: bool = True
    dataset_effects_exposure: bool = True
    dataset_effects_noise: bool = True
    dataset_effects_compression: bool = True
    ultralytics_owner: str = ""
    ultralytics_dataset: str = ""
    ultralytics_key_protected: str = ""
    ultralytics_annotation_model: str = ""
    ultralytics_annotation_provider: str = "auto"
    ultralytics_annotation_passes: int = 2
    ultralytics_annotation_confidence: float = 0.55
    ultralytics_annotation_iou: float = 0.85
    ultralytics_annotation_nms_iou: float = 0.45
    ultralytics_image_limit: int = 100
    ultralytics_output_directory: str = ""
    ultralytics_effects_enabled: bool = False
    ultralytics_effects_extra_copies: bool = False
    ultralytics_effects_copies: int = 1
    ultralytics_effects_strength: str = "medium"
    ultralytics_effects_blur: bool = True
    ultralytics_effects_colour: bool = True
    ultralytics_effects_exposure: bool = True
    ultralytics_effects_noise: bool = True
    ultralytics_effects_compression: bool = True
    dataset_export_val_split: float = 0.15
    dataset_review_approval_key: str = "enter"
    dataset_review_class_slots: list[int] = field(default_factory=lambda: list(range(9)))
    training_base_model: str = ""
    training_dataset: str = ""
    training_name: str = "vaporlock-training"
    training_platform_project: str = ""
    training_epochs: int = 100
    training_imgsz: int = 640
    training_batch: str = "auto"
    training_device: str = "auto"
    training_workers: int = 8
    training_patience: int = 100
    training_optimizer: str = "auto"
    training_lr0: float = 0.01
    training_lrf: float = 0.01
    training_weight_decay: float = 0.0005
    training_warmup_epochs: float = 3.0
    training_close_mosaic: int = 10
    training_cache: str = "disk"
    training_amp: bool = True
    training_pretrained: bool = True
    training_cos_lr: bool = False
    training_seed: int = 0
    training_deterministic: bool = True
    training_fraction: float = 1.0
    training_export_onnx: bool = True
    training_dynamic_onnx: bool = True
    training_extra_arguments: str = ""

    def __post_init__(self) -> None:
        from cloud_settings import validate as validate_cloud
        validate_cloud(vars(self))
        from batch6_settings import validate
        validate(vars(self))
        from batch7_settings import validate as validate_batch7
        validate_batch7(vars(self))
        from crosshair_reference import validate as validate_crosshair
        validate_crosshair(vars(self))
        from exclusion_zones import validate_zones
        self.exclusion_zones = validate_zones(self.exclusion_zones)
        # Legacy frame durations have a fixed 60 Hz compatibility baseline.
        for duration, frames in (("track_retention_ms", "max_missed"),
                                 ("lost_target_timeout_ms", "lost_target_timeout"),
                                 ("occlusion_grace_ms", "occlusion_grace_frames")):
            if getattr(self, duration) < 0:
                setattr(self, duration, max(0, round(getattr(self, frames) * 1000 / 60)))


SETTING_ENUMS = {
    "ultralytics_annotation_provider": ('auto','directml','cuda','cpu'),
    "dataset_effects_strength": ("mild", "medium", "strong"),
    "ultralytics_effects_strength": ("mild", "medium", "strong"),
    "dataset_annotation_provider": ANNOTATION_BACKENDS,
    "provider": ("auto", "dml", "cuda", "trt", "cpu"),
    "resize_mode": ("letterbox", "stretch"),
    "model_output_format": ("auto", "yolo-class", "yolo-objectness", "end-to-end"),
    "model_coordinate_units": ("auto", "pixels", "normalized"),
    "strategy": (0, 1, 2, 3, 4),
    "fov_shape": ("circle", "rectangle"),
    "anchor_mode": ("center", "top", "bottom", "upper-quarter"),
    "activation_mode": ("hold", "toggle"),
    "activation_source": ("keyboard", "controller", "either"),
    "manual_lock_source": ("keyboard", "controller", "either"),
    "event_action_mode": ("click", "hold"),
    "event_action_button": ("left", "right", "middle"),
    "output_transport": ("mouse", "virtual-mouse", "virtual-xbox", "makcu-v1", "makcu-v2", "serial-hid-bridge", "kmbox-net", "kmbox-a"),
    "virtual_controller_mode": ("slot0-proxy", "separate"),
    "virtual_controller_stick": ("left", "right"),
    "response_curve": ("linear", "quadratic"),
    "aim_reference": ("capture-center", "crosshair-offset"),
    "motion_compensation_activation_mode": ("hold", "click", "constant", "automatic"),
    "motion_compensation_mode": ("preset", "automatic", "combined"),
    "desktop_overlay_layout": ("minimal", "standard", "diagnostics"),
    "desktop_overlay_palette": ("vapor", "contrast"),
    "dataset_review_approval_key": ("enter", "space"),
    "training_cache": ("disk", "ram", "false"),
    "training_optimizer": ("auto", "SGD", "Adam", "AdamW", "RMSProp"),
}

AIM_NATIVE_SETTINGS = (
    "assistance_x", "assistance_y", "controller_output_deadzone", "acquire_confidence", "retain_confidence",
    "track_retention_ms", "lost_target_timeout_ms", "occlusion_grace_ms",
    "snap_bypasses_confirmation", "near_candidate_wait_ms", "output_max_observation_age_ms",
    "prediction_max_speed", "prediction_max_acceleration", "prediction_max_distance", "prediction_noise_floor",
    "input_units_per_pixel_x", "input_units_per_pixel_y", "max_speed",
    "aim_reference", "aim_reference_x", "aim_reference_y",
)

PROFILE_PATH_FIELDS = (
    "model_path", "dataset_directory", "dataset_annotation_model",
    "ultralytics_annotation_model", "ultralytics_output_directory",
    "training_base_model", "training_dataset", "motion_compensation_library",
    "cloud_project", "cloud_output", "cloud_initial_model", "cloud_checkpoint",
)
ANCHORS = SETTING_ENUMS["anchor_mode"]


def validated_policies(rows):
    if not isinstance(rows, list) or len(rows) > 128:
        raise ValueError("Class policies must be a list of at most 128 rules.")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid class policy.")
        class_id, priority = row.get("class_id"), row.get("priority", 0)
        mode = row.get("anchor_mode", "center")
        x, y = row.get("anchor_x", 0.0), row.get("anchor_y", 0.0)
        if type(class_id) is not int or not 0 <= class_id <= 1_000_000 or class_id in seen:
            raise ValueError("Class IDs must be unique integers between 0 and 1000000.")
        if type(priority) is not int or not -1_000_000 <= priority <= 1_000_000 or mode not in ANCHORS:
            raise ValueError("Invalid priority or anchor mode.")
        if any(type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 1 for v in (x, y)):
            raise ValueError("Class anchor offsets must be finite values between -1 and 1.")
        seen.add(class_id)
        result.append(dict(class_id=class_id, priority=priority, anchor_mode=mode, anchor_x=float(x), anchor_y=float(y)))
    return result


def policies_wire(rows):
    rows = validated_policies(rows)
    return " ".join([str(len(rows))] + [
        f"{r['class_id']} {r['priority']} {ANCHORS.index(r['anchor_mode'])} {r['anchor_x']} {r['anchor_y']}"
        for r in rows
    ])


def _validate_value(value, depth=0):
    """Bound JSON data carried by legacy collection settings."""
    if depth > 8:
        raise ValueError("Profile values are nested too deeply.")
    kind = type(value)
    if kind is str and len(value) <= 32_768:
        value.encode("utf-8", errors="strict")
    elif kind is int and abs(value) <= MAX_SAFE_INTEGER:
        return
    elif kind is float and math.isfinite(value):
        return
    elif kind is bool or value is None:
        return
    elif kind is list and len(value) <= 128:
        for child in value:
            _validate_value(child, depth + 1)
    elif kind is dict and len(value) <= 256 and all(type(key) is str for key in value):
        for key, child in value.items():
            _validate_value(key, depth + 1)
            _validate_value(child, depth + 1)
    else:
        raise ValueError("Profile contains an unsupported or oversized value.")


def _validate_fields(values: dict) -> None:
    kinds = {"str": str, "int": int, "float": float, "bool": bool,
             "dict": dict, "list[int]": list, "list[dict]": list}
    for name, value in values.items():
        kind = Settings.__dataclass_fields__[name].type
        allowed = (int, float) if kind == "float" else (kinds[kind],)
        if type(value) not in allowed:
            raise ValueError(f"Invalid profile value for {name}.")
        if kind in ("list[int]", "list[dict]"):
            item_type = int if kind == "list[int]" else dict
            if any(type(item) is not item_type for item in value):
                raise ValueError(f"Invalid collection item for {name}.")
        if name in SETTING_ENUMS and value not in SETTING_ENUMS[name]:
            raise ValueError(f"Invalid profile choice for {name}.")
        _validate_value(value)


def _validate_annotation(settings: Settings) -> None:
    from annotation_variations import EFFECTS, VariationOptions

    for prefix in ("dataset", "ultralytics"):
        VariationOptions(
            enabled=getattr(settings, f"{prefix}_effects_enabled"),
            extra_copies=getattr(settings, f"{prefix}_effects_extra_copies"),
            copies=getattr(settings, f"{prefix}_effects_copies"),
            strength=getattr(settings, f"{prefix}_effects_strength"),
            effects=tuple(name for name in EFFECTS if getattr(settings, f"{prefix}_effects_{name}")),
        ).validate()
        if not .01 <= getattr(settings, f"{prefix}_annotation_nms_iou") <= .99:
            raise ValueError("Invalid annotation NMS threshold.")
    if (not 1 <= settings.ultralytics_annotation_passes <= 10
            or not 1 <= settings.ultralytics_image_limit <= 10_000
            or not .01 <= settings.ultralytics_annotation_confidence <= .99
            or not .05 <= settings.ultralytics_annotation_iou <= .99):
        raise ValueError("Invalid Ultralytics profile settings.")


def validate_settings(settings: Settings) -> None:
    """Check a complete settings snapshot before persisting or launching it."""
    from batch3_settings import validate as validate_assistance
    from batch6_settings import validate as validate_devices
    from batch7_settings import validate as validate_batch_mode
    from cloud_settings import validate as validate_cloud
    from collection_limits import validate_limits
    from controller_thresholds import validate_thresholds
    from crosshair_reference import validate as validate_crosshair
    from exclusion_zones import validate_zones
    from model_classes import validate_aliases
    from training_security import load_extra_arguments
    from ultralytics_credentials import validate_envelope
    from vlui_c014_settings import validated_capture_settings

    values = asdict(settings)
    _validate_fields(values)
    validate_assistance(values)
    validate_devices(values)
    validate_batch_mode(values)
    validate_cloud(values)
    validate_crosshair(values)
    validate_limits(values)
    validate_thresholds(settings.controller_trigger_press, settings.controller_trigger_release)
    validate_zones(settings.exclusion_zones)
    validate_aliases(settings.model_class_aliases)
    validated_policies(settings.class_policies)
    validated_capture_settings(values)
    validate_envelope(settings.ultralytics_key_protected)
    load_extra_arguments(settings.training_extra_arguments)
    _validate_annotation(settings)
    if not 1 <= settings.capture_fps <= MAX_SAFE_INTEGER:
        raise ValueError("Profile capture rate must be a positive safe integer.")


def _section(raw: dict, name: str) -> dict:
    section = raw.get(name, {})
    if type(section) is not dict:
        raise ValueError(f"Invalid legacy profile section {name}.")
    return section


def _legacy_profile(raw: dict) -> dict:
    model = _section(raw, "model")
    capture = _section(raw, "capture")
    tracking = _section(raw, "tracking")
    detection = _section(raw, "detection")
    general = _section(raw, "general")
    selection = _section(raw, "selection")
    movement = _section(raw, "movement")
    performance = _section(raw, "performance")
    overlay = _section(raw, "overlay")
    visuals = _section(raw, "visuals")
    dataset = _section(raw, "dataset")
    region = capture.get("region", [0, 0, 640, 640])
    if isinstance(region, str):
        try:
            region = [int(value.strip()) for value in region.split(",")]
        except ValueError:
            region = [0, 0, 640, 640]
    if not isinstance(region, list) or len(region) != 4:
        region = [0, 0, 640, 640]
    strategy_names = {"center": 0, "confidence": 1, "largest": 2, "smallest": 3, "movement": 4}
    activation_keys = {
        "RightMouseButton": "right", "LeftShift": "shift", "LeftControl": "control", "Space": "space",
    }
    prediction = tracking.get("predictionSeconds", 0.05)
    if "predictLost" not in tracking and type(prediction) not in (int, float):
        raise ValueError("Invalid legacy prediction duration.")
    return {
        "profile_name": general.get("profileName", "Default"),
        "model_path": model.get("path", ""),
        "provider": model.get("provider", "auto"),
        "class_filter": model.get("classFilter", ""),
        "confidence": model.get("confidenceThreshold", 0.55),
        "nms_iou": model.get("iouThreshold", 0.45),
        "nms_enabled": detection.get("nmsEnabled", True),
        "nms_class_agnostic": detection.get("classAgnosticNms", False),
        "model_output_format": model.get("outputFormat", "auto"),
        "max_detections": detection.get("maxDetections", 300),
        "resize_mode": model.get("resizeMode", "letterbox"),
        "monitor": capture.get("monitor", 0 if capture.get("surface") == "all" else 1),
        "region_x": region[0],
        "region_y": region[1],
        "region_width": region[2],
        "region_height": region[3],
        "capture_fps": capture.get("fps", 30),
        "inference_fps": performance.get("inferenceFps", 30),
        "frame_skip": performance.get("frameSkipping", 0),
        "ort_intra_op_threads": performance.get("ortIntraOpThreads", 0),
        "ort_inter_op_threads": performance.get("ortInterOpThreads", 0),
        "cuda_graph_replay": performance.get("cudaGraphReplay", True),
        "cuda_preprocess": performance.get("cudaPreprocess", True),
        "match_iou": tracking.get("matchIou", 0.30),
        "max_missed": tracking.get("maxLostFrames", 18),
        "smoothing": tracking.get("smoothing", 0.35),
        "box_smoothing": tracking.get("boxSmoothing", 0.25),
        "anchor_smoothing": tracking.get("anchorSmoothing", 0.65),
        "anchor_settle_radius": tracking.get("anchorSettleRadius", 2.0),
        "predict_lost": tracking["predictLost"] if "predictLost" in tracking else prediction > 0,
        "prediction_lead_ms": tracking.get("predictionLeadMs", 50),
        "prediction_limit_ms": tracking.get("predictionLimitMs", 500),
        "occlusion_handling": tracking.get("occlusionHandling", True),
        "occlusion_grace_frames": tracking.get("occlusionGraceFrames", 12),
        "occlusion_reacquire_radius": tracking.get("occlusionReacquireRadius", 180),
        "strategy": strategy_names.get(selection.get("strategy", "center"), 0),
        "acquisition_radius": selection.get("acquisitionRadius", 420),
        "near_radius": selection.get("nearRadius", 180),
        "snap_radius": selection.get("snapRadius", 48),
        "retention_radius": selection.get("retentionRadius", 540),
        "fov_shape": selection.get("fovShape", "circle"),
        "switching": selection.get("switchingHysteresis", 0.18),
        "lock_persistence_ms": selection.get("lockPersistenceMs", 900),
        "lost_target_timeout": selection.get("lostTargetTimeoutFrames", 12),
        "minimum_track_age": selection.get("minimumTrackAge", 2),
        "anchor_mode": selection.get("anchorMode", "center"),
        "anchor_x": selection.get("anchorOffsetX", 0.0),
        "anchor_y": selection.get("anchorOffsetY", 0.0),
        "watchlist": selection.get("watchlist", ""),
        "movement_enabled": movement.get("enabled", False),
        "activation_mode": movement.get("activationMode", "hold"),
        "activation_key": activation_keys.get(movement.get("activationKey", "RightMouseButton"), "right"),
        "gain": movement.get("gain", 0.18),
        "max_step": movement.get("maxStep", 24),
        "deadzone": movement.get("deadzone", 8),
        "movement_smoothing": movement.get("smoothing", 0.35),
        "output_rate": movement.get("outputRate", 120),
        "invert_x": movement.get("invertX", False),
        "invert_y": movement.get("invertY", False),
        "response_curve": movement.get("responseCurve", "linear"),
        "preview_enabled": visuals.get("enabled", True),
        "preview_fps": visuals.get("fps", 5),
        "preview_quality": visuals.get("quality", 80),
        "overlay_enabled": overlay.get("enabled", True),
        "overlay_boxes": overlay.get("boxes", True),
        "overlay_motion": overlay.get("motion", True),
        "overlay_fov": overlay.get("fov", True),
        "overlay_segmentation_masks": overlay.get("segmentationMasks", True),
        "segmentation_mask_limit": overlay.get("segmentationMaskLimit", 8),
        "segmentation_mask_threshold": overlay.get("segmentationMaskThreshold", 0.50),
        "dataset_enabled": dataset.get("enabled", False),
        "dataset_capture_confidence_enabled": dataset.get("captureConfidenceEnabled", False),
        "dataset_directory": dataset.get("directory", ""),
        "dataset_min_confidence": dataset.get("minConfidence", 0.50),
        "dataset_interval_ms": dataset.get("intervalMs", 1000),
        "dataset_capture_requires_activation": dataset.get("captureRequiresActivation", True),
        "dataset_jpeg_quality": dataset.get("jpegQuality", 92),
        "dataset_annotation_model": dataset.get("annotationModel", ""),
        "dataset_annotation_passes": dataset.get("annotationPasses", 2),
        "dataset_annotation_confidence": dataset.get("annotationConfidence", 0.55),
        "dataset_annotation_iou": dataset.get("annotationIou", 0.85),
        "dataset_annotation_replace": dataset.get("annotationReplace", False),
        "dataset_export_val_split": dataset.get("exportValSplit", 0.15),
    }



def _unique_members(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate profile member.")
        result[key] = value
    return result


def _nonfinite(_token):
    raise ValueError("Profile contains a nonfinite number.")


def decode_profile(data: bytes, paths: ProductPaths) -> Settings:
    """Read flat Community profiles and nested profiles from earlier releases."""
    if type(data) is not bytes or len(data) > MAX_PROFILE_BYTES:
        raise ValueError("Profile exceeds the size limit.")
    raw = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_members, parse_constant=_nonfinite)
    if type(raw) is not dict:
        raise ValueError("A profile must contain a settings object.")
    if "model" in raw:
        raw = _legacy_profile(raw)
    values = {name: value for name, value in raw.items() if name in Settings.__dataclass_fields__}
    _validate_fields(values)
    # Files written before these controls existed used the original snap and speed rules.
    values.setdefault("snap_bypasses_confirmation", True)
    values.setdefault("max_speed", float(values.get("max_step", 24)) * 60.0)
    values["class_policies"] = validated_policies(values.get("class_policies", []))
    settings = Settings(**values)
    for name in PROFILE_PATH_FIELDS:
        value = getattr(settings, name)
        if value:
            setattr(settings, name, str(paths.resolve(value)))
    validate_settings(settings)
    return settings


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    created = getattr(info, "st_birthtime_ns", info.st_ctime_ns)
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, created


def read_profile(path: Path, paths: ProductPaths) -> Settings:
    path = Path(path)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_PROFILE_BYTES:
            raise ValueError("Profile must be a regular file within the size limit.")
        data = stream.read(MAX_PROFILE_BYTES + 1)
        after = os.fstat(stream.fileno())
        if (len(data) != after.st_size or _file_identity(before) != _file_identity(after)
                or _file_identity(path.stat()) != _file_identity(after)):
            raise ValueError("Profile changed while it was being read.")
    return decode_profile(data, paths)


def save_profile(path: Path, settings: Settings, paths: ProductPaths) -> None:
    """Write a validated profile atomically, with product-relative asset paths."""
    validate_settings(settings)
    payload = asdict(settings)
    for name in PROFILE_PATH_FIELDS:
        payload[name] = paths.portable(payload[name])
    raw = (json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_PROFILE_BYTES:
        raise ValueError("Profile exceeds the size limit.")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="wb", dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
