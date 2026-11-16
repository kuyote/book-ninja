"""
Windows desktop GUI (tkinter — ships with Python, no extra installs).

Add one or more books, pick a base folder, and each book is split in turn
into its own subfolder named from the folder template.
Tip: dragging .m4b files onto Book Ninja.exe (or run.bat) adds them.
"""
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .batch import (BookJob, NamingSettings, OutputSettings, PlanSettings, fits_already, make_plan,
                    naming_context, preview_names, run_batch)
from .core import SplitError, find_tool, fmt_time, probe
from .template import DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE, TemplateError
from .help import HelpWindow, build_menu

APP_NAME = "Book Ninja"
AUDIO_EXT = (".m4b", ".m4a")


def _settings_path(app_name=APP_NAME):
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, app_name, "settings.json")


class App(tk.Tk):
    def __init__(self, initial_files=()):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1040x860")
        self.minsize(860, 700)
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        self.jobs = []                 # list[BookJob]
        self.worker = None
        self.cancel_event = threading.Event()
        self.q = queue.Queue()

        # split settings
        self.mode = tk.StringVar(value="duration")
        self.hours = tk.StringVar(value="5")
        self.minutes = tk.StringVar(value="0")
        self.balance = tk.BooleanVar(value=False)
        self.cut_long = tk.BooleanVar(value=False)
        self.skip_fits = tk.BooleanVar(value=False)
        # naming
        self.base_dir = tk.StringVar()
        self.folder_tpl = tk.StringVar(value=DEFAULT_FOLDER_TEMPLATE)
        self.file_tpl = tk.StringVar(value=DEFAULT_FILE_TEMPLATE)
        self.separator = tk.StringVar(value=" - ")
        self.regex = tk.StringVar(value="")
        # output
        self.reencode = tk.BooleanVar(value=False)
        self.bitrate = tk.StringVar(value="64k")
        self.mono = tk.BooleanVar(value=False)
        self.keep_cover = tk.BooleanVar(value=True)
        self.overwrite = tk.BooleanVar(value=False)
        self.resume = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Ready")

        self._load_settings()
        self._build()
        build_menu(self, os.path.dirname(_settings_path()))
        for v in (self.mode, self.hours, self.minutes, self.balance, self.cut_long, self.skip_fits,
                  self.base_dir, self.folder_tpl, self.file_tpl, self.separator, self.regex):
            v.trace_add("write", lambda *_: self.refresh())
        self._toggle_reencode()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        self.after(200, self._check_ffmpeg)
        if initial_files:
            self.after(300, lambda: self.add_paths(initial_files))
        else:
            self.after(600, self._offer_restore)

    # ------------------------------------------------------------ settings
    _PERSIST = ("mode", "hours", "minutes", "balance", "cut_long", "skip_fits", "base_dir", "folder_tpl",
                "file_tpl", "separator", "regex", "reencode", "bitrate", "mono", "keep_cover", "overwrite", "resume")

    def _load_settings(self):
        path = _settings_path()
        if not os.path.exists(path):              # carry over settings from the old "M4B Splitter" name
            old = _settings_path("M4B Splitter")
            if os.path.exists(old):
                path = old
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for k in self._PERSIST:
                if k in data:
                    getattr(self, k).set(data[k])
        except (OSError, ValueError, tk.TclError):
            pass

    def _save_settings(self):
        try:
            os.makedirs(os.path.dirname(_settings_path()), exist_ok=True)
            with open(_settings_path(), "w", encoding="utf-8") as f:
                json.dump({k: getattr(self, k).get() for k in self._PERSIST}, f, indent=2)
        except OSError:
            pass

    def _on_close(self):
        if self.worker and not messagebox.askyesno(APP_NAME, "A split is running. Stop it and quit?"):
            return
        self.cancel_event.set()
        if self.worker:
            self.status.set("Stopping…")
            self.update_idletasks()
            self.worker.join(timeout=5)   # let ffmpeg stop and the unfinished part be cleaned up
        self._save_settings()
        self.destroy()

    # ------------------------------------------------------------ batch recovery
    def _session_path(self):
        return os.path.join(os.path.dirname(_settings_path()), "session.json")

    def _save_session(self, running):
        try:
            os.makedirs(os.path.dirname(self._session_path()), exist_ok=True)
            with open(self._session_path(), "w", encoding="utf-8") as f:
                json.dump({"running": running,
                           "jobs": [{"path": j.path, "status": j.status} for j in self.jobs]}, f, indent=1)
        except OSError:
            pass

    def _clear_session(self):
        try:
            os.remove(self._session_path())
        except OSError:
            pass

    def _offer_restore(self):
        try:
            with open(self._session_path(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not data.get("running"):
            return
        left = [j["path"] for j in data.get("jobs", [])
                if j.get("status") not in ("Done", "Skipped") and os.path.isfile(j.get("path", ""))]
        if not left:
            self._clear_session()
            return
        if messagebox.askyesno(APP_NAME,
                               "The last batch didn't finish. Book Ninja may have been closed or the "
                               f"computer may have shut down.\n\n{len(left)} book(s) weren't completed. "
                               "Add them back to the list?\n\nWhen you press Split all, parts that were "
                               "already finished are kept and only the rest are made (as long as "
                               "'Resume unfinished books' is ticked)."):
            self.add_paths(left)
        else:
            self._clear_session()

    # ------------------------------------------------------------ layout
    def _build(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # 1. Books ------------------------------------------------------
        bf = ttk.LabelFrame(root, text="1. Books (split one at a time, top to bottom)", padding=8)
        bf.pack(fill="both", expand=True)
        btns = ttk.Frame(bf)
        btns.pack(fill="x", pady=(0, 6))
        self.add_btn = ttk.Button(btns, text="Add files…", command=self.browse_files)
        self.add_btn.pack(side="left")
        self.addf_btn = ttk.Button(btns, text="Add folder…", command=self.browse_folder)
        self.addf_btn.pack(side="left", padx=4)
        self.edit_btns = [
            ttk.Button(btns, text="Remove", command=self.remove_selected),
            ttk.Button(btns, text="Clear", command=self.clear_all),
            ttk.Button(btns, text="Move up", command=lambda: self.move(-1)),
            ttk.Button(btns, text="Move down", command=lambda: self.move(1)),
        ]
        for b in self.edit_btns:
            b.pack(side="left", padx=(4, 0))
        self.count_lbl = ttk.Label(btns, text="", foreground="#555")
        self.count_lbl.pack(side="right")

        cols = ("file", "length", "chapters", "parts", "folder", "status")
        wrap = ttk.Frame(bf)
        wrap.pack(fill="both", expand=True)
        self.books = ttk.Treeview(wrap, columns=cols, show="headings", height=7, selectmode="extended")
        for c, w, t, st in zip(cols, (260, 70, 70, 50, 330, 150),
                               ("File", "Length", "Chapters", "Parts", "Output folder", "Status"),
                               (True, False, False, False, True, False)):
            self.books.heading(c, text=t)
            self.books.column(c, width=w, stretch=st,
                              anchor="center" if c in ("length", "chapters", "parts") else "w")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.books.yview)
        self.books.configure(yscrollcommand=sb.set)
        self.books.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        for tag, color in (("done", "#15803d"), ("error", "#b91c1c"), ("busy", "#1d4ed8"),
                           ("skip", "#6b7280"), ("warn", "#b45309")):
            self.books.tag_configure(tag, foreground=color)
        self.books.bind("<<TreeviewSelect>>", lambda e: self.refresh_selected())
        self.books.bind("<Double-1>", self._show_job_message)
        self.books.bind("<Delete>", lambda e: self.remove_selected())

        # 2. How to split ----------------------------------------------
        o = ttk.LabelFrame(root, text="2. How to split (applies to every book)", padding=8)
        o.pack(fill="x", pady=(6, 0))
        r1 = ttk.Frame(o)
        r1.pack(fill="x")
        ttk.Radiobutton(r1, text="Parts no longer than", variable=self.mode, value="duration").pack(side="left")
        ttk.Spinbox(r1, from_=0, to=99, width=4, textvariable=self.hours).pack(side="left", padx=(6, 2))
        ttk.Label(r1, text="hours").pack(side="left")
        ttk.Spinbox(r1, from_=0, to=59, increment=5, width=4, textvariable=self.minutes).pack(side="left", padx=(8, 2))
        ttk.Label(r1, text="minutes   (cuts at the chapter closest to this without going over)").pack(side="left")
        ttk.Radiobutton(r1, text="One file per chapter", variable=self.mode, value="chapter").pack(side="right")
        r2 = ttk.Frame(o)
        r2.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(r2, text="Even out part lengths", variable=self.balance).pack(side="left", padx=(22, 0))
        ttk.Checkbutton(r2, text="Cut chapters longer than the limit", variable=self.cut_long).pack(side="left", padx=(14, 0))
        ttk.Checkbutton(r2, text="Skip books already within the limit", variable=self.skip_fits).pack(side="left", padx=(14, 0))

        # 3. Output & naming -------------------------------------------
        n = ttk.LabelFrame(root, text="3. Output folders and names", padding=8)
        n.pack(fill="x", pady=(6, 0))
        n.columnconfigure(1, weight=1)
        ttk.Label(n, text="Base folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(n, textvariable=self.base_dir).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(n, text="Choose…", command=self.choose_base).grid(row=0, column=2, sticky="ew")

        ttk.Label(n, text="Book folder:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(n, textvariable=self.folder_tpl).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(n, text="Tokens…", command=self.show_tokens).grid(row=1, column=2, sticky="ew", pady=(6, 0))

        ttk.Label(n, text="Part file name:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(n, textvariable=self.file_tpl).grid(row=2, column=1, sticky="ew", padx=6, pady=(4, 0))
        presets = ttk.Menubutton(n, text="Presets")
        pm = tk.Menu(presets, tearoff=False)
        for label, fol, fil in (
            ("Folder = file name", "{name}", DEFAULT_FILE_TEMPLATE),
            ("Author \\ Title  (from 'Author - Title.m4b')", "{1}\\{2-}", "{2-} - Part {part:02d}"),
            ("Title only  (from 'Author - Title.m4b')", "{2-}", "{2-} - Part {part:02d}"),
            ("Author \\ Title  (from tags)", "{author|Unknown Author}\\{title}", "{title} - Part {part:02d}"),
            ("Series \\ Title  (from tags)", "{series|No Series}\\{title}", "{title} - Part {part:02d}"),
        ):
            pm.add_command(label=label, command=lambda a=fol, b=fil: (self.folder_tpl.set(a), self.file_tpl.set(b)))
        presets["menu"] = pm
        presets.grid(row=2, column=2, sticky="ew", pady=(4, 0))

        adv = ttk.Frame(n)
        adv.grid(row=3, column=1, columnspan=2, sticky="ew", padx=6, pady=(4, 0))
        ttk.Label(adv, text="Split name on:").pack(side="left")
        ttk.Entry(adv, textvariable=self.separator, width=6).pack(side="left", padx=(4, 14))
        ttk.Label(adv, text="Regex (optional, named groups become tokens):").pack(side="left")
        ttk.Entry(adv, textvariable=self.regex).pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.pieces_lbl = ttk.Label(n, text="", foreground="#555")
        self.pieces_lbl.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.result_lbl = ttk.Label(n, text="", foreground="#1d4ed8")
        self.result_lbl.grid(row=5, column=0, columnspan=3, sticky="w")

        r = ttk.Frame(n)
        r.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(r, text="Keep cover art", variable=self.keep_cover).pack(side="left")
        ttk.Checkbutton(r, text="Resume unfinished books", variable=self.resume).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(r, text="Overwrite existing files", variable=self.overwrite).pack(side="left", padx=12)
        ttk.Checkbutton(r, text="Re-encode to AAC-LC", variable=self.reencode,
                        command=self._toggle_reencode).pack(side="left", padx=(12, 4))
        self.br_box = ttk.Combobox(r, textvariable=self.bitrate, width=6, state="readonly",
                                   values=("32k", "48k", "64k", "96k", "128k"))
        self.br_box.pack(side="left")
        self.mono_chk = ttk.Checkbutton(r, text="Mono", variable=self.mono)
        self.mono_chk.pack(side="left", padx=6)

        # 4. Parts preview ----------------------------------------------
        p = ttk.LabelFrame(root, text="4. Parts for the selected book", padding=8)
        p.pack(fill="both", expand=True, pady=(6, 0))
        self.sel_info = ttk.Label(p, text="", foreground="#555")
        self.sel_info.pack(fill="x", pady=(0, 4))
        pwrap = ttk.Frame(p)
        pwrap.pack(fill="both", expand=True)
        p = pwrap
        pcols = ("part", "start", "end", "length", "chapters", "note")
        self.tree = ttk.Treeview(p, columns=pcols, show="headings", height=6)
        for c, w, t in zip(pcols, (50, 80, 80, 80, 480, 170),
                           ("Part", "Start", "End", "Length", "Chapters", "Note")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c in ("chapters", "note") else "center",
                             stretch=c == "chapters")
        psb = ttk.Scrollbar(p, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=psb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        psb.pack(side="left", fill="y")
        self.tree.tag_configure("warn", foreground="#b45309")

        # Run ------------------------------------------------------------
        run = ttk.Frame(root)
        run.pack(fill="x", pady=(10, 0))
        run.columnconfigure(1, weight=1)
        self.book_status = tk.StringVar(value="")
        self.book_pct = tk.StringVar(value="")
        self.overall_pct = tk.StringVar(value="")
        ttk.Label(run, text="Current book:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.book_bar = ttk.Progressbar(run, maximum=1000)
        self.book_bar.grid(row=0, column=1, sticky="ew")
        ttk.Label(run, textvariable=self.book_pct, width=5, anchor="e").grid(row=0, column=2, padx=(6, 0))
        self.cancel_btn = ttk.Button(run, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.grid(row=0, column=3, sticky="ew", padx=(8, 0))
        ttk.Label(run, textvariable=self.book_status, foreground="#555").grid(row=1, column=1, columnspan=2,
                                                                               sticky="w", pady=(1, 6))
        ttk.Label(run, text="Overall:").grid(row=2, column=0, sticky="w", padx=(0, 8))
        self.bar = ttk.Progressbar(run, maximum=1000)
        self.bar.grid(row=2, column=1, sticky="ew")
        ttk.Label(run, textvariable=self.overall_pct, width=5, anchor="e").grid(row=2, column=2, padx=(6, 0))
        self.go_btn = ttk.Button(run, text="Split all", command=self.start, state="disabled")
        self.go_btn.grid(row=2, column=3, sticky="ew", padx=(8, 0))
        ttk.Label(run, textvariable=self.status).grid(row=3, column=1, columnspan=3, sticky="w", pady=(1, 0))

    def _toggle_reencode(self):
        self.br_box.configure(state="readonly" if self.reencode.get() else "disabled")
        self.mono_chk.configure(state="normal" if self.reencode.get() else "disabled")

    def _check_ffmpeg(self):
        if not find_tool("ffmpeg") or not find_tool("ffprobe"):
            messagebox.showwarning(APP_NAME,
                "ffmpeg / ffprobe were not found.\n\n"
                "Download a Windows build (e.g. from gyan.dev or BtbN on GitHub) and either:\n"
                "  • put ffmpeg.exe and ffprobe.exe in the same folder as this app, or\n"
                "  • put the whole 'ffmpeg' folder next to this app, or\n"
                "  • add its 'bin' folder to your PATH.\n\n"
                "Then use Help → Check ffmpeg setup to confirm. Press F1 for more help.")

    # ------------------------------------------------------------ settings → objects
    def plan_settings(self):
        try:
            secs = float(self.hours.get() or 0) * 3600 + float(self.minutes.get() or 0) * 60
        except ValueError:
            secs = 0
        return PlanSettings(mode=self.mode.get(), max_seconds=secs, balance=self.balance.get(),
                            cut_long_chapters=self.cut_long.get(), skip_if_fits=self.skip_fits.get())

    def naming_settings(self):
        return NamingSettings(base_dir=self.base_dir.get().strip(), folder_template=self.folder_tpl.get(),
                              file_template=self.file_tpl.get(), separator=self.separator.get(),
                              regex=self.regex.get().strip())

    def output_settings(self):
        return OutputSettings(reencode=self.reencode.get(), reencode_bitrate=self.bitrate.get(),
                              reencode_mono=self.mono.get(), keep_cover=self.keep_cover.get(),
                              overwrite=self.overwrite.get(), resume=self.resume.get())

    # ------------------------------------------------------------ adding books
    def browse_files(self):
        paths = filedialog.askopenfilenames(title="Choose audiobooks",
                                            filetypes=[("Audiobooks", "*.m4b *.m4a"), ("All files", "*.*")])
        if paths:
            self.add_paths(paths)

    def browse_folder(self):
        d = filedialog.askdirectory(title="Add every .m4b in this folder (and subfolders)")
        if not d:
            return
        found = []
        for dirpath, _, files in os.walk(d):
            found += [os.path.join(dirpath, f) for f in sorted(files) if f.lower().endswith(AUDIO_EXT)]
        if not found:
            messagebox.showinfo(APP_NAME, "No .m4b files found in that folder.")
            return
        self.add_paths(found)

    def add_paths(self, paths):
        have = {os.path.normcase(os.path.abspath(j.path)) for j in self.jobs}
        new = []
        for p in paths:
            key = os.path.normcase(os.path.abspath(p))
            if key not in have and os.path.isfile(p):
                have.add(key)
                job = BookJob(path=p, status="Reading…")
                self.jobs.append(job)
                new.append(job)
        if not new:
            return
        if not self.base_dir.get().strip():
            self.base_dir.set(os.path.join(os.path.dirname(os.path.abspath(new[0].path)), "Split"))
        self.refresh()

        def probe_all():
            for job in new:
                try:
                    job.book = probe(job.path)
                    job.status, job.message = "Pending", ""
                except SplitError as e:
                    job.status, job.message = "Error", str(e)
                self.q.put(("probed", job))

        threading.Thread(target=probe_all, daemon=True).start()

    def _selected_indices(self):
        return sorted(self.books.index(i) for i in self.books.selection())

    def remove_selected(self):
        if self.worker:
            return
        for i in reversed(self._selected_indices()):
            del self.jobs[i]
        self.refresh()

    def clear_all(self):
        if self.worker:
            return
        self.jobs.clear()
        self.refresh()

    def move(self, d):
        if self.worker:
            return
        sel = self._selected_indices()
        if not sel or (d < 0 and sel[0] == 0) or (d > 0 and sel[-1] == len(self.jobs) - 1):
            return
        for i in (sel if d < 0 else reversed(sel)):
            self.jobs[i], self.jobs[i + d] = self.jobs[i + d], self.jobs[i]
        self.refresh(select=[i + d for i in sel])

    def choose_base(self):
        d = filedialog.askdirectory(title="Choose the base output folder")
        if d:
            self.base_dir.set(d)

    # ------------------------------------------------------------ refresh views
    def refresh(self, select=None):
        if select is None:
            select = self._selected_indices()
        self.books.delete(*self.books.get_children())
        ps, ns = self.plan_settings(), self.naming_settings()
        folders = {}
        rows = []
        for job in self.jobs:
            length = chapters = parts = folder = ""
            tag = ()
            b = job.book
            if b:
                length = fmt_time(b.duration)
                chapters = str(len(b.chapters)) if b.has_real_chapters else "none"
                try:
                    if ps.mode == "duration" and ps.max_seconds <= 0:
                        raise SplitError("Set a duration")
                    if ps.skip_if_fits and fits_already(b, ps):
                        parts = "—"
                    else:
                        pl = make_plan(b, ps)
                        parts = str(len(pl))
                        if any(p.oversized for p in pl):
                            tag = ("warn",)
                    folder, _ = preview_names(b, ns, int(parts) if parts.isdigit() else 1)
                    folders.setdefault(os.path.normcase(folder), []).append(job)
                except (SplitError, TemplateError) as e:
                    folder = f"⚠ {e}"
                    tag = ("error",)
            st = job.status
            if st in ("Done",):
                tag = ("done",)
            elif st == "Error":
                tag = ("error",)
            elif st == "Splitting":
                tag = ("busy",)
            elif st == "Skipped":
                tag = ("skip",)
            rows.append([job.name, length, chapters, parts, folder, st, tag])
        # flag two books that resolve to the same folder
        dup = {id(j) for js in folders.values() if len(js) > 1 for j in js}
        for job, row in zip(self.jobs, rows):
            if id(job) in dup and job.status not in ("Done", "Skipped"):
                row[5] = "Same folder as another book"
                row[6] = ("warn",)
            iid = self.books.insert("", "end", values=row[:6], tags=row[6])
        kids = self.books.get_children()
        for i in select:
            if i < len(kids):
                self.books.selection_add(kids[i])
        if not select and kids:
            self.books.selection_set(kids[0])

        total = sum(j.book.duration for j in self.jobs if j.book)
        self.count_lbl.configure(text=f"{len(self.jobs)} book(s), {fmt_time(total)} total" if self.jobs else "")
        busy = self.worker is not None
        ready = bool(self.jobs) and not busy and any(j.book for j in self.jobs)
        self.go_btn.configure(state="normal" if ready else "disabled")
        for b in self.edit_btns:
            b.configure(state="disabled" if busy else "normal")
        self.refresh_selected()

    def refresh_selected(self):
        self.tree.delete(*self.tree.get_children())
        sel = self._selected_indices()
        job = self.jobs[sel[0]] if sel and sel[0] < len(self.jobs) else None
        if not job or not job.book:
            self.pieces_lbl.configure(text="Select a book to see its name pieces and output path.")
            self.result_lbl.configure(text="")
            self.sel_info.configure(text="")
            return
        b, ps, ns = job.book, self.plan_settings(), self.naming_settings()
        try:
            ctx = naming_context(b, ns)
            pieces = "   ".join(f"{{{i + 1}}} = {p}" for i, p in enumerate(ctx["_pieces"]))
            extra = [k for k in ctx if not k.startswith("_") and k not in
                     ("name", "title", "album", "artist", "author", "narrator", "year", "genre", "series")]
            regex_bits = "   ".join(f"{{{k}}} = {ctx[k]}" for k in extra)
            self.pieces_lbl.configure(text=f"Name pieces:   {pieces}" + (f"      Regex:   {regex_bits}" if regex_bits else ""))
            if ps.mode == "duration" and ps.max_seconds <= 0:
                raise SplitError("Enter a duration greater than zero.")
            plan = make_plan(b, ps)
            folder, first = preview_names(b, ns, len(plan))
            note = "   (will be skipped — already within the limit)" if ps.skip_if_fits and fits_already(b, ps) else ""
            self.result_lbl.configure(text=f"→  {os.path.join(folder, first)}{note}", foreground="#1d4ed8")
        except (SplitError, TemplateError) as e:
            self.result_lbl.configure(text=f"⚠  {e}", foreground="#b91c1c")
            self.sel_info.configure(text="")
            return
        for p in plan:
            if p.chapters:
                a, z = p.chapters[0].title, p.chapters[-1].title
                span = a if a == z else f"{a}  to  {z}   ({len(p.chapters)} chapters)"
            else:
                span = ""
            note = "Longer than limit (one long chapter)" if p.oversized else ""
            self.tree.insert("", "end", values=(p.index, fmt_time(p.start), fmt_time(p.end),
                                                fmt_time(p.duration), span, note),
                             tags=("warn",) if p.oversized else ())
        self.sel_info.configure(text=f"{job.name}: {len(plan)} parts, longest {fmt_time(max(p.duration for p in plan))}. "
                            f"{b.audio_codec.upper()} {b.audio_profile} {b.bitrate // 1000} kbps"
                            + ("" if b.has_real_chapters else " — no chapters, splits by time")
                            + (" — HE-AAC: tick Re-encode for older iPods" if "HE" in (b.audio_profile or "").upper() else ""))

    def show_tokens(self):
        HelpWindow.show(self, 2)

    def _show_job_message(self, _e):
        sel = self._selected_indices()
        if sel and self.jobs[sel[0]].message:
            j = self.jobs[sel[0]]
            if j.status == "Error":
                messagebox.showerror(j.name, j.message + "\n\nSee Help → Troubleshooting (F1) for common fixes.")
            else:
                messagebox.showinfo(j.name, j.message)

    # ------------------------------------------------------------ running
    def start(self):
        ns = self.naming_settings()
        if not ns.base_dir:
            messagebox.showerror(APP_NAME, "Choose a base output folder.")
            return
        ps = self.plan_settings()
        if ps.mode == "duration" and ps.max_seconds <= 0:
            messagebox.showerror(APP_NAME, "Enter a duration greater than zero.")
            return
        if any(j.status == "Reading…" for j in self.jobs):
            messagebox.showinfo(APP_NAME, "Still reading some of the books — try again in a moment.")
            return
        runnable = [j for j in self.jobs if j.status in ("Pending", "Error", "Cancelled")]
        if not runnable:
            if not messagebox.askyesno(APP_NAME, "Every book has already been processed. Split them all again?"):
                return
            for j in self.jobs:
                if j.status in ("Done", "Skipped"):
                    j.status, j.message = "Pending", ""
        self._save_settings()
        self._save_session(running=True)
        self.cancel_event.clear()
        self.cancel_btn.configure(state="normal")
        self.bar["value"] = 0
        self.book_bar["value"] = 0
        self.overall_pct.set("0%")
        self.book_pct.set("0%")
        self.book_status.set("")
        jobs, os_ = self.jobs, self.output_settings()

        def work():
            try:
                run_batch(jobs, ps, ns, os_,
                          progress=lambda k, n, f, m: self.q.put(("prog", (f, m))),
                          book_progress=lambda f, m: self.q.put(("bookprog", (f, m))),
                          on_status=lambda i, j: self.q.put(("status", j)),
                          cancel=self.cancel_event)
                self.q.put(("finished", None))
            except Exception as e:  # noqa
                self.q.put(("fatal", str(e)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.refresh()

    def cancel(self):
        self.cancel_event.set()
        self.status.set("Cancelling…")

    def _poll(self):
        dirty = False
        report = False
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "prog":
                    frac, msg = data
                    self.bar["value"] = frac * 1000
                    self.overall_pct.set(f"{int(frac * 100)}%")
                    self.status.set(msg.split(":")[0] if msg.startswith("Book ") else msg)
                elif kind == "bookprog":
                    frac, msg = data
                    self.book_bar["value"] = frac * 1000
                    self.book_pct.set(f"{int(frac * 100)}%")
                    self.book_status.set(msg)
                elif kind in ("probed", "status"):
                    dirty = True
                    if kind == "status" and self.worker:
                        self._save_session(running=True)
                elif kind in ("finished", "fatal"):
                    self.worker = None
                    self.cancel_btn.configure(state="disabled")
                    dirty = True
                    if kind == "fatal":
                        messagebox.showerror(APP_NAME, data)
                    else:
                        report = True
        except queue.Empty:
            pass
        if dirty:
            self.refresh()
        if report:
            self._report()
        self.after(100, self._poll)

    def _report(self):
        if all(j.status in ("Done", "Skipped") for j in self.jobs):
            self._clear_session()
        else:
            self._save_session(running=False)
        done = [j for j in self.jobs if j.status == "Done"]
        err = [j for j in self.jobs if j.status == "Error"]
        skip = [j for j in self.jobs if j.status == "Skipped"]
        cancelled = self.cancel_event.is_set()
        summary = f"{len(done)} split, {len(skip)} skipped, {len(err)} failed" + (" — cancelled" if cancelled else "")
        self.status.set(summary)
        if not cancelled:
            self.bar["value"] = 1000
            self.overall_pct.set("100%")
        else:
            self.book_status.set("Cancelled")
        msg = summary + "."
        if err:
            msg += "\n\nFailed:\n" + "\n".join(f"  • {j.name}: {j.message.splitlines()[0]}" for j in err[:10])
            msg += "\n\nDouble-click a book in the list for the full error, or see Help → Troubleshooting."
        if done and messagebox.askyesno(APP_NAME, msg + "\n\nOpen the base folder?"):
            self._open_folder(self.base_dir.get())
        elif not done:
            (messagebox.showwarning if err else messagebox.showinfo)(APP_NAME, msg)

    @staticmethod
    def _open_folder(path):
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except Exception:
            pass


def main():
    if os.name == "nt":  # crisp text on high-DPI screens
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    App(files).mainloop()


if __name__ == "__main__":
    main()
