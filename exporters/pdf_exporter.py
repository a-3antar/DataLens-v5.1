"""
exporters/pdf_exporter.py
=========================
تصدير التقارير إلى PDF مع دعم كامل للعربية والإنجليزية.
يستخدم ReportLab + arabic-reshaper + python-bidi.
"""

import logging
from pathlib import Path
from typing  import Optional
from io      import BytesIO

from reportlab.lib              import colors
from reportlab.lib.pagesizes    import A4
from reportlab.lib.styles       import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units        import cm
from reportlab.platypus         import (
    BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, Table, TableStyle, PageBreak, Image,
    FrameBreak, NextPageTemplate
)
from reportlab.pdfbase          import pdfmetrics
from reportlab.pdfbase.ttfonts  import TTFont
from reportlab.lib.enums        import TA_RIGHT, TA_LEFT, TA_CENTER

import re
import arabic_reshaper
from bidi.algorithm import get_display

from exporters.report_manager import ReportManager
from exporters.chart_renderer import render_chart, render_gauge

logger = logging.getLogger(__name__)

# ── ثوابت ──────────────────────────────────────────────────
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 2 * cm
_MAX_DASHBOARD_COLUMNS = 4   # أقصى عدد أعمدة نبني لها قوالب صفحة مسبقاً

# ── أنماط Markdown خفيفة مدعومة داخل الفقرات/نص السرد ──────
_MD_HEADER_RE     = re.compile(r'^(#{1,6})\s+(.*)$')
_MD_BOLD_RE       = re.compile(r'\*\*(.+?)\*\*')
_MD_TABLE_SEP_RE  = re.compile(r'^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$')


# ألوان متسلسلة للرسوم البيانية (متوافقة مع ثيم التطبيق)
CHART_PALETTE = ["#2563EB", "#10B981", "#F59E0B", "#A855F7", "#EF4444", "#0EA5E9"]


def _reshape(text: str) -> str:
    """إعادة تشكيل النص العربي للعرض الصحيح في PDF."""
    if not text:
        return ""
    try:
        reshaped = arabic_reshaper.reshape(str(text))
        # نحدد اتجاه الفقرة صراحة (RTL) بدل الاعتماد على الكشف التلقائي،
        # لأن السلوك التلقائي قد يختلف بين إصدارات مكتبة python-bidi
        # وقد ينتج ترتيباً معكوساً مع نصوص عربية تحتوي أرقاماً لاتينية.
        return get_display(reshaped, base_dir="R")
    except Exception:
        return str(text)


def _is_arabic(text: str) -> bool:
    """هل النص يحتوي على أحرف عربية؟"""
    if not text:
        return False
    return any("\u0600" <= c <= "\u06FF" for c in str(text))


def _prepare_text(text: str) -> str:
    """تجهيز النص: إعادة تشكيل لو كان عربياً."""
    text = str(text)
    if _is_arabic(text):
        return _reshape(text)
    return text

def _prepare_rich_text(text: str) -> str:
    """
    تحويل **نص عريض** (Markdown) إلى <b>...</b> (وسم ReportLab)، مع
    تطبيق reshape/bidi على كل جزء نصي عربي على حدة بحيث لا يتأثر
    الوسم نفسه بعملية التشكيل. يُستخدم بدل _prepare_text في أي مكان
    قد يحتوي تنسيق **bold** فعلي (فقرات التقرير ونص Story Telling).
    """
    parts = _MD_BOLD_RE.split(text)   # [نص، عريض، نص، عريض، ...]
    out = []
    for i, part in enumerate(parts):
        if not part:
            continue
        prepared = _prepare_text(part)
        out.append(f"<b>{prepared}</b>" if i % 2 == 1 else prepared)
    return "".join(out)

