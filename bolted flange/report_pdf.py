from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

import os
import re
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors


def _fmt(val: Any, unit: str = "") -> str:
    # allow passing (value, unit)
    if isinstance(val, tuple) and len(val) == 2:
        val, unit = val

    if val is None:
        return "—"
    if isinstance(val, bool):
        return "YES" if val else "NO"
    if isinstance(val, int):
        return f"{val:,d}" + (f" {unit}" if unit else "")
    if isinstance(val, float):
        a = abs(val)
        if a >= 1e6 or (a > 0 and a < 1e-3):
            s = f"{val:.3e}"
        elif a >= 1000:
            s = f"{val:,.1f}"
        elif a >= 10:
            s = f"{val:.2f}"
        else:
            s = f"{val:.4f}"
        return s + (f" {unit}" if unit else "")
    return str(val) + (f" {unit}" if unit else "")


def _p(text: Any, style: ParagraphStyle) -> Paragraph:
    s = "" if text is None else str(text)
    s = s.replace("&", "&amp;")
    return Paragraph(s, style)


def _badge(text: str, ok: bool) -> Table:
    bg = colors.HexColor("#0B6B3A") if ok else colors.HexColor("#9B1C1C")
    fg = colors.white
    styles = getSampleStyleSheet()
    p = Paragraph(
        f"<b>{text}</b>",
        ParagraphStyle("b", parent=styles["BodyText"], textColor=fg, fontSize=9),
    )
    t = Table([[p]])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("BOX", (0, 0), (-1, -1), 0.5, bg),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return t


def _kv_table(
    rows: List[List[Any]],
    col_widths: List[float],
    header_bg: colors.Color | None = None,
    header_fg: colors.Color | None = None,
) -> Table:
    """Key/value table with repeat header + soft styling."""
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header_bg is not None:
        style_cmds.append(("BACKGROUND", (0, 0), (-1, 0), header_bg))
    if header_fg is not None:
        style_cmds.append(("TEXTCOLOR", (0, 0), (-1, 0), header_fg))

    tbl.setStyle(TableStyle(style_cmds))
    return tbl

_VERSION_RE = re.compile(r"^(?P<stem>.+)_(?P<ts>\d{8}_\d{6})_v(?P<v>\d{2})$")


def _format_ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%d_%H%M%S")


def _existing_timestamp_for_base(base_path: Path) -> datetime | None:
    """
    If a file with the base name exists, use its mtime as the grouping timestamp.
    We prefer mtime over "creation time" because Windows/POSIX semantics differ.
    """
    if base_path.exists():
        try:
            return datetime.fromtimestamp(base_path.stat().st_mtime)
        except OSError:
            return None
    return None


def _next_versioned_filename(requested_filename: str) -> str:
    """
    If requested_filename already exists, create:
        <stem>_<YYYYMMDD_HHMMSS>_v01.pdf
    If that exists too, bump version:
        ..._v02.pdf, ..._v03.pdf, etc.

    Timestamp selection:
      - If the base filename exists (e.g., flange_report.pdf), use its mtime timestamp.
      - Otherwise, use "now".
    """
    req = Path(requested_filename)
    folder = req.parent if str(req.parent) not in ("", ".") else Path(".")
    stem = req.stem
    ext = req.suffix if req.suffix else ".pdf"

    # if the requested file doesn't exist, keep it as-is
    ts = _format_ts(datetime.now())
    return str(folder / f"{stem}_{ts}_v01{ext}")

    # choose timestamp grouping
    ts_dt = _existing_timestamp_for_base(req) or datetime.now()
    ts = _format_ts(ts_dt)

    # find max existing version for this (stem, ts)
    max_v = 0
    try:
        for p in folder.glob(f"{stem}_{ts}_v??{ext}"):
            m = _VERSION_RE.match(p.stem)
            if m and m.group("stem") == stem and m.group("ts") == ts:
                max_v = max(max_v, int(m.group("v")))
    except OSError:
        # if listing fails, fall back to v01 and try sequentially
        max_v = 0

    # pick next
    for v in range(max_v + 1, 100):
        candidate = folder / f"{stem}_{ts}_v{v:02d}{ext}"
        if not candidate.exists():
            return str(candidate)

    raise RuntimeError("Too many report versions (v01-v99) already exist for this timestamp.")


