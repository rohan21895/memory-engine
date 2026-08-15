"""Emit serde-compatible Rust types from the contract IR.

Two Rust-specific decisions worth knowing about:

* Every optional field becomes `Option<T>` with `#[serde(default)]` and
  `skip_serializing_if`, including fields the schema gives a non-null default.
  Encoding a JSON default as a Rust `Default` impl would require a helper
  function per distinct default value; `Option` is type-correct, round-trips
  cleanly, and leaves the default where it belongs -- in the schema.

* Tagged unions become internally-tagged serde enums, and the discriminator
  property is omitted from the variant structs, because serde strips the tag
  before handing the remaining map to the variant. A struct used *both* inside
  a tagged union and standalone would need different treatment; the generator
  raises rather than emitting something subtly wrong.
"""

from __future__ import annotations

from .common import BANNER, pascal, wrap_doc
from .ir import (
    AliasType,
    AnyType,
    Contracts,
    EnumType,
    ListOf,
    LiteralOf,
    MapOf,
    ObjectType,
    Primitive,
    Property,
    TypeRef,
    UnionOf,
)

PRIMITIVES = {
    "string": "String",
    "integer": "i64",
    "number": "f64",
    "boolean": "bool",
    "null": "()",
    "any": "serde_json::Value",
}

RESERVED = frozenset(
    {
        "abstract", "as", "async", "await", "become", "box", "break", "const",
        "continue", "crate", "do", "dyn", "else", "enum", "extern", "false",
        "final", "fn", "for", "if", "impl", "in", "let", "loop", "macro",
        "match", "mod", "move", "mut", "override", "priv", "pub", "ref",
        "return", "self", "static", "struct", "super", "trait", "true", "try",
        "type", "typeof", "unsafe", "unsized", "use", "virtual", "where",
        "while", "yield",
    }
)


class UnionConflict(Exception):
    """A struct is used both inside a tagged union and on its own."""


def _type(node: AnyType) -> str:
    if isinstance(node, TypeRef):
        return node.name
    if isinstance(node, Primitive):
        return PRIMITIVES[node.kind]
    if isinstance(node, ListOf):
        return f"Vec<{_type(node.item)}>"
    if isinstance(node, MapOf):
        inner = _type(node.value) if node.value else "serde_json::Value"
        return f"BTreeMap<String, {inner}>"
    if isinstance(node, LiteralOf):
        if isinstance(node.value, bool):
            return "bool"
        if isinstance(node.value, int):
            return "i64"
        if isinstance(node.value, float):
            return "f64"
        return "String"
    if isinstance(node, UnionOf):
        # Untagged and tagged unions alike are emitted as named enums ahead of
        # use; a bare inline union has no Rust spelling.
        raise TypeError("inline unions must be hoisted to a named enum first")
    raise TypeError(f"unhandled IR node: {node!r}")


def _field_name(name: str) -> tuple[str, bool]:
    return (f"r#{name}", True) if name in RESERVED else (name, False)


def _doc(text: str, indent: str) -> list[str]:
    return [f"{indent}/// {line}" if line else f"{indent}///" for line in wrap_doc(text, 84)]


def _render_property(prop: Property) -> list[str]:
    base = _type(prop.type)
    lines = _doc(prop.description, "    ")

    identifier, renamed = _field_name(prop.name)
    attributes: list[str] = []

    if prop.optional:
        base = f"Option<{base}>"
        attributes.append("default")
        attributes.append('skip_serializing_if = "Option::is_none"')
    elif prop.nullable:
        base = f"Option<{base}>"

    if renamed:
        attributes.append(f'rename = "{prop.name}"')

    if attributes:
        lines.append(f"    #[serde({', '.join(attributes)})]")
    lines.append(f"    pub {identifier}: {base},")
    return lines


def _render_struct(entry: ObjectType, skip_field: str | None) -> list[str]:
    lines = _doc(entry.description, "")
    lines.append("#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]")
    properties = [p for p in entry.properties if p.name != skip_field]
    if not properties:
        lines.append(f"pub struct {entry.name} {{}}")
        return lines
    lines.append(f"pub struct {entry.name} {{")
    for index, prop in enumerate(properties):
        if index:
            lines.append("")
        lines.extend(_render_property(prop))
    lines.append("}")
    return lines


