"""
exporters/excel_exporter.py
============================
تصدير التقارير إلى Excel (.xlsx).
كل بلوك جدول يذهب في sheet منفصل.

🆕 تحسينات لجعل التقرير المُصدَّر احترافياً:
------------------------------------------------
1. تفسير Markdown فعلياً في الفقرات (بدل نص خام سطراً سطراً):
   - '#'..'######' → عنوان حقيقي بحجم/لون متدرّج (نفس تدرّج PDF).
   - '**نص**' → جزء عريض فعلي داخل نفس الخلية عبر CellRichText
     (openpyxl 3.1+)، وليس نجمتين حرفيتين.
   - جدول Markdown (| ... |) → جدول Excel حقيقي بخلايا/حدود/تلوين رأس
     منفصلة، بما في ذلك حالة الرأس الفارغ بالكامل (رسم بياني نصي
     █/░ من Story Telling) التي لا تُرسم كصف Header ملوَّن.
2. روابط داخلية (Hyperlink) فعلية من شيت "ملخص" إلى شيت كل رسم/جدول
   بدل نص "انظر الشيت المخصص" الميت.
3. صورة ثابتة (PNG) للرسم البياني تُضاف بجانب سطره في شيت "ملخص"،
   بالإضافة إلى الرسم الحي الموجود أصلاً في شيت الرسم المخصص.
4. تنسيق أرقام (number_format) بدل القيم الخام — فواصل آلاف للأعداد
   الصحيحة، منزلتان عشريتان للكسور.
5. شيت اللوحة (dashboard): استبدال الإزاحات اليدوية بعناوين أعمدة
   مدمجة (merge_cells) وتجميد الصف الأول (freeze_panes) لتسهيل
   التصفح في اللوحات الكبيرة.

لا تغيير في الواجهة العامة (export()) — ui/reports.py لا يحتاج أي تعديل.
"""

import re
import logging
from pathlib import Path
from numbers import Number

import openpyxl
from openpyxl.styles import (
    Font, PatternFill, Alignment,
    Border, Side,
)
from openpyxl.utils  import get_column_letter
from openpyxl.chart  import BarChart, LineChart, PieChart, AreaChart, Reference
from openpyxl.chart.marker import Marker
from openpyxl.drawing.image import Image as XLImage
from openpyxl.cell.text import InlineFont
from openpyxl.cell.rich_text import CellRichText, TextBlock

from exporters.report_manager import ReportManager
from exporters.chart_renderer import render_gauge, render_chart

logger = logging.getLogger(__name__)

# ── ألوان ────────────────────────────────────────────────────
COLOR_HEADER_BG  = "1E3A5F"
COLOR_HEADER_FG  = "FFFFFF"
COLOR_ROW_ALT    = "F1F5F9"
COLOR_KPI_VALUE  = "1D4ED8"
COLOR_TITLE      = "1E3A5F"
COLOR_H1         = "2563EB"
COLOR_H2         = "3B82F6"
COLOR_CARD_BG    = "EFF6FF"
COLOR_CARD_BORDER = "BFDBFE"
COLOR_LINK       = "1D4ED8"
CHART_COLORS     = ["2563EB", "10B981", "F59E0B", "A855F7", "EF4444", "0EA5E9"]

# ── أنماط Markdown خفيفة مدعومة داخل الفقرات/نص السرد ──────
# (نفس القواعد المستخدمة في exporters/pdf_exporter.py، معاد كتابتها
# هنا محلياً — هذا الملف يبقى مستقلاً بالكامل عمداً).
_MD_HEADER_RE    = re.compile(r'^(#{1,6})\s+(.*)$')
_MD_BOLD_RE      = re.compile(r'\*\*(.+?)\*\*')
_MD_TABLE_SEP_RE = re.compile(r'^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$')

_HEADER_STYLE_BY_LEVEL = {
    1: {"size": 14, "color": COLOR_H1},
    2: {"size": 12, "color": COLOR_H2},
}


# ══════════════════════════════════════════════════════════════
#  🆕 تحليل Markdown خفيف → أجزاء نصية (bold runs) / جداول / عناوين
# ══════════════════════════════════════════════════════════════

def _split_bold_runs(line: str) -> list[tuple[str, bool]]:
    """
    تقسيم سطر يحتوي '**نص عريض**' إلى قائمة (نص, هل_عريض) بالترتيب.
    مثال: "القيمة **25000** ريال" →
        [("القيمة ", False), ("25000", True), (" ريال", False)]
    """
    parts = _MD_BOLD_RE.split(line)
    runs = []
    for i, part in enumerate(parts):
        if not part:
            continue
        runs.append((part, i % 2 == 1))
    return runs or [("", False)]


