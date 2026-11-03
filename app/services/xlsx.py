"""The smallest .xlsx reader/writer the language export needs, with only the standard library.

write() stores every cell as text (and formats the columns as text), so Excel never reads a
value such as "+ New dashboard" as a formula. read() returns the first sheet as rows of strings;
error cells (#NAME? …) come back empty.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN}
# characters XML 1.0 can't hold
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _col(n: int) -> str:
    name = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return name


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def write(path: str | Path, rows: list[list[str]], widths: list[int] | None = None, rtl: bool = False) -> None:
    sheet_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            text = escape(_BAD.sub("", str(value)))
            style = ' s="1"' if r == 1 else ' s="2"'
            cells.append(f'<c r="{_col(c)}{r}" t="inlineStr"{style}><is><t xml:space="preserve">{text}</t></is></c>')
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" style="2" customWidth="1"/>'
                   for i, w in enumerate(widths or []))
    view = ' rightToLeft="1"' if rtl else ""
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             f'<worksheet xmlns="{MAIN}" xmlns:r="{REL}">'
             f'<sheetViews><sheetView workbookViewId="0"{view}>'
             f'<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             f'<sheetFormatPr defaultRowHeight="15"/>'
             f'{"<cols>" + cols + "</cols>" if cols else ""}'
             f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<styleSheet xmlns="{MAIN}">'
              f'<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
              f'<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              f'<fills count="2"><fill><patternFill patternType="none"/></fill>'
              f'<fill><patternFill patternType="gray125"/></fill></fills>'
              f'<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              f'<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              f'<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              f'<xf numFmtId="49" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>'
              f'<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
              f'<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              f'</styleSheet>')
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>',
        "_rels/.rels":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PKG_REL}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{MAIN}" xmlns:r="{REL}">'
            '<sheets><sheet name="texts" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PKG_REL}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'styles" Target="styles.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
        "xl/styles.xml": styles,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)


def _text(node) -> str:
    """All <t> under a shared string or inline string (rich text comes in runs)."""
    return "".join(t.text or "" for t in node.iter(f"{{{MAIN}}}t"))


def _first_sheet(z: zipfile.ZipFile) -> str:
    try:
        book = ElementTree.fromstring(z.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid = book.find("m:sheets/m:sheet", NS).get(f"{{{REL}}}id")
        for rel in rels:
            if rel.get("Id") == rid:
                target = rel.get("Target").lstrip("/")
                return target if target.startswith("xl/") else "xl/" + target
    except (KeyError, AttributeError):
        pass
    return "xl/worksheets/sheet1.xml"


def read(path: str | Path) -> list[list[str]]:
    """Raises zipfile.BadZipFile / KeyError / ElementTree.ParseError on a file that isn't .xlsx."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = [_text(si) for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
                      .findall("m:si", NS)]
        sheet = ElementTree.fromstring(z.read(_first_sheet(z)))
    rows: list[list[str]] = []
    for row in sheet.iterfind("m:sheetData/m:row", NS):
        values: dict[int, str] = {}
        for i, c in enumerate(row.iterfind("m:c", NS)):
            ref, kind = c.get("r"), c.get("t", "n")
            col = _col_index(ref) if ref else i
            v = c.find("m:v", NS)
            if kind == "inlineStr":
                value = _text(c.find("m:is", NS)) if c.find("m:is", NS) is not None else ""
            elif kind == "s":
                value = shared[int(v.text)] if v is not None and v.text else ""
            elif kind == "e":
                value = ""
            else:
                value = v.text if v is not None and v.text else ""
                if kind == "n" and value.endswith(".0"):
                    value = value[:-2]
            values[col] = value
        r = int(row.get("r", len(rows) + 1))
        while len(rows) < r - 1:
            rows.append([])
        rows.append([values.get(c, "") for c in range(max(values) + 1)] if values else [])
    return rows
