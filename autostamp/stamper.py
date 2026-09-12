"""
AutoStamp core engine.

Given a PDF and a stamp image, this module:
  1. Renders every target page and builds a map of "ink" (anything already
     printed on the page - text, lines, logos, existing stamps).
  2. Looks for existing stamp-like graphics (round engineer seals, permit
     boxes, etc.) on the page, ignoring the drawing's table grid lines so
     they don't get confused with real stamp artwork.
  3. Picks a spot for the new stamp that:
       - never overlaps anything already on the page (true empty space),
       - sits right next to any stamp cluster it found, and
       - stays clear of the page border and the title block.
  4. Places the stamp image at a *fixed* physical size (same width/height,
     in inches, on every page/document) so results are consistent.

Nothing here talks to the GUI directly - `stamp_document()` takes an
optional `log` callback so any front end (Tk GUI, CLI, tests) can report
progress the same way.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import pymupdf as fitz
from PIL import Image
from scipy import ndimage

# --------------------------------------------------------------------------
# Tunable defaults. All distances are in inches unless the name says *_px.
# --------------------------------------------------------------------------

DEFAULT_DPI = 150
DEFAULT_STAMP_WIDTH_IN = 2.75          # fixed physical width -> consistency
INK_THRESHOLD = 248                    # 0-255 gray value; below = "ink"
PAGE_MARGIN_IN = 0.2                   # never place flush against the edge
TOP_EXCLUDE_IN = 0.15                  # header sliver to avoid
BOTTOM_EXCLUDE_IN = 1.5                # keep clear of the title block
GAP_IN = 0.15                          # visual breathing room next to a stamp
EMPTY_THRESHOLD = 0.006                # max fraction of "ink" pixels allowed
                                        # inside the stamp's footprint
SEARCH_STEP_IN = 0.12                  # placement search grid resolution

# "Never skip" fallback (see find_placement_px): on a page so densely
# packed that the fixed-size stamp can't sit anywhere completely empty,
# shrink it (preserving its aspect ratio) in steps of SHRINK_FACTOR down to
# MIN_STAMP_WIDTH_IN before giving up on a perfectly clean fit. If even the
# smallest legible size can't find a completely empty spot, the very last
# resort is the least-obstructive spot available, so a page is only ever
# skipped when never_skip=False.
MIN_STAMP_WIDTH_IN = 1.0
SHRINK_FACTOR = 0.85

# Existing-stamp detection (round PE seals, permit boxes, ...)
STAMP_MIN_DIM_IN = 0.45
STAMP_MAX_DIM_IN = 3.4
STAMP_MIN_DENSITY = 0.02
CLUSTER_MERGE_GAP_IN = 0.6
LINE_STRIP_H_IN = 0.5                  # min length to count as a "line"
LINE_STRIP_V_IN = 0.3


@dataclass
class StampOptions:
    stamp_image_path: str
    stamp_width_in: float = DEFAULT_STAMP_WIDTH_IN
    stamp_height_in: Optional[float] = None   # None -> derive from image AR
    dpi: int = DEFAULT_DPI
    pages: str = "all"                # "all" or e.g. "1,3-5"
    margin_in: float = PAGE_MARGIN_IN
    top_exclude_in: float = TOP_EXCLUDE_IN
    bottom_exclude_in: float = BOTTOM_EXCLUDE_IN
    gap_in: float = GAP_IN
    empty_threshold: float = EMPTY_THRESHOLD
    never_skip: bool = True           # always place a stamp; shrink/overlap
                                       # as a last resort rather than skip
    min_stamp_width_in: float = MIN_STAMP_WIDTH_IN


@dataclass
class PageResult:
    page_number: int                  # 1-based
    placed: bool
    rect_pt: Optional[tuple] = None   # (x0,y0,x1,y1) in PDF points
    used_existing_stamp: bool = False
    tight_fit: bool = False           # True if the page was so packed the
                                       # stamp had to be shrunk and/or a
                                       # small amount of overlap was unavoidable
    reason: str = ""


@dataclass
class FileResult:
    input_path: str
    output_path: Optional[str] = None
    pages: list = field(default_factory=list)   # list[PageResult]
    error: Optional[str] = None


LogFn = Callable[[str], None]


def _noop_log(_msg: str) -> None:
    pass


# --------------------------------------------------------------------------
# Page rasterisation / ink masks
# --------------------------------------------------------------------------

def _render_gray(page: "fitz.Page", dpi: int) -> np.ndarray:
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    return arr.copy()


def _strip_lines(mask: np.ndarray, h_len_px: int, v_len_px: int) -> np.ndarray:
    """Remove long straight runs (table grid / border lines) from a mask,
    leaving only blob-like content (text, logos, stamps)."""
    h_len_px = max(3, h_len_px)
    v_len_px = max(3, v_len_px)
    h_struct = np.ones((1, h_len_px), dtype=bool)
    v_struct = np.ones((v_len_px, 1), dtype=bool)
    h_lines = ndimage.binary_opening(mask, structure=h_struct)
    v_lines = ndimage.binary_opening(mask, structure=v_struct)
    lines = ndimage.binary_dilation(h_lines | v_lines, iterations=2)
    return mask & ~lines


def _integral_image(mask: np.ndarray) -> np.ndarray:
    ii = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), dtype=np.float64)
    ii[1:, 1:] = mask.astype(np.float64).cumsum(axis=0).cumsum(axis=1)
    return ii


def _rect_sum(ii: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float:
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(ii.shape[1] - 1, x1); y1 = min(ii.shape[0] - 1, y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]


def _ink_fraction(ii: np.ndarray, x0: int, y0: int, w: int, h: int) -> float:
    area = w * h
    if area <= 0:
        return 1.0
    return _rect_sum(ii, x0, y0, x0 + w, y0 + h) / area


# --------------------------------------------------------------------------
# Existing-stamp detection
# --------------------------------------------------------------------------

def _find_clusters(mask_stripped: np.ndarray, dpi: int,
                    top_excl_px: int, bottom_excl_px: int) -> list:
    """Return bounding boxes (x0,y0,x1,y1) in px of stamp-like graphics
    already on the page (engineer seals, permit boxes, logos, ...)."""
    min_dim = STAMP_MIN_DIM_IN * dpi
    max_dim = STAMP_MAX_DIM_IN * dpi
    h, w = mask_stripped.shape

    labeled, n = ndimage.label(mask_stripped, structure=np.ones((3, 3), dtype=bool))
    if n == 0:
        return []
    objects = ndimage.find_objects(labeled)

    candidates = []
    candidate_mask = np.zeros_like(mask_stripped)
    for idx, sl in enumerate(objects, start=1):
        if sl is None:
            continue
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        if y1 <= top_excl_px or y0 >= h - bottom_excl_px:
            continue
        bw, bh = x1 - x0, y1 - y0
        if bw < min_dim or bh < min_dim or bw > max_dim or bh > max_dim:
            continue
        area = float(np.count_nonzero(labeled[sl] == idx))
        density = area / (bw * bh)
        if density < STAMP_MIN_DENSITY:
            continue
        candidates.append((x0, y0, x1, y1))
        candidate_mask[sl][labeled[sl] == idx] = True

    if not candidates:
        return []

    # Merge nearby candidates (e.g. two seals + a permit box) into clusters.
    merge_px = int(CLUSTER_MERGE_GAP_IN * dpi)
    grown = ndimage.binary_dilation(candidate_mask, iterations=max(1, merge_px // 2))
    grouped, ng = ndimage.label(grown, structure=np.ones((3, 3), dtype=bool))

    clusters = {}
    for (x0, y0, x1, y1) in candidates:
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        gid = grouped[min(cy, h - 1), min(cx, w - 1)]
        if gid == 0:
            continue
        bb = clusters.get(gid)
        if bb is None:
            clusters[gid] = [x0, y0, x1, y1]
        else:
            bb[0] = min(bb[0], x0); bb[1] = min(bb[1], y0)
            bb[2] = max(bb[2], x1); bb[3] = max(bb[3], y1)
    return [tuple(v) for v in clusters.values()]


# --------------------------------------------------------------------------
# Placement search
# --------------------------------------------------------------------------

def _nearest_empty_window(ii, page_w, page_h, win_w, win_h,
                           anchor, margin_px, top_bound_px, bottom_bound_px,
                           gap_px, empty_thresh, step_px):
    """Find the empty win_w x win_h window closest to `anchor`
    (x0,y0,x1,y1) that doesn't overlap the (gap-inflated) anchor box."""
    x_lo, x_hi = margin_px, page_w - margin_px - win_w
    y_lo, y_hi = max(margin_px, top_bound_px), min(page_h - margin_px, bottom_bound_px) - win_h
    if x_hi < x_lo or y_hi < y_lo:
        return None

    ax0, ay0, ax1, ay1 = anchor
    ax0 -= gap_px; ay0 -= gap_px; ax1 += gap_px; ay1 += gap_px
    acx, acy = (ax0 + ax1) / 2.0, (ay0 + ay1) / 2.0

    step = max(2, int(step_px))
    xs = list(range(int(x_lo), int(x_hi) + 1, step)) or [int(x_lo)]
    ys = list(range(int(y_lo), int(y_hi) + 1, step)) or [int(y_lo)]

    best = None
    best_d = None
    for y in ys:
        for x in xs:
            if not (x + win_w <= ax0 or x >= ax1 or y + win_h <= ay0 or y >= ay1):
                continue  # would sit on top of (or inside the gap of) the anchor
            wcx, wcy = x + win_w / 2.0, y + win_h / 2.0
            d = math.hypot(wcx - acx, wcy - acy)
            if best_d is not None and d >= best_d:
                continue
            if _ink_fraction(ii, x, y, win_w, win_h) <= empty_thresh:
                best_d = d
                best = (x, y)
    return best


