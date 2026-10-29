"""Versioned research detectors with box-guided contour or raster-mask heads.

This is deliberately a readable baseline architecture. It is neither a
pretrained player detector nor an optimized CUDA inference engine.
"""

from __future__ import annotations

import math

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from .config import ModelConfig


def perimeter_template(count: int, *, dtype: torch.dtype = torch.float32) -> Tensor:
    """Clockwise square perimeter in image coordinates, starting at top-left."""

    distance = torch.arange(count, dtype=dtype) * (4.0 / count)
    x = torch.where(distance < 1, distance,
                    torch.where(distance < 2, torch.ones_like(distance),
                                torch.where(distance < 3, 3 - distance,
                                            torch.zeros_like(distance))))
    y = torch.where(distance < 1, torch.zeros_like(distance),
                    torch.where(distance < 2, distance - 1,
                                torch.where(distance < 3, torch.ones_like(distance),
                                            4 - distance)))
    return torch.stack((x, y), dim=-1)


def decode_boxes(raw_boxes: Tensor, config: ModelConfig | None = None) -> Tensor:
    """Decode B,4,H,W cell predictions to normalized B,H,W,4 xyxy boxes."""

    if not torch.jit.is_tracing():
        if raw_boxes.ndim != 4 or raw_boxes.shape[1] != 4:
            raise ValueError("raw_boxes must have shape B,4,H,W")
    values = raw_boxes.permute(0, 2, 3, 1).sigmoid()
    height, width = raw_boxes.shape[-2:]
    y, x = torch.meshgrid(
        torch.arange(height, device=raw_boxes.device, dtype=raw_boxes.dtype),
        torch.arange(width, device=raw_boxes.device, dtype=raw_boxes.dtype),
        indexing="ij",
    )
    offsets = values[..., :2]
    if config is not None and config.is_mask:
        # A nearby free cell can represent a crowded object's true center.
        # At zero logits the center is still the center of the assigned cell.
        offsets = offsets * 3.0 - 1.0
    center_x = (x + offsets[..., 0]) / width
    center_y = (y + offsets[..., 1]) / height
    half_width, half_height = values[..., 2] * 0.5, values[..., 3] * 0.5
    return torch.stack((center_x - half_width, center_y - half_height,
                        center_x + half_width, center_y + half_height), dim=-1).clamp(0, 1)


