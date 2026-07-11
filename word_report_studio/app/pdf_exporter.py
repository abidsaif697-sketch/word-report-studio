"""
pdf_exporter.py
-----------------
Converts generated .docx files to .pdf entirely offline using a locally
installed LibreOffice (headless mode). No network calls, no cloud
converters. If LibreOffice isn't installed, callers get a clear error they
can show the user instead of a silent failure.
"""

from __future__ import annotations
import os
import shutil
import subprocess
from typing import Dict, List, Optional

CANDIDATE_PATHS = [
    # Windows
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    # macOS
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    # Linux
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/snap/bin/libreoffice",
]


class PDFExportError(RuntimeError):
    pass


def find_soffice() -> Optional[str]:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for path in CANDIDATE_PATHS:
        if os.path.isfile(path):
            return path
    return None


def is_available() -> bool:
    return find_soffice() is not None


def convert_to_pdf(docx_path: str, output_dir: Optional[str] = None, timeout: int = 120) -> str:
    """Convert a single .docx to .pdf. Returns the resulting pdf path."""
    soffice = find_soffice()
    if not soffice:
        raise PDFExportError(
            "LibreOffice was not found on this machine. Install it (free, offline installer) "
            "to enable PDF preview/export: https://www.libreoffice.org/download/ "
            "The .docx files are still generated and fully usable without it."
        )
    if not os.path.isfile(docx_path):
        raise PDFExportError(f"File not found: {docx_path}")

    out_dir = output_dir or os.path.dirname(docx_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    cmd = [soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", out_dir, docx_path]
    try:
        subprocess.run(cmd, check=True, timeout=timeout, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise PDFExportError(f"LibreOffice failed to convert {docx_path}: {e.stderr.decode(errors='ignore')}")
    except subprocess.TimeoutExpired:
        raise PDFExportError(f"LibreOffice timed out converting {docx_path}")

    pdf_path = os.path.join(out_dir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
    if not os.path.exists(pdf_path):
        raise PDFExportError(f"Conversion reported success but no PDF was produced for {docx_path}")
    return pdf_path


def convert_batch(docx_paths: List[str], output_dir: Optional[str] = None) -> Dict[str, str]:
    """Returns {docx_path: pdf_path_or_error_message}."""
    results: Dict[str, str] = {}
    for path in docx_paths:
        try:
            results[path] = convert_to_pdf(path, output_dir=output_dir)
        except PDFExportError as e:
            results[path] = f"ERROR: {e}"
    return results
