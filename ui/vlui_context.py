"""Disconnected R3 display-context validation. No native or live authority.

The only external input is exact immutable bytes. A successful result is a
complete private frozen graph, including its display-only projection. Neither
mode, sequence nor UUIDs establish provenance, readiness or permission.
"""
from functools import lru_cache
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType

from vlui_resource_root import bundled_data_root
DEV = bundled_data_root()
CONTRACT_PATH = DEV / "ui_system/runtime/bundled/context-contract.json"
CONTRACT_SHA256 = "2f140c2a3cd3bfa71154f579a767fe9fa12c37baa8a6f88c95f2fceb97666f78"
MAX_BYTES = 32768
MAX_PRIMITIVES = 512
UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
FAILURES = {
    "inspect": {"invalid-model", "operation-failed"},
    "validate": {"invalid-model", "runtime-unavailable", "operation-failed"},
    "benchmark": {"invalid-model", "runtime-unavailable", "operation-failed"},
    "train": {"invalid-model", "invalid-dataset", "runtime-unavailable", "operation-failed"},
    "dataset-check": {"invalid-dataset", "runtime-unavailable", "operation-failed"},
    "runtime-check": {"runtime-unavailable", "operation-failed"},
    "runtime-install": {"runtime-unavailable", "operation-failed"},
    "annotate": {"invalid-model", "invalid-dataset", "runtime-unavailable", "operation-failed"},
    "archive": {"invalid-dataset", "operation-failed"},
}


class ContextRejected(ValueError):
    """Fixed public code; bounded private category, never echoed input."""
    code = "CONTEXT_REJECTED"

    def __init__(self, category):
        self.category = category
        super().__init__(self.code)


def require(condition, category="RELATION"):
    if not condition:
        raise ContextRejected(category)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON")
        result[key] = value
    return result


def _constant(_value):
    raise ContextRejected("JSON")


def _number(token):
    # All wire numeric types are integers. Check exact lexemes before host
    # rounding, so fractional near-integers cannot impersonate safe integers.
    # JSON lexemes with a zero significand are exact zero for any exponent.
    # Decimal's representable exponent range must not reject mathematical zero.
    significand = token.lower().partition("e")[0]
    if not any(char in "123456789" for char in significand):
        return 0
    try:
        value = Decimal(token)
    except InvalidOperation:
        raise ContextRejected("JSON") from None
    require(value.is_finite() and value.copy_abs() <= 9007199254740991
            and value == value.to_integral_value(), "SHAPE")
    return int(value)


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def snapshot_to_json(snapshot):
    """Copy an already validated frozen snapshot for inert result interchange."""
    if type(snapshot) is MappingProxyType:
        return {key: snapshot_to_json(value) for key, value in snapshot.items()}
    if type(snapshot) is tuple:
        return [snapshot_to_json(value) for value in snapshot]
    return snapshot


@lru_cache(maxsize=1)
def _types():
    # This is a fixed Program resource, never selected by a theme or caller.
    for path in (CONTRACT_PATH, *CONTRACT_PATH.parents):
        if getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Context contract resource reparse")
    if CONTRACT_PATH.stat().st_size > 65536:
        raise RuntimeError("Context contract resource bound")
    raw = CONTRACT_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CONTRACT_SHA256:
        raise RuntimeError("Context contract resource identity changed")
    return _freeze(json.loads(raw)["wire"]["types"])


def _primitive_bound(value):
    count, stack = 0, [(value, 0)]
    while stack:
        item, depth = stack.pop()
        # Valid R3 shapes have much smaller depth. Bound hostile parser graphs.
        require(depth <= 64, "BOUNDS")
        if type(item) is dict:
            stack.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        else:
            count += 1
            require(count <= MAX_PRIMITIVES, "BOUNDS")


