"""Small local equivalents of TinyViT's optional timm helper dependencies.

Written for Cloud Studio, 2026-10-17. The loaded MobileSAM uses drop_path_rate=0;
DropPath is also a no-op during evaluation. Weight initialization is provided by
PyTorch itself and is replaced by the strict pretrained state-dict load.
"""

import torch
from torch import nn


trunc_normal_ = nn.init.trunc_normal_


def to_2tuple(value):
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            raise ValueError("Expected two dimensions")
        return tuple(value)
    return (value, value)


class DropPath(nn.Module):
    def __init__(self, drop_prob=0.0):
        super().__init__()
        self.drop_prob = float(drop_prob or 0.0)
        if not 0 <= self.drop_prob <= 1:
            raise ValueError("Drop probability must be in [0,1]")

    def forward(self, value):
        if not self.training or self.drop_prob == 0:
            return value
        keep_probability = 1 - self.drop_prob
        if keep_probability == 0:
            return torch.zeros_like(value)
        shape = (value.shape[0],) + (1,) * (value.ndim - 1)
        gate = value.new_empty(shape).bernoulli_(keep_probability)
        return value * gate / keep_probability
