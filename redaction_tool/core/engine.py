"""
RedactionEngine: dispatches files to the correct redactor based on extension,
manages output paths, and aggregates audit entries.
"""

from pathlib import Path
from typing import Callable

SUPPORTED_EXTENSIONS = {
    ".pdf":  "pdf",
    ".docx": "word",
    ".doc":  "word",
    ".xlsx": "excel",
    ".xls":  "excel",
    ".jpg":  "image",
    ".jpeg": "image",
    ".png":  "image",
    ".tiff": "image",
    ".tif":  "image",
    ".bmp":  "image",
}


class RedactionEngine:
    def __init__(self):
        self._redactors = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def process_files(
        self,
        files: list,
        patterns: list,
        custom_terms: list,
        replacement: str,
        case_sensitive: bool,
        output_dir: str | None = None,
        progress_callback: Callable | None = None,
    ) -> list:
        """
        Process all files. Returns a flat list of audit entry dicts:
        {File, Page, Category, Original Term, Count}
        """
        all_audit = []

        for i, file_path in enumerate(files):
            if progress_callback:
                progress_callback(i, len(files), Path(file_path).name)

            ext = Path(file_path).suffix.lower()
            file_type = SUPPORTED_EXTENSIONS.get(ext)

            if not file_type:
                continue

            out_path = self._make_output_path(file_path, ext, output_dir)

            try:
                fn = self._get_redactor(file_type)
                count, entries = fn(
                    file_path, out_path, patterns, custom_terms,
                    replacement, case_sensitive,
                )
                all_audit.extend(entries)
            except Exception as exc:
                all_audit.append({
                    "File": Path(file_path).name,
                    "Page": "—",
                    "Category": "ERROR",
                    "Original Term": str(exc)[:80],
                    "Count": 0,
                })

        if progress_callback:
            progress_callback(len(files), len(files), "Done")

        return all_audit

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_redactor(self, file_type: str):
        if file_type not in self._redactors:
            if file_type == "pdf":
                from redactors.pdf_redactor import redact_pdf
                self._redactors["pdf"] = redact_pdf
            elif file_type == "word":
                from redactors.word_redactor import redact_word
                self._redactors["word"] = redact_word
            elif file_type == "excel":
                from redactors.excel_redactor import redact_excel
                self._redactors["excel"] = redact_excel
            elif file_type == "image":
                from redactors.image_redactor import redact_image
                self._redactors["image"] = redact_image
        return self._redactors[file_type]

    @staticmethod
    def _make_output_path(file_path: str, ext: str, output_dir: str | None) -> str:
        p = Path(file_path)
        if output_dir:
            out_dir = Path(output_dir)
        else:
            out_dir = p.parent / "_redacted"
        out_dir.mkdir(parents=True, exist_ok=True)
        return str(out_dir / f"{p.stem}_redacted{ext}")
