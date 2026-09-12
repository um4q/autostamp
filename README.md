# AutoStamp

A small desktop tool for stamping "AS BUILT MARK UP" onto PDF drawings/data
sheets in bulk.

For every page of every PDF you give it, AutoStamp:

1. Renders the page and figures out where there is already ink on it
   (text, tables, logos, existing stamps/seals).
2. Looks for stamp-like graphics already on the page (engineer seals,
   permit-to-practice boxes, etc.), ignoring the drawing's table grid lines
   so they don't get mistaken for stamp artwork.
3. Places the new stamp **right next to** any such existing stamp(s) it
   finds, in the nearest spot that is completely empty.
4. If a page has no existing stamps, it finds the best open space instead.
5. Stamps at a fixed physical size (in inches) by default, so every
   document looks consistent - but if a page is too densely packed for
   that size to fit anywhere cleanly (e.g. a page that's just a filled-in
   data table with no full-size gap), it **never just skips the page**:
   it shrinks the stamp (preserving its proportions) step by step until it
   finds a spot that's still completely empty, down to a configurable
   minimum legible size. Only in the extreme case where even that smallest
   size can't find a totally clean spot does it fall back to the single
   least-obstructive spot on the page, so a stamp always ends up
   somewhere. Every page's log line says exactly what happened (placed at
   full size / shrunk to fit cleanly / tight fit with unavoidable overlap).
   The old strict "skip rather than shrink or overlap" behaviour is still
   available - untick "Never skip a page" in the GUI (or pass
   `never_skip=False` when scripting).

Everything happens on a copy of the file - your original PDFs are never
modified in place (unless you point the output at the same folder AND
manually overwrite - the default behaviour is to write `..._STAMPED.pdf`
next to the original).

## About the stamp image

`assets/as_built_stamp.png` is a recreation of the YANDA "AS BUILT MARK UP"
stamp built from the reference screenshot - **no source image file has been
provided**, so it's a redrawn approximation (same layout, text and
colour-coded legend, "Levy Ostrup" / "403-892-8877" pre-filled,
"Checked/Certified By" and "Date" left blank), not a pixel-perfect copy.
For an exact match, share the actual `.png`/`.jpg` file (attach/upload it
as a file, not a pasted screenshot) and drop it in as
`assets/as_built_stamp.png`, or use the "Browse..." button in the GUI to
point at it directly - the placement engine works with any PNG/JPG image,
at any aspect ratio, so your real file is used exactly as-is with no
redrawing involved.

To regenerate the bundled default stamp image (e.g. after tweaking
`assets/generate_stamp.py`):

```bash
python3 assets/generate_stamp.py
```

## Requirements

- Python 3.9+
- Tkinter (ships with the standard Windows/macOS Python installers; on
  Linux install it separately, e.g. `sudo apt install python3-tk` -
  see below, this is the one thing that can't be auto-installed)

That's it - you do **not** need to manually `pip install` anything.

## Running it

```bash
python3 main.py
```

The first time you run this, it automatically downloads and installs the
packages it needs (PyMuPDF, Pillow, NumPy, SciPy - listed in
`requirements.txt` for reference/manual use) directly into your Python.
If your Python won't allow that (e.g. a Linux distro that locks down the
system Python with PEP 668), it transparently creates a small private
virtual environment instead (`.autostamp-venv/`, next to `main.py`),
installs there, and relaunches itself inside it - still with the same
`python3 main.py` command. Subsequent runs skip straight to the GUI since
everything is already installed.

The one piece that genuinely can't be downloaded automatically is
Tkinter itself (Python's GUI toolkit) - on Linux it's a separate OS
package, not something pip can fetch. If it's missing, AutoStamp prints
the exact one-line command to install it (e.g.
`sudo apt install python3-tk`) and exits, instead of failing with a
confusing traceback.

Once everything's in place, this opens the GUI:

1. **Add PDFs...** - pick one or more documents to stamp.
2. **Stamp image** - defaults to the bundled `assets/as_built_stamp.png`;
   browse to a different file if you have your own.
3. **Stamp size** - fixed width in inches used on *every* page of *every*
   document (height is derived automatically from the image's aspect
   ratio, shown next to the width field). 2.75" is the default and is
   sized to sit comfortably next to typical engineering seals/permit
   boxes without overwhelming the page - adjust if your drawings run
   larger/smaller.
4. **Pages to stamp** - `all` (default), or something like `2` or `1-2,4`.
5. **Placement rules (advanced)** - page margin, how much of the top/
   bottom of the page to always keep clear (the bottom default of 1.5"
   is meant to keep the stamp off the title block), the gap to leave
   next to existing stamps, and the "Never skip a page" checkbox (on by
   default) with the smallest size the stamp is allowed to shrink to when
   a page is too tight for the full fixed size.
6. **Output** - either write `<name>_STAMPED.pdf` next to each original,
   or send everything to a chosen folder.
7. **Preview First Page** - renders the first page of the first document
   with the stamp placed where the tool would put it, so you can sanity
   check size/position before running the whole batch.
8. **Stamp All Documents** - runs the batch (in the background, so the
   window stays responsive) and reports progress + a per-page log,
   including *why* any page was skipped.

## Using the engine without the GUI

`autostamp/stamper.py` has no Tkinter dependency, so it can be scripted or
tested directly:

```python
from autostamp.stamper import StampOptions, stamp_document

opts = StampOptions(stamp_image_path="assets/as_built_stamp.png")
result = stamp_document("drawing.pdf", "drawing_STAMPED.pdf", opts, log=print)
for page in result.pages:
    print(page)
```

## Tuning the detection

All of the thresholds used to detect "ink", existing stamps, and empty
space live as constants at the top of `autostamp/stamper.py`
(`INK_THRESHOLD`, `EMPTY_THRESHOLD`, `STAMP_MIN_DIM_IN`/`STAMP_MAX_DIM_IN`,
etc.) with comments explaining what each one does, in case a particular
drawing template needs different defaults than the ones tuned against the
sample CNOOC/Kinosis data sheets this tool was built and tested against.
