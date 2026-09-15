"""Category mapping: Organizze category name -> Visor category slug.

Unlike account IDs or recurring-pattern amounts, this mapping is a naming
*decision* (which Visor category an Organizze category becomes) rather than
a secret -- but it's still specific to one person's category setup, so it
lives in a local YAML file under $VISORSYNC_HOME/config, not in this repo.
See `mapping/examples/categories.example.yaml` for the format and
`mapping/loader.py` for how it's loaded/seeded.

For `is_custom` entries, `visor_slug` is only a *hint*/preferred label --
Visor's `create_category` tool takes a `name` (and `emoji`), not a slug; it
generates the slug itself. `sync_structure` matches existing custom
categories by name (not by this hint) and, once created, caches the real
assigned slug in state.db -- see `commands/sync_structure.py` and
`resolve_category_slug` below.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from visorsync.mapping.loader import ensure_config_dir, load_yaml


@dataclass(frozen=True)
class CategoryMapping:
    organizze_name: str
    visor_slug: str
    is_custom: bool = False
    emoji: str = "🏷️"
    category_type: Optional[str] = None  # "expense" | "income" -- required by Visor for top-level custom categories
    parent_slug: Optional[str] = None


@dataclass(frozen=True)
class MiscategorizedPattern:
    pattern_name: str
    current_slug: str
    correct_slug: str


@dataclass(frozen=True)
class CategoryConfig:
    mappings: list[CategoryMapping]
    hidden_system_category_slugs: list[str]
    protected_category_trees: list[str]
    known_miscategorized_patterns: list[MiscategorizedPattern]

    def find_by_organizze_name(self, name: str) -> CategoryMapping | None:
        for mapping in self.mappings:
            if mapping.organizze_name == name:
                return mapping
        return None


def load_category_config(config_dir: Path) -> CategoryConfig:
    ensure_config_dir(config_dir)
    data = load_yaml(config_dir, "categories.yaml")
    return CategoryConfig(
        mappings=[
            CategoryMapping(
                organizze_name=c["organizze_name"],
                visor_slug=c["visor_slug"],
                is_custom=bool(c.get("is_custom", False)),
                emoji=c.get("emoji", "🏷️"),
                category_type=c.get("category_type"),
                parent_slug=c.get("parent_slug"),
            )
            for c in data.get("categories", [])
        ],
        hidden_system_category_slugs=list(data.get("hidden_system_category_slugs", [])),
        protected_category_trees=list(data.get("protected_category_trees", [])),
        known_miscategorized_patterns=[
            MiscategorizedPattern(
                pattern_name=p["pattern_name"],
                current_slug=p["current_slug"],
                correct_slug=p["correct_slug"],
            )
            for p in data.get("known_miscategorized_patterns", [])
        ],
    )
