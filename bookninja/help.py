"""
Help menu for Book Ninja: guide, troubleshooting, setup check, diagnostics, about.
"""
import os
import platform
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

from . import __version__
from .core import _popen_kwargs, find_tool

APP_NAME = "Book Ninja"

# Each page is a list of (style, text). Styles: h1, h2, p, li, code, tip
GETTING_STARTED = [
    ("h1", "Getting started"),
    ("p", "Book Ninja splits long .m4b audiobooks into shorter .m4b files that older iPods can play. "
          "It cuts only between chapters, and every part keeps its chapters, cover art and book details."),
    ("h2", "1. Add your books"),
    ("li", "Click Add files… to pick one or more .m4b files. You can select several at once by holding Ctrl."),
    ("li", "Or click Add folder… to add every .m4b in a folder, including its subfolders."),
    ("li", "Or drag .m4b files onto Book Ninja.exe (or run.bat) before it opens."),
    ("li", "Books are split one at a time, from the top of the list down. Use Move up / Move down to change the order."),
    ("h2", "2. Choose how to split"),
    ("li", "Parts no longer than: type the longest a part may be, for example 5 hours 0 minutes. "
           "Each part ends at the chapter closest to that length without going over."),
    ("li", "One file per chapter: every chapter becomes its own file."),
    ("h2", "3. Choose where the files go"),
    ("li", "Base folder: every book gets its own folder inside this one."),
    ("li", "Book folder: how each book's folder is named. {name} uses the original file name. "
           "See the Naming tab for more."),
    ("li", "Click a book in the list to see exactly where its files will be saved (the blue line)."),
    ("h2", "4. Check and split"),
    ("li", "The Parts table shows where each part starts and ends and which chapters it contains."),
    ("li", "Click Split all. The progress bar covers the whole batch. Cancel stops after cleaning up the file "
           "being written."),
    ("li", "When it finishes, you can open the base folder. Copy the parts to your iPod with iTunes, "
           "the Apple Devices app, or the tool you normally use."),
    ("tip", "Tip: press F1 at any time to open this help."),
]

OPTIONS = [
    ("h1", "Split options"),
    ("h2", "Even out part lengths"),
    ("p", "Without this, parts are filled as full as possible, which can leave a short last part "
          "(for example 5 h + 5 h + 10 min). With it ticked you get the same number of parts, but they are about "
          "the same length (about 3½ hours each). No part goes over your limit either way."),
    ("h2", "Cut chapters longer than the limit"),
    ("p", "Sometimes one chapter alone is longer than your limit. If this is off, that chapter becomes its own part "
          "and is shown in orange. If this is on, the chapter is cut by time into equal pieces so every part "
          "fits. Books with no chapters at all are always cut by time."),
    ("h2", "Skip books already within the limit"),
    ("p", "Books shorter than your limit are left alone instead of being copied as a single 'Part 1 of 1'."),
    ("h2", "Keep cover art"),
    ("p", "Copies the book's cover into every part so it shows on the iPod."),
    ("h2", "Resume unfinished books (on by default)"),
    ("p", "If a split stops part-way (you cancel, Book Ninja closes, or the computer shuts down), the parts that "
          "already finished are kept. Press Split all again and only the missing parts are made."),
    ("li", "Parts are written under a temporary name ending in .partial and get their real name only when "
           "complete, so a half-written part is never mistaken for a finished one. Leftover .partial files are "
           "deleted on the next run."),
    ("li", "While a book is in progress, a small hidden .bookninja-progress file in its folder records which parts "
           "are done. It is deleted when the book finishes."),
    ("li", "A part is only reused if it matches your current settings (same cut points, quality and cover). "
           "If you changed the settings, see Overwrite below."),
    ("li", "If Book Ninja was closed in the middle of a batch, it offers to add the unfinished books back to the "
           "list the next time it opens."),
    ("h2", "Overwrite existing files"),
    ("p", "Lets Book Ninja replace existing files that don't match the current settings. With Resume on, "
          "matching finished parts are still kept. To redo a book completely, untick Resume and tick Overwrite."),
    ("h2", "Re-encode to AAC-LC"),
    ("p", "Normally the audio is copied as-is: fast, with no quality loss. Tick this only if:"),
    ("li", "the book uses HE-AAC, which many older iPods cannot play "
           "(Book Ninja ticks this for you when it spots HE-AAC), or"),
    ("li", "you want smaller files. 64k is plenty for speech, and 32k–48k mono saves even more space."),
    ("p", "Re-encoding is much slower than copying."),
]

