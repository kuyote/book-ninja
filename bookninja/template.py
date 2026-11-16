"""
Naming templates for book folders and part files.

Write tokens in {braces}. Anything else is copied as-is. A / or \\ in a
folder template makes subfolders, e.g.  {1}\\{2-}  ->  Author\\Title

File-name tokens (taken from the input file's name, without .m4b):
  {name}      the whole file name
  {1} {2} …   pieces of the name, split on the separator (default " - ")
  {-1}        the last piece ({-2} = second to last, …)
  {2-}        piece 2 to the end, re-joined with the separator
  {1-2}       pieces 1 to 2
  {name:0:20} characters 0–20 of the name (Python-style slice)

Tag tokens (from the audiobook's metadata, blank if missing):
  {title} {author} {artist} {album} {narrator} {year} {genre} {series}

Regex tokens: if a regex with named groups is given, e.g.
  (?P<author>.+?) - (?P<book>.+?) \\((?P<year>\\d{4})\\)
then {author}, {book}, {year} come from the file name and override tags.

Part tokens (file names only): {part} {total}, with optional format
  {part:02d}  ->  01, 02 …

Any token may have a fallback after a |, e.g. {author|Unknown Author}.
The fallback can be another token: {book|{name}}.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Optional

TOKEN = re.compile(r"\{([^{}]+)\}")
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}

DEFAULT_FOLDER_TEMPLATE = "{name}"
DEFAULT_FILE_TEMPLATE = "{title} - Part {part:02d} of {total:02d}"


class TemplateError(ValueError):
    pass


def safe_segment(name: str) -> str:
    """Make one folder/file name safe for Windows."""
    name = _INVALID.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if name.upper().split(".")[0] in _RESERVED:
        name = "_" + name
    return name[:150]


def build_context(path: str, tags: Optional[dict] = None, *, separator: str = " - ",
                  regex: str = "") -> Dict[str, object]:
    stem = os.path.splitext(os.path.basename(path))[0]
    tags = {k.lower(): v for k, v in (tags or {}).items()}
    ctx: Dict[str, object] = {
        "name": stem,
        "title": tags.get("title") or tags.get("album") or stem,
        "album": tags.get("album", ""),
        "artist": tags.get("artist") or tags.get("album_artist", ""),
        "author": tags.get("artist") or tags.get("album_artist") or tags.get("author", ""),
        "narrator": tags.get("composer") or tags.get("narrator", ""),
        "year": (tags.get("date") or tags.get("year") or "")[:4],
        "genre": tags.get("genre", ""),
        "series": tags.get("series") or tags.get("grouping") or tags.get("show", ""),
        "_pieces": [p.strip() for p in stem.split(separator)] if separator else [stem],
        "_sep": separator,
    }
    if regex:
        try:
            rx = re.compile(regex)
        except re.error as e:
            raise TemplateError(f"Invalid regex: {e}")
        for g in rx.groupindex:          # known even when this file doesn't match
            ctx.setdefault(g, "")
        m = rx.search(stem)
        if m:
            for k, v in m.groupdict().items():
                if v is not None:
                    ctx[k] = v.strip()
    return ctx


_PIECE = re.compile(r"^(-?\d+)?(-)?(-?\d+)?$")


def _resolve(expr: str, ctx: Dict[str, object]) -> str:
    key, _, fallback = expr.partition("|")
    key = key.strip()
    pieces = ctx["_pieces"]  # type: ignore[index]
    sep = ctx["_sep"]

    # numeric piece / range:  1   -1   2-   1-3
    m = _PIECE.match(key)
    if m and (m.group(1) or m.group(3)):
        a, dash, b = m.group(1), m.group(2), m.group(3)
        n = len(pieces)

        def idx(s, default):
            if s is None:
                return default
            i = int(s)
            return i - 1 if i > 0 else n + i

        if not dash:
            i = idx(a, 0)
            val = pieces[i] if 0 <= i < n else ""
        else:
            i, j = idx(a, 0), idx(b, n - 1)
            val = sep.join(pieces[max(i, 0): j + 1]) if i <= j else ""  # type: ignore[union-attr]
        return val or fallback

    # name:start:end slice, or part:02d format
    name, _, spec = key.partition(":")
    if name in ("part", "total") and name not in ctx:
        raise TemplateError(f"{{{name}}} only works in the part file name, not the folder")
    if name not in ctx or name.startswith("_"):
        raise TemplateError(f"Unknown token {{{name}}}")
    val = ctx[name]
    if spec:
        if isinstance(val, int):
            try:
                return format(val, spec)
            except ValueError:
                raise TemplateError(f"Bad format in {{{key}}}")
        parts = spec.split(":")
        try:
            sl = slice(*[int(p) if p.strip() else None for p in parts])
        except ValueError:
            raise TemplateError(f"Bad slice in {{{key}}} — use e.g. {{name:0:20}}")
        val = str(val)[sl]
    val = str(val).strip()
    return val or fallback


def render(template: str, ctx: Dict[str, object]) -> str:
    # innermost tokens first, so a fallback can itself be a token: {book|{name}}
    out = template
    for _ in range(3):
        new = TOKEN.sub(lambda m: _resolve(m.group(1), ctx), out)
        if new == out or not TOKEN.search(new):
            return new
        out = new
    return out


def render_folder(template: str, ctx: Dict[str, object]) -> str:
    """Render a folder template; / or \\ create subfolders. Returns a relative path."""
    raw = render(template, ctx)
    segs = [safe_segment(s) for s in re.split(r"[\\/]+", raw)]
    segs = [s for s in segs if s and s not in (".", "..")]
    if not segs:
        segs = [safe_segment(str(ctx["name"])) or "book"]
    return os.path.join(*segs)


def render_file(template: str, ctx: Dict[str, object], part: int, total: int) -> str:
    c = dict(ctx, part=part, total=total)
    return safe_segment(render(template, c).replace("/", "-").replace("\\", "-")) or f"Part {part:02d}"
