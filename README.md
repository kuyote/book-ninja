# book-ninja
UI for slicing and dicing m4b audio books

Splits long `.m4b` audiobooks into smaller `.m4b` files that older iPods can handle. It only cuts **at chapter boundaries**, and each part keeps its own chapter list, the book's tags and the cover art.

Add as many books as you like. They're split **one at a time**, and each book goes into **its own folder** under a base folder you choose. Folder and file names come from templates that can use pieces of the original file name.

## Help inside the app

Press **F1**, or use the **Help** menu, for:
- **Getting started**, **Split options explained**, **Naming tokens**, **Troubleshooting** and **iPod tips** (tabs in one help window).
- **Check ffmpeg setup**: confirms that ffmpeg and ffprobe are found and shows where they are and which version.
- **Copy diagnostic info**: copies the app version, OS, ffmpeg details, your settings, and each book's status and errors to the clipboard, ready to paste when asking for help. No audio is included.
- **Open settings folder** and **About**.

The **File** menu has Add files… (Ctrl+O), Add folder…, Open base folder and Exit. Error messages point you to the Troubleshooting tab.

## Batch splitting

- **Add files…** (multi-select) or **Add folder…** (adds every `.m4b` in the folder and its subfolders). You can also drag several `.m4b` files onto `run.bat` or the `.exe`.
- Books are split top to bottom. Use **Move up** / **Move down** to change the order.
- The book list shows the length, chapter count, number of parts, the resolved output folder and the status of each book. Click a book to see its part plan and the full path of its first file.
- Two progress bars: **Current book** shows the book being split and which part it's on; **Overall** shows progress through the whole batch (Book 2 of 5).
- **Recovery / resume:** if a split stops part-way because you cancelled, the app closed or the PC shut down, the parts that already finished are kept. Press **Split all** again and only the missing parts are made.
  - Each part is written as `name.m4b.partial` and renamed to `name.m4b` only when it's complete, so a half-written file never looks finished. Leftover `.partial` files are cleaned up automatically.
  - A hidden `.bookninja-progress-*.json` file in the book's folder tracks finished parts and the settings used. It's removed when the book completes.
  - Parts are reused only if they match the current settings: cut points, quality and cover. Without a progress file, each existing part is checked for length, track number, chapters and audio format.
  - If Book Ninja closes in the middle of a batch, it offers to add the unfinished books back the next time it starts.
  - The **Resume unfinished books** option is on by default. To redo a book completely, untick it and tick **Overwrite**.
- If a book fails, for example because the file is damaged, it's marked **Error** and the batch moves on to the next one. Double-click the book to see the full error. Pressing **Split all** again retries failed and cancelled books only.
- **Skip books already within the limit** leaves short books alone.
- If two books would land in the same folder, they're flagged before you start.
- Your settings (base folder, templates, split options) are remembered between runs in `%APPDATA%\Book Ninja\settings.json`.

## Folder and file name templates

Write tokens in `{braces}`. A `\` or `/` in the **Book folder** template creates subfolders. When you select a book, the app shows how its name breaks into pieces and the exact output path, and it updates as you type.

| Token | Meaning (for `Brandon Sanderson - Mistborn 01 - The Final Empire.m4b`) |
|---|---|
| `{name}` | the whole file name, without `.m4b` |
| `{1}` `{2}` `{3}` | pieces split on **Split name on** (default ` - `): `Brandon Sanderson`, `Mistborn 01`, `The Final Empire` |
| `{-1}` `{-2}` | counting from the end: `{-1}` = `The Final Empire` |
| `{2-}` / `{1-2}` | a range of pieces, re-joined: `Mistborn 01 - The Final Empire` / `Brandon Sanderson - Mistborn 01` |
| `{name:0:17}` | characters 0–17 of the name: `Brandon Sanderson` |
| `{title}` `{author}` `{album}` `{narrator}` `{year}` `{genre}` `{series}` | taken from the book's tags |
| `{part}` `{total}` | part number and part count (file names only); `{part:02d}` gives `01` |
| `{x\|fallback}` | used when `x` is blank: `{author\|Unknown}`, `{book\|{name}}` |

**Regex (optional).** Named groups become tokens. For example, with
`(?P<author>.+?) - (?P<series>.+?) (?P<num>\d+) - (?P<book>.+)` the template
`{author}\{series}\{num} {book}` gives `Brandon Sanderson\Mistborn\01 The Final Empire`. If a file doesn't match the regex, those tokens are blank, so use `|fallback` for that case.

The **Presets** menu has common setups, such as Author\Title from the file name or from tags. Names are cleaned up automatically to be valid on Windows: characters like `: ? * "` are replaced, and trailing dots and reserved names like `CON` are fixed.

Two ways to split:

