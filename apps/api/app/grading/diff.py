"""Unified-diff parser wrapping the ``unidiff`` library."""

from __future__ import annotations

from dataclasses import dataclass, field

from unidiff import PatchSet


def _strip_ab_prefix(path: str) -> str:
    """Strip the git ``a/`` / ``b/`` prefix from a patch path."""
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


@dataclass
class ParsedDiff:
    raw: str
    _patch: PatchSet = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._patch = PatchSet(self.raw)

    # ------------------------------------------------------------------ paths

    def changed_paths(self) -> list[str]:
        """Return list of all modified file paths (source path, no a/ b/ prefix)."""
        paths: list[str] = []
        for patched_file in self._patch:
            # Use source_file for removed files, target_file for added/modified.
            if patched_file.is_added_file:
                paths.append(_strip_ab_prefix(patched_file.path))
            elif patched_file.is_removed_file:
                paths.append(_strip_ab_prefix(patched_file.source_file))
            else:
                # For modified files, prefer the target (b/) path.
                paths.append(_strip_ab_prefix(patched_file.path))
        return paths

    # ----------------------------------------------------------------- counts

    def added_lines_total(self) -> int:
        """Total lines added across all files."""
        return sum(pf.added for pf in self._patch)

    def removed_lines_total(self) -> int:
        """Total lines removed across all files."""
        return sum(pf.removed for pf in self._patch)

    # ----------------------------------------------------------------- text

    def diff_text_for_file(self, path: str) -> str:
        """Return the concatenated hunk text for a specific file path.

        Comparison strips the ``a/`` / ``b/`` prefix from the PatchedFile path.
        Returns an empty string if the file is not in the diff.
        """
        for patched_file in self._patch:
            candidate = _strip_ab_prefix(patched_file.path)
            if candidate == path:
                return str(patched_file)
        return ""

    def full_diff_text(self) -> str:
        """Return the complete raw diff text."""
        return self.raw

    def is_empty(self) -> bool:
        return len(self._patch) == 0

    # ----------------------------------------------------------------- helpers

    def added_lines_for_file(self, path: str) -> list[str]:
        """Return added line values (without the ``+`` prefix) for a specific file."""
        lines: list[str] = []
        for patched_file in self._patch:
            candidate = _strip_ab_prefix(patched_file.path)
            if candidate == path:
                for hunk in patched_file:
                    for line in hunk:
                        if line.is_added:
                            lines.append(line.value)
        return lines

    def removed_lines_for_file(self, path: str) -> list[str]:
        """Return removed line values (without the ``-`` prefix) for a specific file."""
        lines: list[str] = []
        for patched_file in self._patch:
            candidate = _strip_ab_prefix(patched_file.path)
            if candidate == path:
                for hunk in patched_file:
                    for line in hunk:
                        if line.is_removed:
                            lines.append(line.value)
        return lines

    def all_added_lines(self) -> list[str]:
        """Return all added lines across the entire diff."""
        lines: list[str] = []
        for patched_file in self._patch:
            for hunk in patched_file:
                for line in hunk:
                    if line.is_added:
                        lines.append(line.value)
        return lines

    def all_removed_lines(self) -> list[str]:
        """Return all removed lines across the entire diff."""
        lines: list[str] = []
        for patched_file in self._patch:
            for hunk in patched_file:
                for line in hunk:
                    if line.is_removed:
                        lines.append(line.value)
        return lines