def _find_clean_placement(mask_full: np.ndarray, mask_stripped: np.ndarray, dpi: int,
                           win_w: int, win_h: int, opts: StampOptions,
                           top_excl_px: int, bottom_excl_px: int):
    """Returns (x, y, used_cluster, reason) for a *completely* empty
    win_w x win_h spot, or (None, None, False, reason) if none exists."""
    h, w = mask_full.shape
    margin_px = int(opts.margin_in * dpi)
    gap_px = int(opts.gap_in * dpi)
    step_px = int(SEARCH_STEP_IN * dpi)

    if win_w > (w - 2 * margin_px) or win_h > (h - top_excl_px - bottom_excl_px - 2 * margin_px):
        return None, None, False, "page too small for the stamp at this size"

    ii = _integral_image(mask_full)
    clusters = _find_clusters(mask_stripped, dpi, top_excl_px, bottom_excl_px)

    bottom_bound_px = h - bottom_excl_px

    best = None
    best_d = None
    for cluster in clusters:
        cx0, cy0, cx1, cy1 = cluster
        pos = _nearest_empty_window(ii, w, h, win_w, win_h, cluster,
                                     margin_px, top_excl_px, bottom_bound_px,
                                     gap_px, opts.empty_threshold, step_px)
        if pos is None:
            continue
        ccx, ccy = (cx0 + cx1) / 2.0, (cy0 + cy1) / 2.0
        wcx, wcy = pos[0] + win_w / 2.0, pos[1] + win_h / 2.0
        d = math.hypot(wcx - ccx, wcy - ccy)
        if best_d is None or d < best_d:
            best_d, best = d, pos

    if best is not None:
        return best[0], best[1], True, "placed next to existing stamp(s)"

    # No cluster found (or none had room nearby) - fall back to a general
    # empty-space search, biased toward the bottom-right of the usable area
    # (the conventional spot for a review/markup stamp).
    fallback_anchor = (w - margin_px, bottom_bound_px, w - margin_px, bottom_bound_px)
    pos = _nearest_empty_window(ii, w, h, win_w, win_h, fallback_anchor,
                                 margin_px, top_excl_px, bottom_bound_px,
                                 0, opts.empty_threshold, step_px)
    if pos is not None:
        reason = "no existing stamps detected; placed in open space" if not clusters \
            else "existing stamp(s) found but no clear space beside them; used nearest open space"
        return pos[0], pos[1], False, reason

    return None, None, False, "no sufficiently empty area found at this size"


