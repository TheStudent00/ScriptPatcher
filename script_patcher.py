# [BLOCK: MODULE_DOCSTRING] START
"""
ScriptPatcher v2 — LLM-friendly block-based code patching utility.

Patches named blocks of code within a larger file using human-readable
markers in comments. Tolerant of LLM marker drift (case, whitespace,
line endings) and avoids silent failures by returning structured results.

Marker grammar (case-insensitive, whitespace-tolerant):
    [BLOCK: <name>] START   ...content...   [BLOCK: <name>] END

Names may contain letters, digits, underscore, hyphen, and dot.
The marker can be embedded in any comment style — the surrounding
characters on the marker line are preserved on patch.

Typical workflow:
    p = ScriptPatcher(filepath="game.html")
    print(p.validate())              # check baseline integrity
    print(p.list_blocks())           # see what's available
    snippet = p.extract("PHYSICS")   # send to LLM as context
    # ... LLM returns a patch_text containing one or more blocks ...
    print(p.diff(patch_text))        # preview
    result = p.patch(patch_text)     # apply (writes to disk if filepath set)
    assert result.ok
"""
# [BLOCK: MODULE_DOCSTRING] END

# [BLOCK: IMPORTS] START
import re
import shutil
import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union, List, Dict, Tuple

try:
    import IPython.display as _ipy_display
    _HAS_IPYTHON = True
except ImportError:
    _HAS_IPYTHON = False
# [BLOCK: IMPORTS] END


# [BLOCK: MARKER_RE] START
# Forgiving marker pattern. Allows:
#   - any case (BLOCK / block / Block)
#   - whitespace inside the brackets ([ BLOCK : NAME ])
#   - any whitespace between ] and START/END
#   - names: alphanumeric + _ - .
MARKER_RE = re.compile(
    r"\[\s*BLOCK\s*:\s*([A-Za-z0-9_\-\.]+)\s*\]\s*(START|END)\b",
    re.IGNORECASE,
)
# [BLOCK: MARKER_RE] END


# [BLOCK: PATCH_RESULT] START
@dataclass
class PatchResult:
    """Outcome of a patch() call. Use .ok for a one-shot pass/fail check."""
    patched: List[str] = field(default_factory=list)
    not_found: List[str] = field(default_factory=list)        # in patch but not in target
    malformed_in_patch: List[str] = field(default_factory=list)  # orphan START or END
    duplicates_in_patch: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            bool(self.patched)
            and not self.not_found
            and not self.malformed_in_patch
        )

    def __str__(self) -> str:
        parts = []
        if self.patched:
            parts.append(f"  patched: {self.patched}")
        if self.not_found:
            parts.append(f"  not found in target: {self.not_found}")
        if self.malformed_in_patch:
            parts.append(f"  malformed in patch: {self.malformed_in_patch}")
        if self.duplicates_in_patch:
            parts.append(f"  duplicate names in patch: {self.duplicates_in_patch}")
        body = "\n".join(parts) if parts else "  (no changes)"
        return f"PatchResult(ok={self.ok}):\n{body}"
# [BLOCK: PATCH_RESULT] END


# [BLOCK: VALIDATION_RESULT] START
@dataclass
class ValidationResult:
    """Structural check of all markers in the current content."""
    blocks: List[str] = field(default_factory=list)
    orphan_starts: List[str] = field(default_factory=list)
    orphan_ends: List[str] = field(default_factory=list)
    duplicates: List[str] = field(default_factory=list)  # well-formed blocks appearing more than once

    @property
    def ok(self) -> bool:
        return not (self.orphan_starts or self.orphan_ends or self.duplicates)

    def __str__(self) -> str:
        parts = [f"  blocks: {self.blocks}"]
        if self.orphan_starts:
            parts.append(f"  orphan STARTs: {self.orphan_starts}")
        if self.orphan_ends:
            parts.append(f"  orphan ENDs: {self.orphan_ends}")
        if self.duplicates:
            parts.append(f"  duplicates: {self.duplicates}")
        return f"ValidationResult(ok={self.ok}):\n" + "\n".join(parts)
# [BLOCK: VALIDATION_RESULT] END


