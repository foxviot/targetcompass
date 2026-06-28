from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


OCR_SCHEMA = "v4.ocr_result/0.1"


def paddleocr_available() -> bool:
    try:
        _prepare_paddle_runtime_flags()
        _prepare_windows_dll_paths()
        import paddle  # noqa: F401
        import paddleocr  # noqa: F401
        import pypdfium2  # noqa: F401
    except Exception:
        return False
    return True


def ocr_pdf_with_paddle(path: Path, out_dir: Path, max_pages: int = 3, lang: str = "en") -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not paddleocr_available():
        raise RuntimeError("PaddleOCR backend is not installed or not importable")

    _prepare_paddle_runtime_flags()
    _prepare_windows_dll_paths()
    import pypdfium2 as pdfium  # type: ignore
    from paddleocr import PaddleOCR  # type: ignore

    ocr = _build_ocr(lang)
    pdf = pdfium.PdfDocument(str(path))
    page_count = min(len(pdf), max(1, int(max_pages or 3)))
    page_items = []
    texts = []
    image_dir = out_dir / "ocr_pages"
    image_dir.mkdir(exist_ok=True)
    for index in range(page_count):
        page = pdf[index]
        bitmap = page.render(scale=2.0)
        image_path = image_dir / f"{path.stem}_page_{index + 1:03d}.png"
        bitmap.to_pil().save(image_path)
        lines = _recognize_image(ocr, image_path)
        texts.extend(line.get("text", "") for line in lines)
        page_items.append(
            {
                "page": index + 1,
                "image_path": str(image_path),
                "line_count": len(lines),
                "lines": lines,
            }
        )
    result = {
        "schema_version": OCR_SCHEMA,
        "backend": "paddleocr",
        "source_pdf": str(path),
        "page_count": page_count,
        "text": "\n".join(text for text in texts if text).strip(),
        "pages": page_items,
    }
    result_path = out_dir / f"{path.stem}_paddleocr.json"
    result["artifact_path"] = str(result_path)
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def _build_ocr(lang: str):
    _prepare_paddle_runtime_flags()
    _prepare_windows_dll_paths()
    from paddleocr import PaddleOCR  # type: ignore

    try:
        return PaddleOCR(
            lang=lang,
            ocr_version="PP-OCRv4",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
            device="cpu",
        )
    except TypeError:
        return PaddleOCR(lang=lang, use_angle_cls=False, enable_mkldnn=False)


def _recognize_image(ocr: Any, image_path: Path) -> list[dict[str, Any]]:
    if hasattr(ocr, "predict"):
        result = ocr.predict(str(image_path))
    else:
        result = ocr.ocr(str(image_path), cls=False)
    return _flatten_result(result)


def _flatten_result(result: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    if not result:
        return lines
    for item in result if isinstance(result, list) else [result]:
        if isinstance(item, dict):
            rec_texts = item.get("rec_texts") or []
            rec_scores = item.get("rec_scores") or []
            for idx, text in enumerate(rec_texts):
                lines.append({"text": str(text), "score": _score_at(rec_scores, idx), "bbox": ""})
            continue
        if isinstance(item, list):
            for child in item:
                if isinstance(child, list) and len(child) >= 2:
                    text = ""
                    score = None
                    if isinstance(child[1], (list, tuple)) and child[1]:
                        text = str(child[1][0])
                        score = child[1][1] if len(child[1]) > 1 else None
                    elif isinstance(child[1], str):
                        text = child[1]
                    if text:
                        lines.append({"text": text, "score": score, "bbox": child[0] if child else ""})
    return lines


def _score_at(values: Any, idx: int) -> float | None:
    try:
        return float(values[idx])
    except Exception:
        return None


def _prepare_windows_dll_paths() -> None:
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    try:
        import torch  # type: ignore

        lib_dir = Path(torch.__file__).resolve().parent / "lib"
        if lib_dir.exists():
            os.add_dll_directory(str(lib_dir))
    except Exception:
        return


def _prepare_paddle_runtime_flags() -> None:
    os.environ.setdefault("FLAGS_use_mkldnn", "false")
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
