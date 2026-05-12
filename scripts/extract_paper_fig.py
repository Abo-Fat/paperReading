"""
extract_paper_fig.py
====================
Extract all figures and tables (with captions) from an academic PDF.

Handles three common layouts:
  1. Two-column journal papers with embedded raster figures (e.g. IEEE)
  2. Single-column papers with mixed raster / vector figures (e.g. Science Advances)
  3. Dense multi-column conference summaries with many small sub-panels

Usage:
    python extract_paper_fig.py <pdf_path> [output_dir] [--dpi N]

Output:
    PNG files named  p<page>_Fig<N>.png  /  p<page>_Table<N>.png
    saved in output_dir (default: <pdf_stem>_figures/)
"""

import re
import argparse
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:
    import fitz              # older PyMuPDF

try:
    from PIL import Image, ImageChops
except ImportError:
    Image = None
    ImageChops = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Minimum display size (in PDF points) for an image to be considered a real
# figure panel (not an inline icon / legend marker).
MIN_IMG_PTS = 30

# Caption detection regex.
# Matches the very start of a text block, requiring either:
#   "Fig[.] <digits> ."   e.g. "Fig. 3." or "Figure 3."
#   "TABLE <roman/arabic>" e.g. "TABLE I" or "Table 2"
CAPTION_RE = re.compile(
    r"\b(?:"
    r"(?:Fig\.?|Figure|FIGURE)\s*([\dIVX]+)\s*[:.]"      # Fig. N: / Fig.N.
    r"|"
    r"(?:Table|TABLE)\s+([\dIVX]+)\s*[:.]"   # TABLE N: / Table N.
    r")",
    re.IGNORECASE,
)
FIG_CAPTION_RE = re.compile(
    r"(?:\b(?:Fig\.?|Figure|FIGURE)\s*([\dIVX]+)\s*:|"
    r"^\s*(?:Fig\.?|Figure|FIGURE)\s*([\dIVX]+)\s*\.)",
    re.IGNORECASE,
)
TABLE_CAPTION_RE = re.compile(
    r"(?:\b(?:Table|TABLE)\s+([\dIVX]+)\s*:|"
    r"^\s*(?:Table|TABLE)\s+([\dIVX]+)\s*(?:\.|$))",
    re.IGNORECASE,
)

# Minimum column-overlap fraction for a block to be considered "in the same
# column" as the caption.
COL_OVERLAP_FRAC = 0.35

# Vertical search radius (pts) when looking for images near a caption.
IMG_SEARCH_RADIUS = 350

# Padding (pts) added around the final figure rectangle before rendering.
PADDING = 4

# Text very close to a caption is usually an axis label, panel label, or table
# cell, not body text that should stop the crop.
CAPTION_INTERNAL_GAP = 28
TABLE_INTERNAL_GAP = 70


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_meaningful_images(page: fitz.Page) -> list[fitz.Rect]:
    """Return display-rects of all embedded images larger than MIN_IMG_PTS."""
    seen = set()
    rects = []
    for img in page.get_images():
        xref = img[0]
        if xref in seen:
            continue
        seen.add(xref)
        for r in page.get_image_rects(xref):
            if (r.x1 - r.x0) >= MIN_IMG_PTS and (r.y1 - r.y0) >= MIN_IMG_PTS:
                rects.append(r)
    return rects


def find_captions(page: fitz.Page) -> list[dict]:
    """Return a list of caption dicts for every Figure/Table caption on the page."""
    captions = []
    for b in page.get_text("blocks"):
        if b[6] != 0:          # skip image-blocks
            continue
        raw = b[4]
        # Try each line of the block – caption may follow embedded labels
        for line_start, line in enumerate(raw.splitlines()):
            stripped = line.strip()
            fig_m = FIG_CAPTION_RE.search(stripped)
            table_m = TABLE_CAPTION_RE.search(stripped)
            if fig_m or table_m:
                # Reconstruct caption text from this line onward
                rest = "\n".join(raw.splitlines()[line_start:]).strip()
                fig_type = "table" if table_m and not fig_m else "figure"
                m = table_m if fig_type == "table" else fig_m
                num = next(g for g in m.groups() if g)
                captions.append({
                    "bbox":  fitz.Rect(b[0], b[1], b[2], b[3]),
                    "text":  rest,
                    "type":  fig_type,
                    "num":   num,
                })
                break   # one caption per block
    return captions