class _Graph:
    def __init__(self):
        self.roles = {}
        self.configurations = {}
        self.runtime_states = {}

    def shape(self, value, type_name):
        spec = _types()[type_name]
        kind = spec["kind"]
        if value is None and spec.get("nullable"):
            return
        if kind == "closed-object":
            require(type(value) is dict and set(value) == set(spec["fields"]), "SHAPE")
            for field, child_type in spec["fields"].items():
                self.shape(value[field], child_type)
            role = spec.get("entityRole")
            if role:
                state, identity = value["state"], value["displayId"]
                require((state == "known") == (identity is not None))
                if identity is not None:
                    require(self.roles.get(identity, role) == role)
                    self.roles[identity] = role
                if type_name == "SelectedBox":
                    require((state == "known") == (value["ordinal"] is not None))
            if type_name == "RuntimeFact":
                self.ref(value["revision"], "unknown" if value["state"] == "unknown" else "known")
                identity = value["revision"]["displayId"]
                if identity is not None:
                    require(self.runtime_states.get(identity, value["state"]) == value["state"])
                    self.runtime_states[identity] = value["state"]
            if type_name == "ModelConfiguration" and value["configuration"]["state"] == "known":
                identity = value["configuration"]["displayId"]
                facts = {k: v for k, v in value.items() if k != "runtime"}
                require(self.configurations.get(identity, facts) == facts)
                self.configurations[identity] = facts
        elif kind == "enum":
            require(type(value) is str and value in spec["values"], "SHAPE")
        elif kind in ("literal-integer", "literal-string"):
            expected_type = int if kind == "literal-integer" else str
            require(type(value) is expected_type and value == spec["value"], "SHAPE")
        elif kind == "integer":
            require(type(value) is int and spec["minimum"] <= value <= spec["maximum"], "SHAPE")
        elif kind == "canonical-uuid-v4":
            require(type(value) is str and UUID4.fullmatch(value) is not None, "SHAPE")
        elif kind == "string":
            require(type(value) is str, "SHAPE")
            require(spec["minUnicodeScalars"] <= len(value) <= spec["maxUnicodeScalars"], "SHAPE")
            for char in value:
                cp = ord(char)
                require(not 0xD800 <= cp <= 0xDFFF and not any(lo <= cp <= hi for lo, hi in spec["forbiddenRanges"]), "SHAPE")
        elif kind == "array":
            require(type(value) is list and spec.get("minimumItems", 0) <= len(value) <= spec.get("maximumItems", spec.get("maxItems", 0)), "SHAPE")
            for item in value:
                self.shape(item, spec.get("itemType", spec.get("items")))
            if spec.get("unique"):
                require(len(set(value)) == len(value), "SHAPE")
            if spec.get("uniqueBy"):
                require(len({item[spec["uniqueBy"]] for item in value}) == len(value))
        else:
            raise RuntimeError("Unsupported pinned context type")

    @staticmethod
    def ref(value, state):
        require(value["state"] == state)

    def blank(self, configuration):
        for name in ("model", "configuration", "inputSettings", "captureSettings"):
            self.ref(configuration[name], "unknown")
        require(configuration["preferredProvider"] == "unknown" and configuration["runtime"]["state"] == "unknown")

    def observed(self, configuration):
        for name in ("model", "configuration", "inputSettings", "captureSettings"):
            self.ref(configuration[name], "known")

    @staticmethod
    def unknown_result(value):
        require(all(value[field] == "unknown" for field in ("outcome", "freshness", "staleReason")))

    @staticmethod
    def outcome(value, operation, passed="completed"):
        state, outcome = value["state"], value["outcome"]
        if state == passed:
            require(outcome == "ok")
        elif state == "cancelled":
            require(outcome == "cancelled")
        elif state == "failed":
            require(outcome in FAILURES[operation])
        else:
            require(outcome == "unknown")

    @staticmethod
    def freshness(value, equal):
        require((value["freshness"], value["staleReason"]) == (("current", "none") if equal else ("stale", "configuration-changed")))

    def result(self, value, current):
        state = value["state"]
        if state in ("unknown", "none"):
            self.ref(value["operationId"], state)
            self.blank(value["captured"])
            self.unknown_result(value)
            require(value["actualProvider"] == "unknown")
            return
        self.ref(value["operationId"], "known")
        self.observed(value["captured"])
        self.outcome(value, value["operation"], "passed")
        self.freshness(value, value["captured"] == current)
        if value["operation"] == "inspect" or state in ("pending", "running"):
            require(value["actualProvider"] == "unknown")
        elif state == "passed":
            require(value["actualProvider"] != "unknown")

    def preflight(self, value, prospective):
        state = value["state"]
        if state in ("unknown", "none"):
            self.ref(value["requestId"], state)
            for field in ("configuration", "baseModel", "dataset"):
                self.ref(value[field], "unknown")
            self.unknown_result(value)
            return
        for field in ("requestId", "configuration", "dataset"):
            self.ref(value[field], "known")
        require(value["baseModel"]["state"] in ("known", "none"))
        self.outcome(value, "dataset-check", "passed")
        self.freshness(value, all(value[field] == prospective[field] for field in ("configuration", "dataset", "baseModel")))

    def job(self, value, training):
        fields = ("job", "configuration", "run", "baseModel", "dataset", "requestedDevice") if training else ("job", "configuration", "dataset", "annotationModel")
        state, operation = value["state"], value["operation"]
        if state in ("unknown", "none"):
            require(operation == state and value["outcome"] == "unknown")
            for field in fields:
                self.ref(value[field], state)
            return
        require(operation not in ("unknown", "none"))
        for field in ("job", "configuration"):
            self.ref(value[field], "known")
        if training:
            for field in ("run", "baseModel", "requestedDevice"):
                self.ref(value[field], "known" if operation == "train" else "none")
            self.ref(value["dataset"], "none" if operation in ("runtime-check", "runtime-install") else "known")
        else:
            self.ref(value["dataset"], "known")
            self.ref(value["annotationModel"], "known" if operation == "annotate" else "none")
        self.outcome(value, operation)

    def benchmark(self, value):
        state = value["state"]
        if state in ("unknown", "none"):
            self.ref(value["benchmark"], state)
            self.blank(value["captured"])
            self.unknown_result(value)
            require(not value["comparedProviders"])
            count = None if state == "unknown" else 0
            require(value["completedCount"] == count and value["successfulCount"] == count)
            return
        self.ref(value["benchmark"], "known")
        self.observed(value["captured"])
        size = len(value["comparedProviders"])
        completed, successful = value["completedCount"], value["successfulCount"]
        require(type(completed) is int and type(successful) is int and 0 <= successful <= completed <= size)
        require(size > 0 or state == "failed")
        if state == "completed":
            require(completed == size and value["outcome"] in ({"ok"} if successful > 0 else {"operation-failed", "runtime-unavailable"}))
        else:
            self.outcome(value, "benchmark")
        self.freshness(value, value["captured"] == value["current"])

    def relations(self, snapshot):
        groups = snapshot["groups"]
        inspection = groups["model-inspection"]
        current = inspection["current"]
        require(current == groups["provider-benchmark"]["current"] == groups["official-models"]["current"])
        training = groups["local-training"]
        prospective = training["prospective"]
        require(groups["official-models"]["selectedTrainingBaseModel"] == prospective["baseModel"])
        self.result(inspection["inspectionResult"], current)
        self.result(inspection["validationResult"], current)
        self.preflight(prospective["preflight"], prospective)
        self.job(training["activeJob"], True)
        selected = groups["training-status"]["selected"]
        self.job(selected, True)
        attribution = selected["attribution"]
        if attribution == "current":
            require(selected["state"] not in ("unknown", "none"))
            require({k: v for k, v in selected.items() if k != "attribution"} == training["activeJob"])
        elif attribution == "historical":
            require(selected["state"] in ("completed", "cancelled", "failed"))
        else:
            require(selected["state"] == attribution)
        self.benchmark(groups["provider-benchmark"])
        collection = groups["data-collection"]
        self.job(collection["activeJob"], False)
        source_state = collection["captureSource"]["state"]
        if source_state == "unknown":
            require(collection["captureAvailability"] == "unknown")
        elif source_state == "none":
            require(collection["captureAvailability"] in ("unknown", "unavailable"))
        review = groups["data-review"]
        dataset, image = review["dataset"]["state"], review["image"]["state"]
        if dataset in ("unknown", "none"):
            require(image == dataset)
        if image == "known":
            require(dataset == "known")
        else:
            require(review["selectedBox"]["state"] != "known" and review["decision"] == "unknown" and review["unsavedState"] == "unknown")
        projection = snapshot["displayProjection"]
        labels = {record["displayId"]: record["role"] for record in projection["entityLabels"]}
        require(labels == {identity: role for identity, role in self.roles.items() if role != "box"})
        require({summary["configurationId"] for summary in projection["configurationSummaries"]} == set(self.configurations))


def validate_context_bytes(raw):
    """Validate one atomic snapshot; callers cannot supply objects or buffers.

    Sequencing, generation ownership, native provenance, operation fencing,
    semantic revision production and presentation visibility are integration
    responsibilities. This stateless validator checks only the wire snapshot.
    """
    require(type(raw) is bytes, "SHAPE")
    require(0 < len(raw) <= MAX_BYTES, "BOUNDS")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise ContextRejected("UTF8") from None
    require(not text.startswith("\ufeff"), "UTF8")
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant,
                           parse_int=_number, parse_float=_number)
    except (ValueError, RecursionError) as error:
        if isinstance(error, ContextRejected):
            raise
        raise ContextRejected("JSON") from None
    _primitive_bound(value)
    graph = _Graph()
    graph.shape(value, "Snapshot")
    graph.relations(value)
    return _freeze(value)
