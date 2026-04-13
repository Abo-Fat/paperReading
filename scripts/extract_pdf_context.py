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


if __name__ == "__main__":
    main()