| Mode | What it does |
|---|---|
| **Parts no longer than _H_ hours _M_ minutes** | Fills each part with whole chapters up to the chapter boundary closest to the limit **without going over**. Example: with a 5 h limit and chapters ending at 4:41, 5:07 … the first part ends at 4:41. |
| **One file per chapter** | Writes every chapter as its own file. |

Options for the duration mode:

- **Even out part lengths.** Keeps the same number of parts but picks chapter boundaries so the parts are about the same length. For example, a 10 h 10 m book becomes three parts of about 3½ hours each instead of 5 h + 5 h + 10 m. No part goes over the limit.
- **Cut chapters that are longer than the limit.** If one chapter by itself is longer than the limit, it is cut by time into equal pieces. If this is off, that chapter becomes its own part and is flagged in the preview. Files with no chapters are always split by time.

Output options:

- **Re-encode to AAC-LC** (32–128 kbps, optional mono). The default is a stream copy, which is fast and loses no quality. Re-encode only if the source is HE-AAC (older iPods can't play it; the app detects this and ticks the box for you) or if you want smaller files.
- Keep cover art, overwrite existing files.

Every part is tagged as an audiobook: title "Book (Part 2 of 4)", track 2/4, the original album, artist and other tags, a sort name that keeps the parts in order, and the `M4B` brand, so iTunes and the iPod treat it as an audiobook.

---

## Running it on Windows

### Option A: with Python (quickest)
1. Install Python 3.9 or later from python.org. Tick **"Add python.exe to PATH"**.
2. Download an ffmpeg build for Windows, such as the "release essentials" zip from gyan.dev or a BtbN build on GitHub. Copy **`ffmpeg.exe`** and **`ffprobe.exe`** from its `bin` folder into this folder. (Leaving them on your PATH also works.)
3. Double-click **`run.bat`**. You can also drag an `.m4b` onto `run.bat` to open it straight away.

### Option B: build a standalone `.exe` (no Python needed on the target PC)
1. Do steps 1 and 2 above on the PC that will do the build.
2. Double-click **`build_exe.bat`**.
3. The finished app is in `dist\Book Ninja\`. Zip that folder and copy it anywhere.

### Option C: let GitHub build it
Push this folder to a GitHub repo. `.github/workflows/build-windows.yml` builds the app with ffmpeg bundled, and you download the zip from the **Actions** tab.

The app looks for ffmpeg in these places, in this order: next to the app → an `ffmpeg\` subfolder → `ffmpeg\bin\` → PATH.

---

## Command line

```
bookninja book.m4b --max 5h                    (or: python bookninja_cli.py book.m4b --max 5h)
bookninja *.m4b --max 5h --base "D:\iPod Books" --folder "{1}\{2-}"
bookninja "D:\Audiobooks" --max 4:30 --balance --skip-fits      (every .m4b in a folder)
bookninja book.m4b --each-chapter
bookninja *.m4b --max 5h --dry-run             (show every book's plan and paths only)
bookninja book.m4b --max 5h --reencode --bitrate 64k --mono
```

Naming flags: `--base`, `--folder`, `--pattern`, `--separator`, `--regex`. With no `--base`, output goes to a `Split` folder next to the first input.

Durations can be written as `5`, `5h`, `4.5`, `4:30`, `4h30m`, `270m` or `4:30:00`. A bare number means hours.

Other flags: `--cut-long-chapters`, `--skip-fits`, `--no-cover`, `--overwrite`, `--no-resume`. Resuming is on by default on the command line too.

---

## How it works

1. `ffprobe -show_chapters -show_format -show_streams` reads the length, chapters, tags, codec and cover.
2. The planner works only with chapter start times as cut points. Greedy mode extends each part to the furthest boundary that is ≤ the limit. Balanced mode uses a small dynamic program that keeps the same part count and minimises how far each part is from the average length.
3. For each part, the app writes an `FFMETADATA` file holding the tags and the chapters that fall inside the part, shifted to start at 0. It then runs:
   ```
   ffmpeg -ss START -t LEN -i book.m4b -i meta.txt -i cover.jpg
          -map 0:a:0 -map 2:v:0 -disposition:v:0 attached_pic
          -map_metadata 1 -map_chapters 1 -c copy -f ipod -brand "M4B " part.m4b
   ```
   The cover is pulled out once and added to every part. Progress comes from ffmpeg's `-progress` output. On Windows the ffmpeg runs don't open a console window.

## Project layout

```
book_ninja.pyw       GUI launcher (double-click, no console)
bookninja_cli.py        CLI launcher
bookninja/core.py       probing, planning, ffmpeg splitting
bookninja/batch.py      multi-book queue, one at a time
bookninja/template.py   folder / file name templates
bookninja/gui.py        tkinter window
bookninja/help.py       Help menu, guide, setup check, diagnostics
bookninja/cli.py        argparse front end
run.bat / build_exe.bat
```

