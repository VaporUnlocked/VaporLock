"""Disconnected fixed49 schema resource consumer, without theme authority.

Original r5 validators/resources remain unchanged. A public dataclass or digest
is NOT an accepted-theme/installation/composition receipt. Trusted coordinators
must bind original bytes, exact stage provenance, owner and current generation
before independently reviewed activation; old receipts cannot authorize it.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time

from vlui_schema import SchemaContract, SchemaSnapshot, VluiSchemaError, freeze, inspect_schemas

COMPONENT_CONTRACT_INDEX_SHA256 = "7aee77eddb59a43a7b8b233ac3734181ba1d13114064db9ce50e1c30ab97377a"
REGISTRY_SHA256 = "22a3c761d9afc132e6307c2398cbbcee4b8f57ab783daf67b1f8db53dfe50891"
SOURCE_MAP_INDEX_SHA256 = "9adb329b4932c8f93cf7106c5e3dcaf8e2af68f84d2f3d99933b882c627c4710"
BASE_CONTRACT_INDEX_SHA256 = "8798d95932c84552634232cbb4eadd72dd4b3e164494a1c5897de714a6674106"
NAMES = frozenset(("manifest.schema.json", "style.schema.json", "layout.schema.json", "semantics.json",
    "css-policy.json", "tokens.json", "presentation-defaults.json", "components-v1.json"))
IDENTITY = (COMPONENT_CONTRACT_INDEX_SHA256, BASE_CONTRACT_INDEX_SHA256, REGISTRY_SHA256, SOURCE_MAP_INDEX_SHA256)


@dataclass(frozen=True)
class ComponentContract:
    schema: SchemaContract
    identity: tuple[str, str, str, str]


@dataclass(frozen=True)
class ComponentSchemaSnapshot:
    schema: SchemaSnapshot
    resource_identity: tuple[str, str, str, str]
    validation_stage: str = field(init=False, default="component-schema-only")


def _identity_error():
    raise VluiSchemaError("CONTRACT_IDENTITY", "components")


def _read_fixed(path, expected_bytes=None):
    for part in (path, *path.parents):
        if part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400:
            _identity_error()
    if path.stat().st_size > 256 * 1024:
        _identity_error()
    raw = path.read_bytes()
    if len(raw) > 256 * 1024 or expected_bytes is not None and len(raw) != expected_bytes:
        _identity_error()
    return raw


def load_component_contract() -> ComponentContract:
    """Load the eight fixed trusted resources; no caller-controlled path."""
    from vlui_resource_root import bundled_data_root
    root = bundled_data_root() / "ui_system/contracts/component-contract-v1-r1"
    raw = _read_fixed(root / "index.json")
    if hashlib.sha256(raw).hexdigest() != COMPONENT_CONTRACT_INDEX_SHA256:
        _identity_error()
    index = json.loads(raw)
    if (index["registrySHA256"], index["sourceMapIndexSHA256"], index["baseContractIndexSHA256"]) != (REGISTRY_SHA256, SOURCE_MAP_INDEX_SHA256, BASE_CONTRACT_INDEX_SHA256):
        _identity_error()
    if len(index["files"]) != 8 or {e["path"] for e in index["files"]} != NAMES:
        _identity_error()
    documents = {}
    for entry in index["files"]:
        raw = _read_fixed(root / entry["path"], entry["bytes"])
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            _identity_error()
        documents[entry["path"]] = json.loads(raw)
    registry = documents["components-v1.json"]
    if len(registry["components"]) != 49:
        _identity_error()
    schema = SchemaContract(*(freeze(documents[name]) for name in
        ("manifest.schema.json", "style.schema.json", "layout.schema.json", "semantics.json", "tokens.json")),
        freeze({entry["id"]: entry for entry in registry["components"]}))
    return ComponentContract(schema, IDENTITY)


def inspect_component_schemas(structure, *, product_version="0.1.4", cancelled=None,
                              clock=time.monotonic, deadline=None) -> ComponentSchemaSnapshot:
    """Schema-only result with explicit fixed resource provenance; no activation."""
    contract = load_component_contract()
    return ComponentSchemaSnapshot(inspect_schemas(structure, contract.schema,
        product_version=product_version, cancelled=cancelled, clock=clock, deadline=deadline), contract.identity)
