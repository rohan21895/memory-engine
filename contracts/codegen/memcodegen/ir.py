"""Intermediate representation built from the contract JSON Schemas.

The generators for Python, TypeScript and Rust all consume this IR rather than
touching raw schema dictionaries, so a schema-subset decision is made exactly
once, here, instead of three times in three subtly different ways.

The supported subset is deliberately narrow -- it is the subset the Memory
Engine contracts actually use, and the loader raises on anything outside it.
Silently generating a wrong type for an unsupported construct would be exactly
the kind of quiet failure that the contract layer exists to prevent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_SUFFIX = ".schema.json"

# JSON Schema keywords that constrain values but do not change the generated
# type. They are validated at runtime by jsonschema; the generators skip them.
IGNORED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "examples",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minItems",
        "maxItems",
        "uniqueItems",
        "multipleOf",
        "allOf",  # if/then constraint blocks -- validation only, no type impact
        "propertyNames",
    }
)


class UnsupportedSchema(Exception):
    """Raised when a schema uses a construct the generator does not model."""


@dataclass(frozen=True)
class TypeRef:
    """A reference to a generated named type."""

    name: str


@dataclass(frozen=True)
class Primitive:
    """string | integer | number | boolean | null | any."""

    kind: str


@dataclass(frozen=True)
class ListOf:
    item: "AnyType"


@dataclass(frozen=True)
class MapOf:
    """An object with additionalProperties; `value` is None for a free-form map."""

    value: "AnyType | None"


@dataclass(frozen=True)
class LiteralOf:
    """A `const`. `value` keeps its JSON type -- a boolean const is a boolean,
    not the string "False". Getting this wrong silently turns `pixel_data_present:
    false` into an unsatisfiable literal, which is precisely the guarantee that
    field exists to make."""

    value: Any


@dataclass(frozen=True)
class UnionOf:
    options: tuple["AnyType", ...]
    discriminator: str | None = None


AnyType = TypeRef | Primitive | ListOf | MapOf | LiteralOf | UnionOf


@dataclass
class Property:
    name: str
    type: AnyType
    required: bool
    nullable: bool
    default: Any = None
    has_default: bool = False
    description: str = ""

    @property
    def optional(self) -> bool:
        """True when the field may be absent from a valid document."""
        return not self.required


@dataclass
class ObjectType:
    name: str
    properties: list[Property]
    description: str = ""
    source: str = ""
    discriminator: str | None = None


@dataclass
class EnumType:
    name: str
    values: list[Any]
    description: str = ""
    source: str = ""

    @property
    def value_type(self) -> str:
        """'string', 'integer' or 'number'. Enums of numbers are common in the
        contracts (permitted bit depths, sample rates, beat units) and must not be
        generated as string enums -- 48000 is not "48000" on the wire."""
        if all(isinstance(value, str) for value in self.values):
            return "string"
        if all(isinstance(value, bool) for value in self.values):
            raise UnsupportedSchema(f"{self.name}: boolean enums are not supported")
        if all(isinstance(value, int) and not isinstance(value, bool) for value in self.values):
            return "integer"
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in self.values):
            return "number"
        raise UnsupportedSchema(
            f"{self.name}: enum mixes value types {[type(v).__name__ for v in self.values]}"
        )


@dataclass
class AliasType:
    """A named type that is not an object: a constrained string, a union, a list."""

    name: str
    target: AnyType
    description: str = ""
    source: str = ""


NamedType = ObjectType | EnumType | AliasType


@dataclass
class Contracts:
    """Everything the generators need, in deterministic order."""

    types: list[NamedType] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)

    def by_name(self, name: str) -> NamedType | None:
        for entry in self.types:
            if entry.name == name:
                return entry
        return None


def _pascal(text: str) -> str:
    parts = re.split(r"[^0-9a-zA-Z]+", text)
    return "".join(part[:1].upper() + part[1:] for part in parts if part)


def _is_null(node: dict[str, Any]) -> bool:
    return node.get("type") == "null"


class _Loader:
    def __init__(self, schema_dir: Path) -> None:
        self.schema_dir = schema_dir
        self.documents: dict[str, dict[str, Any]] = {}
        self.types: dict[str, NamedType] = {}
        self.order: list[str] = []
        self.roots: list[str] = []
        # Maps "file.schema.json#/$defs/Name" -> generated type name, so the
        # same def referenced from two files resolves to one type.
        self.ref_names: dict[str, str] = {}

    # -- loading ---------------------------------------------------------

    def load(self) -> Contracts:
        for path in sorted(self.schema_dir.glob(f"*{SCHEMA_SUFFIX}")):
            self.documents[path.name] = json.loads(path.read_text(encoding="utf-8"))

        # Pass 1: reserve a stable name for every $defs entry and every root,
        # so forward references resolve regardless of processing order.
        for filename in sorted(self.documents):
            document = self.documents[filename]
            for def_name in sorted(document.get("$defs", {})):
                key = f"{filename}#/$defs/{def_name}"
                self.ref_names[key] = self._reserve(_pascal(def_name), key)
            if self._is_root_schema(document):
                root_name = _pascal(document.get("title") or filename.split(".")[0])
                key = f"{filename}#"
                self.ref_names[key] = self._reserve(root_name, key)

        # Pass 2: build.
        for filename in sorted(self.documents):
            document = self.documents[filename]
            for def_name in sorted(document.get("$defs", {})):
                key = f"{filename}#/$defs/{def_name}"
                self._build_named(
                    self.ref_names[key], document["$defs"][def_name], filename, key
                )
            if self._is_root_schema(document):
                key = f"{filename}#"
                name = self.ref_names[key]
                self._build_named(name, document, filename, key)
                self.roots.append(name)

        ordered = [self.types[name] for name in self.order]
        return Contracts(types=ordered, roots=sorted(self.roots))

    @staticmethod
    def _is_root_schema(document: dict[str, Any]) -> bool:
        """common.schema.json declares no root type; every other file does."""
        return "properties" in document or document.get("type") == "object"

    def _reserve(self, preferred: str, key: str) -> str:
        name = preferred
        suffix = 2
        while name in self.ref_names.values():
            name = f"{preferred}{suffix}"
            suffix += 1
        return name

    # -- building --------------------------------------------------------

    def _register(self, entry: NamedType) -> str:
        existing = self.types.get(entry.name)
        if existing is None:
            self.types[entry.name] = entry
            self.order.append(entry.name)
        return entry.name

    def _build_named(
        self, name: str, node: dict[str, Any], filename: str, key: str
    ) -> str:
        description = node.get("description", "")

        if "enum" in node:
            return self._register(
                EnumType(
                    name=name,
                    values=list(node["enum"]),
                    description=description,
                    source=filename,
                )
            )

        unwrapped = self._unwrap_nullable(node)
        if unwrapped is not node and "enum" in unwrapped:
            return self._register(
                EnumType(
                    name=name,
                    values=list(unwrapped["enum"]),
                    description=description,
                    source=filename,
                )
            )

        if self._is_object(node):
            properties = self._build_properties(name, node, filename)
            return self._register(
                ObjectType(
                    name=name,
                    properties=properties,
                    description=description,
                    source=filename,
                    discriminator=None,
                )
            )

        return self._register(
            AliasType(
                name=name,
                target=self._build_type(node, filename, name),
                description=description,
                source=filename,
            )
        )

    @staticmethod
    def _is_object(node: dict[str, Any]) -> bool:
        if "properties" in node:
            return True
        return node.get("type") == "object" and "additionalProperties" not in node

    def _build_properties(
        self, owner: str, node: dict[str, Any], filename: str
    ) -> list[Property]:
        required = set(node.get("required", []))
        properties: list[Property] = []
        for prop_name in node.get("properties", {}):
            prop_node = node["properties"][prop_name]
            unwrapped = self._unwrap_nullable(prop_node)
            nullable = unwrapped is not prop_node
            hint = f"{owner}{_pascal(prop_name)}"
            properties.append(
                Property(
                    name=prop_name,
                    type=self._build_type(unwrapped, filename, hint),
                    required=prop_name in required,
                    nullable=nullable,
                    default=prop_node.get("default"),
                    has_default="default" in prop_node,
                    description=prop_node.get("description", ""),
                )
            )
        return properties

    def _unwrap_nullable(self, node: dict[str, Any]) -> dict[str, Any]:
        """Collapse `oneOf: [X, {"type": "null"}]` down to X."""
        options = node.get("oneOf")
        if not isinstance(options, list) or len(options) != 2:
            return node
        non_null = [option for option in options if not _is_null(option)]
        if len(non_null) != 1:
            return node
        merged = dict(non_null[0])
        for keyword in ("description", "default"):
            if keyword in node and keyword not in merged:
                merged[keyword] = node[keyword]
        return merged

    def _build_type(self, node: dict[str, Any], filename: str, hint: str) -> AnyType:
        if "$ref" in node:
            return TypeRef(self._resolve_ref(node["$ref"], filename))

        if "const" in node:
            return LiteralOf(node["const"])

        if "enum" in node:
            return TypeRef(
                self._register(
                    EnumType(name=hint, values=list(node["enum"]), source=filename)
                )
            )

        if "oneOf" in node:
            return self._build_union(node["oneOf"], filename, hint)

        node_type = node.get("type")

        if node_type == "array":
            items = node.get("items")
            if items is None:
                return ListOf(Primitive("any"))
            unwrapped = self._unwrap_nullable(items)
            return ListOf(self._build_type(unwrapped, filename, f"{hint}Item"))

        if node_type == "object" or "properties" in node or "additionalProperties" in node:
            extra = node.get("additionalProperties")
            if "properties" in node:
                # An inline object literal: promote it to a named type so every
                # generated language gets a real, referenceable structure.
                properties = self._build_properties(hint, node, filename)
                return TypeRef(
                    self._register(
                        ObjectType(
                            name=hint,
                            properties=properties,
                            description=node.get("description", ""),
                            source=filename,
                        )
                    )
                )
            if extra is True or extra is None:
                return MapOf(None)
            if extra is False:
                return MapOf(None)
            return MapOf(self._build_type(extra, filename, f"{hint}Value"))

        if node_type in {"string", "integer", "number", "boolean", "null"}:
            return Primitive(node_type)

        if node_type is None and not (set(node) - IGNORED_KEYWORDS):
            return Primitive("any")

        raise UnsupportedSchema(
            f"{filename}: cannot generate a type for node with keys {sorted(node)}"
        )

    def _build_union(
        self, options: list[dict[str, Any]], filename: str, hint: str
    ) -> AnyType:
        built = tuple(
            self._build_type(self._unwrap_nullable(option), filename, f"{hint}Option")
            for option in options
        )
        discriminator = self._find_discriminator(options, filename)
        return UnionOf(built, discriminator)

    def _find_discriminator(
        self, options: list[dict[str, Any]], filename: str
    ) -> str | None:
        """Detect a tagged union: every branch is a $ref to an object carrying a
        distinct `const` value under one shared property name."""
        if len(options) < 2 or not all("$ref" in option for option in options):
            return None

        resolved = [
            self._resolve_node(option["$ref"], filename) for option in options
        ]
        if not all(isinstance(node, dict) and "properties" in node for node in resolved):
            return None

        candidates = set(resolved[0]["properties"])
        for node in resolved[1:]:
            candidates &= set(node["properties"])

        for candidate in sorted(candidates):
            values = [node["properties"][candidate].get("const") for node in resolved]
            if all(isinstance(value, str) for value in values) and len(set(values)) == len(values):
                return candidate
        return None

    def _resolve_ref(self, ref: str, filename: str) -> str:
        key = self._normalise_ref(ref, filename)
        if key not in self.ref_names:
            raise UnsupportedSchema(f"{filename}: unresolved $ref {ref!r}")
        return self.ref_names[key]

    def _resolve_node(self, ref: str, filename: str) -> Any:
        key = self._normalise_ref(ref, filename)
        target_file, _, pointer = key.partition("#")
        document = self.documents[target_file]
        if pointer in ("", "/"):
            return document
        node: Any = document
        for token in pointer.strip("/").split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            node = node[token]
        return node

    @staticmethod
    def _normalise_ref(ref: str, filename: str) -> str:
        if ref.startswith("#"):
            return f"{filename}{ref}"
        if "#" not in ref:
            return f"{ref}#"
        return ref


def load_contracts(schema_dir: Path) -> Contracts:
    return _Loader(schema_dir).load()
