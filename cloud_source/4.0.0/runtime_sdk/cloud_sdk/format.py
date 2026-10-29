"""Bounded, non-executable .cloud format with a strict tensor schema.

File layout (all integers little-endian):
  magic[8] = CLOUDDET; version:u32; JSON-bytes:u32; tensor-bytes:u64;
  SHA256[32] of the JSON followed by raw tensor bytes;
  UTF-8 JSON descriptor; contiguous little-endian float32 tensors.

The digest detects corruption, not authorship. No Python objects, pickle,
compressed archives, ONNX graphs, or executable kernels are embedded or loaded.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
import tempfile
from typing import Any, TYPE_CHECKING

import numpy as np

from .config import ARCHITECTURE, SUPPORTED_ARCHITECTURES, ModelConfig, weight_shapes

if TYPE_CHECKING:
    from .model import CloudNet


MAGIC = b"CLOUDDET"
FORMAT_VERSION = 1
HEADER = struct.Struct("<8sIIQ32s")
MAX_JSON_BYTES = 1024 * 1024
MAX_TENSOR_BYTES = 128 * 1024 * 1024


class CloudFormatError(ValueError):
    """File is corrupt, incompatible, or violates the bounded format schema."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CloudFormatError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise CloudFormatError(f"nonfinite JSON constant: {value}")


