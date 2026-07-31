"""Render the RQ1 and RQ2 Markdown reports as one evaluation PDF."""

# ruff: noqa: E402, I001

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("XDG_CACHE_HOME", "/tmp/hpc-site-preflight-font-cache")

from mistune import create_markdown
from weasyprint import HTML


HERE = Path(__file__).resolve().parent
RQ1 = HERE / "rq1/rq1-results.md"
RQ2 = HERE / "rq2/rq2-results.md"
COMBINED_MARKDOWN = HERE / "combined-results.md"
COMBINED_PDF = HERE / "combined-results.pdf"


def localize_links(text: str, section: str) -> str:
    """Make report-relative figure links relative to the evaluation directory."""
    return text.replace("](figures/", f"]({section}/figures/")


def main() -> None:
    """Combine both reports, render their figures, and write one PDF."""
    rq1 = localize_links(RQ1.read_text(encoding="utf-8"), "rq1")
    rq2 = localize_links(RQ2.read_text(encoding="utf-8"), "rq2")
    combined = f"{rq1}\n\n<div class=\"page-break\"></div>\n\n{rq2}\n"
    COMBINED_MARKDOWN.write_text(combined, encoding="utf-8")

    markdown = create_markdown(plugins=["table"])
    body = markdown(combined)
    css = """
    @page {
      size: letter; margin: 0.65in 0.7in 0.7in; background: white;
      @bottom-center { content: counter(page); color: #5b6570; font-size: 8pt; }
    }
    html, body { background: white; }
    body { color: #26333d; font-family: Arial, Helvetica, sans-serif;
           font-size: 9.5pt; line-height: 1.38; }
    h1 { color: #173f5f; font-size: 20pt; margin: 0 0 12pt; }
    h2 { color: #245b78; font-size: 14pt; border-bottom: 1px solid #b8c5ce;
         padding-bottom: 3pt; margin-top: 18pt; }
    h3 { color: #2f5f73; font-size: 11.5pt; margin-top: 14pt; }
    p { margin: 5pt 0 8pt; }
    a { color: #245b78; text-decoration: none; }
    code { font-family: Menlo, monospace; font-size: 8.5pt;
           background: #eef2f4; padding: 1pt 2pt; }
    pre { background: #eef2f4; border-left: 3px solid #4c78a8;
          padding: 7pt; white-space: pre-wrap; }
    ul { margin: 5pt 0 10pt; padding-left: 18pt; }
    li { margin: 2pt 0; }
    table { border-collapse: collapse; width: 100%; margin: 9pt 0 12pt;
            font-size: 8pt; }
    th { background: #dce7ed; font-weight: 700; }
    th, td { border: 0.6px solid #9eabb4; padding: 4pt; text-align: left; }
    tr:nth-child(even) td { background: #f5f7f8; }
    img { display: block; max-width: 100%; max-height: 7.1in;
          margin: 8pt auto 5pt; }
    h2, h3, table, img { break-inside: avoid; }
    .page-break { break-before: page; }
    """
    document = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{css}</style></head><body>{body}</body></html>"
    )
    HTML(string=document, base_url=str(HERE)).write_pdf(COMBINED_PDF)
    print(f"Combined report: {COMBINED_PDF}")


if __name__ == "__main__":
    main()
