"""
Command-line interface.

Examples
  bookninja book.m4b --max 5h
  bookninja *.m4b --max 5h --base D:\\Audiobooks\\iPod --folder "{1}\\{2-}"
  bookninja "C:\\Books" --max 4:30 --balance          (every .m4b in a folder)
  bookninja book.m4b --each-chapter
  bookninja *.m4b --max 5h --dry-run
"""
import argparse
import glob
import os
import sys
import threading

from .batch import (BookJob, NamingSettings, OutputSettings, PlanSettings, fits_already, make_plan,
                    preview_names, run_batch)
from .core import SplitError, fmt_time, parse_duration, probe
from .template import DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE, TemplateError


def _expand(inputs):
    out = []
    for item in inputs:
        if os.path.isdir(item):
            for d, _, files in os.walk(item):
                out += [os.path.join(d, f) for f in sorted(files) if f.lower().endswith((".m4b", ".m4a"))]
        elif any(c in item for c in "*?["):        # Windows shells don't expand wildcards
            out += sorted(glob.glob(item))
        else:
            out.append(item)
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # Windows consoles may not be UTF-8
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="bookninja",
                                 description="Split .m4b audiobooks into smaller .m4b files at chapter boundaries. "
                                             "Several books are processed one at a time, each into its own folder.")
    ap.add_argument("inputs", nargs="+", help=".m4b files, wildcards, or folders to scan")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--max", metavar="DURATION",
                      help="longest a part may be, e.g. 5h, 4:30, 270m. Splits at the chapter closest to this without going over.")
    mode.add_argument("--each-chapter", action="store_true", help="write one file per chapter")
    ap.add_argument("--balance", action="store_true",
                    help="with --max: keep the same number of parts but make them roughly equal length")
    ap.add_argument("--cut-long-chapters", action="store_true",
                    help="with --max: if one chapter is longer than the limit, cut it by time")
    ap.add_argument("--skip-fits", action="store_true", help="skip books already within the --max limit")
    ap.add_argument("--base", "--out", dest="base",
                    help="base output folder; each book gets a subfolder (default: 'Split' next to the first input)")
    ap.add_argument("--folder", default=DEFAULT_FOLDER_TEMPLATE,
                    help="book folder template, e.g. \"{1}\\{2-}\" (default {name}). See README for tokens.")
    ap.add_argument("--pattern", default=DEFAULT_FILE_TEMPLATE, help=f"part file name template (default \"{DEFAULT_FILE_TEMPLATE}\")")
    ap.add_argument("--separator", default=" - ", help="what splits the file name into {1} {2} … (default ' - ')")
    ap.add_argument("--regex", default="", help="regex with named groups, e.g. \"(?P<author>.+?) - (?P<book>.+)\"")
    ap.add_argument("--reencode", action="store_true", help="re-encode to AAC-LC instead of copying the audio")
    ap.add_argument("--bitrate", default="64k", help="bitrate when re-encoding (default 64k)")
    ap.add_argument("--mono", action="store_true", help="downmix to mono when re-encoding")
    ap.add_argument("--no-cover", action="store_true", help="don't copy the cover art")
    ap.add_argument("--overwrite", action="store_true", help="replace existing output files")
    ap.add_argument("--no-resume", action="store_true",
                    help="don't reuse parts finished by an earlier, interrupted run (use with --overwrite to redo)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan without writing anything")
    a = ap.parse_args(argv)

    try:
        paths = _expand(a.inputs)
        if not paths:
            raise SplitError("No input files found.")
        ps = PlanSettings(mode="chapter" if a.each_chapter else "duration",
                          max_seconds=parse_duration(a.max) if a.max else 0,
                          balance=a.balance, cut_long_chapters=a.cut_long_chapters, skip_if_fits=a.skip_fits)
        base = a.base or os.path.join(os.path.dirname(os.path.abspath(paths[0])), "Split")
        ns = NamingSettings(base_dir=base, folder_template=a.folder, file_template=a.pattern,
                            separator=a.separator, regex=a.regex)
        os_ = OutputSettings(reencode=a.reencode, reencode_bitrate=a.bitrate, reencode_mono=a.mono,
                             keep_cover=not a.no_cover, overwrite=a.overwrite,
                             resume=not a.no_resume)

        jobs = []
        for n, p in enumerate(paths, 1):
            job = BookJob(p)
            jobs.append(job)
            try:
                job.book = b = probe(p)
            except SplitError as e:
                job.status, job.message = "Error", str(e)
                print(f"[{n}/{len(paths)}] {job.name}\n    ERROR: {e}")
                continue
            print(f"[{n}/{len(paths)}] {job.name}  —  {fmt_time(b.duration)}, "
                  f"{len(b.chapters) if b.has_real_chapters else 0} chapters, "
                  f"{b.audio_codec} {b.audio_profile} {b.bitrate // 1000} kbps")
            if ps.skip_if_fits and fits_already(b, ps):
                print("    skip: already within the limit")
                continue
            parts = make_plan(b, ps)
            folder, first = preview_names(b, ns, len(parts))
            print(f"    -> {os.path.join(folder, first)}")
            for pt in parts:
                first_ch = pt.chapters[0].title if pt.chapters else ""
                last_ch = pt.chapters[-1].title if pt.chapters else ""
                span = first_ch if first_ch == last_ch else f"{first_ch}  to  {last_ch}"
                flag = "  [!] longer than limit (single long chapter)" if pt.oversized else ""
                print(f"    Part {pt.index:>2}: {fmt_time(pt.start)} – {fmt_time(pt.end)}  "
                      f"({fmt_time(pt.duration)})  {span}{flag}")
        if a.dry_run:
            return 0

        print()
        last = [""]

        def prog(k, n, f, msg):
            line = f"\r{msg}  {int(f * 100):3d}%"
            sys.stdout.write(line + " " * max(0, len(last[0]) - len(line)))
            last[0] = line
            sys.stdout.flush()

        def status(i, job):
            if job.status in ("Done", "Skipped", "Error", "Cancelled"):
                sys.stdout.write("\r" + " " * len(last[0]) + "\r")
                print(f"{job.status:>9}: {job.name}" + (f"  — {job.message.splitlines()[0]}" if job.message else ""))

        cancel = threading.Event()
        try:
            run_batch(jobs, ps, ns, os_, progress=prog, on_status=status, cancel=cancel)
        except KeyboardInterrupt:
            cancel.set()
            raise
        done = sum(j.status == "Done" for j in jobs)
        failed = sum(j.status == "Error" for j in jobs)
        sys.stdout.write("\r" + " " * len(last[0]) + "\r")
        print(f"\nFinished: {done} split, {sum(j.status == 'Skipped' for j in jobs)} skipped, "
              f"{failed} failed.  Output: {base}")
        return 1 if failed else 0
    except (SplitError, TemplateError, ValueError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
