#!/usr/bin/env python3
"""
Extract text and rendered page images from a paper PDF.

Figure extraction is now unified to a pdffigures2-style JSON adapter.

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


def empty_figure_result(extracted: bool = True, reason: str = "") -> Dict[str, Any]:
    return {
        "extracted": extracted,
        "reason": reason,
        "figures_by_page": {},
        "figures_meta_by_page": {},
    }


def count_extracted_figures(result: Dict[str, Any]) -> int:
    return sum(len(v) for v in result.get("figures_by_page", {}).values())


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
        "--pdffigures2-json",
        default="",
        help=(
            "Path to pdffigures2 JSON output. "
            "If empty, tries <out-dir>/pdffigures2/figures.json."
        ),
    )
    parser.add_argument(
        "--pdffigures2-fig-root",
        default="",
        help=(
            "Optional root directory for figure image paths referenced in "
            "pdffigures2 JSON. Useful when JSON stores relative paths."
        ),
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


def _pick_first_nonempty_str(record: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        val = record.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _resolve_pdffigures2_image_path(
    raw_path: str,
    json_path: Path,
    fig_root: Path | None,
) -> str:
    p = Path(raw_path)
    if p.is_absolute():
        return str(p.resolve())
    if fig_root is not None:
        return str((fig_root / p).resolve())
    return str((json_path.parent / p).resolve())


def extract_figures_pdffigures2(
    pdf_path: Path,
    json_path: Path,
    start_page: int,
    end_page: int,
    fig_root: Path | None = None,
) -> Dict[str, Any]:
    """Adapt pdffigures2-style outputs into the paperReading manifest shape.

    Expected JSON shape:
    - list[dict] or {"figures": list[dict]}
    Supported per-figure keys (best effort):
    - page/pageNumber/pageno
    - path/imagePath/figPath/renderURL
    - caption/name/figureType/regionBoundary/captionBoundary
    """
    if not json_path.exists():
        return empty_figure_result(
            extracted=False,
            reason=f"pdffigures2 json not found: {json_path}",
        )

    try:
        with json_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:  # pragma: no cover - file/content dependent
        return empty_figure_result(
            extracted=False,
            reason=f"failed to parse pdffigures2 json: {exc}",
        )

    rows: List[Dict[str, Any]]
    if isinstance(payload, list):
        rows = [r for r in payload if isinstance(r, dict)]
    elif isinstance(payload, dict) and isinstance(payload.get("figures"), list):
        rows = [r for r in payload["figures"] if isinstance(r, dict)]
    else:
        return empty_figure_result(
            extracted=False,
            reason="unsupported pdffigures2 json format (need list or {'figures': [...]})",
        )

    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    start, end = normalize_page_range(total_pages, start_page, end_page)

    figures_by_page: Dict[str, List[str]] = {}
    figures_meta_by_page: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        raw_page = row.get("page", row.get("pageNumber", row.get("pageno")))
        if raw_page is None:
            continue
        try:
            page_num = int(raw_page)
        except Exception:
            continue

        # Some pdffigures2 dumps use 0-based page index.
        if 0 <= page_num < total_pages and page_num < start:
            page_num += 1
        if page_num < start or page_num > end:
            continue

        img_rel = _pick_first_nonempty_str(
            row,
            ["path", "imagePath", "figPath", "renderURL"],
        )
        if not img_rel:
            continue

        img_path = _resolve_pdffigures2_image_path(
            raw_path=img_rel,
            json_path=json_path,
            fig_root=fig_root,
        )
        if not Path(img_path).exists():
            continue

        page_key = str(page_num)
        figures_by_page.setdefault(page_key, []).append(img_path)
        figures_meta_by_page.setdefault(page_key, []).append(
            {
                "source": "pdffigures2",
                "caption": _pick_first_nonempty_str(row, ["caption"]),
                "name": _pick_first_nonempty_str(row, ["name"]),
                "figure_type": _pick_first_nonempty_str(row, ["figureType"]),
                "region_boundary": row.get("regionBoundary"),
                "caption_boundary": row.get("captionBoundary"),
            }
        )

    if not figures_by_page:
        return empty_figure_result(
            extracted=False,
            reason="no usable figures found in pdffigures2 output",
        )

    return {
        "extracted": True,
        "reason": "",
        "figures_by_page": figures_by_page,
        "figures_meta_by_page": figures_meta_by_page,
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

    pdffigures2_json = (
        Path(args.pdffigures2_json).resolve()
        if args.pdffigures2_json
        else (out_dir / "pdffigures2" / "figures.json").resolve()
    )
    pdffigures2_root = (
        Path(args.pdffigures2_fig_root).resolve()
        if args.pdffigures2_fig_root
        else None
    )
    figure_result = extract_figures_pdffigures2(
        pdf_path=pdf_path,
        json_path=pdffigures2_json,
        start_page=args.start_page,
        end_page=args.end_page,
        fig_root=pdffigures2_root,
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
                "figures_meta_by_page": figure_result["figures_meta_by_page"],
                "figure_extractor_selected": "pdffigures2",
                "pdffigures2_json": str(pdffigures2_json),
                "pdffigures2_fig_root": str(pdffigures2_root) if pdffigures2_root else "",
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
        total_figs = count_extracted_figures(figure_result)
        print("[OK] figure extractor selected: pdffigures2")
        print(
            "[OK] individual figures extracted: "
            f"{total_figs} across {len(figure_result['figures_by_page'])} pages"
        )
    else:
        print(f"[WARN] figures not extracted: {figure_result['reason']}")


if __name__ == "__main__":
    main()

