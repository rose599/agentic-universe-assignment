"""Workflow lead schemas and the disposition catalogue (read from the given schemas dir)."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import normalize_header


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    required: bool = False
    pii: bool = False
    reference: bool = False
    default: str | None = None
    values: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Schema:
    workflow_id: str
    fields: tuple[Field, ...]

    @property
    def phone_field(self) -> Field:
        return next(f for f in self.fields if f.type == "phone")

    @property
    def reference_field(self) -> Field | None:
        return next((f for f in self.fields if f.reference), None)

    def header_lookup(self) -> dict[str, str]:
        """normalised header text -> field name (names and aliases)."""
        out: dict[str, str] = {}
        for f in self.fields:
            for label in (f.name, *f.aliases):
                out.setdefault(normalize_header(label), f.name)
        return out


@dataclass(frozen=True)
class Disposition:
    code: str
    is_terminal: bool
    is_success: bool


@dataclass
class Config:
    schemas: dict[str, Schema] = field(default_factory=dict)
    # (workflow_id, code) -> Disposition
    dispositions: dict[tuple[str, str], Disposition] = field(default_factory=dict)


def load_config(schemas_dir: str | Path) -> Config:
    schemas_dir = Path(schemas_dir)
    cfg = Config()
    for p in sorted(schemas_dir.glob("*.json")):
        raw = json.loads(p.read_text(encoding="utf-8"))
        fields = tuple(
            Field(
                name=f["name"], type=f["type"], required=f.get("required", False),
                pii=f.get("pii", False), reference=f.get("reference", False),
                default=f.get("default"), values=tuple(f.get("values", ())),
                aliases=tuple(f.get("aliases", ())),
            )
            for f in raw["fields"]
        )
        cfg.schemas[raw["workflow_id"]] = Schema(raw["workflow_id"], fields)
    disp = schemas_dir / "dispositions.csv"
    with disp.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            cfg.dispositions[(row["workflow_id"].strip(), row["code"].strip())] = Disposition(
                code=row["code"].strip(),
                is_terminal=row["is_terminal"].strip().lower() == "true",
                is_success=row["is_success"].strip().lower() == "true",
            )
    return cfg