NAMING = [
    ("h1", "Naming folders and files"),
    ("p", "Write tokens in {curly braces}. Everything else is copied as typed. A \\ in the Book folder "
          "template makes subfolders."),
    ("p", "Click a book in the list and Book Ninja shows how its file name breaks into pieces "
          "({1}, {2}, …) and the exact output path, updating as you type."),
    ("h2", "From the file name"),
    ("code", "{name}        whole file name (no .m4b)\n"
             "{1} {2} {3}   pieces split on 'Split name on' (default ' - ')\n"
             "{-1}          the last piece\n"
             "{2-}          piece 2 to the end\n"
             "{1-2}         pieces 1 to 2\n"
             "{name:0:20}   the first 20 characters"),
    ("h2", "From the book's tags"),
    ("code", "{title} {author} {album} {narrator} {year} {genre} {series}"),
    ("h2", "Part file names only"),
    ("code", "{part} {total}       e.g. 3 and 7\n"
             "{part:02d}           03"),
    ("h2", "Fallbacks"),
    ("code", "{author|Unknown Author}     use 'Unknown Author' if blank\n"
             "{book|{name}}               use the file name if {book} is blank"),
    ("h2", "Examples for 'Brandon Sanderson - Mistborn 01 - The Final Empire.m4b'"),
    ("code", "{1}\\{3}            Brandon Sanderson\\The Final Empire\n"
             "{1}\\{2-}           Brandon Sanderson\\Mistborn 01 - The Final Empire\n"
             "{-1} ({1})         The Final Empire (Brandon Sanderson)"),
    ("h2", "Regex (advanced)"),
    ("p", "If your file names follow a fixed pattern, a regular expression with named groups turns each group into "
          "a token. For example:"),
    ("code", "(?P<author>.+?) - (?P<series>.+?) (?P<num>\\d+) - (?P<book>.+)\n"
             "{author}\\{series}\\{num} {book}   ->   Brandon Sanderson\\Mistborn\\01 The Final Empire"),
    ("p", "Files that don't match get blank values, so add a fallback: {series|Standalone}."),
    ("tip", "The Presets button next to the file name box fills in common layouts."),
]

TROUBLESHOOTING = [
    ("h1", "Troubleshooting"),
    ("h2", "\"ffmpeg / ffprobe were not found\""),
    ("p", "Book Ninja uses ffmpeg to do the actual splitting. Download a Windows build (for example "
          "'ffmpeg-release-essentials' from gyan.dev) and copy ffmpeg.exe and ffprobe.exe from its bin folder "
          "into the same folder as Book Ninja. Then use Help → Check ffmpeg setup to confirm."),
    ("h2", "A book shows Error: \"Not a readable audio file\""),
    ("li", "The file may be incomplete or damaged. Try playing it in another player."),
    ("li", "Audible .aax/.aaxc files and DRM-protected iTunes purchases can't be split. Book Ninja only works with "
           "unprotected .m4b/.m4a files."),
    ("li", "Double-click the book in the list to see the full error message."),
    ("h2", "A book shows Error: \"Output already exists\""),
    ("p", "That folder already has parts from a split made with different settings (for example a different "
          "time limit or bitrate), so they can't be reused. Tick Overwrite existing files, delete the old "
          "parts, or choose a different base folder."),
    ("h2", "Splitting stopped part-way (crash, power cut, closed the app)"),
    ("p", "Leave Resume unfinished books ticked and press Split all again. Finished parts are kept and only the "
          "missing ones are made. If Book Ninja was closed mid-batch, it offers to restore the list when it next "
          "opens. You can also add the same books again by hand; as long as the settings are the same, it "
          "carries on where it stopped."),
    ("h2", "I want to redo a book from scratch"),
    ("p", "Untick Resume unfinished books and tick Overwrite existing files, or delete the book's folder."),
    ("h2", "Status says \"Same folder as another book\""),
    ("p", "Your Book folder template gives two books the same folder (for example {1} when both start with the "
          "same author). Add more of the name, for example {1}\\{2-}."),
    ("h2", "The output path is red, or says \"Unknown token\""),
    ("li", "Check the token spelling. Tokens are listed on the Naming tab and under Tokens…."),
    ("li", "{part} and {total} only work in the Part file name, not the Book folder."),
    ("li", "A regex error means the Regex box has a typo. Clear the box to turn regex off."),
    ("h2", "The name pieces are wrong"),
    ("p", "Change Split name on to whatever separates the parts of your file names, for example ' - ', '_' "
          "or ' – '. Spaces count."),
    ("h2", "A part is longer than my limit (orange row)"),
    ("p", "That part is a single chapter that is longer than the limit by itself. Tick Cut chapters longer than "
          "the limit to cut it by time."),
    ("h2", "The book has no chapters"),
    ("p", "Book Ninja then splits it into equal pieces by time. The cuts may fall mid-sentence."),
    ("h2", "The parts won't play on my iPod"),
    ("li", "Tick Re-encode to AAC-LC and split again. Many older iPods can't play HE-AAC audio."),
    ("li", "Try a lower limit, for example 3–4 hours. Some older iPods struggle with very long files."),
    ("li", "Sync the files with iTunes or the Apple Devices app rather than copying them into the iPod's folders."),
    ("h2", "The parts show up as separate books or in the wrong order"),
    ("p", "Every part has the same Album and is numbered with Track (1/4, 2/4, …). If your library tool "
          "groups by a different field, check the Album and Track number in its Get Info window."),
    ("h2", "Error about a path or file name being too long"),
    ("p", "Windows limits paths to about 260 characters. Choose a shorter base folder (for example "
          "D:\\iPod) or a shorter Book folder template."),
    ("h2", "Windows says the app is unrecognised (SmartScreen)"),
    ("p", "Book Ninja.exe isn't code-signed, so Windows may warn you the first time. Click More info → Run "
          "anyway. Your antivirus might also check it on first launch."),
    ("h2", "Still stuck?"),
    ("p", "Use Help → Copy diagnostic info and paste it wherever you're asking for help. It includes your "
          "version, ffmpeg details, settings and any errors, but not your audio."),
]