def col_overlap_frac(bx0: float, bx1: float,
                     col_x0: float, col_x1: float) -> float:
    """Fraction of the caption column width covered by block [bx0, bx1]."""
    col_w = col_x1 - col_x0
    if col_w <= 0:
        return 0.0
    overlap = min(bx1, col_x1) - max(bx0, col_x0)
    return max(overlap / col_w, 0.0)


def page_columns(page: fitz.Page) -> dict[str, tuple[float, float]]:
    """Infer left, right, and full text columns from substantive text blocks."""
    page_w = page.rect.width
    left_blocks = []
    right_blocks = []
    all_blocks = []

    for b in page.get_text("blocks"):
        if b[6] != 0:
            continue
        x0, y0, x1, y1 = b[:4]
        text = b[4].strip()
        if FIG_CAPTION_RE.search(text) or TABLE_CAPTION_RE.search(text):
            continue
        if len(text) < 20 or (x1 - x0) < 80 or (y1 - y0) < 20:
            continue
        all_blocks.append((x0, x1))
        if (x0 + x1) / 2 < page_w / 2:
            left_blocks.append((x0, x1))
        else:
            right_blocks.append((x0, x1))

    left = (
        min((x0 for x0, _ in left_blocks), default=page_w * 0.08),
        max((x1 for _, x1 in left_blocks), default=page_w * 0.49),
    )
    right = (
        min((x0 for x0, _ in right_blocks), default=page_w * 0.51),
        max((x1 for _, x1 in right_blocks), default=page_w * 0.92),
    )
    full = (
        min((x0 for x0, _ in all_blocks), default=page_w * 0.08),
        max((x1 for _, x1 in all_blocks), default=page_w * 0.92),
    )
    return {"left": left, "right": right, "full": full}


def caption_column_bounds(page: fitz.Page, cap_rect: fitz.Rect) -> tuple[float, float]:
    """Return the logical column bounds for a caption-owned figure/table."""
    cols = page_columns(page)
    left_x0, left_x1 = cols["left"]
    right_x0, right_x1 = cols["right"]
    full_x0, full_x1 = cols["full"]
    single_w = max(left_x1 - left_x0, right_x1 - right_x0)

    crosses_gutter = cap_rect.x0 < left_x1 and cap_rect.x1 > right_x0
    very_wide = cap_rect.width > single_w * 1.25
    if crosses_gutter or very_wide:
        return max(full_x0 - PADDING, 0), min(full_x1 + PADDING, page.rect.width)

    if cap_rect.x1 <= page.rect.width / 2 + 20:
        return max(left_x0 - PADDING, 0), min(left_x1 + PADDING, page.rect.width)

    return max(right_x0 - PADDING, 0), min(right_x1 + PADDING, page.rect.width)


def find_top_boundary(page: fitz.Page,
                      cap_rect: fitz.Rect,
                      col_x0: float, col_x1: float) -> float:
    """
    Find the bottom edge of the nearest "substantive" text block that lies
    above cap_rect in the same column.

    "Substantive" means the block is wide enough to be body text, a section
    header, or another caption — not a tiny single-character figure label.
    A block qualifies if its width >= MIN_BOUNDARY_WIDTH pts.

    Returns the y-coordinate to use as the top of the figure region.
    """
    # A block qualifies as a boundary only if it looks like body text,
    # a section heading, or another caption — NOT an in-figure label.
    # We require both a minimum display width AND a minimum character count
    # so that tiny markers like "(d)" or "0.0\n0.2\n" are excluded.
    MIN_BOUNDARY_WIDTH = 25   # pts
    MIN_BOUNDARY_CHARS = 20   # characters (stripped)

    above_y = 0.0  # default: top of page

    for b in page.get_text("blocks"):
        if b[6] != 0:
            continue
        bx0, by0, bx1, by1 = b[0], b[1], b[2], b[3]

        # Must overlap with the caption's column
        if col_overlap_frac(bx0, bx1, col_x0, col_x1) < COL_OVERLAP_FRAC:
            continue

        # Must be wide enough (filters single-char / narrow labels)
        if (bx1 - bx0) < MIN_BOUNDARY_WIDTH:
            continue

        # Short labels inside plots are not paragraph boundaries.
        if (by1 - by0) < 20:
            continue

        # Must have enough characters (filters short axis/panel labels)
        if len(b[4].strip()) < MIN_BOUNDARY_CHARS:
            continue

        # Must end above the caption
        if by1 <= cap_rect.y0 - CAPTION_INTERNAL_GAP:
            if by1 > above_y:
                above_y = by1

    return above_y


