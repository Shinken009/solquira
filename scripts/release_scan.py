"""Shared release scanning: secret patterns, path boundaries and build inventory.

Extracted from the retired static-Pages stager so the GitHub and hosted packagers
keep the same guarantees without carrying the pages workflow itself.
"""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path

MAX_FILE_BYTES = 25 * 1024 * 1024
# Entry files a built UI context may serve at its root.
ROOT_FILES = {"index.html", "favicon.svg", "robots.txt"}
CESIUM_SUFFIXES = {
    ".css",
    ".gif",
    ".jpg",
    ".jpeg",
    ".js",
    ".json",
    ".png",
    ".svg",
    ".wasm",
    ".xml",
}
TEXT_SUFFIXES = {".html", ".js", ".css", ".json", ".svg", ".xml", ".txt", ".md"}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,})\b"),
    re.compile(
        rb"(?:NEBIUS_API_KEY|CLOUDFLARE_API_TOKEN|JIRA_API_TOKEN)\s*[=:]\s*[\"']?[A-Za-z0-9_/-]{16,}",
        re.I,
    ),
    re.compile(rb"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+[\\/]", re.I),
)

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def regular_path(path: Path, boundary: Path) -> None:
    """Reject symlinks/junctions, including ancestors, before reading bytes."""
    resolved_boundary = boundary.resolve(strict=True)
    if not path.resolve(strict=True).is_relative_to(resolved_boundary):
        raise ValueError("A build path escapes its source directory")
    for candidate in (
        path,
        *(boundary / parent for parent in path.relative_to(boundary).parents),
    ):
        info = candidate.lstat()
        if candidate.is_symlink() or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError("Linked or reparse-point build paths are not permitted")


def allowed(relative: Path) -> bool:
    parts = relative.parts
    if any(
        part.startswith(".")
        or part.lower() in {"backend", "docs", "scripts", "node_modules", "__pycache__"}
        for part in parts
    ):
        return False
    if len(parts) == 1:
        return relative.name in ROOT_FILES
    if parts[0] == "assets":
        return len(parts) == 2 and relative.suffix.lower() in {".js", ".css"}
    if parts[0] == "cesium":
        if len(parts) == 2:
            return relative.name in {
                "LICENSE.md",
                "ThirdParty.json",
                "ThirdParty.extra.json",
            }
        return (
            parts[1] in {"Assets", "Workers", "ThirdParty", "Widgets"}
            and relative.suffix.lower() in CESIUM_SUFFIXES
        )
    return False


def inventory(source: Path) -> dict[str, dict[str, str | int]]:
    """Enumerate a built UI with its allowlist, size ceiling and secret scan."""
    if not source.is_dir():
        raise ValueError("The static build directory does not exist")
    files: dict[str, dict[str, str | int]] = {}
    for path in sorted(source.rglob("*")):
        regular_path(path, source)
        if path.is_dir():
            continue
        relative = path.relative_to(source)
        if not allowed(relative):
            raise ValueError(f"Unexpected static build path: {relative.as_posix()}")
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("A build file is not regular or exceeds the size ceiling")
        data = path.read_bytes()
        if path.suffix.lower() in TEXT_SUFFIXES and any(
            pattern.search(data) for pattern in SECRET_PATTERNS
        ):
            raise ValueError(
                f"Sensitive-looking content in static build: {relative.as_posix()}"
            )
        files[relative.as_posix()] = {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    if "index.html" not in files or not any(
        name.startswith("assets/") and name.endswith(".js") for name in files
    ):
        raise ValueError("A complete Vite static build is required")
    return files
