"""
Core engine for Book Ninja.

Reads chapters with ffprobe, plans where to cut, and runs ffmpeg to write
each part as its own .m4b (stream copy by default, so there is no quality
loss and it is fast). Each part keeps the book's tags and cover art and gets
its own chapter list, re-timed to start at 0.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .template import build_context, render_file


# --------------------------------------------------------------------------
# Locating ffmpeg / ffprobe
# --------------------------------------------------------------------------

def _app_dir() -> str:
    """Folder the app runs from (works for both .py and a PyInstaller .exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_tool(name: str) -> Optional[str]:
    """Look for ffmpeg/ffprobe next to the app first, then on PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    base = _app_dir()
    for candidate in (
        os.path.join(base, exe),
        os.path.join(base, "ffmpeg", exe),
        os.path.join(base, "ffmpeg", "bin", exe),
    ):
        if os.path.isfile(candidate):
            return candidate
    return shutil.which(name)


def _popen_kwargs() -> dict:
    """Hide the console window that would otherwise flash up on Windows."""
    if os.name == "nt":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------

@dataclass
class Chapter:
    start: float
    end: float
    title: str

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Book:
    path: str
    duration: float
    chapters: List[Chapter]
    tags: dict
    audio_codec: str
    audio_profile: str
    bitrate: int
    has_cover: bool
    has_real_chapters: bool

    @property
    def title(self) -> str:
        for k in ("title", "album"):
            if self.tags.get(k):
                return self.tags[k]
        return os.path.splitext(os.path.basename(self.path))[0]


@dataclass
class Part:
    index: int            # 1-based
    start: float
    end: float
    chapters: List[Chapter] = field(default_factory=list)
    oversized: bool = False   # one chapter longer than the limit, kept whole

    @property
    def duration(self) -> float:
        return self.end - self.start


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------

class SplitError(Exception):
    pass


def probe(path: str, ffprobe: Optional[str] = None) -> Book:
    ffprobe = ffprobe or find_tool("ffprobe")
    if not ffprobe:
        raise SplitError("ffprobe was not found. Put ffmpeg.exe and ffprobe.exe "
                         "next to the app (or in an 'ffmpeg' folder), or add them to PATH.")
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", "-show_chapters", path]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", **_popen_kwargs())
    if res.returncode != 0:
        detail = (res.stderr.strip().splitlines() or ["unknown error"])[-1]
        detail = detail.replace(path, "").strip(": ").strip()
        raise SplitError(f"Not a readable audio file ({detail})")
    data = json.loads(res.stdout or "{}")

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or 0)
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}

    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise SplitError("This file has no audio stream.")
    brand = (tags.get("major_brand") or "").strip().lower()
    tag = (audio.get("codec_tag_string") or "").lower()
    if brand in ("aax", "aaxc") or path.lower().endswith((".aax", ".aaxc")):
        raise SplitError("This is an Audible .aax file, which is copy-protected and can't be split.")
    if tag in ("drms", "drmi") or audio.get("codec_name") in (None, "none"):
        raise SplitError("This file is copy-protected (DRM) and can't be split.")
    has_cover = any(
        s.get("codec_type") == "video" and (s.get("disposition") or {}).get("attached_pic") == 1
        for s in data.get("streams", [])
    ) or any(s.get("codec_type") == "video" and s.get("codec_name") in ("mjpeg", "png")
             for s in data.get("streams", []))

    chapters: List[Chapter] = []
    for i, c in enumerate(data.get("chapters", [])):
        start = float(c.get("start_time", 0))
        end = float(c.get("end_time", start))
        title = (c.get("tags") or {}).get("title") or f"Chapter {i + 1}"
        if end > start:
            chapters.append(Chapter(start, min(end, duration or end), title))
    chapters.sort(key=lambda c: c.start)

    has_real = bool(chapters)
    if not chapters:
        chapters = [Chapter(0.0, duration, tags.get("title", "Audio"))]

    return Book(
        path=path,
        duration=duration or chapters[-1].end,
        chapters=chapters,
        tags=tags,
        audio_codec=audio.get("codec_name", "?"),
        audio_profile=audio.get("profile", ""),
        bitrate=int(audio.get("bit_rate") or fmt.get("bit_rate") or 0),
        has_cover=has_cover,
        has_real_chapters=has_real,
    )


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

def _boundaries(book: Book) -> List[float]:
    """Cut points available to us: 0, every chapter start, and the end."""
    pts = {0.0, book.duration}
    for c in book.chapters[1:]:
        if 0 < c.start < book.duration:
            pts.add(round(c.start, 3))
    return sorted(pts)


def _parts_from_cuts(book: Book, cuts: List[float], max_seconds: Optional[float]) -> List[Part]:
    parts = []
    for i in range(len(cuts) - 1):
        s, e = cuts[i], cuts[i + 1]
        chs = [c for c in book.chapters if c.end > s + 0.01 and c.start < e - 0.01]
        p = Part(i + 1, s, e, chs)
        if max_seconds and p.duration > max_seconds + 0.5:
            p.oversized = True
        parts.append(p)
    return parts


def plan_each_chapter(book: Book) -> List[Part]:
    """One output file per chapter."""
    return _parts_from_cuts(book, _boundaries(book), None)


def plan_max_duration(book: Book, max_seconds: float, *, balance: bool = False,
                      cut_long_chapters: bool = False) -> List[Part]:
    """
    Split so every part is as long as possible without going over
    `max_seconds`, cutting only at chapter starts.

    balance=False : greedy — fill each part up to the chapter boundary closest
                    to the limit without going over (what most people expect).
    balance=True  : same number of parts as greedy, but boundaries are chosen
                    so parts come out roughly equal length (avoids e.g. a
                    5h + 5h + 10min split).
    cut_long_chapters : if a single chapter is longer than the limit, cut it
                    by time into equal pieces instead of leaving it oversized.
                    (Always on for files with no chapters.)
    """
    if max_seconds <= 0:
        raise SplitError("Maximum duration must be greater than zero.")
    pts = _boundaries(book)

    if cut_long_chapters or not book.has_real_chapters:
        refined = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            if b - a > max_seconds:
                n = math.ceil((b - a) / max_seconds)
                step = (b - a) / n
                refined += [a + step * k for k in range(1, n)]
            refined.append(b)
        pts = refined

    cuts = _greedy(pts, max_seconds)
    if balance and len(cuts) > 2:
        cuts = _balanced(pts, max_seconds, len(cuts) - 1) or cuts
    return _parts_from_cuts(book, cuts, max_seconds)


def _greedy(pts: List[float], max_s: float) -> List[float]:
    cuts = [pts[0]]
    i = 0
    while i < len(pts) - 1:
        start = pts[i]
        j = i + 1
        # advance to the furthest boundary that keeps the part <= max
        while j + 1 < len(pts) and pts[j + 1] - start <= max_s + 1e-6:
            j += 1
        cuts.append(pts[j])
        i = j
    return cuts


def _balanced(pts: List[float], max_s: float, n_parts: int) -> Optional[List[float]]:
    """DP: n_parts parts, each <= max_s, minimising squared deviation from the mean."""
    m = len(pts)
    target = (pts[-1] - pts[0]) / n_parts
    INF = float("inf")
    cost = [[INF] * m for _ in range(n_parts + 1)]
    back = [[-1] * m for _ in range(n_parts + 1)]
    cost[0][0] = 0.0
    for k in range(1, n_parts + 1):
        for j in range(1, m):
            best, arg = INF, -1
            for i in range(j - 1, -1, -1):
                length = pts[j] - pts[i]
                if length > max_s + 1e-6:
                    break
                if cost[k - 1][i] < INF:
                    c = cost[k - 1][i] + (length - target) ** 2
                    if c < best:
                        best, arg = c, i
            cost[k][j], back[k][j] = best, arg
    if cost[n_parts][m - 1] == INF:
        return None
    cuts, j = [], m - 1
    for k in range(n_parts, 0, -1):
        cuts.append(pts[j])
        j = back[k][j]
    cuts.append(pts[0])
    return cuts[::-1]


# --------------------------------------------------------------------------
# Writing parts
# --------------------------------------------------------------------------

@dataclass
class SplitOptions:
    output_dir: str
    filename_pattern: str = "{title} - Part {part:02d} of {total:02d}"
    name_context: Optional[dict] = None   # from template.build_context; built from the book if None
    reencode: bool = False          # re-encode to AAC-LC (for very old iPods / HE-AAC sources)
    reencode_bitrate: str = "64k"
    reencode_mono: bool = False
    keep_cover: bool = True
    overwrite: bool = False
    resume: bool = True             # keep parts finished by an earlier, interrupted run


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str) -> str:
    name = _INVALID.sub("_", name).strip().rstrip(".")
    return name[:180] or "part"


def _ffmeta_escape(s: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", str(s))


def _write_ffmetadata(path: str, tags: dict, chapters: List[Chapter], offset: float, end: float):
    lines = [";FFMETADATA1"]
    for k, v in tags.items():
        if v is None or v == "":
            continue
        lines.append(f"{_ffmeta_escape(k)}={_ffmeta_escape(v)}")
    for c in chapters:
        s = max(c.start, offset) - offset
        e = min(c.end, end) - offset
        if e - s < 0.05:
            continue
        lines += ["", "[CHAPTER]", "TIMEBASE=1/1000",
                  f"START={int(round(s * 1000))}", f"END={int(round(e * 1000))}",
                  f"title={_ffmeta_escape(c.title)}"]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def _part_tags(book: Book, part: Part, total: int) -> dict:
    tags = dict(book.tags)
    for k in ("major_brand", "minor_version", "compatible_brands", "encoder",
              "creation_time", "duration"):
        tags.pop(k, None)
    base = book.title
    tags["title"] = f"{base} (Part {part.index} of {total})"
    tags.setdefault("album", base)
    tags["track"] = f"{part.index}/{total}"
    # Keeps parts grouped and in order on the iPod
    tags["sort_name"] = f"{base} {part.index:03d}"
    tags["media_type"] = "2"   # 2 = Audiobook in iTunes' stik atom
    return tags


ProgressCB = Callable[[int, int, float, str], None]  # part, total, fraction 0..1, message


@dataclass
class SplitResult:
    outputs: List[str]
    written: int = 0      # parts created this run
    reused: int = 0       # parts kept from an earlier, interrupted run


# ---------------------------------------------------------------- resume support
#
# Each part is written to "<name>.m4b.partial" and renamed to "<name>.m4b" only
# once ffmpeg has finished, so a finished-looking file is never half-written.
# While a book is in progress, a small hidden progress file in its folder records
# what has been completed and with which settings. Re-running the same book with
# the same settings skips every part that is already done. The progress file is
# removed once the book is complete.

PARTIAL_EXT = ".partial"


def _fingerprint(book: Book, parts: List[Part], outs: List[str], opts: "SplitOptions") -> str:
    try:
        st = os.stat(book.path)
        src = [st.st_size, int(st.st_mtime)]
    except OSError:
        src = []
    data = {
        "src": src, "dur": round(book.duration, 2),
        "cuts": [[round(p.start, 3), round(p.end, 3)] for p in parts],
        "names": [os.path.basename(o) for o in outs],
        "enc": [opts.reencode, opts.reencode_bitrate if opts.reencode else "", opts.reencode_mono if opts.reencode else False],
        "cover": opts.keep_cover and book.has_cover,
    }
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _manifest_path(book: Book, out_dir: str) -> str:
    key = hashlib.sha1(os.path.normcase(os.path.abspath(book.path)).encode("utf-8", "replace")).hexdigest()[:10]
    return os.path.join(out_dir, f".bookninja-progress-{key}.json")


def _load_manifest(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_manifest(path: str, data: dict):
    tmp = path + ".tmp"
    try:
        if os.name == "nt" and os.path.exists(path):
            _set_hidden(path, False)   # a hidden file can't be replaced on Windows
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
        _set_hidden(path, True)
    except OSError:
        pass   # progress tracking is best-effort; verification still protects us


def _set_hidden(path: str, hidden: bool):
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02 if hidden else 0x80)
        except Exception:  # noqa
            pass


def _expected_chapters(book: Book, part: Part) -> int:
    if not book.has_real_chapters:
        return 0
    n = 0
    for c in part.chapters:
        s, e = max(c.start, part.start), min(c.end, part.end)
        if e - s >= 0.05:
            n += 1
    return n


def verify_part(path: str, book: Book, part: Part, total: int, opts: "SplitOptions") -> bool:
    """Is this existing file a complete, matching copy of `part`?"""
    try:
        got = probe(path)
    except SplitError:
        return False
    if abs(got.duration - part.duration) > 1.0:
        return False
    if got.tags.get("track", "").split("/")[:2] != [str(part.index), str(total)]:
        return False
    if (len(got.chapters) if got.has_real_chapters else 0) != _expected_chapters(book, part):
        return False
    def near(a, b):   # bitrates within 25% of each other
        return not a or not b or abs(a - b) <= 0.25 * b
    if opts.reencode:
        want = int(float(opts.reencode_bitrate.lower().rstrip("k")) * 1000) if opts.reencode_bitrate else 0
        if got.audio_codec != "aac" or "HE" in (got.audio_profile or "").upper() or not near(got.bitrate, want):
            return False
    elif (got.audio_codec != book.audio_codec or got.audio_profile != book.audio_profile
          or not near(got.bitrate, book.bitrate)):
        return False
    if opts.keep_cover and book.has_cover and not got.has_cover:
        return False
    return True


def split(book: Book, parts: List[Part], opts: SplitOptions,
          progress: Optional[ProgressCB] = None,
          cancel: Optional[threading.Event] = None,
          ffmpeg: Optional[str] = None) -> SplitResult:
    ffmpeg = ffmpeg or find_tool("ffmpeg")
    if not ffmpeg:
        raise SplitError("ffmpeg was not found. Put ffmpeg.exe next to the app "
                         "(or in an 'ffmpeg' folder), or add it to PATH.")
    total = len(parts)
    ctx = opts.name_context or build_context(book.path, book.tags)
    names = [render_file(opts.filename_pattern, ctx, p.index, total) for p in parts]
    if len(set(n.lower() for n in names)) != len(names):
        raise SplitError("The file name template gives several parts the same name. "
                         "Include {part} in it.")
    os.makedirs(opts.output_dir, exist_ok=True)
    outs = [os.path.join(opts.output_dir, n + ".m4b") for n in names]

    # Leftovers from a crash or power cut are never trusted.
    for o in outs:
        try:
            os.remove(o + PARTIAL_EXT)
        except OSError:
            pass

    mpath = _manifest_path(book, opts.output_dir)
    fp = _fingerprint(book, parts, outs, opts)
    manifest = _load_manifest(mpath) if opts.resume else {}
    done_before = manifest.get("done", {}) if manifest.get("fingerprint") == fp else {}

    keep = set()
    conflicts = []
    for part, out in zip(parts, outs):
        if not os.path.exists(out):
            continue
        if opts.resume:
            rec = done_before.get(str(part.index))
            if rec and rec.get("size") == os.path.getsize(out):
                keep.add(part.index)
                continue
            if verify_part(out, book, part, total, opts):
                keep.add(part.index)
                continue
        if not opts.overwrite:
            conflicts.append(out)
    if conflicts:
        hint = ("They don't match the current settings, so they can't be reused. "
                if opts.resume else "")
        raise SplitError(f"Output already exists:\n{conflicts[0]}"
                         + (f"\n(and {len(conflicts) - 1} more)" if len(conflicts) > 1 else "")
                         + f"\n\n{hint}Tick 'Overwrite' or pick another folder.")

    if keep == {p.index for p in parts}:
        for part in parts:
            if progress:
                progress(part.index, total, 1.0, f"Part {part.index} of {total} already done")
        try:
            os.remove(mpath)
        except OSError:
            pass
        return SplitResult(outs, written=0, reused=len(keep))

    manifest = {"app": "Book Ninja", "source": book.path, "fingerprint": fp,
                "done": {str(i): {"file": os.path.basename(outs[i - 1]),
                                  "size": os.path.getsize(outs[i - 1])} for i in sorted(keep)}}
    _save_manifest(mpath, manifest)
    written = 0

    with tempfile.TemporaryDirectory(prefix="bookninja_") as tmp:
        cover = None
        if opts.keep_cover and book.has_cover:
            cover = os.path.join(tmp, "cover.jpg")
            r = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", book.path, "-map", "0:v:0",
                                "-frames:v", "1", "-c:v", "mjpeg", "-q:v", "2", cover],
                               capture_output=True, **_popen_kwargs())
            if r.returncode != 0 or not os.path.isfile(cover):
                cover = None

        for part in parts:
            if cancel and cancel.is_set():
                raise SplitError("Cancelled.")
            out = outs[part.index - 1]
            if part.index in keep:
                if progress:
                    progress(part.index, total, 1.0, f"Part {part.index} of {total} already done")
                continue
            partial = out + PARTIAL_EXT

            meta = os.path.join(tmp, f"meta_{part.index}.txt")
            _write_ffmetadata(meta, _part_tags(book, part, total),
                             part.chapters if book.has_real_chapters else [], part.start, part.end)

            cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y",
                   "-ss", f"{part.start:.3f}", "-t", f"{part.duration:.3f}", "-i", book.path,
                   "-i", meta]
            if cover:
                cmd += ["-i", cover]
            cmd += ["-map", "0:a:0"]
            if cover:
                cmd += ["-map", "2:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
            cmd += ["-map_metadata", "1", "-map_chapters", "1"]
            if opts.reencode:
                cmd += ["-c:a", "aac", "-profile:a", "aac_low", "-b:a", opts.reencode_bitrate]
                if opts.reencode_mono:
                    cmd += ["-ac", "1"]
            else:
                cmd += ["-c:a", "copy"]
            cmd += ["-movflags", "+faststart", "-f", "ipod", "-brand", "M4B ",
                    "-progress", "pipe:1", "-stats_period", "0.5", partial]

            if progress:
                progress(part.index, total, 0.0, f"Writing part {part.index} of {total}…")
            try:
                _run_with_progress(cmd, part.duration, cancel,
                                   lambda f: progress and progress(part.index, total, f,
                                                                   f"Writing part {part.index} of {total}…"))
                os.replace(partial, out)          # only now does the real name appear
            except BaseException:
                try:  # don't leave a half-written file behind
                    os.remove(partial)
                except OSError:
                    pass
                raise
            written += 1
            manifest["done"][str(part.index)] = {"file": os.path.basename(out), "size": os.path.getsize(out)}
            _save_manifest(mpath, manifest)
            if progress:
                progress(part.index, total, 1.0, f"Finished part {part.index} of {total}")

    try:  # book complete — the progress file is no longer needed
        _set_hidden(mpath, False)
        os.remove(mpath)
    except OSError:
        pass
    return SplitResult(outs, written=written, reused=len(keep))


def _run_with_progress(cmd, duration, cancel, on_fraction):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", **_popen_kwargs())
    err_lines: List[str] = []

    def drain_err():
        for line in proc.stderr:
            err_lines.append(line)
            if len(err_lines) > 200:
                del err_lines[:100]

    t = threading.Thread(target=drain_err, daemon=True)
    t.start()
    for line in proc.stdout:
        if cancel and cancel.is_set():
            proc.kill()
            proc.wait()
            raise SplitError("Cancelled.")
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
                if duration > 0:
                    on_fraction(min(1.0, us / 1e6 / duration))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=2)
    if proc.returncode != 0:
        raise SplitError("ffmpeg failed:\n" + "".join(err_lines[-15:]).strip())


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def fmt_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def parse_duration(text: str) -> float:
    """Accepts '5', '5h', '4.5', '4:30', '4h30m', '4:30:00', '270m'."""
    t = text.strip().lower().replace(" ", "")
    if not t:
        raise ValueError("empty")
    if ":" in t:
        nums = [float(x) for x in t.split(":")]
        while len(nums) < 3:
            nums.append(0.0)
        h, m, s = nums[:3]
        return h * 3600 + m * 60 + s
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?", t)
    if m and any(m.groups()):
        h, mi, s = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mi * 60 + s
    return float(t) * 3600   # bare number = hours