class CloudNet(nn.Module):
    """Legacy S1 contours or S2 multiscale features and spatial ROI masks."""

    def __init__(self, config: ModelConfig | None = None):
        super().__init__()
        self.config = config or ModelConfig()
        if self.config.is_mask:
            self._init_mask_model()
            return
        c = self.config.channels
        hidden = self.config.hidden_channels
        self.conv1 = nn.Conv2d(3, c // 2, 3, stride=2, padding=1)
        self.conv2 = nn.Conv2d(c // 2, c, 3, stride=2, padding=1)
        self.conv3 = nn.Conv2d(c, c, 3, stride=2, padding=1)
        # A torso-local 15-pixel receptive field cannot infer a player's full box.
        # Dilated context extends the theoretical receptive field to 255 pixels
        # while preserving stride-eight localization and shared contour features.
        for i, dilation in enumerate((1, 2, 4, 8), start=1):
            setattr(self, f"context{i}", nn.Conv2d(c, c, 3, padding=dilation,
                                                   dilation=dilation))
        self.objectness = nn.Conv2d(c, 1, 1)
        self.box_head = nn.Conv2d(c, 4, 1)
        self.roi_fc = nn.Linear(c * self.config.roi_size ** 2, hidden)
        self.wrapper_head = nn.Linear(hidden, self.config.vertices * 2)
        # Derived constants are specified by the architecture, not serialized weights.
        self.register_buffer("template", perimeter_template(self.config.vertices),
                             persistent=False)
        self.register_buffer("template_logits", torch.logit(self.template * 0.90 + 0.05),
                             persistent=False)
        line = torch.linspace(0, 1, self.config.roi_size)
        gy, gx = torch.meshgrid(line, line, indexing="ij")
        self.register_buffer("roi_grid", torch.stack((gx, gy), dim=-1),
                             persistent=False)
        # Sparse positives are typical. The initial confidence is approximately 1%.
        nn.init.constant_(self.objectness.bias, math.log(0.01 / 0.99))
        nn.init.zeros_(self.box_head.bias)
        # Initial objects occupy about 20% of each image dimension.
        with torch.no_grad():
            self.box_head.bias[2:] = math.log(0.2 / 0.8)
        # Initial wrappers are a slightly inset perimeter. Smooth sigmoid bounds
        # avoid the dead gradients that hard clipping at box edges would create.
        nn.init.zeros_(self.wrapper_head.weight)
        nn.init.zeros_(self.wrapper_head.bias)

    def _init_mask_model(self) -> None:
        """Spatial S2 model; no learned polygon template or flattened ROI FC.

        Fine stride-four features receive context from strides 8, 16 and 32.
        Depthwise spatial filtering and pointwise channel mixing limit learned
        storage without changing the FP32 representation of learned values.
        """
        c, h = self.config.channels, self.config.mask_channels
        self.conv1 = nn.Conv2d(3, c // 2, 3, stride=2, padding=1)
        self.detail_dw = nn.Conv2d(c // 2, c // 2, 3, stride=2, padding=1, groups=c // 2)
        self.detail_pw = nn.Conv2d(c // 2, c, 1)
        for name, stride, dilation in (("stage8", 2, 1), ("stage16", 2, 1),
                                      ("stage32", 2, 1), ("context", 1, 2),
                                      ("fuse16", 1, 1), ("fuse8", 1, 1), ("fuse4", 1, 1)):
            setattr(self, name + "_dw", nn.Conv2d(c, c, 3, stride=stride,
                                                padding=dilation, dilation=dilation, groups=c))
            setattr(self, name + "_pw", nn.Conv2d(c, c, 1))
        self.objectness = nn.Conv2d(c, 1, 1)
        self.box_head = nn.Conv2d(c, 4, 1)
        self.mask_reduce = nn.Conv2d(c + 2, h, 1)
        self.mask_dw = nn.Conv2d(h, h, 3, padding=1, groups=h)
        self.mask_pw = nn.Conv2d(h, h, 1)
        self.mask_refine_dw = nn.Conv2d(h, h, 3, padding=1, groups=h)
        self.mask_out = nn.Conv2d(h, 1, 1)
        line = (torch.arange(self.config.sample_size, dtype=torch.float32) + 0.5) / self.config.sample_size
        gy, gx = torch.meshgrid(line, line, indexing="ij")
        self.register_buffer("roi_grid", torch.stack((gx, gy), dim=-1), persistent=False)
        self.register_buffer("roi_coordinates", torch.stack((gx * 2 - 1, gy * 2 - 1))[None],
                             persistent=False)
        nn.init.constant_(self.objectness.bias, math.log(0.01 / 0.99))
        nn.init.zeros_(self.box_head.bias)
        with torch.no_grad():
            self.box_head.bias[2:] = math.log(0.2 / 0.8)
        nn.init.constant_(self.mask_out.bias, -2.0)

    def _separable(self, values: Tensor, name: str) -> Tensor:
        return F.relu(getattr(self, name + "_pw")(F.relu(getattr(self, name + "_dw")(values))))

    def _detect_mask(self, images: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        detail = self._separable(F.relu(self.conv1(images)), "detail")
        stage8 = self._separable(detail, "stage8")
        stage16 = self._separable(stage8, "stage16")
        stage32 = self._separable(stage16, "stage32")
        stage32 = (stage32 + self._separable(stage32, "context")) * 0.5
        pyramid16 = self._separable((stage16 + F.interpolate(stage32, scale_factor=2, mode="nearest")) * 0.5,
                                   "fuse16")
        pyramid8 = self._separable((stage8 + F.interpolate(pyramid16, scale_factor=2, mode="nearest")) * 0.5,
                                  "fuse8")
        features = self._separable((detail + F.interpolate(pyramid8, scale_factor=2, mode="nearest")) * 0.5,
                                  "fuse4")
        return features, self.objectness(features), self.box_head(features)

    def detect(self, images: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        if not torch.jit.is_tracing():
            if images.ndim != 4 or images.shape[1] != 3:
                raise ValueError("images must have shape B,3,S,S")
            if tuple(images.shape[-2:]) != (self.config.input_size,) * 2:
                raise ValueError(f"model expects {self.config.input_size} by "
                                 f"{self.config.input_size} letterboxed images")
        if self.config.is_mask:
            return self._detect_mask(images)
        features = F.relu(self.conv1(images))
        features = F.relu(self.conv2(features))
        features = F.relu(self.conv3(features))
        for i in range(1, 5):
            features = F.relu(features + getattr(self, f"context{i}")(features))
        return features, self.objectness(features), self.box_head(features)

    def forward(self, images: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        return self.detect(images)

    @staticmethod
    def decode_boxes(raw_boxes: Tensor, config: ModelConfig | None = None) -> Tensor:
        return decode_boxes(raw_boxes, config)

    def _mask_from_rois(self, roi: Tensor) -> Tensor:
        coordinates = self.roi_coordinates.to(roi).expand(roi.shape[0], -1, -1, -1)
        encoded = F.relu(self.mask_reduce(torch.cat((roi, coordinates), dim=1)))
        encoded = F.relu(encoded + F.relu(self.mask_pw(F.relu(self.mask_dw(encoded)))))
        output_size = (self.config.mask_size, self.config.mask_size)
        resizer = getattr(self, "training_mask_resizer", None)
        if not torch.jit.is_tracing() and torch.is_grad_enabled() and callable(resizer):
            encoded = resizer(encoded, output_size)
        else:
            encoded = F.interpolate(encoded, size=output_size, mode="bilinear", align_corners=False)
        encoded = F.relu(encoded + self.mask_refine_dw(encoded))
        return self.mask_out(encoded)

    def _sample_shared_mask_rois(self, features: Tensor, grid: Tensor,
                                 batch_indices: Tensor) -> Tensor:
        """Sample all ROIs without copying a complete feature map for each.

        Stack feature images vertically once, remap each ROI into its batch
        slice, and sample one concatenated grid. Boxes are clipped beforehand,
        so bilinear interpolation never crosses between those image slices.
        Shape arithmetic remains in the ONNX graph for dynamic B and N.
        """
        batch, channels, height, width = features.shape
        stacked = features.permute(1, 0, 2, 3).reshape(1, channels, batch * height, width)
        denominator = batch * height - 1
        gy = ((grid[..., 1] + 1) * (height - 1) / denominator
              + (2 * batch_indices.to(grid) * height / denominator)[:, None, None] - 1)
        stacked_grid = torch.stack((grid[..., 0], gy), dim=-1)
        count, size = grid.shape[0], self.config.sample_size
        sampled = F.grid_sample(stacked, stacked_grid.reshape(1, count * size, size, 2),
                                mode="bilinear", padding_mode="zeros", align_corners=True)
        return sampled.reshape(channels, count, size, size).permute(1, 0, 2, 3)

    def wrap(self, features: Tensor, boxes: Tensor, batch_indices: Tensor) -> Tensor:
        """Predict one encasing candidate per guiding normalized box.

        S1 returns N,K,2 ordered polygon coordinates. S2 returns N,1,M,M raw
        foreground logits; its raster representation permits holes and separate
        components. Both receive shared features from the detector backbone.
        Invalid S1 loops and empty/nonfinite S2 masks are rejected by the runtime.
        """

        if not torch.jit.is_tracing():
            if features.ndim != 4 or features.shape[1] != self.config.channels:
                raise ValueError("features have the wrong channel count or rank")
            if boxes.ndim != 2 or boxes.shape[1] != 4:
                raise ValueError("boxes must have shape N,4")
            if batch_indices.ndim != 1 or batch_indices.shape[0] != boxes.shape[0]:
                raise ValueError("batch_indices must have shape N")
            if batch_indices.dtype != torch.int64:
                raise ValueError("batch_indices must be int64")
            if boxes.shape[0] == 0:
                # Preserve a differentiable connection for empty optional batches.
                if self.config.is_mask:
                    return features.sum().mul(0).expand(0, 1, self.config.mask_size, self.config.mask_size)
                return features.sum().mul(0).expand(0, self.config.vertices, 2)
            if bool(torch.any(batch_indices < 0)) or bool(torch.any(batch_indices >= features.shape[0])):
                raise ValueError("batch index is outside the feature batch")
        boxes = boxes.clamp(0, 1)
        origin = boxes[:, None, None, :2]
        extent = (boxes[:, 2:] - boxes[:, :2]).clamp_min(0)
        grid = (origin + self.roi_grid.to(boxes) * extent[:, None, None, :]) * 2 - 1
        grid = grid.to(dtype=features.dtype)
        # The trainer can install an equivalent strict-determinism sampler.
        # Inference/SDK construction has no dependency on training modules.
        sampler = getattr(self, "training_roi_sampler", None)
        if not torch.jit.is_tracing() and torch.is_grad_enabled() and callable(sampler):
            roi = sampler(features, grid, batch_indices)
        elif self.config.is_mask and (not torch.is_grad_enabled() or torch.jit.is_tracing()):
            roi = self._sample_shared_mask_rois(features, grid, batch_indices)
        else:
            selected_features = features.index_select(0, batch_indices)
            # GPU AMP can make features half precision while boxes remain
            # float32; grid_sample needs compatible dtypes without autocast.
            roi = F.grid_sample(selected_features, grid, mode="bilinear",
                                padding_mode="zeros", align_corners=True)
        if self.config.is_mask:
            return self._mask_from_rois(roi)
        encoded = F.relu(self.roi_fc(roi.flatten(1)))
        offsets = torch.tanh(self.wrapper_head(encoded)).reshape(-1, self.config.vertices, 2)
        relative = torch.sigmoid(self.template_logits.to(offsets) + 5.0 * offsets)
        return boxes[:, None, :2] + relative * extent[:, None, :]
