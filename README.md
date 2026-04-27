# ScriptPatcher

A lightweight, language-agnostic Python utility for safe, modular code updates between Large Language Models and local files. Patch named blocks of a source file without making the LLM rewrite the whole thing.

---

## The Problem

When working with an LLM on a large source file (a 200-line HTML/JS game, a multi-component Python script), asking it to change a single function traditionally forces it to regenerate the entire file. That costs tokens, costs time, and — most importantly — invites hallucination as the model tries to reproduce code it isn't actively reasoning about.

Conventional diff-based patching (GNU `patch`, unified diffs) doesn't solve the problem either. LLMs frequently drift on whitespace, indentation, and line endings. A single misplaced space breaks the patch.

## The Approach

ScriptPatcher uses a **Component String Architecture**: explicit, human-readable boundary markers placed inside ordinary comments.

```javascript
// [BLOCK: PHYSICS] START
function step(obj, dt) {
  obj.vy += CONFIG.gravity * dt;
  obj.y  += obj.vy * dt;
}
// [BLOCK: PHYSICS] END
```

A regex search locates marker pairs by name and splices replacement content into the exact byte range between them. Because the matcher only cares about the marker substrings, it is immune to:

- **Language syntax** — `//`, `#`, `/* */`, `<!-- -->`, anything that hosts a comment.
- **Whitespace and line endings** — CRLF, LF, lone CR, indentation, surrounding characters.
- **Marker style drift** — case, internal spacing, hyphens or dots in names.

The LLM only ever needs to output one block at a time, with its markers intact. ScriptPatcher does the rest.

## Install

Single file, standard library only (IPython optional for `render_html`). Drop `script_patcher.py` into your project:

```bash
curl -O https://raw.githubusercontent.com/<you>/<repo>/main/script_patcher.py
```

## Quick Start

```python
from script_patcher import ScriptPatcher

p = ScriptPatcher(filepath="game.html")
print(p.validate())              # check baseline integrity
print(p.list_blocks())           # ['CONFIG', 'PHYSICS', 'RENDER']

snippet = p.extract("PHYSICS")   # paste this into your LLM prompt as context

# ... LLM returns a string containing the modified block, markers and all ...

result = p.patch(llm_output)     # writes to disk if filepath was set
assert result.ok
```

## Marker Grammar

```
[BLOCK: NAME] START
...content...
[BLOCK: NAME] END
```

| Tolerated                | Example                         |
| ------------------------ | ------------------------------- |
| Any case                 | `[block: foo] start`            |
| Internal whitespace      | `[ BLOCK : FOO ] START`         |
| Hyphens and dots in name | `[BLOCK: my-feature.v2] START`  |
| Any comment style        | `# [BLOCK: X] START`, `/* ... */`, `<!-- ... -->` |
| Mixed line endings       | CRLF / LF / lone CR all collapsed to LF on load |

Names are alphanumeric plus `_`, `-`, `.`. Matching is case-insensitive; the casing in the START marker is treated as canonical.

## API

### `ScriptPatcher(content=None, filepath=None, normalize_endings=True)`
Construct from an in-memory string, a file path, or both. With a filepath, content is loaded automatically and `patch()` writes back to disk by default.

### `list_blocks() -> list[str]`
Sorted names of well-formed (paired) blocks in the current content.

### `validate() -> ValidationResult`
Inspect marker integrity without modifying anything. Returns a structured report with:
- `blocks` — well-formed pairs found
- `orphan_starts` — STARTs without matching END
- `orphan_ends` — ENDs without matching START
- `duplicates` — block names appearing more than once
- `ok` — `True` iff none of the error lists are populated

Run this once after annotating a new baseline file.

### `extract(block_name) -> str | None`
Returns the requested block including its marker lines, or `None` if absent. Ideal for pulling a single function's context into a prompt.