def _least_ink_position(mask_full: np.ndarray, win_w: int, win_h: int,
                         margin_px: int, top_excl_px: int, bottom_excl_px: int,
                         step_px: int):
    """Absolute last resort: the win_w x win_h spot with the LEAST ink in
    it anywhere on the page (never returns None as long as the window fits
    within the page margins at all). Tries to stay clear of the header/
    title-block bands first, and only gives those up if the window
    genuinely doesn't fit outside them."""
    h, w = mask_full.shape
    ii = _integral_image(mask_full)

    def search(y_lo, y_hi):
        x_lo, x_hi = margin_px, w - margin_px - win_w
        y_lo, y_hi = max(margin_px, y_lo), min(h - margin_px, y_hi) - win_h
        if x_hi < x_lo or y_hi < y_lo:
            return None
        step = max(2, step_px)
        best, best_frac = None, None
        for y in range(int(y_lo), int(y_hi) + 1, step):
            for x in range(int(x_lo), int(x_hi) + 1, step):
                frac = _ink_fraction(ii, x, y, win_w, win_h)
                if best_frac is None or frac < best_frac:
                    best_frac, best = frac, (x, y)
        return best, best_frac

    result = search(top_excl_px, h - bottom_excl_px)
    if result is None:
        # Excluded bands leave no room at all for this size - drop them.
        result = search(0, h)
    if result is None:
        # Still nothing (window wider/taller than the page margins allow) -
        # this is the true "page too small" edge case; place flush at the
        # top-left inside whatever margin exists so it's at least on-page.
        return max(0, margin_px), max(0, margin_px), 1.0
    (x, y), frac = result
    return x, y, frac


