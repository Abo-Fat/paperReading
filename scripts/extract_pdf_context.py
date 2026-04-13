#!/usr/bin/env python3
"""
Extract text and rendered page images from a paper PDF.

Outputs:
1) text_by_page.json
2) rendered page images (if pypdfium2 is available)
3) first_page_title_authors.png (crop from first rendered page)
4) extraction_manifest.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from pypdf import PdfReader

FIG_PATTERN = re.compile(r"\b(?:Fig(?:ure)?\.?\s*\d+[A-Za-z]?)\b", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract paper context from PDF.")
    parser.add_argument("--pdf", required=True, help="Path to input PDF.")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Output directory for JSON and rendered images.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=220,
        help="Render DPI used by pypdfium2 (default: 220).",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="1-based first page to process (default: 1).",
    )
    parser.add_argument(
        "--end-page",
        type=int,
        default=0,
        help="1-based last page to process. 0 means the last page.",
    )
    return parser.parse_args()


def normalize_page_range(total_pages: int, start_page: int, end_page: int) -> Tuple[int, int]:
    if total_pages <= 0:
        raise ValueError("PDF has no pages.")
    start = max(1, start_page)
    end = total_pages if end_page <= 0 else min(total_pages, end_page)
    if start > end:
        raise ValueError(f"Invalid page range: start={start}, end={end}.")
    return start, end


def extract_text_by_page(pdf_path: Path, start_page: int, end_page: int) -> Tuple[List[Dict[str, Any]], int]:
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    start, end = normalize_page_range(total_pages, start_page, end_page)

    records: List[Dict[str, Any]] = []
    for page_idx in range(start - 1, end):
        page_num = page_idx + 1
        text = reader.pages[page_idx].extract_text() or ""
        fig_mentions = sorted(set(FIG_PATTERN.findall(text)))
        records.append(
            {
                "page": page_num,
                "text": text,
                "figure_mentions": fig_mentions,
            }
        )
    return records, total_pages


def render_pages(
    pdf_path: Path,
    render_dir: Path,
    dpi: int,
    start_page: int,
    end_page: int,
) -> Dict[str, Any]:
    try:
        import pypdfium2 as pdfium
    except Exception as exc:  # pragma: no cover - environment dependent
        return {
            "rendered": False,
            "reason": f"pypdfium2 unavailable: {exc}",
            "images": [],
            "first_page_title_authors": None,
        }

    render_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    total_pages = len(doc)
    start, end = normalize_page_range(total_pages, start_page, end_page)
    scale = max(0.2, dpi / 72.0)

    image_paths: List[str] = []
    for page_idx in range(start - 1, end):
        page_num = page_idx + 1
        bitmap = doc[page_idx].render(scale=scale)
        image = bitmap.to_pil()
        image_path = render_dir / f"page_{page_num:04d}.png"
        image.save(image_path)
        image_paths.append(str(image_path.resolve()))

    title_crop = None
    if image_paths:
        try:
            from PIL import Image

            first_page = Path(image_paths[0])
            crop_path = render_dir / "first_page_title_authors.png"
            with Image.open(first_page) as img:
                crop_h = max(1, int(img.height * 0.38))
                cropped = img.crop((0, 0, img.width, crop_h))
                cropped.save(crop_path)
            title_crop = str(crop_path.resolve())
        except Exception:
            title_crop = None

    return {
        "rendered": True,
        "reason": "",
        "images": image_paths,
        "first_page_title_authors": title_crop,
    }


def extract_figures_fitz(
    pdf_path: Path,
    figures_dir: Path,
    dpi: int,
    start_page: int,
    end_page: int,
) -> Dict[str, Any]:
    """Extract individual figures (with captions) from PDF using pymupdf (fitz).

    Strategy
    --------
    1. Use ``page.get_images() + page.get_image_rects()`` for precise image
       placement.  Falls back to ``get_text("dict")`` image blocks if needed.
    2. For each image, locate its caption by looking for a text block *below*
       the image that **starts with "Fig." or "Figure"** (the universal academic
       convention).  Position-only heuristics are unreliable because body-text
       paragraphs occupy the same column as figures.
    3. Once the caption start is found, extend ``cy1`` to include any immediately
       adjacent continuation lines (gap ≤ ``CAP_CONTINUE_GAP`` pts).
    4. Render ``page.get_pixmap(clip=...)`` — captures raster and vector figures.
       Horizontal clip is fixed to the image bbox; only the bottom edge grows.

    Returns a dict with keys:
        extracted (bool), reason (str), figures_by_page (dict[str, list[str]])
    """
    try:
        import fitz  # pymupdf
    except Exception as exc:  # pragma: no cover - environment dependent
        return {
            "extracted": False,
            "reason": f"pymupdf unavailable: {exc}",
            "figures_by_page": {},
        }

    # Caption is recognised only when text starts with "Fig." / "Figure N"
    FIG_CAPTION_RE = re.compile(
        r"^\s*fig\.?\s*\d+|^\s*figure\s*\d+", re.IGNORECASE | re.MULTILINE
    )
    CAP_SEARCH_MARGIN = 80  # max PDF pts below image to search for "Fig." label
    CAP_CONTINUE_GAP = 8    # max vertical gap (pts) to continue a caption block
    H_SLACK = 15            # caption tx0 allowed within [bx0-slack, bx1+slack]
    PAD = 8                 # padding around clip rect (PDF pts)
    MIN_DIM = 60            # minimum rendered size (px) on each side

    figures_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    start, end = normalize_page_range(total_pages, start_page, end_page)
    scale = max(0.2, dpi / 72.0)
    mat = fitz.Matrix(scale, scale)

    figures_by_page: Dict[str, List[str]] = {}
    for page_idx in range(start - 1, end):
        page_num = page_idx + 1
        page = doc[page_idx]
        page_rect = page.rect

        # ── text blocks with content, sorted top-to-bottom ───────────────────
        # get_text("blocks") returns (x0,y0,x1,y1,text,block_no,block_type)
        raw = page.get_text("blocks")
        text_blocks: List[Tuple[float, float, float, float, str]] = sorted(
            [(b[0], b[1], b[2], b[3], b[4]) for b in raw if b[6] == 0],
            key=lambda t: t[1],
        )

        # ── image positions via xref → rect ──────────────────────────────────
        img_rects: List[Tuple[float, float, float, float]] = []
        seen: set = set()
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen:
                continue
            seen.add(xref)
            for r in page.get_image_rects(xref):
                if r.width > 30 and r.height > 30:
                    img_rects.append((r.x0, r.y0, r.x1, r.y1))

        # Fallback: image blocks from get_text("dict")
        if not img_rects:
            dict_blocks = page.get_text("dict")["blocks"]
            img_rects = [
                tuple(b["bbox"])
                for b in dict_blocks
                if b.get("type") == 1
                and (b["bbox"][2] - b["bbox"][0]) > 30
                and (b["bbox"][3] - b["bbox"][1]) > 30
            ]

        if not img_rects:
            continue

        page_figs: List[str] = []
        for fig_idx, (bx0, by0, bx1, by1) in enumerate(img_rects):
            if (bx1 - bx0) < 30 or (by1 - by0) < 30:
                continue

            # ── find caption by "Fig." keyword, then extend for continuation ──
            cy1 = by1
            caption_found = False

            for (tx0, ty0, tx1, ty1, text) in text_blocks:
                if ty0 < by1 - 5:
                    continue  # above or overlapping image bottom — skip

                if not caption_found:
                    if ty0 > by1 + CAP_SEARCH_MARGIN:
                        break  # too far below without finding "Fig." — give up
                    # Horizontal check: caption must start within image's column
                    if tx0 < bx0 - H_SLACK or tx0 > bx1 + H_SLACK:
                        continue
                    if FIG_CAPTION_RE.search(text):
                        caption_found = True
                        cy1 = max(cy1, ty1)
                else:
                    # Continue only if the next block is immediately adjacent
                    if ty0 <= cy1 + CAP_CONTINUE_GAP:
                        cy1 = max(cy1, ty1)
                    else:
                        break  # gap too large — caption has ended

            # ── clip: horizontal fixed to image bbox, bottom extends to cy1 ──
            clip = fitz.Rect(
                max(page_rect.x0, bx0 - PAD),
                max(page_rect.y0, by0 - PAD),
                min(page_rect.x1, bx1 + PAD),
                min(page_rect.y1, cy1 + PAD),
            )

            pix = page.get_pixmap(matrix=mat, clip=clip)
            if pix.width < MIN_DIM or pix.height < MIN_DIM:
                continue

            fig_path = figures_dir / f"fig_p{page_num:04d}_{fig_idx:02d}.png"
            pix.save(str(fig_path))
            page_figs.append(str(fig_path.resolve()))

        if page_figs:
            figures_by_page[str(page_num)] = page_figs

    return {
        "extracted": True,
        "reason": "",
        "figures_by_page": figures_by_page,
    }


def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    text_records, total_pages = extract_text_by_page(
        pdf_path=pdf_path,
        start_page=args.start_page,
        end_page=args.end_page,
    )

    text_json_path = out_dir / "text_by_page.json"
    with text_json_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "pdf_path": str(pdf_path),
                "total_pages": total_pages,
                "start_page": text_records[0]["page"] if text_records else None,
                "end_page": text_records[-1]["page"] if text_records else None,
                "pages": text_records,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    render_result = render_pages(
        pdf_path=pdf_path,
        render_dir=out_dir / "rendered_pages",
        dpi=args.dpi,
        start_page=args.start_page,
        end_page=args.end_page,
    )

    figure_result = extract_figures_fitz(
        pdf_path=pdf_path,
        figures_dir=out_dir / "rendered_pages" / "figures",
        dpi=args.dpi,
        start_page=args.start_page,
        end_page=args.end_page,
    )

    manifest_path = out_dir / "extraction_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "pdf_path": str(pdf_path),
                "text_json": str(text_json_path.resolve()),
                "rendered": render_result["rendered"],
                "render_reason": render_result["reason"],
                "rendered_images": render_result["images"],
                "first_page_title_authors": render_result["first_page_title_authors"],
                "figures_extracted": figure_result["extracted"],
                "figures_reason": figure_result["reason"],
                "figures_by_page": figure_result["figures_by_page"],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"[OK] text json: {text_json_path}")
    print(f"[OK] manifest : {manifest_path}")
    if render_result["rendered"]:
        print(f"[OK] rendered pages: {len(render_result['images'])}")
    else:
        print(f"[WARN] pages not rendered: {render_result['reason']}")
    if figure_result["extracted"]:
        total_figs = sum(len(v) for v in figure_result["figures_by_page"].values())
        print(f"[OK] individual figures extracted: {total_figs} across {len(figure_result['figures_by_page'])} pages")
    else:
        print(f"[WARN] figures not extracted: {figure_result['reason']}")


if __name__ == "__main__":
    main()

