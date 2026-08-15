"""Emit TypeScript declarations from the contract IR."""

from __future__ import annotations

from typing import Any

from .common import BANNER, wrap_doc
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
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
    "any": "unknown",
}


def _value(value: Any) -> str:
    """Render a JSON value as a TypeScript literal, preserving its type."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _type(node: AnyType) -> str:
    if isinstance(node, TypeRef):
        return node.name
    if isinstance(node, Primitive):
        return PRIMITIVES[node.kind]
    if isinstance(node, ListOf):
        inner = _type(node.item)
        return f"Array<{inner}>" if "|" in inner else f"{inner}[]"
    if isinstance(node, MapOf):
        return f"Record<string, {_type(node.value)}>" if node.value else "Record<string, unknown>"
    if isinstance(node, LiteralOf):
        return _value(node.value)
    if isinstance(node, UnionOf):
        return " | ".join(_type(option) for option in node.options)
    raise TypeError(f"unhandled IR node: {node!r}")


def _doc_block(text: str, indent: str) -> list[str]:
    lines = wrap_doc(text, width=88, indent=indent)
    if not lines:
        return []
    if len(lines) == 1:
        return [f"{indent}/** {lines[0]} */"]
    out = [f"{indent}/**"]
    out.extend(f"{indent} * {line}" if line else f"{indent} *" for line in lines)
    out.append(f"{indent} */")
    return out


def _default_comment(prop: Property) -> str:
    if not prop.has_default:
        return ""
    value = prop.default
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif value is None:
        rendered = "null"
    elif isinstance(value, str):
        rendered = f'"{value}"'
    elif isinstance(value, (list, dict)):
        rendered = "[]" if isinstance(value, list) else "{}"
    else:
        rendered = str(value)
    return f"Default: {rendered}."


def _render_property(prop: Property) -> list[str]:
    annotation = _type(prop.type)
    if prop.nullable:
        annotation = f"{annotation} | null"

    doc = prop.description
    extra = _default_comment(prop)
    if extra:
        doc = f"{doc} {extra}".strip()

    lines = _doc_block(doc, "  ")
    marker = "?" if prop.optional else ""
    key = prop.name if prop.name.isidentifier() else f'"{prop.name}"'
    lines.append(f"  {key}{marker}: {annotation};")
    return lines


def _render_object(entry: ObjectType) -> list[str]:
    lines = _doc_block(entry.description, "")
    lines.append(f"export interface {entry.name} {{")
    if not entry.properties:
        lines.append("  [key: string]: never;")
    for index, prop in enumerate(entry.properties):
        if index:
            lines.append("")
        lines.extend(_render_property(prop))
    lines.append("}")
    return lines


def _render_enum(entry: EnumType) -> list[str]:
    lines = _doc_block(entry.description, "")
    rendered = [_value(value) for value in entry.values]
    single = f"export type {entry.name} = {' | '.join(rendered)};"
    if len(single) <= 100:
        lines.append(single)
    else:
        lines.append(f"export type {entry.name} =")
        for index, value in enumerate(rendered):
            terminator = ";" if index == len(rendered) - 1 else ""
            lines.append(f"  | {value}{terminator}")
    lines.append("")
    lines.append(f"export const {entry.name}Values = [")
    for value in entry.values:
        lines.append(f"  {_value(value)},")
    lines.append(f"] as const satisfies readonly {entry.name}[];")
    return lines


def _render_alias(entry: AliasType) -> list[str]:
    lines = _doc_block(entry.description, "")
    lines.append(f"export type {entry.name} = {_type(entry.target)};")
    return lines


def emit(contracts: Contracts) -> str:
    out: list[str] = ["/**"]
    out.extend(f" * {line}" if line else " *" for line in BANNER.rstrip().split("\n"))
    out.append(" */")
    out.append("")

    for entry in contracts.types:
        if isinstance(entry, EnumType):
            out.extend(_render_enum(entry))
        elif isinstance(entry, ObjectType):
            out.extend(_render_object(entry))
        else:
            out.extend(_render_alias(entry))
        out.append("")

    out.append("/** Root contract types, keyed by schema title. */")
    out.append("export interface ContractRoots {")
    for name in contracts.roots:
        out.append(f"  {name}: {name};")
    out.append("}")
    out.append("")
    out.append("export const CONTRACT_VERSION = \"v0\";")
    out.append("")

    return "\n".join(out)
