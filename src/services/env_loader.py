"""Zero-dependency .env file loader using Python standard library only."""

import os
from pathlib import Path
from typing import Optional, Union

_DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _strip_quotes_and_inline_comment(raw_value: str) -> str:
    """Strip matching single/double quotes or trailing unquoted inline comments."""
    val = raw_value.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        return val[1:-1]

    # Strip inline comment (# preceded by whitespace) only for unquoted values
    if " #" in val:
        val = val.split(" #", 1)[0].rstrip()
    return val


def load_env_file(
    env_path: Optional[Union[str, Path]] = None,
    override: bool = False,
) -> bool:
    """Load key=value pairs from a .env file into os.environ without overwriting existing keys by default."""
    target = Path(env_path) if env_path is not None else _DEFAULT_ENV_PATH
    if not target.exists() or not target.is_file():
        return False

    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue

        key, raw_val = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        val = _strip_quotes_and_inline_comment(raw_val)
        if override:
            os.environ[key] = val
        else:
            os.environ.setdefault(key, val)

    return True