def _markdown_table_rows(lines: list[str]) -> tuple[list[str], list[list[str]], bool]:
    """
    تحويل أسطر جدول Markdown (| ... |) إلى (header, data_rows, has_real_header).
    صف الفاصل (|---|:---:|...) يُتجاهل. رأس فارغ بالكامل (كل الخلايا
    فارغة) يُعتبر "بلا رأس فعلي" — نفس منطق ui/common.py وpdf_exporter.py
    لجداول الرسم البياني النصي (█/░) في نص Story Telling.
    """
    rows = []
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("|") or _MD_TABLE_SEP_RE.match(raw):
            continue
        rows.append([c.strip() for c in raw.strip("|").split("|")])

    if not rows:
        return [], [], False

    n_cols = max(len(r) for r in rows)
    pad = lambda r: r + [""] * (n_cols - len(r))
    header = pad(rows[0])
    data_rows = [pad(r) for r in rows[1:]]
    has_real_header = any(h for h in header)
    return header, data_rows, has_real_header


def _parse_markdown_blocks(text: str) -> list[dict]:
    """
    تحليل نص Markdown خفيف (نفس الصيغة المُنتَجة عبر
    ai/prompt_builder.py::_story_writing_rules وفقرات التقارير) إلى
    قائمة بلوكات مرتّبة:
        {"type": "header", "level": 1-6, "text": "..."}
        {"type": "paragraph", "runs": [(text, is_bold), ...]}
        {"type": "table", "header": [...], "rows": [[...], ...], "has_header": bool}
        {"type": "blank"}
    """
    lines = text.splitlines()
    blocks = []
    i, n = 0, len(lines)

    while i < n:
        raw_line = lines[i]
        line = raw_line.strip()

        if not line:
            blocks.append({"type": "blank"})
            i += 1
            continue

        if line.startswith("|"):
            table_lines = []
            while i < n and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            header, data_rows, has_header = _markdown_table_rows(table_lines)
            if header or data_rows:
                blocks.append({
                    "type": "table", "header": header,
                    "rows": data_rows, "has_header": has_header,
                })
            continue

        header_match = _MD_HEADER_RE.match(line)
        if header_match:
            level = len(header_match.group(1))
            blocks.append({
                "type": "header", "level": level,
                "text": header_match.group(2).strip(),
            })
            i += 1
            continue

        blocks.append({"type": "paragraph", "runs": _split_bold_runs(line)})
        i += 1

    return blocks


def _runs_to_richtext(runs: list[tuple[str, bool]], base_size: int = 11):
    """
    تحويل قائمة (نص, هل_عريض) إلى CellRichText جاهزة للكتابة مباشرة
    في خلية عبر ws.cell(...).value = richtext — تُعرض كأجزاء عريضة
    حقيقية داخل نفس الخلية بدل نجمتين حرفيتين ظاهرتين.
    """
    if len(runs) == 1 and not runs[0][1]:
        return runs[0][0]
    blocks = []
    for text, is_bold in runs:
        if not text:
            continue
        if is_bold:
            blocks.append(TextBlock(InlineFont(b=True, sz=base_size), text))
        else:
            blocks.append(text)
    return CellRichText(*blocks) if blocks else ""


