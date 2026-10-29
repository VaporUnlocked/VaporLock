# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

# Modified for Cloud Studio, 2026-10-17: package contains prompt-based inference
# only; the optional automatic-mask generator and its torchvision ops are omitted.

from .build_sam import (
    build_sam,
    build_sam_vit_h,
    build_sam_vit_l,
    build_sam_vit_b,
    build_sam_vit_t,
    sam_model_registry,
)
from .predictor import SamPredictor
