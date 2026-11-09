"""The smallest xlsx file Excel and LibreOffice both open: one sheet.

An xlsx is a zip of a few XML files. This writes exactly those - the
content types, two relationship files, a workbook, one stylesheet and one
worksheet - and nothing else, so an export needs no new dependency on a
plant PC that should stay as thin as it can.

A cell is one of four things:

- `None` is an empty cell. Never a zero: a limit a specification does not
  have is not a limit of 0, and a spreadsheet that sums the column must not
  be told otherwise.
- A `str` is written inline (`inlineStr`), so there is no shared-string
  table to keep in step.
- An `int` or `float` is a number.
- A `datetime` is a date cell: Excel's serial day number with a built-in
  date-and-time format, so the column sorts and filters as dates. It is
  written as given; the caller decides which clock it reads.
"""

from __future__ import annotations

import io
import math
import zipfile
from collections.abc import Iterable, Sequence
from datetime import datetime
from xml.sax.saxutils import escape

Cell = str | int | float | datetime | None

_EPOCH = datetime(1899, 12, 30)

#: The sheet name Excel shows on the tab; Excel refuses more than 31 characters
#: and any of these.
_SHEET_NAME_FORBIDDEN = set('[]:*?/\\')

_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_OOXML = "application/vnd.openxmlformats-officedocument.spreadsheetml"
_PKG = "http://schemas.openxmlformats.org/package/2006"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_CONTENT_TYPES = (
    f'{_XML}<Types xmlns="{_PKG}/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    f'<Override PartName="/xl/workbook.xml" ContentType="{_OOXML}.sheet.main+xml"/>'
    f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{_OOXML}.worksheet+xml"/>'
    f'<Override PartName="/xl/styles.xml" ContentType="{_OOXML}.styles+xml"/>'
    "</Types>")

_ROOT_RELS = (
    f'{_XML}<Relationships xmlns="{_PKG}/relationships">'
    f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/>'
    "</Relationships>")

_WORKBOOK_RELS = (
    f'{_XML}<Relationships xmlns="{_PKG}/relationships">'
    f'<Relationship Id="rId1" Type="{_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
    f'<Relationship Id="rId2" Type="{_REL}/styles" Target="styles.xml"/>'
    "</Relationships>")

_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

# Style 0 is plain; style 1 is bold (the header row); style 2 is a date and
# time, Excel's built-in format 22 ("m/d/yyyy h:mm"), which each reader shows
# in its own locale's order.
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="22" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _text(value: str) -> str:
    # Control characters other than tab, newline and return are not legal XML
    # 1.0; a reading's free text should never carry one, but one that did must
    # not make the whole file unreadable.
    clean = "".join(ch for ch in value if ch in "\t\n\r" or ord(ch) >= 32)
    return escape(clean)


def _cell(ref: str, value: Cell, bold: bool) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        serial = (value.replace(tzinfo=None) - _EPOCH).total_seconds() / 86400
        return f'<c r="{ref}" s="2"><v>{serial!r}</v></c>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
        return f'<c r="{ref}"><v>{value!r}</v></c>'
    style = ' s="1"' if bold else ""
    return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{_text(str(value))}</t></is></c>'


def _sheet(header: Sequence[str], rows: Iterable[Sequence[Cell]]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           # The header row stays on screen while a person scrolls two months.
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
           'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
           '<sheetData>']
    for number, row in enumerate([header, *rows], start=1):
        cells = "".join(_cell(f"{column_letter(i)}{number}", value, number == 1)
                        for i, value in enumerate(row))
        out.append(f'<row r="{number}">{cells}</row>')
    out.append("</sheetData></worksheet>")
    return "".join(out)


def workbook(sheet_name: str, header: Sequence[str], rows: Iterable[Sequence[Cell]]) -> bytes:
    """One sheet: a bold, frozen header row, then one row per item."""
    name = "".join(ch for ch in sheet_name if ch not in _SHEET_NAME_FORBIDDEN)[:31] or "Sheet1"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", _WORKBOOK.format(name=escape(name, {'"': "&quot;"})))
        z.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        z.writestr("xl/styles.xml", _STYLES)
        z.writestr("xl/worksheets/sheet1.xml", _sheet(header, rows))
    return buffer.getvalue()