def _validate_json_tree(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise CloudFormatError("metadata exceeds maximum JSON nesting depth (32)")
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CloudFormatError("JSON object keys must be strings")
        for child in value.values():
            _validate_json_tree(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_json_tree(child, depth + 1)
    elif value is None or type(value) in (str, int, bool):
        return
    elif type(value) is float and np.isfinite(value):
        return
    else:
        raise CloudFormatError("metadata must contain finite, standard JSON values")


def _validate_weights(config: ModelConfig, weights: dict[str, np.ndarray]) -> None:
    expected = weight_shapes(config)
    if set(weights) != set(expected):
        missing, extra = set(expected) - set(weights), set(weights) - set(expected)
        raise CloudFormatError(f"unexpected tensor schema; missing={sorted(missing)}, "
                               f"extra={sorted(extra)}")
    for name, shape in expected.items():
        array = weights[name]
        if array.shape != shape:
            raise CloudFormatError(f"wrong shape for {name}: {array.shape}; expected {shape}")
        if array.dtype != np.dtype("<f4"):
            raise CloudFormatError(f"{name} must contain little-endian float32 values")
        if not np.isfinite(array).all():
            raise CloudFormatError(f"nonfinite tensor values in {name}")


def deployment_metadata(metadata: dict | None = None) -> dict:
    """Return only the small disclosure needed by a deployed model.

    Training settings, project names, labels, paths, metrics, optimizer state,
    and history belong to the training run, not its inference artifact. The
    synthetic flag is retained so a software-test model cannot lose that label
    when shared. Architecture and tensor configuration are separate required
    descriptor fields, not user metadata.
    """

    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise CloudFormatError("metadata must be a JSON object")
    _validate_json_tree(metadata)
    if "synthetic" in metadata:
        if type(metadata["synthetic"]) is not bool:
            raise CloudFormatError("deployment synthetic metadata must be a boolean")
        return {"synthetic": metadata["synthetic"]}
    return {}


def _write_container(path: str | Path, descriptor: dict, parts: list[bytes]) -> dict:
    """Write one already validated descriptor and its exact tensor byte stream."""

    try:
        descriptor_bytes = json.dumps(descriptor, ensure_ascii=False, allow_nan=False,
                                      sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise CloudFormatError(f"metadata is not valid bounded JSON: {exc}") from exc
    if len(descriptor_bytes) > MAX_JSON_BYTES:
        raise CloudFormatError("JSON descriptor exceeds format size limit")
    payload_size = sum(map(len, parts))
    if not 0 < payload_size <= MAX_TENSOR_BYTES:
        raise CloudFormatError("tensor payload exceeds format size limit")
    digest = hashlib.sha256(descriptor_bytes)
    for part in parts:
        digest.update(part)
    header = HEADER.pack(MAGIC, FORMAT_VERSION, len(descriptor_bytes), payload_size, digest.digest())
    file_digest = hashlib.sha256(header)
    file_digest.update(descriptor_bytes)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp",
                                     dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(header)
            stream.write(descriptor_bytes)
            for part in parts:
                stream.write(part)
                file_digest.update(part)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"file_bytes": HEADER.size + len(descriptor_bytes) + payload_size,
            "descriptor_bytes": len(descriptor_bytes), "payload_bytes": payload_size,
            "file_sha256": file_digest.hexdigest()}


def save_cloud(path: str | Path, model: "CloudNet", metadata: dict | None = None,
               *, deployment_only: bool = False) -> None:
    """Atomically write architecture and learned FP32 weights.

    New training output uses ``deployment_only=True``. The default preserves
    legacy callers that intentionally attach bounded JSON metadata; every
    format-1 loader continues to read either form.
    """

    config = ModelConfig.from_dict(model.config.to_dict())
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise CloudFormatError("metadata must be a JSON object")
    _validate_json_tree(metadata)
    if type(deployment_only) is not bool:
        raise CloudFormatError("deployment_only must be a boolean")
    if deployment_only or config.is_mask:
        metadata = deployment_metadata(metadata)
    weights = {
        name: tensor.detach().cpu().float().contiguous().numpy().astype("<f4", copy=False)
        for name, tensor in model.state_dict().items()
    }
    _validate_weights(config, weights)
    directory: list[dict[str, Any]] = []
    parts: list[bytes] = []
    offset = 0
    for name in sorted(weights):
        array = weights[name]
        part = array.tobytes(order="C")
        directory.append({"name": name, "shape": list(array.shape), "dtype": "<f4",
                          "offset": offset, "nbytes": len(part)})
        parts.append(part)
        offset += len(part)
    if offset > MAX_TENSOR_BYTES:
        raise CloudFormatError("tensor payload exceeds format size limit")
    descriptor = {"architecture": config.architecture, "config": config.to_dict(),
                  "metadata": metadata, "tensors": directory}
    _write_container(path, descriptor, parts)


def _load_container(path: str | Path) -> tuple[ModelConfig, dict[str, np.ndarray], dict, bytes, bytes]:
    """Validate the complete container before exposing any tensors.

    The loader does not import PyTorch. Every tensor's name, shape, dtype, size,
    offset, finiteness, and membership are checked against an allowlisted model.
    """

    source = Path(path)
    with source.open("rb") as stream:
        file_size = os.fstat(stream.fileno()).st_size
        if file_size < HEADER.size:
            raise CloudFormatError("truncated .cloud header")
        raw_header = stream.read(HEADER.size)
        magic, version, json_size, payload_size, checksum = HEADER.unpack(raw_header)
        if magic != MAGIC:
            raise CloudFormatError("not a native CLOUDDET .cloud file")
        if version != FORMAT_VERSION:
            raise CloudFormatError(f"unsupported .cloud format version {version}")
        if not 0 < json_size <= MAX_JSON_BYTES:
            raise CloudFormatError("JSON descriptor size is outside supported limits")
        if not 0 < payload_size <= MAX_TENSOR_BYTES:
            raise CloudFormatError("tensor payload size is outside supported limits")
        if file_size != HEADER.size + json_size + payload_size:
            raise CloudFormatError("file size does not match its declared lengths")
        descriptor_bytes = stream.read(json_size)
        try:
            descriptor = json.loads(descriptor_bytes.decode("utf-8"),
                                    object_pairs_hook=_no_duplicate_keys,
                                    parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise CloudFormatError(f"invalid .cloud descriptor: {exc}") from exc
        _validate_json_tree(descriptor)
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "architecture", "config", "metadata", "tensors"
        }:
            raise CloudFormatError("unsupported .cloud descriptor fields")
        if descriptor["architecture"] not in SUPPORTED_ARCHITECTURES:
            raise CloudFormatError("unsupported model architecture")
        try:
            config = ModelConfig.from_dict(descriptor["config"])
            if config.architecture != descriptor["architecture"]:
                raise ValueError("architecture and configuration do not agree")
        except (TypeError, ValueError) as exc:
            raise CloudFormatError(f"invalid model config: {exc}") from exc
        if not isinstance(descriptor["metadata"], dict):
            raise CloudFormatError("metadata must be a JSON object")
        directory = descriptor["tensors"]
        expected = weight_shapes(config)
        if not isinstance(directory, list) or len(directory) != len(expected):
            raise CloudFormatError("invalid tensor directory length")
        position = 0
        seen: set[str] = set()
        for entry in directory:
            if not isinstance(entry, dict) or set(entry) != {
                "name", "shape", "dtype", "offset", "nbytes"
            }:
                raise CloudFormatError("invalid tensor directory entry")
            name = entry["name"]
            if not isinstance(name, str) or name not in expected or name in seen:
                raise CloudFormatError("unknown or duplicate tensor name")
            seen.add(name)
            shape = entry["shape"]
            if (not isinstance(shape, list) or not all(type(n) is int for n in shape)
                    or tuple(shape) != expected[name]):
                raise CloudFormatError(f"wrong declared shape for {name}")
            if entry["dtype"] != "<f4":
                raise CloudFormatError("only raw little-endian float32 tensors are supported")
            nbytes = int(np.prod(expected[name], dtype=np.int64)) * 4
            if type(entry["offset"]) is not int or entry["offset"] != position:
                raise CloudFormatError("tensor payload must be contiguous without overlaps")
            if type(entry["nbytes"]) is not int or entry["nbytes"] != nbytes:
                raise CloudFormatError(f"wrong tensor byte count for {name}")
            position += nbytes
        if seen != set(expected) or position != payload_size:
            raise CloudFormatError("tensor directory does not exactly cover the payload")
        payload = stream.read(payload_size)
        if len(payload) != payload_size or stream.read(1):
            raise CloudFormatError("truncated or unexpectedly extended tensor payload")
        digest = hashlib.sha256(descriptor_bytes)
        digest.update(payload)
        if not hmac.compare_digest(digest.digest(), checksum):
            raise CloudFormatError(".cloud SHA-256 checksum mismatch")
    weights = {
        entry["name"]: np.frombuffer(payload, dtype="<f4", count=entry["nbytes"] // 4,
                                      offset=entry["offset"]).reshape(entry["shape"]).copy()
        for entry in directory
    }
    _validate_weights(config, weights)
    return config, weights, descriptor, descriptor_bytes, payload


def load_cloud(path: str | Path) -> tuple[ModelConfig, dict[str, np.ndarray], dict]:
    """Validate the complete container before exposing its config and tensors.

    No PyTorch import or executable deserialization is involved. Legacy rich
    metadata and compact deployment metadata are both valid format-1 files.
    """

    config, weights, descriptor, _, _ = _load_container(path)
    return config, weights, descriptor["metadata"]


def compact_cloud(source: str | Path, destination: str | Path) -> dict:
    """Remove training metadata without changing one learned tensor byte.

    The source is fully validated once before output is opened. Its original
    tensor directory order and entire raw payload are preserved, including
    signed zero values. Atomic replacement also permits an explicitly chosen
    in-place destination. No model construction or retraining is required.
    """

    config, weights, descriptor, original_json, payload = _load_container(source)
    original_metadata = descriptor["metadata"]
    metadata = deployment_metadata(original_metadata)
    original_digest = hashlib.sha256(original_json)
    original_digest.update(payload)
    original_header = HEADER.pack(MAGIC, FORMAT_VERSION, len(original_json), len(payload), original_digest.digest())
    original_file_digest = hashlib.sha256(original_header)
    original_file_digest.update(original_json)
    original_file_digest.update(payload)
    descriptor["metadata"] = metadata
    written = _write_container(destination, descriptor, [payload])
    before_bytes = HEADER.size + len(original_json) + len(payload)
    return {"source": str(Path(source).resolve()), "output": str(Path(destination).resolve()),
            "format_version": FORMAT_VERSION, "architecture": config.architecture,
            "model_config": config.to_dict(), "tensor_count": len(weights),
            "before_bytes": before_bytes, "after_bytes": written["file_bytes"],
            "bytes_removed": before_bytes - written["file_bytes"],
            "descriptor_bytes_before": len(original_json),
            "descriptor_bytes_after": written["descriptor_bytes"],
            "tensor_payload_bytes": len(payload), "tensor_payload_unchanged": True,
            "tensor_payload_sha256": hashlib.sha256(payload).hexdigest(),
            "source_sha256": original_file_digest.hexdigest(), "output_sha256": written["file_sha256"],
            "removed_metadata_keys": sorted(set(original_metadata) - set(metadata)),
            "retained_metadata": metadata}


def load_torch_model(path: str | Path, device: str = "cpu") -> "CloudNet":
    """Instantiate the allowlisted model from validated raw tensors, with no pickle."""

    try:
        import torch
        from .model import CloudNet
    except ImportError as exc:
        raise RuntimeError("PyTorch is required for this backend; install the training dependencies") from exc
    config, weights, _ = load_cloud(path)
    with torch.random.fork_rng(devices=[]):
        model = CloudNet(config)
    model.load_state_dict({name: torch.from_numpy(value) for name, value in weights.items()}, strict=True)
    return model.to(device).eval()
