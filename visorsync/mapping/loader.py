"""Generic loader for the personal mapping files under $VISORSYNC_HOME/config.

None of Rafael's real account IDs, card limits, recurring pattern amounts,
or category choices live in this repository -- they are personal data and
belong on the machine running the sync, not in git history. This module
loads them from YAML files in the user's config directory and fails loudly
with setup instructions if they're missing, instead of silently falling
back to placeholder data.

Packaged `*.example.yaml` templates (generic, no real data) ship alongside
this module and are copied into place on first use by `ensure_config_dir`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

EXAMPLES_DIR = Path(__file__).parent / "examples"


class MissingConfigError(RuntimeError):
    pass


def ensure_config_dir(config_dir: Path) -> None:
    """Create config_dir and seed it with example templates if it's empty.

    The seeded files are examples with placeholder values -- they will not
    match anything in Organizze, so sync-structure/migrate will just find no
    matches until they're actually filled in. This only saves a manual
    `mkdir` + copy step; it never fabricates real data.
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    for example_path in EXAMPLES_DIR.glob("*.example.yaml"):
        target_name = example_path.name.replace(".example.yaml", ".yaml")
        target = config_dir / target_name
        if not target.exists():
            shutil.copy(example_path, target)


def load_yaml(config_dir: Path, filename: str) -> dict | list:
    path = config_dir / filename
    if not path.exists():
        example = EXAMPLES_DIR / filename.replace(".yaml", ".example.yaml")
        raise MissingConfigError(
            f"Missing personal config file: {path}\n"
            f"This holds your own account IDs / amounts and is never committed to git.\n"
            f"Copy the template and fill in your real values:\n"
            f"  cp {example} {path}\n"
            f"(or run any visorsync command once to auto-seed it with placeholders)"
        )
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
