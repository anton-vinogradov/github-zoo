"""Secret loading for github-zoo.

A GitHub token in the config file is a token in every backup, every `cat`, and
every screen share. Three sources are supported, in order of preference:

1. `<field>_credential` — a systemd encrypted credential. The ciphertext on
   disk is bound to this machine (TPM where present), systemd decrypts it at
   service start and drops it in a tmpfs directory readable only by the
   service. Nothing readable ever sits on disk.
2. `<field>_file` — a path to a file, for setups without systemd credentials.
3. `<field>` — the plain value, kept working so existing configs do not break.

Nothing here logs a secret, and callers receive the value only as a return.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def load(config: dict, field: str) -> str:
    """Resolve one secret from a config section."""
    name = config.get(f"{field}_credential")
    if name:
        directory = os.environ.get("CREDENTIALS_DIRECTORY")
        if directory:
            try:
                return (Path(directory) / name).read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return ""

    path_value = config.get(f"{field}_file")
    if path_value:
        try:
            return Path(os.path.expanduser(path_value)).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    return config.get(field) or ""


def from_gh_cli() -> str:
    """The token `gh` already holds, for running this on a laptop.

    Developing against a fresh token means minting one, and the one that is
    already authorised is right there. On the service host `gh` is absent and
    this returns nothing, which is the intended outcome.
    """
    for binary in ("gh", "/opt/homebrew/bin/gh", "/usr/local/bin/gh"):
        try:
            out = subprocess.run([binary, "auth", "token"], capture_output=True,
                                 text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return ""


def describe(config: dict, field: str) -> str:
    """Where a secret comes from — for diagnostics that must not leak it."""
    if config.get(f"{field}_credential"):
        return f"systemd credential «{config[f'{field}_credential']}»"
    if config.get(f"{field}_file"):
        return f"файл {config[f'{field}_file']}"
    if config.get(field):
        return "значение в конфиге (открытым текстом)"
    return "не задан"