class ExcelExporter:
    """
    تصدير تقرير إلى Excel.

    الاستخدام:
        exp = ExcelExporter(report_manager)
        exp.export(report_id, Path("report.xlsx"))
    """

    def __init__(self, report_manager: ReportManager):
        self.rm = report_manager

    def export(self, report_id: str, output_path: Path) -> dict:
        """
        تصدير تقرير إلى .xlsx.
        يرجع: {"ok": True, "path": "..."} أو {"ok": False, "error": "..."}
        """
        reports = self.rm.list_reports()
        report  = next((r for r in reports if r["id"] == report_id), None)
        if not report:
            return {"ok": False, "error": "التقرير غير موجود"}

        blocks = self.rm.get_blocks(report_id)

        try:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            wb = openpyxl.Workbook()
            wb.remove(wb.active)   # حذف الـ sheet الافتراضية الفارغة

            # 🆕 نحسب أسماء شيتات الجداول/الرسوم/المقاييس مسبقاً (قبل
            # بناء أي شيت فعلياً) حتى يستطيع شيت "ملخص" الإشارة إليها
            # كروابط داخلية صحيحة بغض النظر عن ترتيب البناء الفعلي.
            table_blocks = [b for b in blocks if b["block_type"] == "table"]
            chart_blocks = [b for b in blocks if b["block_type"] == "chart"]
            gauge_blocks = [b for b in blocks if b["block_type"] == "gauge"]

            block_sheet_names: dict[int, str] = {}
            for i, block in enumerate(table_blocks, 1):
                block_sheet_names[id(block)] = f"جدول {i}"
            for i, block in enumerate(chart_blocks, 1):
                block_sheet_names[id(block)] = f"رسم {i}"
            for i, block in enumerate(gauge_blocks, 1):
                block_sheet_names[id(block)] = f"مقياس {i}"

            # Sheet ملخص التقرير (يُبنى أولاً في الترتيب المنطقي، لكن
            # openpyxl لا يمانع أي ترتيب فعلي لاحق للشيتات في الملف)
            self._build_summary_sheet(wb, report["title"], blocks, block_sheet_names)

            # Sheet لكل جدول بيانات
            for i, block in enumerate(table_blocks, 1):
                self._build_table_sheet(wb, f"جدول {i}", block["content"])

            # Sheet لكل رسم بياني (بيانات + رسم Excel حي)
            for i, block in enumerate(chart_blocks, 1):
                self._build_chart_sheet(wb, f"رسم {i}", block["content"])

            # Sheet لكل Gauge (صورة، لأن Excel لا يدعم رسم gauge حي)
            for i, block in enumerate(gauge_blocks, 1):
                self._build_gauge_sheet(wb, f"مقياس {i}", block["content"])

            # Sheet لـ KPIs
            kpi_blocks = [b for b in blocks if b["block_type"] == "kpi"]
            if kpi_blocks:
                self._build_kpi_sheet(wb, kpi_blocks)

            # Sheet لكل لوحة (بتنسيق مصغر)
            dashboard_blocks = [b for b in blocks if b["block_type"] == "dashboard"]
            for block in dashboard_blocks:
                self._build_dashboard_sheet(wb, block["content"])

            # الشيت الأول ظاهراً عند الفتح يكون "ملخص" دائماً
            if "ملخص" in wb.sheetnames:
                wb.move_sheet("ملخص", offset=-len(wb.sheetnames))
                wb.active = wb.sheetnames.index("ملخص")

            wb.save(str(output_path))
            logger.info("Excel exported: %s", output_path)
            return {"ok": True, "path": str(output_path)}

        except Exception as e:
            logger.error("Excel export error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  أدوات مشتركة: تنسيق أرقام + روابط داخلية + كتابة Markdown
    # ──────────────────────────────────────────────────────────

    def _number_format_for(self, value) -> str:
        """
        إرجاع صيغة تنسيق Excel مناسبة لنوع القيمة: فواصل آلاف للأعداد
        الصحيحة، منزلتان عشريتان ثابتتان للكسور، بدون تنسيق للنصوص.
        """
        if isinstance(value, bool):
            return "General"
        if isinstance(value, int):
            return "#,##0"
        if isinstance(value, float):
            return "#,##0.00"
        return "General"

    def _apply_number_format(self, cell, value) -> None:
        if isinstance(value, Number) and not isinstance(value, bool):
            cell.number_format = self._number_format_for(value)

    def _add_internal_hyperlink(self, cell, sheet_name: str, text: str = None) -> None:
        """
        رابط داخلي فعلي من خلية إلى بداية شيت آخر — يظهر بلون وتسطير
        الروابط المعتادين، وينقل المستخدم فوراً عند الضغط داخل Excel.
        """
        cell.value = text or sheet_name
        cell.hyperlink = f"#'{sheet_name}'!A1"
        cell.font = Font(color=COLOR_LINK, underline="single", size=10)

    def _write_markdown_blocks(self, ws, start_row: int, start_col: int,
                                blocks: list[dict], header_fill, header_font,
                                border, max_span: int = 8) -> int:
        """
        كتابة قائمة بلوكات Markdown (من _parse_markdown_blocks) بدءاً من
        (start_row, start_col)، وإرجاع أول صف فارغ بعدها مباشرة.

        - header: خط أكبر + عريض + لون متدرّج (h1/h2)، مع merge_cells
          عبر max_span عمود لإبرازه كعنوان قسم حقيقي.
        - paragraph: تُكتب كخلية واحدة تحتوي CellRichText (أجزاء عريضة
          حقيقية) بدل نص خام بعلامتي نجمة.
        - table: جدول Excel حقيقي بحدود وتلوين رأس — أو بدون صف رأس
          ملوَّن لو كان الرأس فارغاً بالكامل (رسم بياني نصي █/░).
        """
        row = start_row
        for block in blocks:
            btype = block["type"]

            if btype == "blank":
                row += 1
                continue

            if btype == "header":
                level = block["level"]
                style = _HEADER_STYLE_BY_LEVEL.get(level, _HEADER_STYLE_BY_LEVEL[2])
                cell = ws.cell(row, start_col, block["text"])
                cell.font = Font(bold=True, size=style["size"], color=style["color"])
                cell.alignment = Alignment(horizontal="right")
                try:
                    ws.merge_cells(
                        start_row=row, start_column=start_col,
                        end_row=row, end_column=start_col + max_span - 1,
                    )
                except Exception:
                    pass
                row += 1
                continue

            if btype == "paragraph":
                richtext = _runs_to_richtext(block["runs"])
                cell = ws.cell(row, start_col)
                cell.value = richtext
                cell.alignment = Alignment(horizontal="right", wrap_text=True)
                row += 1
                continue

            if btype == "table":
                row = self._write_excel_table(
                    ws, row, start_col, block["header"], block["rows"],
                    block["has_header"], header_fill, header_font, border,
                )
                row += 1  # سطر فارغ بعد الجدول
                continue

        return row

    def _write_excel_table(self, ws, start_row: int, start_col: int,
                            header: list[str], data_rows: list[list[str]],
                            has_header: bool, header_fill, header_font, border) -> int:
        """كتابة جدول Markdown مُحلَّل كجدول Excel حقيقي، وإرجاع آخر صف مكتوب."""
        r = start_row
        n_cols = len(header) if header else (len(data_rows[0]) if data_rows else 0)
        if n_cols == 0:
            return r

        if has_header:
            for j, col_name in enumerate(header):
                c = ws.cell(r, start_col + j, str(col_name))
                c.fill, c.font, c.border = header_fill, header_font, border
                c.alignment = Alignment(horizontal="center")
            r += 1

        for row_data in data_rows:
            for j in range(n_cols):
                val = row_data[j] if j < len(row_data) else ""
                c = ws.cell(r, start_col + j, val)
                c.border = border
                c.alignment = Alignment(horizontal="center")
            r += 1

        return r - 1

    # ──────────────────────────────────────────────────────────
    #  بناء الـ Sheets
    # ──────────────────────────────────────────────────────────

    def _build_summary_sheet(self, wb, title: str, blocks: list,
                              block_sheet_names: dict) -> None:
        """
        Sheet ملخص منظَّم كتقرير حقيقي:
        - فقرات Markdown فعلية (عناوين/عريض/جداول).
        - بطاقات KPI ملوّنة (خلايا مدمجة بخلفية وحدود) بدل أسطر نصية.
        - Gauge/Chart: رابط داخلي فعلي لشيت التفاصيل بدل نص ميت، بالإضافة
          لصورة الرسم البياني الثابتة بجانب سطره لمن لا يريد فتح الشيت.
        """
        ws = wb.create_sheet("ملخص")
        ws.sheet_view.rightToLeft = True   # RTL

        header_fill = PatternFill("solid", fgColor=COLOR_HEADER_BG)
        header_font = Font(bold=True, color=COLOR_HEADER_FG, size=10)
        thin   = Side(style="thin", color="CBD5E1")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        card_fill = PatternFill("solid", fgColor=COLOR_CARD_BG)
        card_border = Border(
            left=Side(style="thin", color=COLOR_CARD_BORDER),
            right=Side(style="thin", color=COLOR_CARD_BORDER),
            top=Side(style="thin", color=COLOR_CARD_BORDER),
            bottom=Side(style="thin", color=COLOR_CARD_BORDER),
        )

        row = 1
        # عنوان التقرير
        ws.cell(row, 1, title)
        ws.cell(row, 1).font = Font(bold=True, size=16, color=COLOR_TITLE)
        ws.cell(row, 1).alignment = Alignment(horizontal="center")
        ws.merge_cells(f"A{row}:H{row}")
        row += 2

        # طابور الصور المؤجَّلة (نضيفها في النهاية بعد معرفة كل الصفوف
        # حتى لا تتحرك المحتويات لاحقاً وتتراكب مع الصور)
        pending_images: list[tuple] = []

        for block in blocks:
            btype   = block.get("block_type")
            content = block.get("content", {})

            if btype == "paragraph":
                text = content.get("text", "")
                md_blocks = _parse_markdown_blocks(text)
                row = self._write_markdown_blocks(
                    ws, row, 1, md_blocks, header_fill, header_font, border,
                )
                row += 1

            elif btype == "kpi":
                label  = content.get("label", "KPI")
                actual = content.get("actual_value", 0)
                target = content.get("target_value", 0)
                unit   = content.get("unit", "")

                ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
                ws.cell(row, 1, label).font = Font(bold=True, color=COLOR_TITLE)

                val_cell = ws.cell(row, 3, actual)
                self._apply_number_format(val_cell, actual)
                val_cell.font = Font(bold=True, color=COLOR_KPI_VALUE)
                if unit:
                    ws.cell(row, 4, unit)

                target_cell = ws.cell(row, 5, target)
                self._apply_number_format(target_cell, target)
                ws.cell(row, 6, f"(الهدف)")
                ws.cell(row, 6).font = Font(italic=True, color="64748B", size=9)

                for c in range(1, 7):
                    ws.cell(row, c).fill = card_fill
                    ws.cell(row, c).border = card_border
                row += 1

            elif btype == "gauge":
                label   = content.get("label", "Gauge")
                current = content.get("current_value", 0)
                mn      = content.get("min_value", 0)
                mx      = content.get("max_value", 100)
                pct     = ((current - mn) / (mx - mn) * 100) if mx != mn else 0

                ws.cell(row, 1, label).font = Font(bold=True, color=COLOR_TITLE)
                cur_cell = ws.cell(row, 2, current)
                self._apply_number_format(cur_cell, current)
                ws.cell(row, 3, f"({pct:.1f}%)")

                sheet_name = block_sheet_names.get(id(block))
                if sheet_name:
                    self._add_internal_hyperlink(ws.cell(row, 4), sheet_name, "🔗 التفاصيل")
                for c in range(1, 5):
                    ws.cell(row, c).fill = card_fill
                    ws.cell(row, c).border = card_border
                row += 1

            elif btype == "chart":
                chart_title = content.get("title", "رسم بياني")
                ws.cell(row, 1, f"📊 {chart_title}").font = Font(bold=True, color=COLOR_TITLE)

                sheet_name = block_sheet_names.get(id(block))
                if sheet_name:
                    self._add_internal_hyperlink(ws.cell(row, 3), sheet_name, "🔗 التفاصيل والرسم الحي")
                ws.cell(row, 1).fill = card_fill
                ws.cell(row, 1).border = card_border

                # صورة ثابتة صغيرة للرسم بجانب سطره — تُؤجَّل لتُضاف
                # بعد إتمام كل الكتابة النصية، فوق صف فارغ محجوز.
                data   = content.get("data", [])
                x_col  = content.get("x_col", "")
                y_cols = content.get("y_cols", [])
                ctype  = content.get("chart_type", "bar")
                if data and x_col and y_cols:
                    pending_images.append((row + 1, data, x_col, y_cols, ctype, chart_title))
                    row += 9  # مساحة محجوزة للصورة الصغيرة
                else:
                    row += 1

        # ── إضافة الصور المؤجَّلة الآن (بعد ثبات كل الصفوف) ──
        for anchor_row, data, x_col, y_cols, ctype, chart_title in pending_images:
            try:
                img_buf = render_chart(data, x_col, y_cols, chart_type=ctype,
                                        title=chart_title, width=700, height=380)
                xl_img = XLImage(img_buf)
                xl_img.width = 350
                xl_img.height = 190
                ws.add_image(xl_img, f"A{anchor_row}")
            except Exception as e:
                logger.error("Summary chart thumbnail error: %s", e)

        ws.freeze_panes = "A3"
        self._auto_width(ws)

    def _build_dashboard_sheet(self, wb, content: dict) -> None:
        """
        Sheet للقطة لوحة كاملة — كل عمود من أعمدة اللوحة يُكتب في نطاق
        أعمدة مستقل، مع عنوان قسم مدمج (merge_cells) لكل عمود ولصف
        المؤشرات، وتجميد الصف الأول لتسهيل التصفح في اللوحات الكبيرة.
        الفقرات/نص السرد داخل الخلايا يمر الآن عبر نفس كاتب Markdown
        المستخدَم في شيت "ملخص" بدل أسطر نصية خام.
        """
        title = content.get("title", "لوحة معلومات")
        ws = wb.create_sheet((f"لوحة - {title}")[:31])
        ws.sheet_view.rightToLeft = True

        header_fill = PatternFill("solid", fgColor=COLOR_HEADER_BG)
        header_font = Font(bold=True, color=COLOR_HEADER_FG, size=10)
        thin = Side(style="thin", color="CBD5E1")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        ws.cell(1, 1, title).font = Font(bold=True, size=14, color=COLOR_TITLE)
        ws.freeze_panes = "A3"

        row_cursor = 3
        gauges = content.get("gauges", [])
        col_block_width = 6

        if gauges:
            ws.cell(row_cursor, 1, "🎯 المؤشرات الرئيسية").font = Font(bold=True, color=COLOR_H1)
            try:
                ws.merge_cells(
                    start_row=row_cursor, start_column=1,
                    end_row=row_cursor, end_column=max(1, len(gauges) * 3 - 1),
                )
            except Exception:
                pass
            row_cursor += 1
            gauge_row_start = row_cursor
            for i, g in enumerate(gauges):
                self._write_snapshot_cell(ws, g, gauge_row_start, i * 3 + 1, header_fill, header_font, border)
            row_cursor += 9

        for ci, col_cells in enumerate(content.get("columns", [])):
            start_col = ci * col_block_width + 1
            ws.cell(row_cursor, start_col, f"العمود {ci + 1}").font = Font(bold=True, color=COLOR_H1)
            try:
                ws.merge_cells(
                    start_row=row_cursor, start_column=start_col,
                    end_row=row_cursor, end_column=start_col + col_block_width - 2,
                )
            except Exception:
                pass

        col_row_start = row_cursor + 1
        for ci, col_cells in enumerate(content.get("columns", [])):
            r = col_row_start
            for cell in col_cells:
                r = self._write_snapshot_cell(
                    ws, cell, r, ci * col_block_width + 1, header_fill, header_font, border,
                ) + 1

        self._auto_width(ws)

    def _write_snapshot_cell(self, ws, cell: dict, start_row: int, start_col: int,
                              header_fill, header_font, border) -> int:
        """كتابة خلية واحدة بدءاً من (start_row, start_col)، وإرجاع آخر صف مكتوب."""
        ctype = cell.get("type")
        data = cell.get("content", {}) or {}
        title = cell.get("title") or ""

        r = start_row
        if title:
            ws.cell(r, start_col, title).font = Font(bold=True, color=COLOR_TITLE)
            r += 1

        if ctype in ("table", "chart"):
            columns_list = data.get("columns", [])
            if columns_list:
                for j, col_name in enumerate(columns_list):
                    c = ws.cell(r, start_col + j, str(col_name))
                    c.fill, c.font, c.border = header_fill, header_font, border
                r += 1
                for row_data in data.get("rows", []):
                    for j, col_name in enumerate(columns_list):
                        val = row_data.get(col_name, "")
                        c = ws.cell(r, start_col + j, val)
                        c.border = border
                        self._apply_number_format(c, val)
                    r += 1
        elif ctype == "gauge":
            row = (data.get("rows") or [{}])[0]
            for label, key, default in (("القيمة الحالية", "current_value", 0),
                                          ("الحد الأدنى", "min_value", 0),
                                          ("الحد الأقصى", "max_value", 100)):
                ws.cell(r, start_col, label)
                val = row.get(key, default)
                c = ws.cell(r, start_col + 1, val)
                self._apply_number_format(c, val)
                r += 1
        elif ctype == "kpi":
            row = (data.get("rows") or [{}])[0]
            actual = row.get("actual_value", 0)
            target = row.get("target_value", 0)
            ws.cell(r, start_col, "القيمة الفعلية")
            self._apply_number_format(ws.cell(r, start_col + 1, actual), actual)
            r += 1
            ws.cell(r, start_col, "الهدف")
            self._apply_number_format(ws.cell(r, start_col + 1, target), target)
            r += 1
        elif ctype == "story":
            story_text = data.get("story", "") or ""
            md_blocks = _parse_markdown_blocks(story_text)
            thin = Side(style="thin", color="CBD5E1")
            border_local = Border(left=thin, right=thin, top=thin, bottom=thin)
            r = self._write_markdown_blocks(
                ws, r, start_col, md_blocks, header_fill, header_font,
                border_local, max_span=4,
            )

        return r

    def _build_table_sheet(self, wb, sheet_name: str, content: dict) -> None:
        """Sheet بيانات جدول."""
        data    = content.get("data", [])
        columns = content.get("columns", [])
        if not data or not columns:
            return

        ws = wb.create_sheet(sheet_name)
        ws.sheet_view.rightToLeft = True

        # Header
        header_fill = PatternFill("solid", fgColor=COLOR_HEADER_BG)
        header_font = Font(bold=True, color=COLOR_HEADER_FG, size=10)
        thin        = Side(style="thin", color="CBD5E1")
        border      = Border(left=thin, right=thin, top=thin, bottom=thin)

        for col_idx, col_name in enumerate(columns, 1):
            cell = ws.cell(1, col_idx, str(col_name))
            cell.fill      = header_fill
            cell.font      = header_font
            cell.border    = border
            cell.alignment = Alignment(horizontal="center")

        # البيانات
        alt_fill = PatternFill("solid", fgColor=COLOR_ROW_ALT)
        for row_idx, row_data in enumerate(data, 2):
            fill = alt_fill if row_idx % 2 == 0 else None
            for col_idx, col_name in enumerate(columns, 1):
                val  = row_data.get(col_name, "")
                cell = ws.cell(row_idx, col_idx, val)
                if fill:
                    cell.fill = fill
                cell.border    = border
                cell.alignment = Alignment(horizontal="center")
                self._apply_number_format(cell, val)

        ws.freeze_panes = "A2"   # تجميد الـ header
        self._auto_width(ws)

    def _build_chart_sheet(self, wb, sheet_name: str, content: dict) -> None:
        """Sheet يحتوي بيانات الرسم + رسم بياني حي (Excel Chart) يمكن تعديله."""
        data   = content.get("data", [])
        x_col  = content.get("x_col", "")
        y_cols = content.get("y_cols", [])
        ctype  = content.get("chart_type", "bar")
        title  = content.get("title", sheet_name)

        if not data or not x_col or not y_cols:
            return

        ws = wb.create_sheet(sheet_name)
        ws.sheet_view.rightToLeft = True

        # ── كتابة جدول البيانات المصدر (يُستخدم كمصدر مباشر للرسم) ──
        header_fill = PatternFill("solid", fgColor=COLOR_HEADER_BG)
        header_font = Font(bold=True, color=COLOR_HEADER_FG, size=10)
        thin        = Side(style="thin", color="CBD5E1")
        border      = Border(left=thin, right=thin, top=thin, bottom=thin)

        columns = [x_col] + list(y_cols)
        for col_idx, col_name in enumerate(columns, 1):
            cell = ws.cell(1, col_idx, str(col_name))
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = Alignment(horizontal="center")

        for row_idx, row_data in enumerate(data, 2):
            for col_idx, col_name in enumerate(columns, 1):
                val = row_data.get(col_name, "")
                cell = ws.cell(row_idx, col_idx, val)
                cell.border = border
                cell.alignment = Alignment(horizontal="center")
                self._apply_number_format(cell, val)

        self._auto_width(ws)
        n_rows = len(data) + 1  # شامل الهيدر

        # ── بناء الرسم البياني الحي ──
        chart_map = {
            "bar": BarChart, "line": LineChart,
            "pie": PieChart, "area": AreaChart,
            "scatter": LineChart,  # تقريب: خط بدون تعبئة + علامات فقط
        }
        ChartClass = chart_map.get(ctype, BarChart)
        chart = ChartClass()
        chart.title = title
        chart.style = 10
        chart.height = 9
        chart.width = 18
        if hasattr(chart, "type") and ctype == "bar":
            chart.type = "col"

        cats = Reference(ws, min_col=1, min_row=2, max_row=n_rows)
        data_ref = Reference(ws, min_col=2, min_row=1, max_col=1 + len(y_cols), max_row=n_rows)
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats)

        # تلوين السلاسل ليطابق ثيم التطبيق، وتحويلها لعلامات فقط لو scatter
        for i, series in enumerate(chart.series):
            color = CHART_COLORS[i % len(CHART_COLORS)]
            if ctype == "pie":
                break  # PieChart يُلوَّن حسب النقاط لا السلاسل
            if ctype == "scatter":
                series.marker = Marker(symbol="circle", size=7)
                series.marker.graphicalProperties.solidFill = color
                series.graphicalProperties.line.noFill = True
            elif ctype in ("bar", "area"):
                series.graphicalProperties.solidFill = color
            else:  # line
                series.graphicalProperties.line.solidFill = color
                series.graphicalProperties.line.width = 22000

        anchor_col = get_column_letter(len(columns) + 2)
        ws.add_chart(chart, f"{anchor_col}2")

    def _build_gauge_sheet(self, wb, sheet_name: str, content: dict) -> None:
        """Sheet يحتوي صورة Gauge (Excel لا يملك نوع رسم gauge أصلي)."""
        current = content.get("current_value", 0)
        mn      = content.get("min_value", 0)
        mx      = content.get("max_value", 100)
        label   = content.get("label", sheet_name)

        ws = wb.create_sheet(sheet_name)
        ws.sheet_view.rightToLeft = True

        try:
            img_buf = render_gauge(current, mn, mx, label=label, width=900, height=560)
            xl_img = XLImage(img_buf)
            xl_img.width = 630
            xl_img.height = 392
            ws.add_image(xl_img, "B2")
        except Exception as e:
            logger.error("Gauge sheet image error: %s", e)
            ws.cell(1, 1, f"{label}: {current} (النطاق {mn}–{mx})")

        # قيم خام أسفل الصورة لسهولة إعادة الاستخدام في صيغ Excel
        ws.cell(22, 2, "القيمة الحالية")
        self._apply_number_format(ws.cell(22, 3, current), current)
        ws.cell(23, 2, "الحد الأدنى")
        self._apply_number_format(ws.cell(23, 3, mn), mn)
        ws.cell(24, 2, "الحد الأقصى")
        self._apply_number_format(ws.cell(24, 3, mx), mx)

    def _build_kpi_sheet(self, wb, kpi_blocks: list) -> None:
        """Sheet مخصص لـ KPIs."""
        ws = wb.create_sheet("المؤشرات")
        ws.sheet_view.rightToLeft = True

        headers = ["المؤشر", "القيمة الفعلية", "الهدف", "الوحدة", "الفجوة"]
        header_fill = PatternFill("solid", fgColor=COLOR_HEADER_BG)
        header_font = Font(bold=True, color=COLOR_HEADER_FG)

        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(1, col_idx, h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        for row_idx, block in enumerate(kpi_blocks, 2):
            c      = block["content"]
            actual = c.get("actual_value", 0)
            target = c.get("target_value", 0)
            gap    = actual - target
            ws.cell(row_idx, 1, c.get("label", ""))
            self._apply_number_format(ws.cell(row_idx, 2, actual), actual)
            self._apply_number_format(ws.cell(row_idx, 3, target), target)
            ws.cell(row_idx, 4, c.get("unit", ""))
            cell_gap = ws.cell(row_idx, 5, gap)
            self._apply_number_format(cell_gap, gap)
            # تلوين الفجوة: أخضر إيجابي، أحمر سلبي
            cell_gap.font = Font(
                color="166534" if gap >= 0 else "991B1B",
                bold=True,
            )

        ws.freeze_panes = "A2"
        self._auto_width(ws)

    def _auto_width(self, ws) -> None:
        """
        ضبط عرض الأعمدة تلقائياً.

        نتجنب الاعتماد على ws.columns مباشرة لأن بعض الصفوف تحتوي
        خلايا مدمجة (MergedCell عبر merge_cells)، وهذا النوع لا يملك
        خاصية .column قابلة للاستخدام في كل إصدارات openpyxl، مما قد
        يرمي استثناءً ويوقف تصدير الملف بالكامل. بدل ذلك نمر على كل
        الخلايا عبر iter_rows() ونتجاهل أي MergedCell أو خلية فارغة
        بأمان. القيمة النصية لخلايا CellRichText تُقرأ عبر str() التي
        تُرجع النص المُجمَّع من كل أجزائها.
        """
        from openpyxl.cell.cell import MergedCell

        widths: dict[str, int] = {}
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell) or cell.value is None:
                    continue
                try:
                    text_len = len(str(cell.value))
                except Exception:
                    continue
                col_letter = get_column_letter(cell.column)
                widths[col_letter] = max(widths.get(col_letter, 0), text_len)

        for col_letter, max_len in widths.items():
            ws.column_dimensions[col_letter].width = min(max_len + 4, 40)