class PDFExporter:
    """
    تصدير تقرير إلى PDF.

    الاستخدام:
        exp = PDFExporter(report_manager)
        exp.export(report_id, Path("report.pdf"))
    """

    def __init__(self, report_manager: ReportManager):
        self.rm         = report_manager
        self._font_name = "Helvetica"   # افتراضي لو لم يوجد خط عربي
        self._font_path: Optional[Path] = None
        self._setup_font()
        self._styles = self._build_styles()

    # ──────────────────────────────────────────────────────────
    #  إعداد الخط
    # ──────────────────────────────────────────────────────────

    def _setup_font(self) -> None:
        """
        تسجيل خط عربي إن وُجد.
        يبحث عن خط Amiri أو أي خط TTF عربي في مجلد النظام.
        """
        font_candidates = [
            # Windows
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/tahoma.ttf"),
            Path("C:/Windows/Fonts/calibri.ttf"),
            # Linux
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/freefont/FreeSerif.ttf"),
        ]
        for font_path in font_candidates:
            if font_path.exists():
                try:
                    pdfmetrics.registerFont(TTFont("ArabicFont", str(font_path)))
                    self._font_name = "ArabicFont"
                    self._font_path = font_path
                    logger.info("Arabic font registered: %s", font_path.name)
                    return
                except Exception as e:
                    logger.warning("Font registration failed (%s): %s", font_path.name, e)
        logger.warning("No Arabic font found — using Helvetica (Arabic may not render correctly)")

    def _build_doc_templates(self, doc: BaseDocTemplate) -> None:
        """
        قالب 'single': إطار واحد عادي (نفس سلوك SimpleDocTemplate
        الافتراضي) — يُستخدم لكل المحتوى العادي (فقرات، جداول، رسوم
        مستقلة). قوالب 'dash_1col'..'dash_Ncol': عدد أُطر موزّعة أفقياً
        (من اليمين لليسار) — تُستخدم فقط أثناء عرض بلوك "dashboard"
        (راجع _render_dashboard) لإنتاج تخطيط أعمدة حقيقي يدعم تعدد
        الصفحات تلقائياً، خلافاً لمحاولة سابقة بجدول Table واحد كانت
        تفشل بمجرد تجاوز أي عمود ارتفاع صفحة واحدة.

        ⚠️ قيد معروف: لو تجاوز محتوى عمود واحد ارتفاع صفحة كاملة، فائض
        المحتوى ينتقل تلقائياً إلى إطار العمود التالي (وليس لصفحة
        جديدة بنفس العمود) — قيد أساسي في تصميم multi-frame بـ
        ReportLab. مقبول عملياً لأن أعمدة اللوحة عادة محتواها محدود
        (خلايا قليلة نسبياً).
        """
        content_w = PAGE_WIDTH - 2 * MARGIN
        content_h = PAGE_HEIGHT - 2 * MARGIN

        single_frame = Frame(MARGIN, MARGIN, content_w, content_h, id="normal")
        templates = [PageTemplate(id="single", frames=[single_frame])]

        gap = 0.5 * cm
        for n in range(1, _MAX_DASHBOARD_COLUMNS + 1):
            col_w = (content_w - gap * (n - 1)) / n
            frames = []
            for i in range(n):
                # ترتيب من اليمين لليسار: العمود الأول (i=0) أقصى اليمين
                x = MARGIN + (n - 1 - i) * (col_w + gap)
                frames.append(Frame(x, MARGIN, col_w, content_h, id=f"col_{i}"))
            templates.append(PageTemplate(id=f"dash_{n}col", frames=frames))

        doc.addPageTemplates(templates)

    # ──────────────────────────────────────────────────────────
    #  أنماط النص
    # ──────────────────────────────────────────────────────────

    def _build_styles(self) -> dict:
        base = getSampleStyleSheet()
        fn   = self._font_name
        return {
            "title": ParagraphStyle(
                "title",
                fontName = fn,
                fontSize = 18,
                alignment= TA_CENTER,
                spaceAfter= 20,
                textColor= colors.HexColor("#1E3A5F"),
            ),
            "h1": ParagraphStyle(
                "h1",
                fontName = fn,
                fontSize = 14,
                alignment= TA_RIGHT,
                spaceBefore= 12,
                spaceAfter = 6,
                textColor= colors.HexColor("#2563EB"),
            ),
            "h2": ParagraphStyle(
                "h2",
                fontName = fn,
                fontSize = 12,
                alignment= TA_RIGHT,
                spaceBefore= 8,
                spaceAfter = 4,
                textColor= colors.HexColor("#3B82F6"),
            ),
            "body": ParagraphStyle(
                "body",
                fontName = fn,
                fontSize = 10,
                alignment= TA_RIGHT,
                spaceAfter= 8,
                leading  = 16,
            ),
            "label": ParagraphStyle(
                "label",
                fontName = fn,
                fontSize = 9,
                textColor= colors.HexColor("#6B7280"),
                alignment= TA_CENTER,
            ),
            "kpi_value": ParagraphStyle(
                "kpi_value",
                fontName = fn,
                fontSize = 24,
                leading  = 30,
                alignment= TA_CENTER,
                textColor= colors.HexColor("#1D4ED8"),
            ),
        }

    # ──────────────────────────────────────────────────────────
    #  التصدير الرئيسي
    # ──────────────────────────────────────────────────────────

    def export(self, report_id: str, output_path: Path) -> dict:
        """
        تصدير تقرير إلى PDF.
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

            # 🆕 BaseDocTemplate بدل SimpleDocTemplate — يسمح بتعريف
            # قوالب صفحة متعددة الأُطر (أعمدة حقيقية) لبلوك "dashboard".
            doc = BaseDocTemplate(
                str(output_path),
                pagesize    = A4,
                rightMargin = MARGIN,
                leftMargin  = MARGIN,
                topMargin   = MARGIN,
                bottomMargin= MARGIN,
            )
            self._build_doc_templates(doc)

            story = self._build_story(report["title"], blocks)
            doc.build(story)

            logger.info("PDF exported: %s", output_path)
            return {"ok": True, "path": str(output_path)}

        except Exception as e:
            logger.error("PDF export error: %s", e)
            return {"ok": False, "error": str(e)}



    def _build_story(self, title: str, blocks: list) -> list:
        """بناء قائمة العناصر لـ ReportLab."""
        story = []

        story.append(Paragraph(_prepare_text(title), self._styles["title"]))
        story.append(Spacer(1, 0.5 * cm))

        n_blocks = len(blocks)
        for idx, block in enumerate(blocks):
            btype   = block.get("block_type", "")
            content = block.get("content", {})
            is_last = (idx == n_blocks - 1)

            if btype == "paragraph":
                story.extend(self._render_paragraph(content))
            elif btype == "table":
                story.extend(self._render_table(content))
            elif btype == "kpi":
                story.extend(self._render_kpi(content))
            elif btype == "gauge":
                story.extend(self._render_gauge(content))
            elif btype == "chart":
                story.extend(self._render_chart(content))
            elif btype == "dashboard":
                # 🆕 بلوك اللوحة يدير صفحاته وأعمدته بنفسه (PageBreak/
                # NextPageTemplate) — لا نضيف Spacer عادياً بعده.
                story.extend(self._render_dashboard(content, is_last_block=is_last))
                continue

            story.append(Spacer(1, 0.3 * cm))

        return story

    def _render_paragraph(self, content: dict, width: float = None) -> list:
        """
        تحويل نص فقرة/تحليل (Markdown خفيف) إلى Flowables حقيقية:
        - '#' إلى '######': عنوان (مستوى 1 → h1، أي مستوى أعلى → h2).
        - '**نص**': يتحوّل إلى غامق فعلي (<b>) بدل ظهور النجمتين حرفياً.
        - أسطر متتالية تبدأ بـ '|': تُجمَّع وتُبنى كجدول ReportLab حقيقي
          (راجع _render_markdown_table) بدل نص خام يحوي رموز '|'.
        - أي سطر آخر (فقرة عادية أو نقطة '• '): فقرة عادية.
        """
        text  = content.get("text", "")
        lines = text.splitlines()
        items = []
        i = 0
        n = len(lines)

        while i < n:
            raw_line = lines[i]
            line = raw_line.strip()

            if not line:
                items.append(Spacer(1, 0.2 * cm))
                i += 1
                continue

            if line.startswith("|"):
                table_lines = []
                while i < n and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i].strip())
                    i += 1
                tbl = self._render_markdown_table(table_lines, width=width)
                if tbl:
                    items.append(tbl)
                    items.append(Spacer(1, 0.2 * cm))
                continue

            header_match = _MD_HEADER_RE.match(line)
            if header_match:
                level = len(header_match.group(1))
                header_text = header_match.group(2).strip()
                style = self._styles["h1"] if level == 1 else self._styles["h2"]
                items.append(Paragraph(_prepare_rich_text(header_text), style))
                i += 1
                continue

            items.append(Paragraph(_prepare_rich_text(line), self._styles["body"]))
            i += 1

        return items

    def _render_markdown_table(self, lines: list[str], width: float = None) -> Optional[Table]:
        """
        تحويل جدول Markdown (| عمود | عمود |) إلى جدول ReportLab حقيقي.
        صف الفاصل (|---|:---:|...) يُتجاهل تلقائياً. رؤوس فارغة بالكامل
        (حالة "الرسم البياني النصي" █/░ في نص السرد) لا تُرسم كصف Header
        ملوَّن — فقط بيانات بدون رأس، لتفادي شريط فارغ ملوَّن فوقها
        (نفس فلسفة القاعدة المكافئة في ui/common.py للعرض على الشاشة).
        """
        rows = []
        for raw in lines:
            raw = raw.strip()
            if not raw.startswith("|") or _MD_TABLE_SEP_RE.match(raw):
                continue
            rows.append([c.strip() for c in raw.strip("|").split("|")])

        if not rows:
            return None

        n_cols = max(len(r) for r in rows)
        pad = lambda r: r + [""] * (n_cols - len(r))
        header, data_rows = pad(rows[0]), [pad(r) for r in rows[1:]]
        has_real_header = any(h for h in header)

        prepared_header = [Paragraph(_prepare_rich_text(str(c)), self._styles["label"]) for c in header]
        prepared_data = [
            [Paragraph(_prepare_rich_text(str(c)), self._styles["body"]) for c in row]
            for row in data_rows
        ]
        all_rows = ([prepared_header] if has_real_header else []) + prepared_data
        if not all_rows:
            return None

        available  = width or (PAGE_WIDTH - 2 * MARGIN)
        col_width  = available / n_cols
        tbl = Table(all_rows, colWidths=[col_width] * n_cols,
                    repeatRows=1 if has_real_header else 0)

        style_cmds = [
            ("FONTNAME",     (0, 0), (-1, -1), self._font_name),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]
        if has_real_header:
            style_cmds += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
                ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ]
        tbl.setStyle(TableStyle(style_cmds))
        return tbl


    def _render_markdown_table(self, lines: list[str], width: float = None) -> Optional[Table]:
        """
        تحويل جدول Markdown (| عمود | عمود |) إلى جدول ReportLab حقيقي.
        صف الفاصل (|---|:---:|...) يُتجاهل تلقائياً. رؤوس فارغة بالكامل
        (حالة "الرسم البياني النصي" █/░ في نص السرد) لا تُرسم كصف Header
        ملوَّن — فقط بيانات بدون رأس، لتفادي شريط فارغ ملوَّن فوقها
        (نفس فلسفة القاعدة المكافئة في ui/common.py للعرض على الشاشة).
        """
        rows = []
        for raw in lines:
            raw = raw.strip()
            if not raw.startswith("|") or _MD_TABLE_SEP_RE.match(raw):
                continue
            rows.append([c.strip() for c in raw.strip("|").split("|")])

        if not rows:
            return None

        n_cols = max(len(r) for r in rows)
        pad = lambda r: r + [""] * (n_cols - len(r))
        header, data_rows = pad(rows[0]), [pad(r) for r in rows[1:]]
        has_real_header = any(h for h in header)

        prepared_header = [Paragraph(_prepare_rich_text(str(c)), self._styles["label"]) for c in header]
        prepared_data = [
            [Paragraph(_prepare_rich_text(str(c)), self._styles["body"]) for c in row]
            for row in data_rows
        ]
        all_rows = ([prepared_header] if has_real_header else []) + prepared_data
        if not all_rows:
            return None

        available  = width or (PAGE_WIDTH - 2 * MARGIN)
        col_width  = available / n_cols
        tbl = Table(all_rows, colWidths=[col_width] * n_cols,
                    repeatRows=1 if has_real_header else 0)

        style_cmds = [
            ("FONTNAME",     (0, 0), (-1, -1), self._font_name),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]
        if has_real_header:
            style_cmds += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
                ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ]
        tbl.setStyle(TableStyle(style_cmds))
        return tbl
        
    def _render_markdown_table(self, lines: list[str], width: float = None) -> Optional[Table]:
        """
        تحويل جدول Markdown (| عمود | عمود |) إلى جدول ReportLab حقيقي.
        صف الفاصل (|---|:---:|...) يُتجاهل تلقائياً. رؤوس فارغة بالكامل
        (حالة "الرسم البياني النصي" █/░ في نص السرد) لا تُرسم كصف Header
        ملوَّن — فقط بيانات بدون رأس، لتفادي شريط فارغ ملوَّن فوقها
        (نفس فلسفة القاعدة المكافئة في ui/common.py للعرض على الشاشة).
        """
        rows = []
        for raw in lines:
            raw = raw.strip()
            if not raw.startswith("|") or _MD_TABLE_SEP_RE.match(raw):
                continue
            rows.append([c.strip() for c in raw.strip("|").split("|")])

        if not rows:
            return None

        n_cols = max(len(r) for r in rows)
        pad = lambda r: r + [""] * (n_cols - len(r))
        header, data_rows = pad(rows[0]), [pad(r) for r in rows[1:]]
        has_real_header = any(h for h in header)

        prepared_header = [Paragraph(_prepare_rich_text(str(c)), self._styles["label"]) for c in header]
        prepared_data = [
            [Paragraph(_prepare_rich_text(str(c)), self._styles["body"]) for c in row]
            for row in data_rows
        ]
        all_rows = ([prepared_header] if has_real_header else []) + prepared_data
        if not all_rows:
            return None

        available  = width or (PAGE_WIDTH - 2 * MARGIN)
        col_width  = available / n_cols
        tbl = Table(all_rows, colWidths=[col_width] * n_cols,
                    repeatRows=1 if has_real_header else 0)

        style_cmds = [
            ("FONTNAME",     (0, 0), (-1, -1), self._font_name),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]
        if has_real_header:
            style_cmds += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
                ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ]
        tbl.setStyle(TableStyle(style_cmds))
        return tbl

    def _render_table(self, content: dict, width: float = None) -> list:
        """تحويل بيانات جدول إلى ReportLab Table."""
        data    = content.get("data", [])
        columns = content.get("columns", [])

        if not data or not columns:
            return [Paragraph(_prepare_text("جدول فارغ"), self._styles["body"])]

        header = [_prepare_text(str(c)) for c in columns]
        rows   = [header]
        for row in data:
            rows.append([_prepare_text(str(row.get(c, ""))) for c in columns])

        available  = width or (PAGE_WIDTH - 2 * MARGIN)
        col_width  = available / len(columns)
        col_widths = [col_width] * len(columns)

        tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0),  colors.HexColor("#1E3A5F")),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",     (0, 0), (-1, -1), self._font_name),
            ("FONTSIZE",     (0, 0), (-1, 0),  9),
            ("FONTSIZE",     (0, 1), (-1, -1), 8),
            ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
            ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        return [tbl]
    def _render_kpi(self, content: dict) -> list:
        """تحويل KPI إلى بطاقة في الـ PDF."""
        actual = content.get("actual_value", 0)
        target = content.get("target_value", 0)
        label  = content.get("label", "")
        unit   = content.get("unit", "")

        actual_text = f"{actual:,.2f} {unit}".strip()
        target_text = f"الهدف: {target:,.2f} {unit}".strip()

        items = []
        if label:
            items.append(Paragraph(_prepare_text(label), self._styles["label"]))
        items.append(Paragraph(_prepare_text(actual_text), self._styles["kpi_value"]))
        items.append(Spacer(1, 0.35 * cm))
        items.append(Paragraph(_prepare_text(target_text), self._styles["label"]))
        return items

    def _render_gauge(self, content: dict, width: float = None) -> list:
        """رسم Gauge فعلي (صورة PNG)، بعرض العمود/الإطار الفعلي المتاح."""
        current = content.get("current_value", 0)
        mn      = content.get("min_value", 0)
        mx      = content.get("max_value", 100)
        label   = content.get("label", "")

        try:
            img_buf = render_gauge(current, mn, mx, label=label, width=900, height=560)
        except Exception as e:
            logger.error("Gauge image build error: %s", e)
            pct = ((current - mn) / (mx - mn) * 100) if mx != mn else 0
            text = f"{label}: {current:,.2f} ({pct:.1f}%)" if label else f"{current:,.2f} ({pct:.1f}%)"
            return [Paragraph(_prepare_text(text), self._styles["body"])]

        available_w = (width * 0.95) if width else (PAGE_WIDTH - 2 * MARGIN) * 0.75
        aspect = 560 / 900
        img = Image(img_buf, width=available_w, height=available_w * aspect)
        return [img]

    def _render_chart(self, content: dict, width: float = None) -> list:
        """توليد صورة رسم بياني وتضمينها بعرض العمود/الإطار الفعلي المتاح."""
        data   = content.get("data", [])
        x_col  = content.get("x_col", "")
        y_cols = content.get("y_cols", [])
        ctype  = content.get("chart_type", "bar")
        title  = content.get("title", "")

        if not data or not x_col or not y_cols:
            return [Paragraph(_prepare_text(f"[{title or 'رسم بياني'} — لا توجد بيانات كافية للرسم]"), self._styles["label"])]

        try:
            img_buf = render_chart(data, x_col, y_cols, chart_type=ctype, title=title,
                                    width=1000, height=560)
        except Exception as e:
            logger.error("Chart image build error: %s", e)
            return [Paragraph(_prepare_text(f"[تعذر رسم: {title}]"), self._styles["label"])]

        available_w = width or (PAGE_WIDTH - 2 * MARGIN)
        aspect = 560 / 1000
        img = Image(img_buf, width=available_w, height=available_w * aspect)
        return [img]

    def _render_dashboard(self, content: dict, is_last_block: bool = False) -> list:
        """
        عرض لقطة لوحة كاملة بتخطيط أعمدة حقيقي:
        1. صف الـ Gauges يُرسم كالمعتاد في الإطار العادي (single) —
           محتواه دائماً قصير وعرضه = عرض الصفحة كاملة.
        2. قبل عرض الأعمدة: نُبدِّل قالب الصفحة إلى 'dash_Ncol' (N =
           عدد الأعمدة) عبر NextPageTemplate + PageBreak، فيصبح لدينا
           N إطار حقيقي جنباً إلى جنب (وليس Table واحد يفشل بمجرد
           تجاوز أي عمود ارتفاع صفحة).
        3. FrameBreak('col_i') يُستخدم للقفز الصريح لبداية كل عمود،
           بدل الاعتماد على الفيضان التلقائي بين الأُطر.
        4. بعد انتهاء آخر عمود، نُعيد قالب الصفحة إلى 'single' (لو لم
           يكن هذا آخر بلوك في التقرير) حتى يستكمل ما بعده بعرض الصفحة
           الكامل الاعتيادي.
        """
        story = []
        title = content.get("title", "")
        if title:
            story.append(Paragraph(_prepare_text(title), self._styles["h1"]))
            story.append(Spacer(1, 0.3 * cm))

        gauges = content.get("gauges", [])
        if gauges:
            n_g = len(gauges)
            page_w = PAGE_WIDTH - 2 * MARGIN
            gauge_col_w = page_w / n_g
            gauge_flowables = [self._render_snapshot_cell(g, width=gauge_col_w) for g in gauges]
            gauge_table = Table([gauge_flowables], colWidths=[gauge_col_w] * n_g)
            gauge_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(gauge_table)
            story.append(Spacer(1, 0.4 * cm))

        columns = [c for c in content.get("columns", []) if c]
        n = len(columns)
        if n == 0:
            return story

        n_clamped = min(n, _MAX_DASHBOARD_COLUMNS)
        page_w = PAGE_WIDTH - 2 * MARGIN
        gap = 0.5 * cm
        col_width = (page_w - gap * (n_clamped - 1)) / n_clamped

        story.append(NextPageTemplate(f"dash_{n_clamped}col"))
        story.append(PageBreak())

        for i, col_cells in enumerate(columns[:n_clamped]):
            if i > 0:
                story.append(FrameBreak(f"col_{i}"))
            story.append(Paragraph(_prepare_text(f"العمود {i + 1}"), self._styles["h2"]))
            for cell in col_cells:
                story.extend(self._render_snapshot_cell(cell, width=col_width))
                story.append(Spacer(1, 0.25 * cm))

        story.append(NextPageTemplate("single"))
        if not is_last_block:
            story.append(PageBreak())

        return story

    def _render_snapshot_cell(self, cell: dict, width: float = None) -> list:
        """تحويل خلية لوحة واحدة إلى flowables، بعرض محدَّد فعلياً
        (عرض العمود داخل بلوك dashboard، أو عرض خلية Gauge)."""
        ctype = cell.get("type")
        data = cell.get("content", {}) or {}
        title = cell.get("title") or ""
        items = []
        if title:
            items.append(Paragraph(_prepare_text(title), self._styles["h2"]))

        if ctype == "table":
            items.extend(self._render_table({
                "data": data.get("rows", []), "columns": data.get("columns", []),
            }, width=width))
        elif ctype == "chart":
            cols = data.get("columns", [])
            items.extend(self._render_chart({
                "data": data.get("rows", []),
                "x_col": cols[0] if cols else "",
                "y_cols": cols[1:3] if len(cols) > 1 else [],
                "chart_type": data.get("chart_type", "bar"),
                "title": "",
            }, width=width))
        elif ctype == "gauge":
            row = (data.get("rows") or [{}])[0]
            items.extend(self._render_gauge({
                "current_value": row.get("current_value", 0),
                "min_value": row.get("min_value", 0),
                "max_value": row.get("max_value", 100),
                "label": "",
            }, width=width))
        elif ctype == "kpi":
            row = (data.get("rows") or [{}])[0]
            items.extend(self._render_kpi({
                "actual_value": row.get("actual_value", 0),
                "target_value": row.get("target_value", 0),
                "label": "", "unit": "",
            }))
        elif ctype == "story":
            items.extend(self._render_paragraph({"text": data.get("story", "")}, width=width))

        return items

