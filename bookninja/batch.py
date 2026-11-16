"""
Batch processing: split several books one after another, each into its own
folder under a base folder, named from a template.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .core import Book, Part, SplitError, SplitOptions, plan_each_chapter, plan_max_duration, probe, split
from .template import (DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE, TemplateError, build_context,
                       render_file, render_folder)


@dataclass
class PlanSettings:
    mode: str = "duration"          # "duration" | "chapter"
    max_seconds: float = 5 * 3600
    balance: bool = False
    cut_long_chapters: bool = False
    skip_if_fits: bool = False      # leave books that are already short enough alone


@dataclass
class NamingSettings:
    base_dir: str = ""
    folder_template: str = DEFAULT_FOLDER_TEMPLATE
    file_template: str = DEFAULT_FILE_TEMPLATE
    separator: str = " - "
    regex: str = ""


@dataclass
class OutputSettings:
    reencode: bool = False
    reencode_bitrate: str = "64k"
    reencode_mono: bool = False
    keep_cover: bool = True
    overwrite: bool = False
    resume: bool = True


@dataclass
class BookJob:
    path: str
    book: Optional[Book] = None
    status: str = "Pending"         # Pending | Splitting | Done | Skipped | Error | Cancelled
    message: str = ""
    outputs: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def make_plan(book: Book, ps: PlanSettings) -> List[Part]:
    if ps.mode == "chapter":
        return plan_each_chapter(book)
    return plan_max_duration(book, ps.max_seconds, balance=ps.balance,
                             cut_long_chapters=ps.cut_long_chapters)


def fits_already(book: Book, ps: PlanSettings) -> bool:
    return ps.mode == "duration" and book.duration <= ps.max_seconds + 0.5


def naming_context(book: Book, ns: NamingSettings) -> dict:
    return build_context(book.path, book.tags, separator=ns.separator, regex=ns.regex)


def folder_for(book: Book, ns: NamingSettings) -> str:
    return os.path.join(ns.base_dir, render_folder(ns.folder_template, naming_context(book, ns)))


def preview_names(book: Book, ns: NamingSettings, n_parts: int) -> tuple:
    """(folder path, first part file name) — raises TemplateError on bad templates."""
    ctx = naming_context(book, ns)
    folder = os.path.join(ns.base_dir, render_folder(ns.folder_template, ctx))
    return folder, render_file(ns.file_template, ctx, 1, max(n_parts, 1)) + ".m4b"


# progress(book_index, n_books, overall_fraction, message)
BatchProgress = Callable[[int, int, float, str], None]
# book_progress(fraction_of_current_book, message)
BookProgress = Callable[[float, str], None]
# on_status(job_index, job)
StatusCB = Callable[[int, BookJob], None]


def run_batch(jobs: List[BookJob], ps: PlanSettings, ns: NamingSettings, os_: OutputSettings,
              progress: Optional[BatchProgress] = None, on_status: Optional[StatusCB] = None,
              cancel: Optional[threading.Event] = None,
              book_progress: Optional[BookProgress] = None) -> List[BookJob]:
    """Split each job in turn. A failure on one book is recorded and the batch moves on."""
    if not ns.base_dir:
        raise SplitError("Choose a base output folder.")
    best = [0.0]   # keep the overall bar from stepping backwards
    todo = [i for i, j in enumerate(jobs) if j.status in ("Pending", "Error", "Cancelled")]
    n = len(todo)

    for k, i in enumerate(todo):
        job = jobs[i]
        if cancel and cancel.is_set():
            break

        book_best = [0.0]   # keep the current-book bar from stepping backwards
        if book_progress:
            book_progress(0.0, f"{job.name} — starting")
        if progress:
            progress(k + 1, n, best[0], f"Book {k + 1} of {n}")

        def status(s, msg=""):
            job.status, job.message = s, msg
            if on_status:
                on_status(i, job)

        try:
            status("Splitting")
            if job.book is None:
                job.book = probe(job.path)
            book = job.book
            if ps.skip_if_fits and fits_already(book, ps):
                status("Skipped", "Already within the limit")
                if book_progress:
                    book_progress(1.0, f"{job.name} — skipped (already within the limit)")
                continue
            parts = make_plan(book, ps)
            ctx = naming_context(book, ns)
            out_dir = os.path.join(ns.base_dir, render_folder(ns.folder_template, ctx))
            opts = SplitOptions(output_dir=out_dir, filename_pattern=ns.file_template, name_context=ctx,
                                reencode=os_.reencode, reencode_bitrate=os_.reencode_bitrate,
                                reencode_mono=os_.reencode_mono, keep_cover=os_.keep_cover,
                                overwrite=os_.overwrite, resume=os_.resume)

            def prog(pi, pn, f, msg, k=k):
                if book_progress:
                    bf = max(book_best[0], (pi - 1 + f) / pn)
                    book_best[0] = bf
                    book_progress(bf, f"{job.name} — part {pi} of {pn}")
                if progress:
                    overall = max(best[0], (k + (pi - 1 + f) / pn) / n)
                    best[0] = overall
                    progress(k + 1, n, overall, f"Book {k + 1} of {n}: {job.name} — part {pi} of {pn}")

            res = split(book, parts, opts, progress=prog, cancel=cancel)
            job.outputs = res.outputs
            note = ""
            if res.reused and res.written:
                note = f" (resumed: {res.reused} already done, {res.written} new)"
            elif res.reused:
                note = " (all parts were already done)"
            status("Done", f"{len(parts)} parts{note} → {out_dir}")
            if book_progress:
                book_progress(1.0, f"{job.name} — done ({len(parts)} parts{note})")
        except (SplitError, TemplateError) as e:
            if cancel and cancel.is_set():
                status("Cancelled")
                break
            status("Error", str(e).splitlines()[0] if str(e) else "Error")
            job.message = str(e)
        except Exception as e:  # keep going with the next book
            status("Error", f"Unexpected error: {e}")
    if progress and not (cancel and cancel.is_set()):
        progress(n, n, 1.0, "Batch finished")
    return jobs
