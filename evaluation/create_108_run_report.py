"""Create a compact combined PDF of the 108-run RQ1/RQ2 results."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/hpc-site-preflight-matplotlib")

import fitz
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "evaluation" / "analysis-108"
TABLES = ANALYSIS / "tables"
FIGURES = ANALYSIS / "figures"
OUTPUT = ANALYSIS / "rq1-rq2-108-run-report.pdf"
TABLE_PAGES = Path("/tmp/hpc-site-preflight-108-run-tables.pdf")


def render_table(pdf: PdfPages, title: str, frame: pd.DataFrame) -> None:
    """Render one DataFrame as a landscape PDF page."""
    display = frame.copy()
    for column in display.columns:
        if pd.api.types.is_float_dtype(display[column]):
            if "Token" in column:
                display[column] = display[column].map(lambda value: f"{value:,.0f}")
            elif "Latency" in column:
                display[column] = display[column].map(lambda value: f"{value:.1f}")
            else:
                display[column] = display[column].map(lambda value: f"{value:.3f}")
    figure, axis = plt.subplots(figsize=(11.7, 8.3))
    axis.axis("off")
    axis.set_title(title, loc="left", fontsize=15, fontweight="bold", pad=18)
    table = axis.table(
        cellText=display.values,
        colLabels=display.columns,
        cellLoc="center",
        colLoc="center",
        loc="upper center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8 if len(display) > 5 else 10)
    table.scale(1, 1.45)
    for (row, _), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#DDE7EC")
            cell.set_text_props(weight="bold")
        else:
            cell.set_facecolor("#F7F9FA" if row % 2 else "white")
    pdf.savefig(figure, bbox_inches="tight")
    plt.close(figure)


def create_table_pages() -> None:
    """Create the title and table portion of the report."""
    with PdfPages(TABLE_PAGES) as pdf:
        figure = plt.figure(figsize=(8.3, 11.7))
        figure.text(0.08, 0.91, "RQ1/RQ2 Profile Construction", fontsize=22, weight="bold")
        figure.text(0.08, 0.855, "Complete 108-run analysis", fontsize=15)
        figure.text(
            0.08,
            0.80,
            "3 sites × 4 models × 3 retrieval strategies × 3 repetitions\n\n"
            "Frozen login measurements, pilot results, and documentation corpora.\n"
            "Tokens exclude the one-time corpus-discovery cost. Extraction latency\n"
            "covers context selection, model calls, and evidence validation.\n"
            "Run-to-run SD is pooled within each three-repetition configuration.",
            fontsize=11,
            linespacing=1.5,
            va="top",
        )
        figure.text(
            0.08,
            0.58,
            "Key observations\n"
            "• Precision is 0.957–0.992; field accuracy is 0.783–0.910 by site.\n"
            "• All 5,592 accepted citations pass the mechanical integrity checks.\n"
            "• BM25 has the highest retrieval-level accuracy and lowest token cost.\n"
            "• Full corpus uses 2.18× BM25 tokens without improving field accuracy.\n"
            "• Gemini 3.6 Flash is most accurate; GPT-5.6 Terra is cheaper and faster.",
            fontsize=11,
            linespacing=1.55,
            va="top",
        )
        plt.axis("off")
        pdf.savefig(figure)
        plt.close(figure)

        render_table(
            pdf,
            "Table 1: Results by site (RQ1)",
            pd.read_csv(TABLES / "table-1-results-by-site.csv"),
        )
        render_table(
            pdf,
            "Table 3: Results by model (RQ2)",
            pd.read_csv(TABLES / "table-3-results-by-model.csv"),
        )
        render_table(
            pdf,
            "Table 4: Results by retrieval strategy (RQ2)",
            pd.read_csv(TABLES / "table-4-results-by-retrieval.csv"),
        )
        render_table(
            pdf,
            "Table 5: Model-retrieval combinations",
            pd.read_csv(TABLES / "table-5-model-retrieval-combinations.csv"),
        )


def main() -> None:
    """Combine summary pages and paper figures into one PDF."""
    create_table_pages()
    combined = fitz.open()
    for path in [
        TABLE_PAGES,
        FIGURES / "profile-completion-waterfall-error-bars.pdf",
        FIGURES / "rq2-quality-cost-pareto.pdf",
        FIGURES / "rq2-quality-cost-pareto-full-scale.pdf",
    ]:
        source = fitz.open(path)
        combined.insert_pdf(source)
        source.close()
    combined.save(OUTPUT)
    combined.close()
    print(f"Created {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
