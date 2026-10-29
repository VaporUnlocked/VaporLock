"""Format-1 schema stage, disconnected from installation and rendering.

SchemaSnapshot is NOT an accepted theme. CSS semantics and every raster's
isolated bounded decoding must pass before installation, preview or serving.
The checked-in r5 schemas/registry are trusted resources, never theme inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from types import MappingProxyType
from typing import Callable, Mapping
import unicodedata

from vlui_archive import StructureSnapshot, canonical_member, strict_json


CONTRACT_INDEX_SHA256 = "8798d95932c84552634232cbb4eadd72dd4b3e164494a1c5897de714a6674106"
REGISTRY_SHA256 = "6b502e69a6763a23abb0f04eaeaaf896b5f108e97daedb92f6f9771defa0cd4b"
ASCII_WHITESPACE = " \t\r\n\f"


class VluiSchemaError(ValueError):
    def __init__(self, code: str, field: str):
        self.code, self.field = code, field
        super().__init__(f"{code}: {field}")


def _require(condition: bool, code: str, field: str) -> None:
    if not condition:
        raise VluiSchemaError(code, field)


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def _numeric(value) -> bool:
    if type(value) is int:
        # Python's math.isfinite coerces big integers and may overflow. Match
        # the browser's finite Number domain before applying the small bounds.
        return -sys.float_info.max <= value <= sys.float_info.max
    return type(value) is float and math.isfinite(value)


def _canonical_number(value):
    return int(value) if int(value) == value else value


@dataclass(frozen=True)
class SchemaContract:
    manifest: Mapping
    style: Mapping
    layout: Mapping
    semantics: Mapping
    tokens: Mapping
    components: Mapping


def load_bundled_contract() -> SchemaContract:
    """Load fixed trusted resources. No user-controlled path argument exists."""
    from vlui_resource_root import bundled_data_root
    root = bundled_data_root()
    source = root / "ui_system/proposals/contract-v1-r5"
    index_bytes = (source / "index.json").read_bytes()
    _require(hashlib.sha256(index_bytes).hexdigest() == CONTRACT_INDEX_SHA256,
             "CONTRACT_IDENTITY", "contract")
    index = json.loads(index_bytes)
    documents = {}
    for entry in index["files"]:
        # Names come from the verified bundled index, never an archive manifest.
        payload = (source / entry["path"]).read_bytes()
        _require(len(payload) == entry["bytes"] and hashlib.sha256(payload).hexdigest() == entry["sha256"],
                 "CONTRACT_IDENTITY", "contract")
        documents[entry["path"]] = json.loads(payload)
    registry_bytes = (root / "ui_system/handoffs/foundation-v1-r1/components-v1.json").read_bytes()
    _require(hashlib.sha256(registry_bytes).hexdigest() == REGISTRY_SHA256,
             "CONTRACT_IDENTITY", "components")
    registry = json.loads(registry_bytes)
    return SchemaContract(*(freeze(documents[name]) for name in
                            ("manifest.schema.json", "style.schema.json", "layout.schema.json", "semantics.json", "tokens.json")),
                          freeze({entry["id"]: entry for entry in registry["components"]}))


def validate_value(value, definition: Mapping, field: str, checkpoint: Callable[[], None]):
    """Validate the exact subset used by our pinned, closed bundled schemas."""
    checkpoint()
    kind = definition.get("type")
    if kind == "object":
        _require(type(value) is dict, "SCHEMA_TYPE", field)
    elif kind == "string":
        _require(type(value) is str, "SCHEMA_TYPE", field)
    elif kind == "boolean":
        _require(type(value) is bool, "SCHEMA_TYPE", field)
    elif kind in ("number", "integer"):
        _require(_numeric(value), "SCHEMA_TYPE", field)
        if kind == "integer":
            _require(int(value) == value, "SCHEMA_INTEGER", field)
        value = _canonical_number(value)
    if "const" in definition:
        expected = definition["const"]
        same_type = type(value) is bool if type(expected) is bool else type(value) is type(expected)
        _require(same_type and value == expected, "SCHEMA_CONSTANT", field)
    if "enum" in definition:
        _require(any(type(value) is type(expected) and value == expected for expected in definition["enum"]),
                 "SCHEMA_ENUM", field)
    if type(value) is str:
        _require(definition.get("minLength", 0) <= len(value) <= definition.get("maxLength", 4096),
                 "SCHEMA_STRING_LENGTH", field)
        if "pattern" in definition:
            _require(re.fullmatch(definition["pattern"], value, flags=re.ASCII) is not None,
                     "SCHEMA_LEXICAL", field)
    if _numeric(value):
        _require(definition.get("minimum", -math.inf) <= value <= definition.get("maximum", math.inf),
                 "SCHEMA_NUMBER_BOUND", field)
    if type(value) is dict:
        _require(definition.get("minProperties", 0) <= len(value) <= definition.get("maxProperties", 128),
                 "SCHEMA_OBJECT_SIZE", field)
        _require(all(key in value for key in definition.get("required", ())), "SCHEMA_REQUIRED", field)
        properties, extra = definition.get("properties", {}), definition.get("additionalProperties", True)
        result = {}
        for key, item in value.items():
            if "propertyNames" in definition:
                validate_value(key, definition["propertyNames"], field, checkpoint)
            if key in properties:
                result[key] = validate_value(item, properties[key], f"{field}.{key}", checkpoint)
            elif isinstance(extra, Mapping):
                # propertyNames was already checked against the fixed registry.
                result[key] = validate_value(item, extra, f"{field}.{key}", checkpoint)
            else:
                _require(extra is True, "SCHEMA_UNKNOWN_FIELD", field)
                result[key] = item
        value = result
    if "not" in definition:
        try:
            validate_value(value, definition["not"], field, checkpoint)
        except VluiSchemaError as error:
            if error.code in ("VALIDATION_CANCELLED", "VALIDATION_DEADLINE"):
                raise
        else:
            raise VluiSchemaError("SCHEMA_FORBIDDEN_COMBINATION", field)
    for keyword in ("oneOf", "anyOf"):
        if keyword not in definition:
            continue
        accepted = []
        for branch in definition[keyword]:
            try:
                accepted.append(validate_value(value, branch, field, checkpoint))
            except VluiSchemaError as error:
                if error.code in ("VALIDATION_CANCELLED", "VALIDATION_DEADLINE"):
                    raise
        _require(len(accepted) == 1 if keyword == "oneOf" else bool(accepted), "SCHEMA_COMBINATION", field)
        value = accepted[0]
    return value


def normalize_color(value: str) -> str:
    # The schema has already enforced ASCII syntax and exact whole-string match.
    if value.startswith("#"):
        return value.lower()
    parts = value[5:-1].split(",")
    channels = [int(part.strip(" ")) for part in parts[:3]]
    alpha = float(parts[3].strip(" "))
    _require(all(0 <= channel <= 255 for channel in channels) and 0 <= alpha <= 1,
             "COLOR_CHANNEL_BOUND", "style.json")
    return f"rgba({','.join(str(channel) for channel in channels)},{format(alpha, '.6f').rstrip('0').rstrip('.') or '0'})"


def _metadata(value: str, field: str, byte_limit: int, *, description=False, name=False):
    _require(len(value.encode("utf-8")) <= byte_limit, "METADATA_BYTE_BOUND", field)
    for character in value:
        code = ord(character)
        forbidden = (code <= 0x1F or 0x7F <= code <= 0x9F or code in (0x061C, 0x200E, 0x200F)
                     or 0x202A <= code <= 0x202E or 0x2066 <= code <= 0x2069 or 0xD800 <= code <= 0xDFFF)
        if description and code in (0x09, 0x0A):
            forbidden = False
        _require(not forbidden, "METADATA_CONTROL", field)
    if name:
        _require(any(not character.isspace() and unicodedata.category(character) != "Cf" for character in value),
                 "METADATA_EMPTY_NAME", field)


def semantic_version(value: str):
    _require(type(value) is str and len(value) <= 64 and re.fullmatch(
        r"(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", value) is not None,
        "VERSION_LEXICAL", "manifest.json")
    core, _, _build = value.partition("+")
    base, separator, pre = core.partition("-")
    identifiers = tuple(pre.split(".")) if separator else ()
    for identifier in identifiers:
        _require(not (identifier.isascii() and identifier.isdecimal() and len(identifier) > 1 and identifier[0] == "0"),
                 "VERSION_PRERELEASE_ZERO", "manifest.json")
    return tuple(int(part) for part in base.split(".")), identifiers


def compare_versions(left: str, right: str) -> int:
    a, apre = semantic_version(left)
    b, bpre = semantic_version(right)
    if a != b:
        return (a > b) - (a < b)
    if not apre or not bpre:
        return (bool(bpre) > bool(apre)) - (bool(bpre) < bool(apre))
    for first, second in zip(apre, bpre):
        if first == second:
            continue
        nfirst, nsecond = first.isdecimal(), second.isdecimal()
        if nfirst and nsecond:
            key_a, key_b = (len(first), first), (len(second), second)
        elif nfirst != nsecond:
            return -1 if nfirst else 1
        else:
            key_a, key_b = first, second
        return (key_a > key_b) - (key_a < key_b)
    return (len(apre) > len(bpre)) - (len(apre) < len(bpre))


@dataclass(frozen=True)
class SchemaSnapshot:
    structure: StructureSnapshot
    manifest: Mapping
    style: Mapping
    effective_tokens: Mapping
    gradient_enabled: bool
    layout: Mapping | None
    custom_css_source: str | None
    compatible: bool
    validation_stage: str = "schema-only"


def inspect_schemas(structure: StructureSnapshot, contract: SchemaContract, *, product_version="0.1.4",
                    cancelled: Callable[[], bool] | None = None, clock: Callable[[], float] = time.monotonic,
                    deadline: float | None = None) -> SchemaSnapshot:
    limit = min(clock() + 30.0, deadline) if deadline is not None else clock() + 30.0

    def checkpoint():
        _require(cancelled is None or not cancelled(), "VALIDATION_CANCELLED", "theme")
        _require(clock() <= limit, "VALIDATION_DEADLINE", "theme")

    checkpoint()
    _require(isinstance(structure, StructureSnapshot) and structure.validation_stage == "container-only",
             "CONTAINER_STAGE_REQUIRED", "theme")
    budget = [0]
    manifest = validate_value(strict_json(structure.members["manifest.json"], node_budget=budget), contract.manifest, "manifest.json", checkpoint)
    theme = manifest["theme"]
    _metadata(theme["name"], "manifest.json.theme.name", 384, name=True)
    _metadata(theme["author"], "manifest.json.theme.author", 384)
    _metadata(theme["description"], "manifest.json.theme.description", 2048, description=True)
    semantic_version(theme["version"])
    compatible = compare_versions(product_version, manifest["requirements"]["minimumVaporLockVersion"]) >= 0
    features = manifest["features"]
    has_media = any(name == "preview.webp" or name.startswith("assets/") for name in structure.members)
    for flag, present in (("media", has_media), ("layout", "layout.json" in structure.members), ("customCSS", "custom.css" in structure.members)):
        _require(features[flag] is present, "FEATURE_PRESENCE", "manifest.json.features")
    authored = validate_value(strict_json(structure.members["style.json"], node_budget=budget), contract.style, "style.json", checkpoint)
    effective_tokens = {name: spec["default"] for name, spec in contract.tokens.items()}
    for name, value in tuple(authored.items()):
        if name not in contract.tokens:
            continue
        spec = contract.tokens[name]
        if spec["type"] == "color":
            value = normalize_color(value)
        elif spec["type"] in ("pixelLength", "degreeAngle"):
            suffix = "px" if spec["type"] == "pixelLength" else "deg"
            numeric = float(value[:-len(suffix)])
            _require(spec["minimum"] <= numeric <= spec["maximum"], "STYLE_UNIT_BOUND", "style.json")
            value = f"{format(numeric, '.3f').rstrip('0').rstrip('.') or '0'}{suffix}"
        authored[name] = effective_tokens[name] = value

    def asset_reference(value):
        canonical_member(value)
        _require(value.startswith("assets/") and value in structure.members, "MEDIA_REFERENCE", "style.json")
        return value

    def media(value):
        checkpoint()
        reference = value["asset"] if "asset" in value else value["source"]
        defaults = contract.semantics["effectiveDefaults"]["media"]
        result = {key: item for key, item in defaults.items() if key != "overlay"}
        result.update({key: item for key, item in value.items() if key not in ("asset", "source", "overlay")})
        result["asset"] = asset_reference(reference)
        result["overlay"] = dict(value.get("overlay", defaults["overlay"]))
        result["overlay"]["color"] = normalize_color(result["overlay"]["color"])
        return result

    if "background" in authored:
        authored["background"] = media(authored["background"])
    if "panelMedia" in authored:
        authored["panelMedia"] = {identifier: media(value) for identifier, value in authored["panelMedia"].items()}
    if "icons" in authored:
        authored["icons"] = {identifier: asset_reference(value) for identifier, value in authored["icons"].items()}
    gradient_enabled = any(name.startswith("--vl-gradient-") for name in authored)
    layout = None
    if features["layout"]:
        layout = validate_value(strict_json(structure.members["layout.json"], node_budget=budget), contract.layout, "layout.json", checkpoint)
        columns = layout.get("workspace", {}).get("columns", 12)
        rules = {}
        parents = {name: spec["defaultParent"] for name, spec in contract.components.items()}
        for identifier, rule in layout.get("components", {}).items():
            checkpoint()
            spec = contract.components[identifier]
            hidden, pinned = rule.get("hidden", False), rule.get("pinned", False)
            _require(not hidden or spec["hide"], "LAYOUT_REQUIRED_COMPONENT", "layout.json.components")
            _require(not pinned or spec["pinAcrossPages"], "LAYOUT_PIN_CAPABILITY", "layout.json.components")
            parent = rule.get("parent", "workspace" if pinned else spec["defaultParent"])
            _require(parent in spec["allowedLayoutParents"] and (not pinned or parent == "workspace"), "LAYOUT_PARENT", "layout.json.components")
            mode = rule.get("mode", "flow")
            result = {"mode": mode, "parent": parent, "hidden": hidden, "pinned": pinned,
                      "width": None, "height": None, **rule}
            minimum_height = 64 if identifier == "sidebar" and mode == "dock" and rule["edge"] in ("top", "bottom") else spec["minimumHeight"]
            for dimension, minimum in (("width", spec["minimumWidth"]), ("height", minimum_height)):
                if dimension in rule:
                    _require(spec["resize"] and rule[dimension] >= minimum, "LAYOUT_MINIMUM", "layout.json.components")
            if mode == "dock":
                _require(rule["edge"] in spec["allowedDockEdges"], "LAYOUT_DOCK_EDGE", "layout.json.components")
            elif mode == "grid":
                _require(rule["column"] + rule["columnSpan"] - 1 <= columns and rule["row"] + rule["rowSpan"] - 1 <= 64,
                         "LAYOUT_GRID_BOUND", "layout.json.components")
            elif mode == "flow":
                result.setdefault("order", tuple(contract.components).index(identifier))
                result.setdefault("columnSpan", 1)
                _require(result["columnSpan"] <= columns, "LAYOUT_GRID_BOUND", "layout.json.components")
            elif mode == "freeform":
                result.setdefault("order", 0)
            parents[identifier] = parent
            rules[identifier] = result
        for identifier in parents:
            seen, node = set(), identifier
            while node is not None:
                checkpoint()
                _require(node not in seen and node in parents, "LAYOUT_PARENT_CYCLE", "layout.json.components")
                seen.add(node)
                node = parents[node]
        layout = {"workspace": {"columns": columns}, "components": rules}
    css_source = None
    if features["customCSS"]:
        try:
            css_source = structure.members["custom.css"].decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise VluiSchemaError("CSS_UTF8", "custom.css") from error
        _require(bool(css_source.strip(ASCII_WHITESPACE)), "CSS_EMPTY", "custom.css")
    checkpoint()
    return SchemaSnapshot(structure, freeze(manifest), freeze(authored), freeze(effective_tokens),
                          gradient_enabled, freeze(layout), css_source, compatible)
