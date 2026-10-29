"""Local, verified segmentation backends used only while annotating images.

No assistant weights are part of a trained Cloud detector. Importing this module
does not import Torch or load/download a checkpoint.
"""

from __future__ import annotations

from pathlib import Path


DEFAULT_BACKEND = "light_hq_sam"
BACKENDS = {
    "light_hq_sam": {
        "label": "Light HQ-SAM",
        "filename": "sam_hq_vit_tiny.pth",
        "sha256": "0f32c075ccdd870ae54db2f7630e7a0878ede5a2b06d05d6fe02c65a82fb7196",
        "model_type": "vit_tiny",
        "description": "High-quality TinyViT segmentation. Bundled and local; preserves mask holes and separate visible parts.",
    },
    "mobile_sam": {
        "label": "MobileSAM",
        "filename": "mobile_sam.pt",
        "sha256": "6dbb90523a35330fedd7f1d3dfc66f995213d81b29a5ca8108dbcdd4e37d6c2f",
        "model_type": "vit_t",
        "description": "The original bundled MobileSAM assistant, retained for comparison and fallback.",
    },
}


def normalize_backend(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Choose Light HQ-SAM or MobileSAM as the annotation assistant.")
    aliases = {"light hq-sam": "light_hq_sam", "lighthqsam": "light_hq_sam", "mobilesam": "mobile_sam"}
    value = aliases.get(value.lower(), value.lower())
    if value not in BACKENDS:
        raise ValueError("Choose Light HQ-SAM or MobileSAM as the annotation assistant.")
    return value


def checkpoint_path(backend: str = DEFAULT_BACKEND) -> Path:
    return Path(__file__).resolve().parents[1] / "models" / "annotation" / BACKENDS[normalize_backend(backend)]["filename"]


def predictor_factory(backend: str):
    """Return a model builder and predictor class, importing Torch only on use."""
    backend = normalize_backend(backend)
    if backend == "light_hq_sam":
        from ._vendor.light_hq_sam import SamPredictor, sam_model_registry
    else:
        from ._vendor.mobile_sam import SamPredictor, sam_model_registry
    return sam_model_registry[BACKENDS[backend]["model_type"]], SamPredictor
