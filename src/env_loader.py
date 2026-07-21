"""
src/env_loader.py
─────────────────
Lightweight .env file loader — no external dependencies.

Reads a .env file and injects values into os.environ.  Only sets
variables that are not already set (so shell exports always win).

Supported syntax
────────────────
  KEY=value           plain assignment
  KEY="quoted value"  double-quoted (quotes stripped)
  KEY='quoted value'  single-quoted (quotes stripped)
  # comment           ignored
  export KEY=value    'export' prefix stripped
  KEY=                empty value (allowed; variable is set to "")

Multi-line PEM values are supported — if a value starts with
"-----BEGIN", the parser reads subsequent lines until it finds a line
starting with "-----END", then joins them all with newlines.  This
allows RSA/EC private keys to be pasted directly into the .env file.

Usage::

    from src.env_loader import load
    load()                           # loads .env next to project root
    load("/path/to/.env")            # explicit path
    load(override=True)              # set even if already in os.environ
"""

import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a .env file and return a dict of {KEY: value}.

    Does not modify os.environ.  Supports multi-line PEM values: if a
    value begins with "-----BEGIN", subsequent lines are collected until
    a line beginning with "-----END" is found, then joined with newlines.

    Args:
        path: Path to the .env file.

    Returns:
        Dict mapping variable name to value (both as strings).

    Raises:
        FileNotFoundError: If *path* does not exist.
    """
    result: Dict[str, str] = {}

    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        i += 1
        line = raw_line.strip()

        # Skip blanks and comments
        if not line or line.startswith("#"):
            continue

        # Strip optional 'export ' prefix
        if line.startswith("export "):
            line = line[len("export "):].lstrip()

        # Split on first '=' only
        if "=" not in line:
            logger.debug("env_loader: line %d has no '=', skipped: %r", i, line)
            continue

        key, _, raw_value = line.partition("=")
        key = key.strip()

        if not key:
            continue

        raw_value = raw_value.strip()

        # Multi-line PEM block: -----BEGIN ... -----END-----
        if raw_value.startswith("-----BEGIN"):
            pem_lines = [raw_value]
            while i < len(lines):
                pem_line = lines[i].rstrip("\n")
                i += 1
                pem_lines.append(pem_line)
                if pem_line.strip().startswith("-----END"):
                    break
            value = "\n".join(pem_lines)
        else:
            value = _strip_quotes(raw_value)

        result[key] = value

    return result


def load(
    env_path: Optional[str] = None,
    override: bool = False,
) -> Dict[str, str]:
    """Load .env file into os.environ.

    Args:
        env_path: Path to the .env file.  Defaults to ".env" in the
                  project root (two levels above this file).
        override: If True, overwrite variables already in os.environ.
                  Defaults to False (shell exports win).

    Returns:
        Dict of all key/value pairs found in the file (whether loaded
        or skipped because already set).
    """
    if env_path is None:
        # Default: .env next to the project root (parent of src/)
        env_path = str(Path(__file__).parent.parent / ".env")

    path = Path(env_path)

    if not path.exists():
        logger.debug("env_loader: %s not found, skipping", path)
        return {}

    try:
        parsed = parse_env_file(path)
    except Exception as exc:
        logger.warning("env_loader: failed to parse %s — %s", path, exc)
        return {}

    loaded: Dict[str, str] = {}
    skipped = 0

    for key, value in parsed.items():
        if value == "" and not override:
            # Don't inject blank placeholders — they would shadow real values
            skipped += 1
            continue
        if key in os.environ and not override:
            skipped += 1
            continue
        os.environ[key] = value
        loaded[key] = value

    if loaded:
        logger.info(
            "env_loader: loaded %d var(s) from %s [%s]",
            len(loaded),
            path.name,
            ", ".join(loaded.keys()),
        )
    if skipped:
        logger.debug("env_loader: skipped %d var(s) (already set or blank)", skipped)

    return loaded


def get_required(key: str, env_path: Optional[str] = None) -> str:
    """Return the value of a required environment variable.

    Calls ``load()`` first if the variable is not already set.

    Args:
        key:      Environment variable name.
        env_path: Optional path to the .env file.

    Returns:
        The value as a string.

    Raises:
        KeyError: If the variable is not set after loading the .env file.
    """
    if key not in os.environ:
        load(env_path)
    value = os.environ.get(key)
    if not value:
        raise KeyError(
            f"Required environment variable '{key}' is not set.  "
            f"Add it to your .env file."
        )
    return value


# ── Internal helpers ─────────────────────────────────────────────────

def _strip_quotes(value: str) -> str:
    """Remove matching surrounding quotes from a string."""
    if len(value) >= 2:
        if (value[0] == '"' and value[-1] == '"') or \
           (value[0] == "'" and value[-1] == "'"):
            return value[1:-1]
    return value