# =============================================================================
# PDF builder (with auto-versioning)
# =============================================================================

def build_flange_pdf_report(
    filename: str,
    project_title: str,
    inputs: Dict[str, Any],
    results: Dict[str, Any],
    checks: Dict[str, bool] | None = None,
    notes: str | None = None,
    warnings: List[str] | None = None,
    calculation_trace: List[str] | None = None,
) -> None:
    # NEW: auto version the filename if it already exists
    filename = _next_versioned_filename(filename)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Title2", parent=styles["Title"], fontSize=20, leading=24, spaceAfter=10))
    styles.add(ParagraphStyle(name="H2", parent=styles["Heading2"], fontSize=12.5, leading=15, spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="Body", parent=styles["BodyText"], fontSize=9.5, leading=12))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10, textColor=colors.grey))

    # Monospace style for trace + supports <sub>/<super>
    styles.add(
        ParagraphStyle(
            name="Mono",
            parent=styles["BodyText"],
            fontName="Courier",
            fontSize=8.5,
            leading=10.5,
        )
    )

    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.8 * inch,
        title="Bolted Flange Joint Report",
    )

    def header_footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(
            0.75 * inch,
            0.55 * inch,
            f"{project_title} • {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )
        canvas.drawRightString(7.75 * inch, 0.55 * inch, f"Page {doc_.page}")
        canvas.restoreState()

    story = []
    story.append(Paragraph("Bolted Flange Joint Report", styles["Title2"]))
    story.append(Paragraph(project_title, styles["Body"]))
    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            "Summary of joint stiffness split, preload, peak bolt force, and quick safety indicators.",
            styles["Body"],
        )
    )

    # Warnings / Flags
    if warnings:
        story.append(Paragraph("Warnings / Flags", styles["H2"]))
        bullet_lines = "<br/>".join([f"• {str(w).replace('&', '&amp;')}" for w in warnings])
        story.append(Paragraph(bullet_lines, styles["Body"]))

    # Calculation Trace
    if calculation_trace:
        story.append(Paragraph("Calculation Trace", styles["H2"]))
        for line in calculation_trace:
            story.append(_p(line, styles["Mono"]))

    # Inputs table
    story.append(Paragraph("Inputs", styles["H2"]))
    inp_rows = [[Paragraph("<b>Input</b>", styles["Body"]), Paragraph("<b>Value</b>", styles["Body"])]]
    for k, v in inputs.items():
        inp_rows.append([_p(k, styles["Body"]), _p(_fmt(v), styles["Body"])])
    inp_tbl = _kv_table(
        inp_rows,
        col_widths=[2.6 * inch, 4.4 * inch],
        header_bg=colors.HexColor("#999999"),
        header_fg=colors.white,
    )
    story.append(inp_tbl)

    # Results table
    story.append(Paragraph("Computed results", styles["H2"]))
    res_rows = [[Paragraph("<b>Quantity</b>", styles["Body"]), Paragraph("<b>Value</b>", styles["Body"])]]
    for k, v in results.items():
        res_rows.append([_p(k, styles["Body"]), _p(_fmt(v), styles["Body"])])
    res_tbl = _kv_table(
        res_rows,
        col_widths=[2.6 * inch, 4.4 * inch],
        header_bg=colors.HexColor("#999999"),
        header_fg=colors.white,
    )
    story.append(res_tbl)

    # Checks
    if checks:
        story.append(Paragraph("Checks", styles["H2"]))
        chk_rows = [[Paragraph("<b>Check</b>", styles["Body"]), Paragraph("<b>Status</b>", styles["Body"])]]
        for name, ok in checks.items():
            chk_rows.append([_p(name, styles["Body"]), _badge("PASS", True) if ok else _badge("FAIL", False)])
        chk_tbl = Table(chk_rows, colWidths=[5.1 * inch, 1.9 * inch], repeatRows=1)
        chk_tbl.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#999999")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ]
            )
        )
        story.append(chk_tbl)

    if notes:
        story.append(Paragraph("Notes", styles["H2"]))
        story.append(Paragraph(notes.replace("&", "&amp;").replace("\n", "<br/>"), styles["Body"]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Generated by the bolted flange calculator.", styles["Small"]))

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)


