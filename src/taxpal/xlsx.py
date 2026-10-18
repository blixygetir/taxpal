"""A small .xlsx writer: several sheets, bold headers, money and date formats, column widths,
a frozen header row and filters. Opens in Excel, Numbers, LibreOffice and Google Sheets."""

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from xml.sax.saxutils import escape

# cell styles (index into cellXfs in STYLES)
PLAIN, HEADER, MONEY, DATE, BOLD, BOLD_MONEY, MUTED, TITLE = range(8)

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0.00;[Red]-&quot;$&quot;#,##0.00"/><numFmt numFmtId="165" formatCode="yyyy-mm-dd"/></numFmts>
<fonts count="4"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font>
<font><sz val="10"/><color rgb="FF5D6673"/><name val="Calibri"/></font><font><b/><sz val="14"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFEEF1F4"/></patternFill></fill></fills>
<borders count="2"><border/><border><bottom style="thin"><color rgb="FFB0B7C0"/></bottom></border></borders>
<cellStyleXfs count="1"><xf/></cellStyleXfs>
<cellXfs count="8">
<xf/>
<xf fontId="1" fillId="2" borderId="1" applyFont="1" applyFill="1" applyBorder="1"/>
<xf numFmtId="164" applyNumberFormat="1"/>
<xf numFmtId="165" applyNumberFormat="1"/>
<xf fontId="1" applyFont="1"/>
<xf numFmtId="164" fontId="1" applyNumberFormat="1" applyFont="1"/>
<xf fontId="2" applyFont="1"/>
<xf fontId="3" applyFont="1"/>
</cellXfs>
</styleSheet>"""


@dataclass
class Sheet:
    name: str
    widths: list[float]  # in characters
    rows: list[list] = field(default_factory=list)  # cells are values or (value, style)
    header_row: int | None = None  # 0-based row to freeze under and filter on

    def add(self, *cells) -> None:
        self.rows.append(list(cells))


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref: str, value, style: int | None) -> str:
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>' if style else ""
    if isinstance(value, date):
        return f'<c r="{ref}" s="{DATE if style is None else style}"><v>{(value - date(1899, 12, 30)).days}</v></c>'
    if isinstance(value, bool):
        value = "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style or PLAIN}"><v>{value}</v></c>'
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
    return f'<c r="{ref}" t="inlineStr" s="{style or PLAIN}"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _sheet_xml(sheet: Sheet) -> str:
    rows = []
    for r, cells in enumerate(sheet.rows, start=1):
        xml = "".join(_cell(f"{_col(c)}{r}", *(cell if isinstance(cell, tuple) else (cell, None)))
                      for c, cell in enumerate(cells))
        rows.append(f'<row r="{r}">{xml}</row>')
    view = ""
    filt = ""
    if sheet.header_row is not None:
        top = sheet.header_row + 2
        view = (f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{top - 1}" topLeftCell="A{top}" '
                'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
        last = max(len(sheet.rows), top - 1)
        filt = f'<autoFilter ref="A{top - 1}:{_col(len(sheet.widths) - 1)}{last}"/>'
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(sheet.widths, start=1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{view}<cols>{cols}</cols><sheetData>{"".join(rows)}</sheetData>{filt}</worksheet>')


def write_xlsx(sheets: list[Sheet]) -> bytes:
    ns = "http://schemas.openxmlformats.org"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="{ns}/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(sheets) + 1)) + "</Types>")
        z.writestr("_rels/.rels",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{ns}/package/2006/relationships">'
                   f'<Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        filters = "".join(
            f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
            f"'{escape(s.name)}'!$A${s.header_row + 1}:${_col(len(s.widths) - 1)}${max(len(s.rows), s.header_row + 1)}</definedName>"
            for i, s in enumerate(sheets) if s.header_row is not None)
        z.writestr("xl/workbook.xml",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{ns}/spreadsheetml/2006/main" '
                   f'xmlns:r="{ns}/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(s.name[:31])}" sheetId="{i}" r:id="rId{i}"/>' for i, s in enumerate(sheets, start=1))
                   + f"</sheets>{f'<definedNames>{filters}</definedNames>' if filters else ''}</workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{ns}/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="{ns}/officeDocument/2006/relationships/worksheet" '
                             f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="{ns}/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   "</Relationships>")
        z.writestr("xl/styles.xml", STYLES)
        for i, s in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(s))
    return out.getvalue()
