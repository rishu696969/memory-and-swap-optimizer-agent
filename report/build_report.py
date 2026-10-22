"""Build the final project report (DOCX + PDF) from the real project files.

    python report/build_report.py

* Code excerpts are cut from the source files with `ast`, so they are always
  the code that was actually built.
* Transcript excerpts, test counts and metrics are read from ./evidence.
* Two passes: the DOCX is converted to PDF with LibreOffice, heading/caption
  page numbers are read back from the PDF, and the TOC is rebuilt with them.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docx_helpers import BLUE, NAVY, SANS, Report, _font, _set_cell_bg  # noqa: E402
from report_content import write_body  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "report"
NAME = "Memory_and_Swap_Optimizer_Agent_Report"
SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
FOOTER = "Memory and Swap Optimizer Agent | Final Project Report"

STUDENTS = [("1", "Kumar Hrishikesh Ranjan", "24BDS0221"), ("2", "Ashish Kumar", "24BKT059")]


# --------------------------------------------------------------------------- evidence helpers
class Evidence:
    def __init__(self, root: Path):
        self.root = root

    def text(self, rel: str) -> str:
        p = self.root / rel
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def json(self, rel: str):
        p = self.root / rel
        return json.loads(p.read_text()) if p.exists() else None

    def excerpt(self, rel: str, start: str, end: str | None = None, max_lines: int = 60) -> str:
        """Lines of a transcript from the first line containing *start* up to (not incl.) *end*."""
        lines = self.text(rel).splitlines()
        try:
            i = next(n for n, line in enumerate(lines) if start in line)
        except StopIteration:
            return f"[excerpt '{start}' not found in {rel}]"
        out = []
        for line in lines[i:]:
            if end and out and end in line:
                break
            out.append(line)
        if len(out) > max_lines:
            out = out[:max_lines] + [f"[... {len(out) - max_lines} more lines in {rel}]"]
        return "\n".join(out)

    def junit(self, rel: str) -> dict:
        p = self.root / rel
        if not p.exists():
            return {}
        suite = ET.parse(p).getroot()
        suite = suite.find("testsuite") if suite.tag == "testsuites" else suite
        per_file = Counter()
        for tc in suite.iter("testcase"):
            per_file[tc.get("classname", "").split(".")[-1]] += 1
        return {"tests": int(suite.get("tests", 0)), "failures": int(suite.get("failures", 0)),
                "errors": int(suite.get("errors", 0)), "skipped": int(suite.get("skipped", 0)),
                "time": float(suite.get("time", 0)), "per_file": per_file}


def code_of(rel: str, name: str, max_lines: int = 45, start_at: str | None = None) -> str:
    """Source of a top-level or nested function/class *name* in src file *rel*."""
    path = ROOT / rel
    src = path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            lines = src.splitlines()[node.lineno - 1 - len(node.decorator_list): node.end_lineno]
            indent = min(len(x) - len(x.lstrip()) for x in lines if x.strip())
            lines = [x[indent:] for x in lines]
            if start_at:
                k = next(i for i, x in enumerate(lines) if start_at in x)
                lines = lines[:1] + ["    ..."] + lines[k:]
            # drop the docstring body to save space, keep the first line
            if len(lines) > max_lines:
                lines = lines[:max_lines] + ["    ...  # (truncated; see " + rel + ")"]
            header = f"# {rel}  (line {node.lineno})"
            return header + "\n" + "\n".join(lines)
    raise KeyError(f"{name} not found in {rel}")


def lines_of(rel: str, first: str, last: str) -> str:
    src = (ROOT / rel).read_text().splitlines()
    i = next(n for n, x in enumerate(src) if first in x)
    j = next(n for n, x in enumerate(src[i:], i) if last in x)
    return f"# {rel}  (line {i + 1})\n" + "\n".join(src[i:j + 1])


# --------------------------------------------------------------------------- cover
def cover(r: Report) -> None:
    d = r.doc
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(18)
    p.add_run().add_picture(str(OUT_DIR / "assets" / "vit_logo_0.png"), width=Cm(11.5))

    def line(text, font, size, color, before=0, after=6, bold=True):
        q = d.add_paragraph()
        q.alignment = WD_ALIGN_PARAGRAPH.CENTER
        q.paragraph_format.space_before = Pt(before)
        q.paragraph_format.space_after = Pt(after)
        _font(q.add_run(text), font, size, bold=bold, color=color)
        return q

    line("COURSE:  OPERATING SYSTEM LAB", "Times New Roman", 24, BLUE, before=28, after=26)
    line("Final Project Report", SANS, 12.5, NAVY, after=14)
    line("MEMORY AND SWAP", SANS, 24, NAVY, after=0)
    line("OPTIMIZER AGENT", SANS, 24, NAVY, after=8)
    line("An MCP-based, human-approved Linux OS agent with file-integrity,\nkernel-module and systemd supporting modules",
         SANS, 10.5, RGBColor(0x40, 0x40, 0x40), after=14, bold=False)
    rule = d.add_paragraph()
    rule.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(rule.add_run("_" * 78), SANS, 9, color=BLUE)
    line("Submitted by", SANS, 13, NAVY, before=14, after=8)

    t = d.add_table(rows=1, cols=3)
    t.style = "Table Grid"
    t.alignment = 1
    for i, h in enumerate(("S.No.", "Name", "Registration Number")):
        c = t.rows[0].cells[i]
        c.text = ""
        c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        _font(c.paragraphs[0].add_run(h), SANS, 10, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
        _set_cell_bg(c, "1F2F5F")
    for sno, name, reg in STUDENTS:
        cells = t.add_row().cells
        for i, v in enumerate((sno, name, reg)):
            cells[i].text = ""
            cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT if i == 1 else WD_ALIGN_PARAGRAPH.CENTER
            _font(cells[i].paragraphs[0].add_run(v), SANS, 10, color=RGBColor(0x22, 0x22, 0x22))
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    t.autofit = False
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    t._tbl.tblPr.append(layout)
    for gc, w in zip(t._tbl.tblGrid.findall(qn("w:gridCol")), (2.6, 7.0, 4.8)):
        gc.set(qn("w:w"), str(int(w * 567)))
    for row in t.rows:
        for i, w in enumerate((2.6, 7.0, 4.8)):
            row.cells[i].width = Cm(w)

    for k, txt in enumerate(("COURSE CODE: BCSE303P", "FACULTY: NAVEENKUMAR J", "SLOT: L53+L54")):
        q = d.add_paragraph()
        q.paragraph_format.left_indent = Cm(1.2)
        q.paragraph_format.space_before = Pt(40 if k == 0 else 4)
        q.paragraph_format.space_after = Pt(4)
        _font(q.add_run(txt), "Times New Roman", 17, bold=True, color=BLUE)
    r.page_break()


# --------------------------------------------------------------------------- build
def render(prev: dict | None) -> Report:
    r = Report()
    r.setup(FOOTER)
    cover(r)
    ev = Evidence(ROOT / "evidence")
    # Front matter (abstract, TOC, lists) is written by report_content.write_body
    write_body(r, ev, code_of, lines_of, prev, ROOT)
    return r


def to_pdf(docx_path: Path) -> Path:
    subprocess.run([SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path)],
                   check=True, capture_output=True, timeout=300)
    return docx_path.with_suffix(".pdf")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def find_pages(pdf: Path, headings: list[str], captions: list[str]) -> tuple[dict, dict]:
    doc = fitz.open(pdf)
    texts = [norm(p.get_text()) for p in doc]
    # Body starts after the last front-matter page (the one containing "List of Tables").
    start = max(i for i, t in enumerate(texts) if "List of Tables" in t) + 1
    pages, i = {}, start
    for h in headings:
        key = norm(h)
        for k in range(i, len(texts)):
            if key in texts[k]:
                pages[h] = k + 1
                i = k
                break
    caps = {}
    for c in captions:
        key = norm(c)[:60]
        for k in range(start, len(texts)):
            if key in texts[k]:
                caps[c] = k + 1
                break
    return pages, caps


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    docx_path = OUT_DIR / f"{NAME}.docx"
    # Pass 1 collects headings/captions; pass 2 lays out the TOC with placeholder numbers;
    # pass 3+ fills in the measured page numbers (same text length, so layout is stable).
    r = render(None)
    prev = {"toc": r.toc, "figures": r.figures, "tables": r.tables, "pages": None, "caps": None}
    for attempt in range(4):
        r = render(prev)
        r.doc.save(docx_path)
        pdf = to_pdf(docx_path)
        pages, caps = find_pages(pdf, [t for _, t in r.toc], r.figures + r.tables)
        if pages == prev["pages"] and caps == prev["caps"]:
            break
        prev = {"toc": r.toc, "figures": r.figures, "tables": r.tables, "pages": pages, "caps": caps}
    missing = [t for _, t in r.toc if t not in pages] + [c for c in r.figures + r.tables if c not in caps]
    print(f"wrote {docx_path.name} and {pdf.name}: {fitz.open(pdf).page_count} pages; "
          f"{len(r.figures)} figures, {len(r.tables)} tables; passes={attempt + 2}; unresolved: {missing}")


if __name__ == "__main__":
    main()