def find_bottom_boundary(page: fitz.Page,
                         cap_rect: fitz.Rect,
                         col_x0: float, col_x1: float) -> float:
    """Find the next substantive text block below a top-captioned table."""
    MIN_BOUNDARY_WIDTH = 25
    MIN_BOUNDARY_CHARS = 20

    below_y = page.rect.height

    for b in page.get_text("blocks"):
        if b[6] != 0:
            continue
        bx0, by0, bx1, by1 = b[0], b[1], b[2], b[3]

        if col_overlap_frac(bx0, bx1, col_x0, col_x1) < COL_OVERLAP_FRAC:
            continue
        if (bx1 - bx0) < MIN_BOUNDARY_WIDTH:
            continue
        if (by1 - by0) < 20:
            continue
        if len(b[4].strip()) < MIN_BOUNDARY_CHARS:
            continue
        if by0 >= cap_rect.y1 + TABLE_INTERNAL_GAP and by0 < below_y:
            below_y = by0

    return below_y


def trim_whitespace_png(path: Path, margin_px: int = 8, threshold: int = 7) -> None:
    """Trim white margins from a rendered page clip."""
    if Image is None or ImageChops is None:
        return

    im = Image.open(path).convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg).convert("L")
    mask = diff.point(lambda p: 255 if p > threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return

    x0, y0, x1, y1 = bbox
    x0 = max(x0 - margin_px, 0)
    y0 = max(y0 - margin_px, 0)
    x1 = min(x1 + margin_px, im.width)
    y1 = min(y1 + margin_px, im.height)
    if x1 - x0 >= 5 and y1 - y0 >= 5:
        im.crop((x0, y0, x1, y1)).save(path)


# ---------------------------------------------------------------------------
# Core: assign images to captions
# ---------------------------------------------------------------------------

def assign_images_to_captions(captions: list[dict],
                               images: list[fitz.Rect],
                              ) -> dict[int, list[fitz.Rect]]:
    """
    图注永远在图的下方，因此每张图只寻找位于其上方的图像。
    对每个 image rect，找到在它下方、列对齐且距离最近的 caption。

    Returns a dict mapping caption index → list of associated image rects.
    """
    cap_images: dict[int, list[fitz.Rect]] = {i: [] for i in range(len(captions))}

    for img_rect in images:
        best_idx  = None
        best_dist = float("inf")

        for idx, cap in enumerate(captions):
            crect = cap["bbox"]

            # 图注必须在图像下方（caption 顶边 >= image 底边 - 小容差）
            if crect.y0 < img_rect.y1 - 10:
                continue

            # 水平列重叠检查
            img_w = img_rect.x1 - img_rect.x0
            overlap = (
                min(img_rect.x1, crect.x1) - max(img_rect.x0, crect.x0)
            ) / max(img_w, 1)
            if overlap < 0.20:
                continue

            # 图像底边到图注顶边的距离
            dist = crect.y0 - img_rect.y1
            if dist > IMG_SEARCH_RADIUS:
                continue

            if dist < best_dist:
                best_dist = dist
                best_idx  = idx

        if best_idx is not None:
            cap_images[best_idx].append(img_rect)

    return cap_images


# ---------------------------------------------------------------------------
# Core: compute final render rect for one figure
# ---------------------------------------------------------------------------

def figure_rect(cap: dict,
                assoc_images: list[fitz.Rect],
                page: fitz.Page) -> fitz.Rect:
    """
    图注永远在图的下方，因此：
      - 底边 = 图注底边 + PADDING（固定，绝不向下延伸）
      - 顶边 = 同列最近的实质性文本块底边（即上方正文/其他图注的边界）
      - 左右 = caption 列宽（如有图像则扩展至图像边界）
    """
    crect  = cap["bbox"]
    page_w = page.rect.width
    page_h = page.rect.height

    # 表格通常比图注文字宽，扩展到整页宽度
    if cap["type"] == "table":
        col_x0 = 0.0
        col_x1 = page_w
    else:
        col_x0 = max(crect.x0 - PADDING, 0)
        col_x1 = min(crect.x1 + PADDING, page_w)

    col_x0, col_x1 = caption_column_bounds(page, crect)

    # 如果有关联图像，用图像的 x 范围扩展列宽
    if assoc_images:
        col_x0 = min(col_x0, min(r.x0 for r in assoc_images) - PADDING)
        col_x1 = max(col_x1, max(r.x1 for r in assoc_images) + PADDING)
    col_x0 = max(col_x0, 0)
    col_x1 = min(col_x1, page_w)

    if cap["type"] == "table":
        fig_top = max(crect.y0 - PADDING, 0)
        fig_bottom = min(find_bottom_boundary(page, crect, col_x0, col_x1) - PADDING, page_h)
        return fitz.Rect(col_x0, fig_top, col_x1, fig_bottom)

    # 上边界：同列最近的实质性文本块底边
    above_y = find_top_boundary(page, crect, col_x0, col_x1)

    # 图区顶边：关联图像的最高点，但不超过 above_y
    if assoc_images:
        fig_top = max(min(r.y0 for r in assoc_images) - PADDING, above_y)
    else:
        # 纯矢量图：图区从上方文字边界开始
        fig_top = above_y

    # 底边固定为图注底边（caption 永远在图下方）
    fig_bottom = crect.y1 + PADDING

    return fitz.Rect(
        col_x0,
        max(fig_top, 0),
        col_x1,
        min(fig_bottom, page_h),
    )


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_figures(pdf_path: str,
                    output_dir: str | None = None,
                    dpi: int = 150) -> list[dict]:
    """
    Extract all figures and tables from *pdf_path*.

    Parameters
    ----------
    pdf_path   : path to input PDF
    output_dir : directory to write PNGs (default: <pdf_stem>_figures/)
    dpi        : render resolution (default 150)

    Returns
    -------
    List of dicts with keys: page, file, caption, bbox
    """
    pdf_path = Path(pdf_path)
    if output_dir is None:
        output_dir = pdf_path.parent / f"{pdf_path.stem}_figures"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    results = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    for page_num, page in enumerate(doc):
        images   = get_meaningful_images(page)
        captions = find_captions(page)

        if not captions:
            continue

        # Assign images to captions
        cap_img_map = assign_images_to_captions(captions, images)

        for idx, cap in enumerate(captions):
            assoc = cap_img_map.get(idx, [])
            rect  = figure_rect(cap, assoc, page)

            # Sanity check
            if rect.is_empty or rect.width < 5 or rect.height < 5:
                print(f"  [skip] p{page_num+1}: {cap['text'][:60]} (empty rect)")
                continue

            # Render page region
            pix = page.get_pixmap(matrix=mat, clip=rect)

            # Output filename
            tag = "Table" if cap["type"] == "table" else "Fig"
            fname = f"p{page_num+1:02d}_{tag}{cap['num']}.png"
            out_path = output_dir / fname
            pix.save(str(out_path))
            trim_whitespace_png(out_path)

            results.append({
                "page":    page_num + 1,
                "file":    fname,
                "caption": cap["text"],
                "bbox":    list(rect),
            })
            preview = cap['text'][:70].replace('\n', ' ').encode('ascii', errors='replace').decode()
            print(f"  p{page_num+1}: {fname}  --  {preview}")

    doc.close()
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract figures/tables from an academic PDF.",
    )
    parser.add_argument("pdf", help="Input PDF file")
    parser.add_argument("output_dir", nargs="?", default=None,
                        help="Output directory (default: <pdf>_figures/)")
    parser.add_argument("--dpi", type=int, default=150,
                        help="Render DPI (default: 150)")
    args = parser.parse_args()

    print(f"Input : {args.pdf}")
    print(f"DPI   : {args.dpi}")
    results = extract_figures(args.pdf, args.output_dir, dpi=args.dpi)
    print(f"\nExtracted {len(results)} item(s).")


if __name__ == "__main__":
    main()
