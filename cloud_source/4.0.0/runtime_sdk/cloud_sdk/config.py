"""Versioned architecture configuration, usable without PyTorch installed."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


ARCHITECTURE = "cloudvision_s1_v2"
MASK_ARCHITECTURE = "cloudvision_s2_v1"
SUPPORTED_ARCHITECTURES = (ARCHITECTURE, MASK_ARCHITECTURE)


@dataclass(frozen=True)
class ModelConfig:
    """The trained capacities of a supported CloudVision model.

    Changing these values requires a matching model, rather than just changing
    an inference preference. Limits also bound memory use when reading files.
    """

    input_size: int = 320
    vertices: int = 32
    channels: int = 48
    roi_size: int = 8
    architecture: str = ARCHITECTURE
    mask_size: int = 64

    def __post_init__(self) -> None:
        for field in fields(self):
            if field.name == "architecture":
                continue
            if type(getattr(self, field.name)) is not int:
                raise ValueError(f"{field.name} must be an integer")
        if self.architecture not in SUPPORTED_ARCHITECTURES:
            raise ValueError("unsupported model architecture")
        if not 64 <= self.input_size <= 1024 or self.input_size % 32:
            raise ValueError("input_size must be a multiple of 32 from 64 to 1024")
        if self.vertices not in (16, 32, 64):
            raise ValueError("vertices must be 16, 32, or 64")
        if not 16 <= self.channels <= 128 or self.channels % 8:
            raise ValueError("channels must be a multiple of 8 from 16 to 128")
        if self.roi_size not in (4, 8, 16):
            raise ValueError("roi_size must be 4, 8, or 16")
        if self.mask_size not in (32, 64, 128):
            raise ValueError("mask_size must be 32, 64, or 128")
        # Inactive capacities must not leak into deployment descriptors or
        # make equivalent loaded/training configurations compare differently.
        if self.is_mask:
            object.__setattr__(self, "vertices", 32)
            object.__setattr__(self, "roi_size", 8)
        else:
            object.__setattr__(self, "mask_size", 64)

    @property
    def is_mask(self) -> bool:
        return self.architecture == MASK_ARCHITECTURE

    @property
    def grid_stride(self) -> int:
        return 4 if self.is_mask else 8

    @property
    def mask_channels(self) -> int:
        return max(16, self.channels // 2)

    @property
    def sample_size(self) -> int:
        return self.mask_size // 2 if self.is_mask else self.roi_size

    @property
    def hidden_channels(self) -> int:
        return max(64, self.channels * 2)

    def to_dict(self) -> dict[str, int | str]:
        if self.is_mask:
            return {"input_size": self.input_size, "channels": self.channels,
                    "architecture": self.architecture, "mask_size": self.mask_size}
        # Keep the exact legacy schema: old models retain the same descriptor.
        return {"input_size": self.input_size, "vertices": self.vertices,
                "channels": self.channels, "roi_size": self.roi_size}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ModelConfig":
        if not isinstance(value, dict):
            raise ValueError("model config must be a JSON object")
        allowed = {field.name for field in fields(cls)}
        extra = set(value) - allowed
        if extra:
            raise ValueError(f"unsupported model config fields: {sorted(extra)}")
        return cls(**value)


def weight_shapes(config: ModelConfig) -> dict[str, tuple[int, ...]]:
    """Exact tensor schema for the architecture; never execute file-supplied code."""

    c = config.channels
    if config.is_mask:
        h = config.mask_channels
        shapes = {}
        def conv(name: str, output: int, inputs: int, kernel: int = 1) -> None:
            shapes[name + ".weight"] = (output, inputs, kernel, kernel)
            shapes[name + ".bias"] = (output,)
        conv("conv1", c // 2, 3, 3)
        conv("detail_dw", c // 2, 1, 3)
        conv("detail_pw", c, c // 2)
        for name in ("stage8", "stage16", "stage32", "context", "fuse16", "fuse8", "fuse4"):
            conv(name + "_dw", c, 1, 3)
            conv(name + "_pw", c, c)
        conv("objectness", 1, c)
        conv("box_head", 4, c)
        conv("mask_reduce", h, c + 2)
        conv("mask_dw", h, 1, 3)
        conv("mask_pw", h, h)
        conv("mask_refine_dw", h, 1, 3)
        conv("mask_out", 1, h)
        return shapes
    hidden = config.hidden_channels
    shapes = {
        "conv1.weight": (c // 2, 3, 3, 3),
        "conv1.bias": (c // 2,),
        "conv2.weight": (c, c // 2, 3, 3),
        "conv2.bias": (c,),
        "conv3.weight": (c, c, 3, 3),
        "conv3.bias": (c,),
        "objectness.weight": (1, c, 1, 1),
        "objectness.bias": (1,),
        "box_head.weight": (4, c, 1, 1),
        "box_head.bias": (4,),
        "roi_fc.weight": (hidden, c * config.roi_size * config.roi_size),
        "roi_fc.bias": (hidden,),
        "wrapper_head.weight": (config.vertices * 2, hidden),
        "wrapper_head.bias": (config.vertices * 2,),
    }
    for i in range(1, 5):
        shapes[f"context{i}.weight"] = (c, c, 3, 3)
        shapes[f"context{i}.bias"] = (c,)
    return shapes
