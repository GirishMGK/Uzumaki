"""
Image Redactor — uses pytesseract (OCR) + Pillow.

Process:
1. OCR the image with pytesseract to get word-level bounding boxes.
2. Match each word (and adjacent word groups for multi-word keywords) against patterns.
3. Draw a white filled rectangle over matched words, then draw the replacement label.

NOTE: Tesseract OCR must be installed on the system.
  Windows: https://github.com/UB-Mannheim/tesseract/wiki
  Set pytesseract.pytesseract.tesseract_cmd if not on PATH.
"""

import re
from pathlib import Path


def redact_image(
    input_path: str,
    output_path: str,
    patterns: list,
    custom_terms: list,
    replacement: str = "[REDACTED]",
    case_sensitive: bool = False,
) -> tuple:
    """Returns (total_count, audit_entries)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise ImportError(
            "Pillow is required for image redaction.\n"
            "Install with:  pip install Pillow"
        )
    try:
        import pytesseract
    except ImportError:
        raise ImportError(
            "pytesseract is required for image redaction.\n"
            "Install with:  pip install pytesseract\n"
            "Also install Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki"
        )

    flags = 0 if case_sensitive else re.IGNORECASE

    compiled = []
    for regex, label in patterns:
        try:
            compiled.append((re.compile(regex, flags), label))
        except re.error:
            pass
    for kw in custom_terms:
        kw = kw.strip()
        if kw:
            try:
                compiled.append((re.compile(re.escape(kw), flags), "Custom Keyword"))
            except re.error:
                pass

    img = Image.open(input_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    try:
        data = pytesseract.image_to_data(
            img,
            output_type=pytesseract.Output.DICT,
            config="--psm 6",
        )
    except Exception as exc:
        raise RuntimeError(
            f"Tesseract OCR failed: {exc}\n"
            "Ensure Tesseract is installed and on PATH (or set pytesseract.pytesseract.tesseract_cmd)."
        )

    n_boxes = len(data["text"])
    total = 0
    audit = []
    file_name = Path(input_path).name

    # Build list of (index, word) for grouping adjacent words
    words = [(i, data["text"][i]) for i in range(n_boxes) if data["text"][i].strip()]

    # ── Word-level matching ───────────────────────────────────────────────────
    redacted_indices: set[int] = set()

    for idx, word in words:
        if idx in redacted_indices:
            continue
        for pat, label in compiled:
            if pat.fullmatch(word):
                _draw_redaction(draw, data, idx, replacement)
                redacted_indices.add(idx)
                total += 1
                audit.append({
                    "File": file_name,
                    "Page": "Image",
                    "Category": label,
                    "Original Term": (word[:45] + "…") if len(word) > 45 else word,
                    "Count": 1,
                })
                break

    # ── Multi-word keyword matching (sliding window up to 6 tokens) ───────────
    multi_kw = [
        (pat, label) for pat, label in compiled
        if label == "Custom Keyword" and " " in pat.pattern
    ]
    if multi_kw:
        for window in range(2, 7):
            for start in range(len(words) - window + 1):
                group_indices = [words[start + j][0] for j in range(window)]
                if any(gi in redacted_indices for gi in group_indices):
                    continue
                phrase = " ".join(words[start + j][1] for j in range(window))
                for pat, label in multi_kw:
                    if pat.search(phrase):
                        for gi in group_indices:
                            _draw_redaction(draw, data, gi, replacement if gi == group_indices[0] else "")
                            redacted_indices.add(gi)
                        total += 1
                        audit.append({
                            "File": file_name,
                            "Page": "Image",
                            "Category": label,
                            "Original Term": (phrase[:45] + "…") if len(phrase) > 45 else phrase,
                            "Count": 1,
                        })
                        break

    img.save(output_path)
    return total, audit


def _draw_redaction(draw, data: dict, idx: int, label: str):
    """Draw a white box with optional label text over a word bounding box."""
    from PIL import ImageFont
    x = data["left"][idx]
    y = data["top"][idx]
    w = data["width"][idx]
    h = data["height"][idx]
    pad = 2
    draw.rectangle([x - pad, y - pad, x + w + pad, y + h + pad], fill="white", outline="white")
    if label:
        font_size = max(8, h - 2)
        font = None
        for fname in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(fname, font_size)
                break
            except Exception:
                pass
        if font is None:
            font = ImageFont.load_default()
        draw.text((x, y), label[:20], fill=(80, 80, 80), font=font)