def find_placement_px(mask_full: np.ndarray, mask_stripped: np.ndarray, dpi: int,
                       target_win_w: int, target_win_h: int, opts: StampOptions):
    """Always returns (x, y, win_w, win_h, used_cluster, tight_fit, reason)
    in analysis-resolution pixels - unless opts.never_skip is False, in
    which case it returns (None, None, None, None, False, False, reason)
    when no completely empty spot exists at the requested fixed size.

    Tries the requested fixed size first. If the page is too densely
    packed for a completely empty spot at that size, it shrinks the stamp
    (preserving its aspect ratio) in steps down to opts.min_stamp_width_in
    and tries again at each size. If even the smallest legible size can't
    find a completely empty spot, it falls back to the least-obstructive
    spot available on the page - so a page is only ever left unstamped
    when never_skip is explicitly turned off.
    """
    h, w = mask_full.shape
    top_excl_px = int(opts.top_exclude_in * dpi)
    bottom_excl_px = int(opts.bottom_exclude_in * dpi)
    aspect = target_win_h / target_win_w if target_win_w else 1.0
    min_w = max(1, min(target_win_w, int(round(opts.min_stamp_width_in * dpi))))

    sizes = []
    win_w = target_win_w
    while win_w > min_w:
        sizes.append(win_w)
        win_w = int(win_w * SHRINK_FACTOR)
    sizes.append(min_w)

    last_reason = "no sufficiently empty area found on this page"
    for win_w in sizes:
        win_h = max(1, int(round(win_w * aspect)))
        x, y, used_cluster, reason = _find_clean_placement(
            mask_full, mask_stripped, dpi, win_w, win_h, opts, top_excl_px, bottom_excl_px)
        if x is not None:
            tight_fit = win_w < target_win_w
            if tight_fit:
                reason = (f"page is tightly packed - stamp shrunk to {win_w / dpi:.2f}in "
                          f"wide to fit cleanly ({reason})")
            return x, y, win_w, win_h, used_cluster, tight_fit, reason
        last_reason = reason

    if not opts.never_skip:
        return None, None, None, None, False, False, last_reason

    # Absolute last resort: never skip. Take the smallest legible size and
    # place it at the least-obstructive spot on the page, even though that
    # means accepting some unavoidable overlap.
    margin_px = int(opts.margin_in * dpi)
    step_px = int(SEARCH_STEP_IN * dpi)
    win_w = min_w
    win_h = max(1, int(round(win_w * aspect)))
    x, y, ink_frac = _least_ink_position(
        mask_full, win_w, win_h, margin_px, top_excl_px, bottom_excl_px, step_px)
    reason = (f"page has no completely empty area even at the smallest legible size "
              f"({win_w / dpi:.2f}in wide) - placed at the least-obstructive spot found "
              f"(~{ink_frac * 100:.1f}% of that spot has existing content)")
    return x, y, win_w, win_h, False, True, reason


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def get_stamp_size_in(stamp_image_path: str, width_in: float,
                       height_in: Optional[float] = None) -> tuple:
    if height_in:
        return width_in, height_in
    with Image.open(stamp_image_path) as im:
        w_px, h_px = im.size
    return width_in, width_in * h_px / w_px


