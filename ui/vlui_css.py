"""Closed CSS semantic stage. No application, filesystem, network or renderer.

Output is CSS-stage-only; it must NEVER be applied or served before complete
package validation, isolated raster decoding and current entitlement checks.
tinycss2 is a syntax parser. All selector/property/value semantics live here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import time
from typing import Callable

import tinycss2

from vlui_archive import canonical_member, VluiArchiveError
from vlui_schema import SchemaContract, SchemaSnapshot


MAX_BYTES = 65536  # Source AND canonical result, checked before publishing.
MAX_TOKENS, MAX_RULES, MAX_DECLARATIONS = 16384, 256, 2048
FONTS = frozenset(("Segoe UI", "Segoe UI Variable", "Arial", "Tahoma", "Verdana"))
DESCENDANTS = frozenset(("vl-card", "vl-heading", "vl-muted", "vl-value", "vl-profile", "vl-card-list", "vl-placeholder",
                         "vl-decoration", "vl-field", "vl-button", "vl-control", "vl-control-label", "vl-control-value", "vl-state"))
PSEUDOS, STATES = frozenset(("hover", "focus", "focus-visible", "disabled")), frozenset(("is-selected", "is-active"))
DECORATIVE_ONLY = frozenset(("opacity", "filter", "transform"))
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]{1,6})?|\.[0-9]{1,6})\Z", re.ASCII)
LENGTH = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]{1,3})?|\.[0-9]{1,3})\Z", re.ASCII)
IGNORABLE = frozenset(("whitespace", "comment"))


class VluiCssError(ValueError):
    def __init__(self, code: str, line=1, column=1):
        self.code, self.line, self.column = code, line, column
        super().__init__(f"{code} at {line}:{column}")


def require(condition, code, node=None):
    if not condition:
        raise VluiCssError(code, getattr(node, "source_line", 1), getattr(node, "source_column", 1))


def meaningful(nodes):
    return [node for node in nodes if node.type not in IGNORABLE]


def comma_groups(nodes):
    groups = [[]]
    for node in meaningful(nodes):
        if node.type == "literal" and node.value == ",":
            require(bool(groups[-1]), "CSS_EMPTY_GROUP", node)
            groups.append([])
        else:
            groups[-1].append(node)
    require(bool(groups[-1]), "CSS_EMPTY_GROUP")
    return groups


def decimal_text(value, digits=6):
    result = format(value, f".{digits}f").rstrip("0").rstrip(".")
    return "0" if result in ("", "-0") else result


@dataclass(frozen=True)
class CssSnapshot:
    css_text: str
    asset_references: tuple[str, ...]
    rules: int
    declarations: int
    tokens: int
    validation_stage: str = "css-only"


class CssCompiler:
    def __init__(self, schema: SchemaSnapshot, contract: SchemaContract, checkpoint: Callable[[], None]):
        self.schema, self.contract, self.checkpoint = schema, contract, checkpoint
        self.assets, self.rules, self.declarations, self.tokens = set(), 0, 0, 0

    def preflight(self, source):
        require(type(source) is str and len(source.encode("utf-8")) <= MAX_BYTES, "CSS_BYTE_BOUND")
        # Comments and escapes are skipped below; reject NUL over the entire
        # source first so discarded text has the same domain as the browser.
        require("\x00" not in source, "CSS_NUL")
        stack, index, quote = [], 0, None
        while index < len(source):
            if index % 256 == 0:
                self.checkpoint()
            character = source[index]
            if quote:
                if character == "\\":
                    index += 2
                    continue
                if character == quote:
                    quote = None
            elif source.startswith("/*", index):
                end = source.find("*/", index + 2)
                require(end != -1, "CSS_UNTERMINATED_COMMENT")
                index = end + 2
                continue
            elif character in ("'", '"'):
                quote = character
            elif character == "\\":
                # Escaped delimiters/quotes are tokens, not structural nesting.
                # AST decoding and complete semantic matching still follow.
                index += 2
                continue
            elif character in "({[":
                stack.append(character)
                require(len(stack) <= 8, "CSS_DELIMITER_DEPTH")
            elif character in ")}]":
                require(bool(stack) and stack.pop() == {")": "(", "}": "{", "]": "["}[character], "CSS_DELIMITER_BALANCE")
            index += 1
        require(not quote and not stack, "CSS_DELIMITER_BALANCE")
        self.checkpoint()

    def count_tokens(self, nodes, function_depth=0):
        for node in nodes:
            self.checkpoint()
            if node.type in IGNORABLE:
                continue
            require(node.type != "error", "CSS_PARSE_ERROR", node)
            require(not (node.type == "literal" and node.value in ("<!--", "-->")),
                    "CSS_LEGACY_HTML_TOKEN", node)
            self.tokens += 1
            require(self.tokens <= MAX_TOKENS, "CSS_TOKEN_BOUND", node)
            if node.type == "function":
                require(function_depth < 2, "CSS_FUNCTION_DEPTH", node)
                self.count_tokens(node.arguments, function_depth + 1)
            elif node.type in ("() block", "[] block", "{} block"):
                self.count_tokens(node.content, function_depth)
            else:
                # The @media keyword counts as one lexical identifier token.
                require(node.type in ("literal", "ident", "at-keyword", "number", "dimension", "percentage", "hash", "string", "url"), "CSS_UNKNOWN_TOKEN", node)

    def selector(self, group):
        # Comments are removed; actual whitespace is retained for descendant
        # combinators. Removing all whitespace would change selector meaning.
        group = [node for node in group if node.type != "comment"]
        while group and group[0].type == "whitespace":
            group.pop(0)
        while group and group[-1].type == "whitespace":
            group.pop()
        require(group and len(tinycss2.serialize(group).encode("utf-8")) <= 256, "CSS_SELECTOR_BOUND")
        root = group[0]
        require(root.type == "[] block", "CSS_SELECTOR_ROOT", root)
        attribute = meaningful(root.content)
        require(len(attribute) == 3 and attribute[0].type == "ident" and attribute[0].value == "data-vl-component"
                and attribute[1].type == "literal" and attribute[1].value == "=" and attribute[2].type == "string", "CSS_SELECTOR_ATTRIBUTE", root)
        identifier = attribute[2].value
        require(identifier in self.contract.components, "CSS_UNKNOWN_COMPONENT", root)
        result, index, descendant = f'[data-vl-component="{identifier}"]', 1, None
        if index < len(group) and group[index].type == "whitespace":
            while index < len(group) and group[index].type == "whitespace":
                index += 1
            require(index + 1 < len(group) and group[index].type == "literal" and group[index].value == "."
                    and group[index + 1].type == "ident" and group[index + 1].value in DESCENDANTS, "CSS_SELECTOR_DESCENDANT", root)
            descendant = group[index + 1].value
            result += f" .{descendant}"
            index += 2
        if index < len(group):
            require(index + 2 == len(group) and group[index].type == "literal" and group[index + 1].type == "ident", "CSS_SELECTOR_STATE", root)
            separator, state = group[index].value, group[index + 1].value
            require((separator == ":" and state in PSEUDOS) or (separator == "." and state in STATES), "CSS_SELECTOR_STATE", root)
            result += separator + state
            index += 2
        require(index == len(group), "CSS_SELECTOR_TRAILING", root)
        return result, descendant == "vl-decoration"

    def selectors(self, prelude):
        groups, group = [], []
        for node in prelude:
            if node.type == "literal" and node.value == ",":
                groups.append(group)
                group = []
            else:
                group.append(node)
        groups.append(group)
        require(1 <= len(groups) <= 8, "CSS_SELECTOR_COUNT")
        results = [self.selector(group) for group in groups]
        return ",".join(result[0] for result in results), all(result[1] for result in results)

    def variable(self, node, family):
        if node.type != "function" or node.lower_name != "var":
            return None
        arguments = meaningful(node.arguments)
        require(len(arguments) == 1 and arguments[0].type == "ident", "CSS_VARIABLE_SHAPE", node)
        name = arguments[0].value
        require(name in self.contract.tokens and self.contract.tokens[name]["type"] == family, "CSS_VARIABLE_FAMILY", node)
        return self.schema.effective_tokens[name]

    def numeric(self, node, low, high, *, unit=None, integer=False):
        family = "pixelLength" if unit == "px" else "degreeAngle" if unit == "deg" else "number"
        substituted = self.variable(node, family)
        if substituted is not None:
            value = float(substituted[:-len(unit)]) if unit else float(substituted)
        else:
            require(node.type == ("dimension" if unit else "number"), "CSS_VALUE_TYPE", node)
            if unit:
                require(node.lower_unit == unit and LENGTH.fullmatch(node.representation), "CSS_UNIT_GRAMMAR", node)
            else:
                require(NUMBER.fullmatch(node.representation), "CSS_NUMBER_GRAMMAR", node)
            value = node.value
        require(math.isfinite(value) and low <= value <= high and (not integer or int(value) == value), "CSS_VALUE_BOUND", node)
        return decimal_text(value, 3 if unit else 6) + (unit or "")

    def color(self, node, minimum_alpha=0):
        substituted = self.variable(node, "color")
        if substituted is not None:
            parsed = meaningful(tinycss2.parse_component_value_list(substituted))
            require(len(parsed) == 1, "CSS_COLOR_GRAMMAR", node)
            return self.color(parsed[0], minimum_alpha)
        if node.type == "hash":
            require(re.fullmatch(r"[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", node.value), "CSS_COLOR_GRAMMAR", node)
            alpha = int(node.value[6:], 16) / 255 if len(node.value) == 8 else 1
            require(alpha >= minimum_alpha, "CSS_TEXT_ALPHA", node)
            return "#" + node.value.lower()
        require(node.type == "function" and node.lower_name == "rgba", "CSS_COLOR_GRAMMAR", node)
        groups = comma_groups(node.arguments)
        require(len(groups) == 4 and all(len(group) == 1 for group in groups), "CSS_COLOR_GRAMMAR", node)
        channels = []
        for group in groups[:3]:
            channel = group[0]
            require(channel.type == "number" and re.fullmatch(r"[0-9]{1,3}", channel.representation), "CSS_COLOR_CHANNEL", channel)
            channels.append(self.numeric(channel, 0, 255, integer=True))
        alpha = self.numeric(groups[3][0], minimum_alpha, 1)
        return "rgba(" + ",".join([*channels, alpha]) + ")"

    def keyword(self, node, choices):
        require(node.type == "ident" and node.lower_value in choices, "CSS_KEYWORD", node)
        return node.lower_value

    def shadow(self, nodes, drop=False):
        require(len(nodes) in ((3, 4) if drop else (5,)), "CSS_SHADOW_GRAMMAR")
        limits = [(0, 24), (0, 24)] + ([(0, 64)] if drop and len(nodes) == 4 else [] if drop else [(0, 64), (0, 16)])
        lengths = [self.numeric(node, *limit, unit="px") for node, limit in zip(nodes[:-1], limits, strict=True)]
        return " ".join([*lengths, self.color(nodes[-1])])

    def url(self, node):
        if node.type == "url":
            value = node.value
        else:
            require(node.type == "function" and node.lower_name == "url", "CSS_URL_GRAMMAR", node)
            arguments = meaningful(node.arguments)
            require(len(arguments) == 1 and arguments[0].type == "string", "CSS_URL_GRAMMAR", node)
            value = arguments[0].value
        try:
            canonical_member(value)
        except VluiArchiveError as error:
            raise VluiCssError("CSS_ASSET_REFERENCE", node.source_line, node.source_column) from error
        require(value.startswith("assets/") and value in self.schema.structure.members, "CSS_ASSET_REFERENCE", node)
        require(re.fullmatch(r"[0-9a-f]{64}", self.schema.structure.sha256), "CSS_SNAPSHOT_IDENTITY")
        self.assets.add(value)
        return f'url("vlui://{self.schema.structure.sha256}/{value}")'

    def property(self, name, values, decorative):
        require(values, "CSS_EMPTY_VALUE")
        if name in DECORATIVE_ONLY:
            require(decorative, "CSS_FUNCTIONAL_EFFECT")
        if name in ("color", "background-color", "border-color"):
            require(1 <= len(values) <= (4 if name == "border-color" else 1), "CSS_VALUE_ARITY")
            return " ".join(self.color(node, .5 if name == "color" else 0) for node in values)
        lengths = {"border-radius": (4, 0, 80), "border-width": (4, 0, 8), "padding": (4, 0, 64),
                   "margin": (4, 0, 64), "gap": (2, 0, 64), "letter-spacing": (1, 0, 3)}
        if name in lengths:
            count, low, high = lengths[name]
            require(1 <= len(values) <= count, "CSS_VALUE_ARITY")
            return " ".join(self.numeric(node, low, high, unit="px") for node in values)
        if name == "font-size":
            require(len(values) == 1, "CSS_VALUE_ARITY")
            size = self.numeric(values[0], 10, 32, unit="px")
            return f"calc({size} * var(--vl-accessibility-text-scale,1))"
        if name == "font-weight":
            require(len(values) == 1, "CSS_VALUE_ARITY")
            value = self.numeric(values[0], 400, 700, integer=True)
            require(value in ("400", "500", "600", "700"), "CSS_FONT_WEIGHT")
            return value
        if name in ("line-height", "opacity"):
            require(len(values) == 1, "CSS_VALUE_ARITY")
            return self.numeric(values[0], *( (1.2, 2) if name == "line-height" else (0, 1)))
        if name == "font-family":
            require(len(values) == 1, "CSS_VALUE_ARITY")
            font = self.variable(values[0], "approvedFont")
            if font is None:
                require(values[0].type == "string", "CSS_FONT_GRAMMAR", values[0])
                font = values[0].value
            require(font in FONTS, "CSS_FONT_GRAMMAR", values[0])
            return f'"{font}","Microsoft YaHei UI","Noto Sans",sans-serif'
        if name == "border-style":
            require(1 <= len(values) <= 4, "CSS_VALUE_ARITY")
            return " ".join(self.keyword(node, ("none", "solid", "dashed", "dotted", "double")) for node in values)
        if name == "box-shadow":
            if len(values) == 1 and values[0].type == "ident":
                return self.keyword(values[0], ("none",))
            groups = comma_groups(values)
            require(len(groups) <= 2, "CSS_SHADOW_COUNT")
            return ",".join(self.shadow(group) for group in groups)
        if name in ("backdrop-filter", "filter", "transform"):
            require(len(values) == 1, "CSS_VALUE_ARITY")
            node = values[0]
            if node.type == "ident":
                return self.keyword(node, ("none",))
            expected = {"backdrop-filter": "blur", "filter": "drop-shadow", "transform": "scale"}[name]
            require(node.type == "function" and node.lower_name == expected, "CSS_EFFECT_GRAMMAR", node)
            arguments = meaningful(node.arguments)
            if expected == "drop-shadow":
                result = self.shadow(arguments, drop=True)
            else:
                require(len(arguments) == 1, "CSS_VALUE_ARITY", node)
                result = self.numeric(arguments[0], 0, 40, unit="px") if expected == "blur" else self.numeric(arguments[0], .85, 1.15)
            return f"{expected}({result})"
        if name == "background-image":
            require(len(values) == 1, "CSS_VALUE_ARITY")
            node = values[0]
            if node.type == "ident":
                return self.keyword(node, ("none",))
            if node.type == "url" or node.type == "function" and node.lower_name == "url":
                return self.url(node)
            require(node.type == "function" and node.lower_name == "linear-gradient", "CSS_GRADIENT_GRAMMAR", node)
            groups = comma_groups(node.arguments)
            require(len(groups) == 3 and all(len(group) == 1 for group in groups), "CSS_GRADIENT_GRAMMAR", node)
            return "linear-gradient(" + ",".join([self.numeric(groups[0][0], 0, 360, unit="deg"), self.color(groups[1][0]), self.color(groups[2][0])]) + ")"
        if name == "background-position":
            require(1 <= len(values) <= 2, "CSS_VALUE_ARITY")
            return self.keyword(values[0], ("center", "top", "bottom", "left", "right")) if len(values) == 1 else " ".join(
                [self.keyword(values[0], ("left", "center", "right")), self.keyword(values[1], ("top", "center", "bottom"))])
        choices = {"background-repeat": ("no-repeat", "repeat"), "background-size": ("cover", "contain")}
        require(name in choices and len(values) == 1, "CSS_UNKNOWN_PROPERTY")
        return self.keyword(values[0], choices[name])

    def media(self, prelude):
        nodes = meaningful(prelude)
        require(len(nodes) == 1 and nodes[0].type == "() block", "CSS_MEDIA_GRAMMAR")
        condition = meaningful(nodes[0].content)
        require(len(condition) == 3 and condition[0].type == "ident" and condition[0].lower_value in ("min-width", "max-width")
                and condition[1].type == "literal" and condition[1].value == ":", "CSS_MEDIA_GRAMMAR", nodes[0])
        value = condition[2]
        require(value.type == "dimension" and value.lower_unit == "px", "CSS_MEDIA_GRAMMAR", value)
        result = self.numeric(value, 240, 8192, unit="px", integer=True)
        return f"({condition[0].lower_value}:{result})"

    def compile_rules(self, rules, media_depth=0):
        output = []
        for rule in rules:
            self.checkpoint()
            if rule.type in IGNORABLE:
                continue
            require(rule.type in ("qualified-rule", "at-rule"), "CSS_UNKNOWN_RULE", rule)
            self.rules += 1
            require(self.rules <= MAX_RULES, "CSS_RULE_BOUND", rule)
            if rule.type == "at-rule":
                require(rule.lower_at_keyword == "media" and rule.content is not None and media_depth < 4, "CSS_AT_RULE", rule)
                condition = self.media(rule.prelude)
                body = self.compile_rules(tinycss2.parse_rule_list(rule.content), media_depth + 1)
                output.append(f"@media {condition}{{{body}}}")
                continue
            selector, decorative = self.selectors(rule.prelude)
            declarations, seen = [], set()
            for declaration in tinycss2.parse_declaration_list(rule.content):
                self.checkpoint()
                if declaration.type in IGNORABLE:
                    continue
                require(declaration.type == "declaration" and not declaration.important, "CSS_DECLARATION", declaration)
                name = declaration.lower_name
                require(name.isascii() and name not in seen, "CSS_DUPLICATE_PROPERTY", declaration)
                seen.add(name)
                self.declarations += 1
                require(self.declarations <= MAX_DECLARATIONS, "CSS_DECLARATION_BOUND", declaration)
                value = self.property(name, meaningful(declaration.value), decorative)
                declarations.append(f"{name}:{value};")
            output.append(f"{selector}{{{''.join(declarations)}}}")
            require(sum(len(part.encode("utf-8")) for part in output) <= MAX_BYTES, "CSS_OUTPUT_BYTE_BOUND")
        return "".join(output)


def compile_custom_css(schema: SchemaSnapshot, contract: SchemaContract, *, cancelled: Callable[[], bool] | None = None,
                       clock: Callable[[], float] = time.monotonic, deadline: float | None = None) -> CssSnapshot:
    require(isinstance(schema, SchemaSnapshot) and schema.validation_stage == "schema-only", "CSS_SCHEMA_STAGE_REQUIRED")
    limit = min(clock() + 5.0, deadline) if deadline is not None else clock() + 5.0

    def checkpoint():
        require(cancelled is None or not cancelled(), "CSS_VALIDATION_CANCELLED")
        require(clock() <= limit, "CSS_VALIDATION_DEADLINE")

    checkpoint()
    compiler = CssCompiler(schema, contract, checkpoint)
    if schema.custom_css_source is None:
        return CssSnapshot("", (), 0, 0, 0)
    source = schema.custom_css_source
    compiler.preflight(source)
    components = tinycss2.parse_component_value_list(source)
    compiler.count_tokens(components)
    checkpoint()
    result = compiler.compile_rules(tinycss2.parse_stylesheet(source))
    require(len(result.encode("utf-8")) <= MAX_BYTES, "CSS_OUTPUT_BYTE_BOUND")
    checkpoint()
    return CssSnapshot(result, tuple(sorted(compiler.assets)), compiler.rules, compiler.declarations, compiler.tokens)
