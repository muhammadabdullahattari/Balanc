"""Zero-dependency native OOXML (.xlsx) Multi-Sheet Audit Workpapers Builder (Phase N4 / Module 10).

Generates standards-compliant Excel workbooks (Bank Rec, AR Aging, AP Aging, Tax,
Prepaids, Fixed Assets, Accruals, Intercompany) with GL vs subledger tie-out
columns and sign-off cells — zero external dependencies required.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

WORKPAPER_SHEETS: list[str] = [
    "Bank Rec",
    "AR Aging",
    "AP Aging",
    "Tax",
    "Prepaids",
    "Fixed Assets",
    "Accruals",
    "Intercompany",
]


def _col_letter(col_idx: int) -> str:
    result = ""
    n = col_idx
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _is_numeric(val: Any) -> bool:
    if isinstance(val, (int, float, Decimal)):
        return True
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return False
        try:
            Decimal(s)
            return True
        except Exception:
            return False
    return False


def _sheet_xml(rows: list[list[Any]]) -> bytes:
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for r_idx, row in enumerate(rows, start=1):
        lines.append(f'<row r="{r_idx}">')
        for c_idx, cell in enumerate(row, start=1):
            ref = f"{_col_letter(c_idx)}{r_idx}"
            if cell is None or cell == "":
                continue
            if r_idx > 3 and _is_numeric(cell):
                num_str = str(Decimal(str(cell)))
                lines.append(f'<c r="{ref}"><v>{escape(num_str)}</v></c>')
            else:
                text_val = escape(str(cell))
                lines.append(f'<c r="{ref}" t="inlineStr"><is><t>{text_val}</t></is></c>')
        lines.append("</row>")
    lines.append("</sheetData></worksheet>")
    return "".join(lines).encode("utf-8")


def build_workpapers_xlsx(
    *,
    business_name: str,
    period_start: date,
    period_end: date,
    sheet_data: dict[str, list[dict[str, Any]]] | None = None,
) -> bytes:
    """Build a native multi-sheet .xlsx audit workpaper package with GL vs subledger tie-out."""
    data = sheet_data or {}
    sheets_xml: list[tuple[str, bytes]] = []

    for title in WORKPAPER_SHEETS:
        items = data.get(title, [])
        rows: list[list[Any]] = [
            [f"LedgerPilot Audit Workpaper — {title}", "", "", "", ""],
            [
                f"Entity: {business_name}",
                f"Period: {period_start.isoformat()} to {period_end.isoformat()}",
                "",
                "Prepared By:",
                "Autonomous Close",
            ],
            [
                "Reference / Account",
                "Description",
                "GL Balance",
                "Subledger Balance",
                "Variance (Tie-Out)",
            ],
        ]
        gl_total = Decimal("0.00")
        sub_total = Decimal("0.00")
        for item in items:
            gl_val = Decimal(str(item.get("gl_balance") or "0.00")).quantize(Decimal("0.01"))
            sub_val = Decimal(str(item.get("subledger_balance") or gl_val)).quantize(
                Decimal("0.01")
            )
            var = (gl_val - sub_val).quantize(Decimal("0.01"))
            gl_total += gl_val
            sub_total += sub_val
            rows.append(
                [
                    str(item.get("ref") or ""),
                    str(item.get("description") or ""),
                    gl_val,
                    sub_val,
                    var,
                ]
            )
        rows.append(
            [
                "TOTAL",
                f"{len(items)} schedule item(s)",
                gl_total.quantize(Decimal("0.01")),
                sub_total.quantize(Decimal("0.01")),
                (gl_total - sub_total).quantize(Decimal("0.01")),
            ]
        )
        rows.append(["", "", "", "", ""])
        rows.append(
            [
                "Reviewer Sign-Off:",
                "Pending Review",
                "Date:",
                "",
                "Tie-Out Status: " + ("TIED" if gl_total == sub_total else "VARIANCE"),
            ]
        )
        sheets_xml.append((title, _sheet_xml(rows)))

    buf = io.BytesIO()
    with ZipFile(buf, "w", compression=ZIP_DEFLATED) as zf:
        content_types = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
            '<Default Extension="xml" ContentType="application/xml"/>',
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet.main+xml"/>',
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.styles+xml"/>',
        ]
        for idx in range(1, len(sheets_xml) + 1):
            content_types.append(
                f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'spreadsheetml.worksheet+xml"/>'
            )
        content_types.append("</Types>")
        zf.writestr("[Content_Types].xml", "".join(content_types))

        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )

        wb_sheets = []
        wb_rels = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
            '<Relationship Id="rIdStyle" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>',
        ]
        for idx, (title, xml_bytes) in enumerate(sheets_xml, start=1):
            wb_sheets.append(f'<sheet name="{escape(title)}" sheetId="{idx}" r:id="rId{idx}"/>')
            wb_rels.append(
                f'<Relationship Id="rId{idx}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                f'relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
            )
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", xml_bytes)
        wb_rels.append("</Relationships>")

        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{''.join(wb_sheets)}</sheets></workbook>",
        )
        zf.writestr("xl/_rels/workbook.xml.rels", "".join(wb_rels))
        zf.writestr(
            "xl/styles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
            '<borders count="1"><border/></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
            'borderId="0"/></cellStyleXfs>'
            '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
            'borderId="0" xfId="0"/></cellXfs>'
            "</styleSheet>",
        )
    return buf.getvalue()
