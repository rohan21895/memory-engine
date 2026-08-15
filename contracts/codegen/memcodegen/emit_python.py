"""Emit pydantic v2 models from the contract IR."""

from __future__ import annotations

import keyword
from typing import Any

from .common import BANNER, screaming_snake, wrap_doc
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
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "null": "None",
    "any": "Any",
}


def _type(node: AnyType) -> str:
    if isinstance(node, TypeRef):
        return node.name
    if isinstance(node, Primitive):
        return PRIMITIVES[node.kind]
    if isinstance(node, ListOf):
        return f"list[{_type(node.item)}]"
    if isinstance(node, MapOf):
        return f"dict[str, {_type(node.value)}]" if node.value else "dict[str, Any]"
    if isinstance(node, LiteralOf):
        return f"Literal[{_literal(node.value)}]"
    if isinstance(node, UnionOf):
        return " | ".join(_type(option) for option in node.options)
    raise TypeError(f"unhandled IR node: {node!r}")


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    if isinstance(value, str):
        return f'"{value}"'
    return repr(value)


def _field_name(name: str) -> tuple[str, str | None]:
    """Return (python identifier, serialisation alias when they differ)."""
    if keyword.iskeyword(name) or keyword.issoftkeyword(name):
        return f"{name}_", name
    return name, None


def _render_property(prop: Property) -> list[str]:
    annotation = _type(prop.type)
    if prop.nullable:
        annotation = f"{annotation} | None"

    identifier, alias = _field_name(prop.name)
    field_args: list[str] = []

    if prop.has_default:
        default = prop.default
        if isinstance(default, list):
            field_args.append("default_factory=list")
        elif isinstance(default, dict):
            field_args.append("default_factory=dict")
        else:
            field_args.append(f"default={_literal(default)}")
    elif prop.optional:
        if not prop.nullable:
            annotation = f"{annotation} | None"
        field_args.append("default=None")

    if alias:
        field_args.append(f'alias="{alias}"')

    if prop.description:
        summary = " ".join(prop.description.split())
        if len(summary) > 240:
            summary = summary[:237] + "..."
        escaped = summary.replace("\\", "\\\\").replace('"', '\\"')
        field_args.append(f'description="{escaped}"')

    lines: list[str] = []
    for line in wrap_doc(prop.description, width=84, indent="    "):
        lines.append(f"    # {line}" if line else "    #")

    if field_args:
        joined = ", ".join(field_args)
        declaration = f"    {identifier}: {annotation} = Field({joined})"
        if len(declaration) <= 100:
            lines.append(declaration)
        else:
            lines.append(f"    {identifier}: {annotation} = Field(")
            for arg in field_args:
                lines.append(f"        {arg},")
            lines.append("    )")
    else:
        lines.append(f"    {identifier}: {annotation}")
    return lines


def _render_object(entry: ObjectType) -> list[str]:
    lines = [f"class {entry.name}(ContractModel):"]
    doc = wrap_doc(entry.description, width=84, indent="    ")
    if doc:
        lines.append('    """')
        lines.extend(f"    {line}" if line else "" for line in doc)
        lines.append('    """')
        lines.append("")
    if not entry.properties:
        lines.append("    pass")
        return lines

    # Required fields first so pydantic's generated __init__ reads naturally
    # and so a reviewer sees the mandatory shape of the record immediately.
    ordered = [p for p in entry.properties if p.required and not p.has_default]
    ordered += [p for p in entry.properties if not (p.required and not p.has_default)]
    for index, prop in enumerate(ordered):
        if index:
            lines.append("")
        lines.extend(_render_property(prop))
    return lines


def _render_enum(entry: EnumType) -> list[str]:
    # str/int mixin so a member compares equal to its wire value, which keeps
    # `record.kind == "image"` working without an explicit .value everywhere.
    base = {"string": "str", "integer": "int", "number": "float"}[entry.value_type]
    lines = [f"class {entry.name}({base}, Enum):"]
    doc = wrap_doc(entry.description, width=84, indent="    ")
    if doc:
        lines.append('    """')
        lines.extend(f"    {line}" if line else "" for line in doc)
        lines.append('    """')
        lines.append("")
    seen: set[str] = set()
    for value in entry.values:
        member = screaming_snake(str(value))
        while member in seen:
            member = f"{member}_"
        seen.add(member)
        lines.append(f"    {member} = {_literal(value)}")
    return lines


def _render_alias(entry: AliasType) -> list[str]:
    lines: list[str] = []
    for line in wrap_doc(entry.description, width=88):
        lines.append(f"# {line}" if line else "#")
    lines.append(f"{entry.name} = {_type(entry.target)}")
    return lines


def emit(contracts: Contracts) -> str:
    out: list[str] = ['"""']
    out.extend(BANNER.rstrip().split("\n"))
    out.append('"""')
    out.append("")
    out.append("from __future__ import annotations")
    out.append("")
    out.append("from enum import Enum")
    out.append("from typing import Any, Literal")
    out.append("")
    out.append("from pydantic import BaseModel, ConfigDict, Field")
    out.append("")
    out.append("")
    out.append("class ContractModel(BaseModel):")
    out.append('    """Base for every generated contract model.')
    out.append("")
    out.append("    `extra=forbid` mirrors `additionalProperties: false` in the schemas:")
    out.append("    an undeclared field is an error on both sides of the agent boundary,")
    out.append("    never a silently ignored one.")
    out.append('    """')
    out.append("")
    out.append("    model_config = ConfigDict(")
    out.append('        extra="forbid",')
    out.append("        populate_by_name=True,")
    out.append("        use_enum_values=False,")
    out.append("        # The contracts legitimately use `model_id` / `model_runs` to")
    out.append("        # describe ML models; pydantic reserves the `model_` prefix by")
    out.append("        # default and would warn on every import.")
    out.append("        protected_namespaces=(),")
    out.append("    )")
    out.append("")

    for entry in contracts.types:
        out.append("")
        if isinstance(entry, EnumType):
            out.extend(_render_enum(entry))
        elif isinstance(entry, ObjectType):
            out.extend(_render_object(entry))
        else:
            out.extend(_render_alias(entry))
        out.append("")

    out.append("")
    out.append("# Resolve forward references between models.")
    for entry in contracts.types:
        if isinstance(entry, ObjectType):
            out.append(f"{entry.name}.model_rebuild()")

    out.append("")
    out.append("")
    out.append("#: Root contract types, keyed by schema title.")
    out.append("ROOT_MODELS: dict[str, type[ContractModel]] = {")
    for name in contracts.roots:
        out.append(f'    "{name}": {name},')
    out.append("}")
    out.append("")
    out.append("__all__ = [")
    out.append('    "ContractModel",')
    out.append('    "ROOT_MODELS",')
    for entry in contracts.types:
        out.append(f'    "{entry.name}",')
    out.append("]")
    out.append("")

    return "\n".join(out)
