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
    parser.add_argument(
        "--figure-extractor",
        choices=["auto", "legacy", "iedm"],
        default="auto",
        help="Figure extraction mode: auto (default), legacy, or iedm.",
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
    """Extract individual figures (with captions) from PDF using pymupdf (fitz)."""
    try:
        import fitz  # pymupdf
    except Exception as exc:  # pragma: no cover - environment dependent
        return {
            "extracted": False,
            "reason": f"pymupdf unavailable: {exc}",
            "figures_by_page": {},
        }

    fig_caption_re = re.compile(
        r"^\s*fig\.?\s*\d+|^\s*figure\s*\d+", re.IGNORECASE | re.MULTILINE
    )
    cap_search_margin = 80
    cap_continue_gap = 8
    h_slack = 15
    pad = 8
    min_dim = 60

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

        raw = page.get_text("blocks")
        text_blocks: List[Tuple[float, float, float, float, str]] = sorted(
            [(b[0], b[1], b[2], b[3], b[4]) for b in raw if b[6] == 0],
            key=lambda t: t[1],
        )

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

            cy1 = by1
            caption_found = False

            for (tx0, ty0, tx1, ty1, text) in text_blocks:
                if ty0 < by1 - 5:
                    continue

                if not caption_found:
                    if ty0 > by1 + cap_search_margin:
                        break
                    if tx0 < bx0 - h_slack or tx0 > bx1 + h_slack:
                        continue
                    if fig_caption_re.search(text):
                        caption_found = True
                        cy1 = max(cy1, ty1)
                else:
                    if ty0 <= cy1 + cap_continue_gap:
                        cy1 = max(cy1, ty1)
                    else:
                        break

            clip = fitz.Rect(
                max(page_rect.x0, bx0 - pad),
                max(page_rect.y0, by0 - pad),
                min(page_rect.x1, bx1 + pad),
                min(page_rect.y1, cy1 + pad),
            )

            pix = page.get_pixmap(matrix=mat, clip=clip)
            if pix.width < min_dim or pix.height < min_dim:
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


def detect_iedm_layout(
    pdf_path: Path,
    start_page: int,
    end_page: int,
) -> Dict[str, Any]:
    """Heuristic detector for IEDM-like layout:
    - early pages are text-heavy with no figure captions
    - dedicated figure pages contain many `Fig.` captions and dense graphics
    """
    try:
        import fitz  # pymupdf
    except Exception as exc:  # pragma: no cover - environment dependent
        return {
            "is_iedm_layout": False,
            "reason": f"pymupdf unavailable: {exc}",
            "page_stats": [],
        }

    fig_caption_re = re.compile(r"^\s*fig\.?\s*\d+|^\s*figure\s*\d+", re.IGNORECASE)
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    start, end = normalize_page_range(total_pages, start_page, end_page)

    stats: List[Dict[str, Any]] = []
    figure_like_pages: List[int] = []
    prefix_text_pages = 0
    for page_idx in range(start - 1, end):
        page_num = page_idx + 1
        page = doc[page_idx]
        blocks = page.get_text("blocks")
        text_blocks = [b for b in blocks if b[6] == 0 and (b[4] or "").strip()]
        caption_blocks = [
            b for b in text_blocks if fig_caption_re.search((b[4] or "").strip())
        ]
        image_count = len(page.get_images(full=True))
        drawing_count = len(page.get_drawings())
        # IEDM/VLSI figure pages often have many "Fig" captions even if the
        # absolute count of image/drawing objects is moderate.
        is_figure_like = (
            (len(caption_blocks) >= 8 and (image_count >= 8 or drawing_count >= 150))
            or len(caption_blocks) >= 12
        )
        if is_figure_like:
            figure_like_pages.append(page_num)
        if (page_num - start) < 2 and len(caption_blocks) == 0 and image_count == 0:
            prefix_text_pages += 1

        stats.append(
            {
                "page": page_num,
                "caption_blocks": len(caption_blocks),
                "images": image_count,
                "drawings": drawing_count,
                "is_figure_like": is_figure_like,
            }
        )

    is_iedm_layout = len(figure_like_pages) >= 1 and prefix_text_pages >= 1
    reason = (
        f"figure_like_pages={figure_like_pages}, prefix_text_pages={prefix_text_pages}"
    )
    return {
        "is_iedm_layout": is_iedm_layout,
        "reason": reason,
        "page_stats": stats,
    }


def extract_figures_iedm_layout(
    pdf_path: Path,
    figures_dir: Path,
    dpi: int,
    start_page: int,
    end_page: int,
) -> Dict[str, Any]:
    """Extract figures for IEDM-like pages where captions and graphics are separated.

    Instead of treating each PDF image object as one figure, this logic:
    1) anchors on caption blocks starting with "Fig."
    2) defines figure regions by column and vertical intervals between captions
    3) keeps only regions that contain sufficient graphical primitives
    """
    try:
        import fitz  # pymupdf
    except Exception as exc:  # pragma: no cover - environment dependent
        return {
            "extracted": False,
            "reason": f"pymupdf unavailable: {exc}",
            "figures_by_page": {},
        }

    fig_caption_re = re.compile(r"^\s*fig\.?\s*\d+|^\s*figure\s*\d+", re.IGNORECASE)
    scale = max(0.2, dpi / 72.0)
    mat = fitz.Matrix(scale, scale)

    figures_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    start, end = normalize_page_range(total_pages, start_page, end_page)

    figures_by_page: Dict[str, List[str]] = {}
    for page_idx in range(start - 1, end):
        page_num = page_idx + 1
        page = doc[page_idx]
        page_rect = page.rect
        mid_x = page_rect.x0 + page_rect.width / 2.0

        blocks = page.get_text("blocks")
        caption_blocks: List[Tuple[float, float, float, float, str]] = []
        for b in blocks:
            if b[6] != 0:
                continue
            text = (b[4] or "").strip()
            if not text:
                continue
            if fig_caption_re.search(text):
                caption_blocks.append((b[0], b[1], b[2], b[3], text))
        if not caption_blocks:
            continue

        graphic_rects: List[Tuple[float, float, float, float]] = []
        seen_xref: set = set()
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen_xref:
                continue
            seen_xref.add(xref)
            for r in page.get_image_rects(xref):
                if r.width > 5 and r.height > 5:
                    graphic_rects.append((r.x0, r.y0, r.x1, r.y1))
        for d in page.get_drawings():
            r = d.get("rect")
            if not r:
                continue
            if r.width > 5 and r.height > 5:
                graphic_rects.append((r.x0, r.y0, r.x1, r.y1))

        if not graphic_rects:
            continue

        page_figs: List[str] = []
        saved_fingerprints: set = set()
        pad = 8
        min_region_pt = 40
        row_merge_gap = 90
        min_graphics_area = 900

        # 1) Build rows by caption y. IEDM/VLSI figure pages are usually row-aligned.
        caps_sorted_y = sorted(caption_blocks, key=lambda t: (t[1], t[0]))
        rows: List[List[Tuple[float, float, float, float, str]]] = []
        for cap in caps_sorted_y:
            if not rows:
                rows.append([cap])
                continue
            last_row = rows[-1]
            row_anchor_y = sum(c[1] for c in last_row) / len(last_row)
            if abs(cap[1] - row_anchor_y) <= row_merge_gap:
                last_row.append(cap)
            else:
                rows.append([cap])

        # 2) Row-wise region: from previous row caption bottom to this row caption bottom.
        row_bounds: List[Tuple[float, float, List[Tuple[float, float, float, float, str]]]] = []
        prev_row_bottom = page_rect.y0
        for row in rows:
            row_bottom = max(c[3] for c in row)
            y0 = max(page_rect.y0, prev_row_bottom + 2)
            y1 = min(page_rect.y1, row_bottom + 4)
            if y1 - y0 >= min_region_pt:
                row_bounds.append((y0, y1, row))
            prev_row_bottom = row_bottom

        # 3) Column-wise split in each row by caption x-centers.
        expected_slots = sum(len(r) for _, _, r in row_bounds)
        avg_graphics_per_slot = (
            float(len(graphic_rects)) / float(max(1, expected_slots))
        )
        # Adaptive filter: dense vector-heavy pages and sparse bitmap pages both work.
        min_graphics_in_region = max(1, min(4, int(round(avg_graphics_per_slot * 0.6))))

        for y0, y1, row in row_bounds:
            row_sorted_x = sorted(row, key=lambda t: (t[0] + t[2]) / 2.0)
            centers = [((c[0] + c[2]) / 2.0) for c in row_sorted_x]

            x_ranges: List[Tuple[float, float]] = []
            if len(row_sorted_x) >= 2:
                for i, cx in enumerate(centers):
                    left_bound = page_rect.x0 if i == 0 else (centers[i - 1] + cx) / 2.0
                    right_bound = page_rect.x1 if i == len(centers) - 1 else (cx + centers[i + 1]) / 2.0
                    x_ranges.append((left_bound, right_bound))
            else:
                cx = centers[0]
                if abs(cx - mid_x) <= 40:
                    x_ranges.append((page_rect.x0, page_rect.x1))
                elif cx < mid_x:
                    x_ranges.append((page_rect.x0, mid_x))
                else:
                    x_ranges.append((mid_x, page_rect.x1))

            for rx0, rx1 in x_ranges:
                rx0 = max(page_rect.x0, rx0 + 2)
                rx1 = min(page_rect.x1, rx1 - 2)
                if rx1 - rx0 < min_region_pt:
                    continue

                # Keep only regions with enough graphics area/instances.
                hits = 0
                covered_area = 0.0
                for gx0, gy0, gx1, gy1 in graphic_rects:
                    ix0 = max(rx0, gx0)
                    iy0 = max(y0, gy0)
                    ix1 = min(rx1, gx1)
                    iy1 = min(y1, gy1)
                    if ix1 <= ix0 or iy1 <= iy0:
                        continue
                    hits += 1
                    covered_area += (ix1 - ix0) * (iy1 - iy0)
                if hits < min_graphics_in_region or covered_area < min_graphics_area:
                    continue

                clip = fitz.Rect(
                    max(page_rect.x0, rx0 - pad),
                    max(page_rect.y0, y0 - pad),
                    min(page_rect.x1, rx1 + pad),
                    min(page_rect.y1, y1 + pad),
                )
                if clip.width < min_region_pt or clip.height < min_region_pt:
                    continue

                fp = (
                    round(clip.x0, 1),
                    round(clip.y0, 1),
                    round(clip.x1, 1),
                    round(clip.y1, 1),
                )
                if fp in saved_fingerprints:
                    continue
                saved_fingerprints.add(fp)

                pix = page.get_pixmap(matrix=mat, clip=clip)
                if pix.width < 120 or pix.height < 120:
                    continue
                fig_path = figures_dir / f"fig_p{page_num:04d}_{len(page_figs):02d}.png"
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

    layout_probe = detect_iedm_layout(
        pdf_path=pdf_path,
        start_page=args.start_page,
        end_page=args.end_page,
    )

    selected_extractor = args.figure_extractor
    if selected_extractor == "auto":
        selected_extractor = "iedm" if layout_probe["is_iedm_layout"] else "legacy"

    if selected_extractor == "iedm":
        figure_result = extract_figures_iedm_layout(
            pdf_path=pdf_path,
            figures_dir=out_dir / "rendered_pages" / "figures",
            dpi=args.dpi,
            start_page=args.start_page,
            end_page=args.end_page,
        )
    else:
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
                "figure_extractor_requested": args.figure_extractor,
                "figure_extractor_selected": selected_extractor,
                "layout_probe": layout_probe,
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
        print(f"[OK] figure extractor selected: {selected_extractor}")
        print(f"[OK] individual figures extracted: {total_figs} across {len(figure_result['figures_by_page'])} pages")
    else:
        print(f"[WARN] figures not extracted: {figure_result['reason']}")


if __name__ == "__main__":
    main()