### `patch(patch_text, dry_run=False, save=True, backup=False) -> PatchResult`
The core mutation. Scans `patch_text` for every well-formed block and splices each one into the matching block in `self.content`. Multiple blocks in a single `patch_text` are handled in one call.

Returns a `PatchResult` with:
- `patched` — block names successfully replaced (in document order)
- `not_found` — blocks present in patch but absent from target
- `malformed_in_patch` — orphan STARTs/ENDs in the patch text
- `duplicates_in_patch` — repeated block names in the patch text
- `ok` — `True` iff at least one block was patched and no errors occurred

`dry_run=True` reports what would happen without mutating. `backup=True` writes a `.bak` sibling before saving.

### `patch_many(updates: dict[str, str], strict=True, **kwargs) -> PatchResult`
Apply multiple block updates from a dict. Keys are block names; values are full block strings (with markers). The marker inside each value remains the source of truth — the key is a label that lets the patcher catch typos.

```python
p.patch_many({
    "CONFIG":  "// [BLOCK: CONFIG] START\n...\n// [BLOCK: CONFIG] END",
    "RENDER":  "// [BLOCK: RENDER] START\n...\n// [BLOCK: RENDER] END",
}, backup=True)
```

With `strict=True` (default), a key whose marker name disagrees raises `ValueError` *before* any change reaches disk. This is the antidote to the "lots of changes accumulated across a long conversation" problem: each entry self-documents, and typos can't slip through silently.

### `diff(patch_text, n=2) -> str`
Unified diff of what `patch()` would produce. Pure preview; does not mutate.

### `render_html()`
Display current content as rendered HTML in a Jupyter or Colab cell. Requires IPython.

## Workflow Walkthrough

The repo includes `demo.py`, a runnable end-to-end demonstration. The nine sections correspond to the nine things you'll actually do with the tool, roughly in order:

1. **Validate** — confirm marker integrity after annotating a file.
2. **List** — see what's available to patch.
3. **Extract** — pull a block to send to the LLM as context.
4. **Single-block patch** — apply one LLM response, with CRLF / case drift tolerated.
5. **Dry run** — see what `patch()` would do without mutating.
6. **Batch patch** via `patch_many()` — dictionary-style updates with named entries.
7. **Strict mismatch check** — typos in dict keys raise before any write.
8. **Error reporting** — malformed or missing blocks return structured results, never crash.
9. **Final state** — inspect the merged file.

A representative excerpt from the demo output:

```
============================================================
6. BATCH PATCH via patch_many()
============================================================
PatchResult(ok=True):
  patched: ['CONFIG', 'RENDER']

============================================================
7. STRICT MISMATCH CHECK
============================================================
Caught: patch_many key/marker mismatch: key='PHISICS' marker='PHYSICS'

============================================================
8. ERROR REPORTING
============================================================
PatchResult(ok=False):
  not found in target: ['GHOST']
  malformed in patch: ['ORPHAN']
result.ok = False
```

Run the full demo with `python demo.py`.

## Error Handling Philosophy

`patch()` and `patch_many()` never raise on bad LLM input — they return a structured `PatchResult` that you can branch on. The exception is `patch_many(strict=True)`, which raises `ValueError` on dict-key/marker mismatches because those are author errors, not LLM errors, and silent acceptance would be worse than failing loudly.

A defensive one-liner for production use:

```python
result = p.patch_many(updates, backup=True)
assert result.ok, result
```

## Standard Workflow

1. **Annotate baseline.** Wrap the regions you want to be able to patch in block markers placed inside language-appropriate comments.
2. **Validate.** Run `validate()` once to confirm every START has its END and no name is duplicated.
3. **Request update.** Send the LLM the relevant `extract()` output as context and ask it to modify the block. Instruct it to return *only* the modified block, including markers.
4. **Patch.** Pass the LLM's response into `patch()`, or collect several responses across a conversation and apply them together with `patch_many()`.
5. **Verify.** Check `result.ok` or inspect the structured result for what changed.
