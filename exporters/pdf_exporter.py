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
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, PageBreak, Image,
)
from reportlab.pdfbase          import pdfmetrics
from reportlab.pdfbase.ttfonts  import TTFont
from reportlab.lib.enums        import TA_RIGHT, TA_LEFT, TA_CENTER

import arabic_reshaper
from bidi.algorithm import get_display

from exporters.report_manager import ReportManager
from exporters.chart_renderer import render_chart, render_gauge

logger = logging.getLogger(__name__)

# ── ثوابت ──────────────────────────────────────────────────
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 2 * cm

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
        # جلب بيانات التقرير
        reports = self.rm.list_reports()
        report  = next((r for r in reports if r["id"] == report_id), None)
        if not report:
            return {"ok": False, "error": "التقرير غير موجود"}

        blocks = self.rm.get_blocks(report_id)

        try:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            doc   = SimpleDocTemplate(
                str(output_path),
                pagesize    = A4,
                rightMargin = MARGIN,
                leftMargin  = MARGIN,
                topMargin   = MARGIN,
                bottomMargin= MARGIN,
            )
            story = self._build_story(report["title"], blocks)
            doc.build(story)

            logger.info("PDF exported: %s", output_path)
            return {"ok": True, "path": str(output_path)}

        except Exception as e:
            logger.error("PDF export error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  بناء محتوى الـ PDF
    # ──────────────────────────────────────────────────────────

    def _build_story(self, title: str, blocks: list) -> list:
        """بناء قائمة العناصر لـ ReportLab."""
        story = []

        # عنوان التقرير
        story.append(Paragraph(_prepare_text(title), self._styles["title"]))
        story.append(Spacer(1, 0.5 * cm))

        for block in blocks:
            btype   = block.get("block_type", "")
            content = block.get("content", {})

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

            story.append(Spacer(1, 0.3 * cm))

        return story

    def _render_paragraph(self, content: dict) -> list:
        """تحويل Markdown بسيط إلى Paragraphs."""
        text  = content.get("text", "")
        lines = text.splitlines()
        items = []
        for line in lines:
            line = line.strip()
            if not line:
                items.append(Spacer(1, 0.2 * cm))
            elif line.startswith("## "):
                items.append(Paragraph(_prepare_text(line[3:]), self._styles["h2"]))
            elif line.startswith("# "):
                items.append(Paragraph(_prepare_text(line[2:]), self._styles["h1"]))
            else:
                items.append(Paragraph(_prepare_text(line), self._styles["body"]))
        return items

    def _render_table(self, content: dict) -> list:
        """تحويل بيانات جدول إلى ReportLab Table."""
        data    = content.get("data", [])
        columns = content.get("columns", [])

        if not data or not columns:
            return [Paragraph(_prepare_text("جدول فارغ"), self._styles["body"])]

        # بناء الصفوف
        header = [_prepare_text(str(c)) for c in columns]
        rows   = [header]
        for row in data:
            rows.append([_prepare_text(str(row.get(c, ""))) for c in columns])

        # حساب عرض الأعمدة
        available = PAGE_WIDTH - 2 * MARGIN
        col_width = available / len(columns)
        col_widths = [col_width] * len(columns)

        tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            # Header
            ("BACKGROUND",   (0, 0), (-1, 0),  colors.HexColor("#1E3A5F")),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",     (0, 0), (-1, -1), self._font_name),
            ("FONTSIZE",     (0, 0), (-1, 0),  9),
            ("FONTSIZE",     (0, 1), (-1, -1), 8),
            ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
            # Alternating rows
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
            # Grid
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

    def _render_gauge(self, content: dict) -> list:
        """رسم Gauge فعلي (صورة PNG) في الـ PDF، بنفس تصميم الواجهة."""
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

        available_w = (PAGE_WIDTH - 2 * MARGIN) * 0.75
        aspect = 560 / 900
        img = Image(img_buf, width=available_w, height=available_w * aspect)
        return [img]

    def _render_chart(self, content: dict) -> list:
        """توليد صورة رسم بياني فعلية (Pillow) وتضمينها في الـ PDF."""
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

        available_w = PAGE_WIDTH - 2 * MARGIN
        aspect = 560 / 1000
        img = Image(img_buf, width=available_w, height=available_w * aspect)
        return [img]