IPOD_TIPS = [
    ("h1", "iPod tips"),
    ("li", "5 hours is a good default limit. Go lower (3–4 hours) if an older iPod skips or freezes on long files."),
    ("li", "Speech sounds fine at 64k. Re-encoding to 32k–48k mono saves a lot of space on small iPods."),
    ("li", "Parts are marked as audiobooks, so the iPod remembers your place in each part and lists them under "
           "Audiobooks."),
    ("li", "A naming layout such as {author}\\{title} keeps a large library tidy before you import it."),
]


class HelpWindow(tk.Toplevel):
    _instance = None

    @classmethod
    def show(cls, master, tab=0):
        if cls._instance is not None and cls._instance.winfo_exists():
            cls._instance.deiconify()
            cls._instance.lift()
            cls._instance.nb.select(tab)
            return cls._instance
        cls._instance = cls(master, tab)
        return cls._instance

    def __init__(self, master, tab=0):
        super().__init__(master)
        self.title(f"{APP_NAME} Help")
        self.geometry("760x620")
        self.minsize(560, 420)
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        for title, page in (("Getting started", GETTING_STARTED), ("Split options", OPTIONS),
                            ("Naming", NAMING), ("Troubleshooting", TROUBLESHOOTING), ("iPod tips", IPOD_TIPS)):
            self.nb.add(self._page(page), text=title)
        self.nb.select(tab)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=8)
        ttk.Button(bar, text="Check ffmpeg setup", command=lambda: check_setup(self)).pack(side="left")
        ttk.Button(bar, text="Copy diagnostic info", command=lambda: copy_diagnostics(master, self)).pack(side="left", padx=6)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())

    def _page(self, items):
        frame = ttk.Frame(self.nb)
        base = tkfont.nametofont("TkDefaultFont")
        size = abs(int(base.cget("size"))) or 9
        body = base.copy(); body.configure(size=size + 1)
        h1 = base.copy(); h1.configure(size=size + 6, weight="bold")
        h2 = base.copy(); h2.configure(size=size + 2, weight="bold")
        mono = tkfont.nametofont("TkFixedFont").copy(); mono.configure(size=size + 1)
        self._fonts = getattr(self, "_fonts", []) + [body, h1, h2, mono]   # keep references alive
        txt = tk.Text(frame, wrap="word", padx=16, pady=12, borderwidth=0, highlightthickness=0,
                      font=body, cursor="arrow", spacing1=2, spacing3=2)
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        txt.tag_configure("h1", font=h1, spacing3=8)
        txt.tag_configure("h2", font=h2, spacing1=12, spacing3=2)
        txt.tag_configure("p", spacing3=4)
        txt.tag_configure("li", lmargin1=12, lmargin2=28, spacing3=3)
        txt.tag_configure("code", font=mono, lmargin1=16, lmargin2=16, background="#f3f4f6",
                          spacing1=4, spacing3=6)
        txt.tag_configure("tip", foreground="#1d4ed8", spacing1=10)
        for style, text in items:
            if style == "li":
                txt.insert("end", "•  " + text + "\n", style)
            else:
                txt.insert("end", text + "\n", style)
        txt.configure(state="disabled")
        return frame


# ---------------------------------------------------------------------------- checks

def _tool_version(path):
    try:
        r = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10,
                           encoding="utf-8", errors="replace", **_popen_kwargs())
        return (r.stdout.splitlines() or ["?"])[0]
    except Exception as e:  # noqa
        return f"could not run ({e})"


