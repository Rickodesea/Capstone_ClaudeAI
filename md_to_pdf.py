"""
md_to_pdf.py
─────────────
Convert a Markdown file to PDF.

Usage:
    python md_to_pdf.py <input.md> [output.pdf]

If output path is omitted, the PDF is written next to the input file
with the same stem (e.g. doc.md → doc.pdf).

Dependencies (install once):
    pip install markdown weasyprint

WeasyPrint on Windows may also need the GTK runtime:
    https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer
If GTK installation is inconvenient, see the pdfkit alternative at the bottom.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path


# ── CSS applied to the generated PDF ──────────────────────────────────────────

_CSS = """
@page {
    size: A4;
    margin: 2.5cm 2.5cm 2.5cm 2.5cm;
}

body {
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1a1a1a;
}

h1 { font-size: 20pt; margin-top: 1.4em; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 16pt; margin-top: 1.2em; border-bottom: 1px solid #aaa; padding-bottom: 2px; }
h3 { font-size: 13pt; margin-top: 1em; }
h4 { font-size: 11pt; margin-top: 0.8em; }

p  { margin: 0.5em 0; }

code {
    font-family: "Consolas", "Courier New", monospace;
    font-size: 9.5pt;
    background: #f4f4f4;
    padding: 1px 4px;
    border-radius: 3px;
}

pre {
    background: #f4f4f4;
    border: 1px solid #ddd;
    border-radius: 4px;
    padding: 10px 14px;
    overflow-x: auto;
    font-size: 8.5pt;
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-all;
}

pre code {
    background: none;
    padding: 0;
    font-size: inherit;
}

blockquote {
    border-left: 4px solid #4a90d9;
    margin: 0.8em 0;
    padding: 0.4em 1em;
    background: #f0f6ff;
    color: #333;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 0.8em 0;
    font-size: 10pt;
}

th {
    background: #2c3e50;
    color: white;
    padding: 7px 10px;
    text-align: left;
}

td {
    padding: 6px 10px;
    border: 1px solid #ccc;
}

tr:nth-child(even) td { background: #f9f9f9; }

a { color: #2980b9; text-decoration: none; }

hr { border: none; border-top: 1px solid #ccc; margin: 1.5em 0; }

ul, ol { padding-left: 1.8em; margin: 0.4em 0; }
li { margin: 0.2em 0; }

strong { color: #1a1a1a; }
"""


# ── Markdown extensions used ───────────────────────────────────────────────────

_MD_EXTENSIONS = [
    "tables",           # GitHub-style tables
    "fenced_code",      # ```lang ... ``` blocks
    "codehilite",       # syntax highlighting (optional — graceful if pygments missing)
    "toc",              # table of contents anchors
    "nl2br",            # newlines become <br>
    "sane_lists",       # better list behaviour
]


def convert(md_path: Path, pdf_path: Path) -> None:
    try:
        import markdown
        from weasyprint import HTML, CSS
    except ImportError as exc:
        print(
            textwrap.dedent(f"""
            Missing dependency: {exc}

            Install with:
                pip install markdown weasyprint

            If WeasyPrint fails on Windows due to missing GTK, use the pdfkit
            alternative at the bottom of this script instead.
            """).strip()
        )
        sys.exit(1)

    md_source = md_path.read_text(encoding="utf-8")

    # Convert Markdown → HTML
    try:
        html_body = markdown.markdown(md_source, extensions=_MD_EXTENSIONS)
    except Exception:
        # Fallback: minimal extensions if codehilite/toc not available
        html_body = markdown.markdown(md_source, extensions=["tables", "fenced_code"])

    html_full = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{md_path.stem}</title>
</head>
<body>
{html_body}
</body>
</html>"""

    # Convert HTML → PDF
    HTML(string=html_full, base_url=str(md_path.parent)).write_pdf(
        str(pdf_path),
        stylesheets=[CSS(string=_CSS)],
    )

    print(f"PDF written to: {pdf_path}")


# ── pdfkit alternative (uncomment if WeasyPrint is not available) ──────────────
#
# Requires:  pip install pdfkit
#            + wkhtmltopdf binary: https://wkhtmltopdf.org/downloads.html
#
# def convert(md_path: Path, pdf_path: Path) -> None:
#     import markdown, pdfkit
#     html = markdown.markdown(
#         md_path.read_text(encoding="utf-8"),
#         extensions=["tables", "fenced_code"]
#     )
#     full = f"<html><head><style>{_CSS}</style></head><body>{html}</body></html>"
#     pdfkit.from_string(full, str(pdf_path))
#     print(f"PDF written to: {pdf_path}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python md_to_pdf.py <input.md> [output.pdf]")
        sys.exit(1)

    md_path = Path(sys.argv[1]).resolve()
    if not md_path.exists():
        print(f"File not found: {md_path}")
        sys.exit(1)

    pdf_path = (
        Path(sys.argv[2]).resolve()
        if len(sys.argv) >= 3
        else md_path.with_suffix(".pdf")
    )

    convert(md_path, pdf_path)