# [BLOCK: SCRIPT_PATCHER_CLASS] START
class ScriptPatcher:
    # [BLOCK: INIT] START
    def __init__(
        self,
        content: Optional[str] = None,
        filepath: Optional[Union[str, Path]] = None,
        normalize_endings: bool = True,
    ):
        self.filepath = Path(filepath) if filepath else None
        self._normalize = normalize_endings
        self.content = self._normalize_text(content) if content else content
        if self.filepath and self.content is None:
            self.load()
    # [BLOCK: INIT] END

    # -- I/O --------------------------------------------------------------

    # [BLOCK: NORMALIZE_TEXT] START
    @staticmethod
    def _normalize_text(text: str) -> str:
        """Collapse CRLF / CR to LF for stable matching."""
        if text is None:
            return text
        return text.replace("\r\n", "\n").replace("\r", "\n")
    # [BLOCK: NORMALIZE_TEXT] END

    # [BLOCK: LOAD] START
    def load(self) -> Optional[str]:
        if self.filepath:
            text = self.filepath.read_text(encoding="utf-8")
            self.content = self._normalize_text(text) if self._normalize else text
        return self.content
    # [BLOCK: LOAD] END

    # [BLOCK: SAVE] START
    def save(self, backup: bool = False) -> None:
        if not (self.filepath and self.content is not None):
            return
        if backup and self.filepath.exists():
            bak = self.filepath.with_suffix(self.filepath.suffix + ".bak")
            shutil.copy2(self.filepath, bak)
        self.filepath.write_text(self.content, encoding="utf-8")
    # [BLOCK: SAVE] END

    # -- marker scanning --------------------------------------------------

    # [BLOCK: SCAN_MARKERS] START
    def _scan_markers(self, text: str):
        """Yield (name, kind, line_start, line_end) for every marker found.

        line_start = index of the first char of the marker's line.
        line_end   = index just past the last char of the marker's line
                     (the trailing '\\n' if present, else len(text)).
        """
        if not text:
            return []
        out = []
        for m in MARKER_RE.finditer(text):
            name = m.group(1)
            kind = m.group(2).upper()
            line_start = text.rfind("\n", 0, m.start()) + 1
            nl = text.find("\n", m.end())
            line_end = (nl + 1) if nl != -1 else len(text)
            out.append((name, kind, line_start, line_end))
        return out
    # [BLOCK: SCAN_MARKERS] END

    # [BLOCK: PAIR_BLOCKS] START
    def _pair_blocks(self, text: str):
        """Pair STARTs with ENDs. Names compared case-insensitively.

        Returns (pairs, orphan_starts, orphan_ends, duplicates) where
        pairs is {canonical_name: [(line_start, line_end), ...]}.
        canonical_name is the casing used in the START marker.
        """
        markers = self._scan_markers(text)
        pairs: Dict[str, List[Tuple[int, int]]] = {}
        orphan_starts: List[str] = []
        orphan_ends: List[str] = []
        open_starts: Dict[str, Tuple[str, int]] = {}  # key=lower, value=(canonical_name, line_start)

        for name, kind, line_start, line_end in markers:
            key = name.lower()
            if kind == "START":
                if key in open_starts:
                    orphan_starts.append(open_starts[key][0])
                open_starts[key] = (name, line_start)
            else:  # END
                if key not in open_starts:
                    orphan_ends.append(name)
                else:
                    canonical, s = open_starts.pop(key)
                    pairs.setdefault(canonical, []).append((s, line_end))

        for _, (canonical, _) in open_starts.items():
            orphan_starts.append(canonical)

        duplicates = [n for n, spans in pairs.items() if len(spans) > 1]
        return pairs, orphan_starts, orphan_ends, duplicates
    # [BLOCK: PAIR_BLOCKS] END

    # -- public API -------------------------------------------------------

    # [BLOCK: LIST_BLOCKS] START
    def list_blocks(self) -> List[str]:
        """Sorted names of well-formed (paired) blocks."""
        if not self.content:
            return []
        pairs, _, _, _ = self._pair_blocks(self.content)
        return sorted(pairs.keys())
    # [BLOCK: LIST_BLOCKS] END

    # [BLOCK: VALIDATE] START
    def validate(self) -> ValidationResult:
        """Inspect marker integrity without modifying anything."""
        r = ValidationResult()
        if not self.content:
            return r
        pairs, os_, oe, dups = self._pair_blocks(self.content)
        r.blocks = sorted(pairs.keys())
        r.orphan_starts = sorted(set(os_))
        r.orphan_ends = sorted(set(oe))
        r.duplicates = sorted(dups)
        return r
    # [BLOCK: VALIDATE] END

    # [BLOCK: EXTRACT] START
    def extract(self, block_name: str) -> Optional[str]:
        """Return the block including its marker lines, or None."""
        if not self.content:
            return None
        pairs, _, _, _ = self._pair_blocks(self.content)
        for name, spans in pairs.items():
            if name.lower() == block_name.lower():
                s, e = spans[0]
                # Strip trailing \n so caller gets a clean snippet; they can re-add it.
                snippet = self.content[s:e]
                return snippet[:-1] if snippet.endswith("\n") else snippet
        return None
    # [BLOCK: EXTRACT] END

    # [BLOCK: PATCH] START
    def patch(
        self,
        patch_text: str,
        dry_run: bool = False,
        save: bool = True,
        backup: bool = False,
        preserve_indent: bool = True,
    ) -> PatchResult:
        """Apply every well-formed block in patch_text to self.content.

        With preserve_indent=True (default), each new block is re-indented
        so its marker line matches the indent of the target's marker line.
        A tab/space mismatch between target and patch raises ValueError.
        """
        result = PatchResult()
        if self.content is None:
            return result
        if self._normalize:
            patch_text = self._normalize_text(patch_text)

        p_pairs, p_os, p_oe, p_dups = self._pair_blocks(patch_text)
        result.malformed_in_patch = sorted(set(p_os + p_oe))
        result.duplicates_in_patch = sorted(p_dups)

        if not p_pairs:
            return result

        t_pairs, _, _, _ = self._pair_blocks(self.content)
        t_lookup = {k.lower(): k for k in t_pairs}  # case-insensitive lookup

        def _leading_ws(line: str) -> str:
            return line[: len(line) - len(line.lstrip())]

        def _reindent(block_text: str, from_ws: str, to_ws: str) -> str:
            if from_ws == to_ws:
                return block_text
            out = []
            for line in block_text.split("\n"):
                if not line.strip():
                    out.append(line)  # leave blank lines alone
                elif line.startswith(from_ws):
                    out.append(to_ws + line[len(from_ws):])
                else:
                    out.append(line)  # less-indented than marker; preserve
            return "\n".join(out)

        # Build replacements first so we can apply them right-to-left
        # (preserves earlier offsets after each splice).
        replacements: List[Tuple[int, int, str, str]] = []
        for name, spans in p_pairs.items():
            ps, pe = spans[0]
            new_block = patch_text[ps:pe]
            target_name = t_lookup.get(name.lower())
            if target_name is None:
                result.not_found.append(name)
                continue
            ts, te = t_pairs[target_name][0]

            if preserve_indent:
                t_line = self.content[ts:].split("\n", 1)[0]
                p_line = new_block.split("\n", 1)[0]
                t_ws = _leading_ws(t_line)
                p_ws = _leading_ws(p_line)
                if t_ws and p_ws:
                    t_tabs = "\t" in t_ws
                    p_tabs = "\t" in p_ws
                    if t_tabs != p_tabs:
                        raise ValueError(
                            f"Indent style mismatch in block {name!r}: "
                            f"target uses {'tabs' if t_tabs else 'spaces'}, "
                            f"patch uses {'tabs' if p_tabs else 'spaces'}. "
                            f"Normalize the patch text to match the target."
                        )
                new_block = _reindent(new_block, p_ws, t_ws)

            replacements.append((ts, te, new_block, name))

        if dry_run:
            result.patched = [r[3] for r in replacements]
            return result

        for ts, te, new_block, name in sorted(replacements, key=lambda r: r[0], reverse=True):
            self.content = self.content[:ts] + new_block + self.content[te:]
            result.patched.append(name)

        result.patched.reverse()  # report in document order

        if save and self.filepath and result.patched:
            self.save(backup=backup)

        return result
    # [BLOCK: PATCH] END

    # [BLOCK: PATCH_MANY] START
    def patch_many(
        self,
        updates: Dict[str, str],
        strict: bool = True,
        **kwargs,
    ) -> PatchResult:
        """Apply multiple block updates from a dict {block_name: block_text}.

        Each value must be a complete block including its START/END markers.
        The dict key is matched (case-insensitive) against the marker name
        inside the value. With strict=True (default), a mismatch raises
        ValueError before any change is applied. kwargs are forwarded to
        patch() (e.g. dry_run=True, save=False, backup=True).
        """
        mismatches = []
        for key, block_text in updates.items():
            m = MARKER_RE.search(block_text or "")
            if m is None:
                mismatches.append((key, None))
            elif m.group(1).lower() != key.lower():
                mismatches.append((key, m.group(1)))

        if mismatches:
            msg = "; ".join(
                f"key={k!r} marker={'<none>' if f is None else repr(f)}"
                for k, f in mismatches
            )
            if strict:
                raise ValueError(f"patch_many key/marker mismatch: {msg}")
            else:
                print(f"Warning: patch_many key/marker mismatch: {msg}")

        combined = "\n".join(updates.values())
        return self.patch(combined, **kwargs)
    # [BLOCK: PATCH_MANY] END

    # [BLOCK: DIFF] START
    def diff(self, patch_text: str, n: int = 2) -> str:
        """Unified diff of what patch() would produce. Does not mutate."""
        if self.content is None:
            return ""
        ghost = ScriptPatcher(content=self.content, normalize_endings=self._normalize)
        ghost.patch(patch_text, save=False)
        return "".join(
            difflib.unified_diff(
                self.content.splitlines(keepends=True),
                ghost.content.splitlines(keepends=True),
                fromfile="before",
                tofile="after",
                n=n,
            )
        )
    # [BLOCK: DIFF] END

    # [BLOCK: RENDER_HTML] START
    def render_html(self) -> None:
        """Display current content as HTML in a Jupyter/Colab cell."""
        if not _HAS_IPYTHON:
            print("IPython not available in this environment.")
            return
        if self.content:
            _ipy_display.display(_ipy_display.HTML(self.content))
    # [BLOCK: RENDER_HTML] END
# [BLOCK: SCRIPT_PATCHER_CLASS] END