def setup_report():
    lines, ok = [], True
    for name in ("ffmpeg", "ffprobe"):
        path = find_tool(name)
        if path:
            lines.append(f"✔ {name}: {path}\n     {_tool_version(path)}")
        else:
            ok = False
            lines.append(f"✘ {name}: NOT FOUND")
    if not ok:
        lines.append("\nCopy ffmpeg.exe and ffprobe.exe into the Book Ninja folder:\n"
                     f"     {_app_folder()}\n"
                     "or add the folder that contains them to your PATH.")
    return ok, "\n".join(lines)


def _app_folder():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_setup(parent):
    ok, report = setup_report()
    (messagebox.showinfo if ok else messagebox.showwarning)(
        "Check ffmpeg setup", ("Everything looks good.\n\n" if ok else "There's a problem.\n\n") + report,
        parent=parent)


def diagnostics_text(app):
    _, report = setup_report()
    lines = [f"{APP_NAME} {__version__}",
             f"OS: {platform.platform()}",
             f"Python: {sys.version.split()[0]} ({'exe' if getattr(sys, 'frozen', False) else 'script'})",
             f"App folder: {_app_folder()}", "", report, "", "Settings:"]
    try:
        for k in app._PERSIST:
            lines.append(f"  {k} = {getattr(app, k).get()!r}")
    except Exception:  # noqa
        pass
    jobs = getattr(app, "jobs", [])
    if jobs:
        lines += ["", f"Books ({len(jobs)}):"]
        for j in jobs:
            b = j.book
            info = (f"{b.audio_codec} {b.audio_profile} {b.bitrate // 1000}kbps, {int(b.duration)}s, "
                    f"{len(b.chapters) if b.has_real_chapters else 0} ch") if b else "not read"
            lines.append(f"  [{j.status}] {j.name} — {info}")
            if j.status == "Error" and j.message:
                lines += ["      " + l for l in j.message.splitlines()[:8]]
    return "\n".join(lines)


def copy_diagnostics(app, parent=None):
    text = diagnostics_text(app)
    app.clipboard_clear()
    app.clipboard_append(text)
    messagebox.showinfo("Diagnostic info copied",
                        "Diagnostic info has been copied to the clipboard. Paste it (Ctrl+V) wherever "
                        "you're asking for help.\n\nIt includes file names and settings, but no audio.",
                        parent=parent or app)


def show_about(parent):
    messagebox.showinfo(f"About {APP_NAME}",
                        f"{APP_NAME} {__version__}\n\n"
                        "Splits .m4b audiobooks into smaller parts at chapter boundaries so they play on "
                        "older iPods.\n\nThe audio work is done by FFmpeg (ffmpeg.org), which is used "
                        "under the LGPL/GPL.",
                        parent=parent)


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def build_menu(app, settings_dir):
    """Attach the menu bar to the main window."""
    menubar = tk.Menu(app)

    filem = tk.Menu(menubar, tearoff=False)
    filem.add_command(label="Add files…", accelerator="Ctrl+O", command=app.browse_files)
    filem.add_command(label="Add folder…", command=app.browse_folder)
    filem.add_separator()
    filem.add_command(label="Open base folder", command=lambda: app.base_dir.get() and open_folder(app.base_dir.get()))
    filem.add_separator()
    filem.add_command(label="Exit", command=app._on_close)
    menubar.add_cascade(label="File", menu=filem)

    helpm = tk.Menu(menubar, tearoff=False)
    helpm.add_command(label="Getting started", accelerator="F1", command=lambda: HelpWindow.show(app, 0))
    helpm.add_command(label="Split options explained", command=lambda: HelpWindow.show(app, 1))
    helpm.add_command(label="Naming tokens", command=lambda: HelpWindow.show(app, 2))
    helpm.add_command(label="Troubleshooting", command=lambda: HelpWindow.show(app, 3))
    helpm.add_command(label="iPod tips", command=lambda: HelpWindow.show(app, 4))
    helpm.add_separator()
    helpm.add_command(label="Check ffmpeg setup", command=lambda: check_setup(app))
    helpm.add_command(label="Copy diagnostic info", command=lambda: copy_diagnostics(app))
    helpm.add_command(label="Open settings folder", command=lambda: open_folder(settings_dir))
    helpm.add_separator()
    helpm.add_command(label=f"About {APP_NAME}", command=lambda: show_about(app))
    menubar.add_cascade(label="Help", menu=helpm)

    app.configure(menu=menubar)
    app.bind_all("<F1>", lambda e: HelpWindow.show(app, 0))
    app.bind_all("<Control-o>", lambda e: app.browse_files())