def _render_enum(entry: EnumType) -> list[str]:
    if entry.value_type != "string":
        # A numeric enum has no unit-variant spelling that round-trips through
        # serde without pulling in serde_repr. Emitting a numeric alias keeps the
        # wire format exactly right and leaves the value constraint where it is
        # already enforced -- in the schema, and in the other two languages.
        rust_type = "i64" if entry.value_type == "integer" else "f64"
        lines = _doc(entry.description, "")
        allowed = ", ".join(str(value) for value in entry.values)
        lines.extend(_doc(f"Permitted values: {allowed}.", ""))
        lines.append(f"pub type {entry.name} = {rust_type};")
        return lines

    lines = _doc(entry.description, "")
    lines.append("#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]")
    lines.append(f"pub enum {entry.name} {{")
    seen: set[str] = set()
    for index, value in enumerate(entry.values):
        variant = pascal(str(value))
        while variant in seen:
            variant = f"{variant}X"
        seen.add(variant)
        if index:
            lines.append("")
        lines.append(f'    #[serde(rename = "{value}")]')
        lines.append(f"    {variant},")
    lines.append("}")
    return lines


def _render_union(name: str, node: UnionOf, contracts: Contracts) -> list[str]:
    lines: list[str] = []
    lines.append("#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]")
    if node.discriminator:
        lines.append(f'#[serde(tag = "{node.discriminator}")]')
    else:
        lines.append("#[serde(untagged)]")
    lines.append(f"pub enum {name} {{")
    for index, option in enumerate(node.options):
        if not isinstance(option, TypeRef):
            raise TypeError(f"{name}: union options must be named types")
        target = contracts.by_name(option.name)
        variant = option.name
        if index:
            lines.append("")
        if node.discriminator and isinstance(target, ObjectType):
            tag = next(
                (p for p in target.properties if p.name == node.discriminator), None
            )
            if tag is not None and isinstance(tag.type, LiteralOf):
                lines.append(f'    #[serde(rename = "{tag.type.value}")]')
        lines.append(f"    {variant}({option.name}),")
    lines.append("}")
    return lines


def _collect_unions(contracts: Contracts) -> tuple[dict[str, UnionOf], dict[str, str]]:
    """Hoist every inline union to a named enum.

    Returns the named unions and, for tagged ones, the discriminator field that
    each participating struct must omit.
    """
    unions: dict[str, UnionOf] = {}
    skip: dict[str, str] = {}

    for entry in contracts.types:
        if not isinstance(entry, ObjectType):
            continue
        for prop in entry.properties:
            node = prop.type
            inner = node.item if isinstance(node, ListOf) else node
            if not isinstance(inner, UnionOf):
                continue
            name = f"{entry.name}{pascal(prop.name)}"
            if isinstance(node, ListOf):
                name = f"{name}Item"
            unions[name] = inner
            if inner.discriminator:
                for option in inner.options:
                    if isinstance(option, TypeRef):
                        previous = skip.get(option.name)
                        if previous and previous != inner.discriminator:
                            raise UnionConflict(
                                f"{option.name} participates in tagged unions with "
                                f"different discriminators ({previous!r} and "
                                f"{inner.discriminator!r})"
                            )
                        skip[option.name] = inner.discriminator
    return unions, skip


def _rewrite_union_refs(contracts: Contracts, unions: dict[str, UnionOf]) -> None:
    """Point properties at the hoisted enum names."""
    by_union = {id(node): name for name, node in unions.items()}
    for entry in contracts.types:
        if not isinstance(entry, ObjectType):
            continue
        for prop in entry.properties:
            node = prop.type
            if isinstance(node, ListOf) and id(node.item) in by_union:
                prop.type = ListOf(TypeRef(by_union[id(node.item)]))
            elif id(node) in by_union:
                prop.type = TypeRef(by_union[id(node)])


def emit(contracts: Contracts) -> str:
    unions, skip = _collect_unions(contracts)
    _rewrite_union_refs(contracts, unions)

    out: list[str] = []
    out.extend(f"//! {line}" if line else "//!" for line in BANNER.rstrip().split("\n"))
    out.append("")
    out.append("#![allow(clippy::all)]")
    out.append("")
    out.append("use std::collections::BTreeMap;")
    out.append("")
    out.append("use serde::{Deserialize, Serialize};")
    out.append("")

    for entry in contracts.types:
        if isinstance(entry, EnumType):
            out.extend(_render_enum(entry))
        elif isinstance(entry, ObjectType):
            out.extend(_render_struct(entry, skip.get(entry.name)))
        else:
            out.extend(_doc(entry.description, ""))
            out.append(f"pub type {entry.name} = {_type(entry.target)};")
        out.append("")

    for name in sorted(unions):
        out.extend(_render_union(name, unions[name], contracts))
        out.append("")

    out.append("/// Contract version every generated record declares.")
    out.append('pub const CONTRACT_VERSION: &str = "v0";')
    out.append("")

    return "\n".join(out)