def _parse_page_spec(spec: str, page_count: int) -> list:
    spec = (spec or "all").strip().lower()
    if spec in ("", "all"):
        return list(range(page_count))
    result = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
            result.update(range(a - 1, b))
        else:
            result.add(int(part) - 1)
    return sorted(p for p in result if 0 <= p < page_count)


def stamp_document(input_path: str, output_path: str, opts: StampOptions,
                    log: Optional[LogFn] = None) -> FileResult:
    log = log or _noop_log
    result = FileResult(input_path=input_path)
    try:
        stamp_w_in, stamp_h_in = get_stamp_size_in(
            opts.stamp_image_path, opts.stamp_width_in, opts.stamp_height_in)

        doc = fitz.open(input_path)
        try:
            target_pages = _parse_page_spec(opts.pages, doc.page_count)
            target_win_w = int(round(stamp_w_in * opts.dpi))
            target_win_h = int(round(stamp_h_in * opts.dpi))
            h_len_px = int(LINE_STRIP_H_IN * opts.dpi)
            v_len_px = int(LINE_STRIP_V_IN * opts.dpi)

            for pidx in target_pages:
                page_no = pidx + 1
                page = doc[pidx]
                gray = _render_gray(page, opts.dpi)
                mask_full = gray < INK_THRESHOLD
                mask_stripped = _strip_lines(mask_full, h_len_px, v_len_px)

                x, y, win_w, win_h, used_cluster, tight_fit, reason = find_placement_px(
                    mask_full, mask_stripped, opts.dpi, target_win_w, target_win_h, opts)

                if x is None:
                    log(f"  page {page_no}: skipped ({reason})")
                    result.pages.append(PageResult(page_no, False, reason=reason))
                    continue

                zoom = opts.dpi / 72.0
                rect = fitz.Rect(x / zoom, y / zoom, (x + win_w) / zoom, (y + win_h) / zoom)
                page.insert_image(rect, filename=opts.stamp_image_path, keep_proportion=True)

                log(f"  page {page_no}: {reason}")
                result.pages.append(PageResult(
                    page_no, True, rect_pt=tuple(rect), used_existing_stamp=used_cluster,
                    tight_fit=tight_fit, reason=reason))

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            doc.save(output_path)
            result.output_path = output_path
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001 - surface any failure to the caller
        result.error = str(exc)
        log(f"  ERROR: {exc}")
    return result


def preview_page(input_path: str, page_number: int, opts: StampOptions,
                  preview_dpi: int = 120) -> Image.Image:
    """Render a page with the stamp composited at its computed location
    (for the GUI preview), without touching the original file."""
    doc = fitz.open(input_path)
    try:
        pidx = page_number - 1
        page = doc[pidx]

        stamp_w_in, stamp_h_in = get_stamp_size_in(
            opts.stamp_image_path, opts.stamp_width_in, opts.stamp_height_in)

        gray = _render_gray(page, opts.dpi)
        mask_full = gray < INK_THRESHOLD
        h_len_px = int(LINE_STRIP_H_IN * opts.dpi)
        v_len_px = int(LINE_STRIP_V_IN * opts.dpi)
        mask_stripped = _strip_lines(mask_full, h_len_px, v_len_px)

        target_win_w = int(round(stamp_w_in * opts.dpi))
        target_win_h = int(round(stamp_h_in * opts.dpi))
        x, y, win_w, win_h, used_cluster, tight_fit, reason = find_placement_px(
            mask_full, mask_stripped, opts.dpi, target_win_w, target_win_h, opts)

        zoom = preview_dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        base = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        if x is not None:
            scale = preview_dpi / opts.dpi
            stamp = Image.open(opts.stamp_image_path).convert("RGBA")
            sw = int(win_w * scale)
            sh = int(win_h * scale)
            stamp = stamp.resize((max(1, sw), max(1, sh)))
            base.paste(stamp, (int(x * scale), int(y * scale)), stamp)
        return base
    finally:
        doc.close()
