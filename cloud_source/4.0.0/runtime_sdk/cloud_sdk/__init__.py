"""Cloud Studio 4.0.0 standalone inference SDK.

The default NumPy path needs NumPy and Pillow only. Optional torch and ONNX
backends are imported only when explicitly requested.
"""
from .config import ARCHITECTURE, MASK_ARCHITECTURE, SUPPORTED_ARCHITECTURES, ModelConfig
from .format import CloudFormatError, compact_cloud, deployment_metadata, load_cloud
from .masks import decode_mask, encode_mask
from .runtime import CloudRuntime

__version__ = "4.0.0"
__all__ = ["ARCHITECTURE", "MASK_ARCHITECTURE", "SUPPORTED_ARCHITECTURES", "ModelConfig",
           "CloudFormatError", "load_cloud", "CloudRuntime", "compact_cloud",
           "deployment_metadata", "decode_mask", "encode_mask"]
