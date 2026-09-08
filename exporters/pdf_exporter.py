"""
exporters/pdf_exporter.py
=========================
تصدير التقارير إلى PDF مع دعم كامل للعربية والإنجليزية.
يستخدم ReportLab + arabic-reshaper + python-bidi.

🆕 تحسينات مظهر PDF (هذا التحديث):
------------------------------------
1. صفحة غلاف (Cover Page): عنوان التقرير، اسم المشروع (إن وُجد)،
   تاريخ التصدير — صفحة مستقلة يتبعها PageBreak قبل بداية المحتوى.
2. Header/Footer ثابتان على كل صفحة (عدا الغلاف): تذييل برقم الصفحة
   + اسم التقرير + تاريخ التصدير — عبر onPage callback على كل
   PageTemplate (بما فيها قوالب أعمدة اللوحات dash_Ncol).
3. فواصل بصرية (HRFlowable) بين بلوكات التقرير الرئيسية بدل الاعتماد
   على Spacer فقط، لفصل الأقسام بصرياً بوضوح أكبر.
4. الجداول: محاذاة الأعمدة الرقمية لليمين بدل توسيط الجميع — يُكتشف
   العمود الرقمي تلقائياً من نوع القيم الفعلية في البيانات.
5. الرسوم والمقاييس: إطار خفيف حول الصورة + تسمية توضيحية أسفلها
   ("شكل N: العنوان") بنمط أكاديمي مألوف.
6. بطاقة KPI حقيقية (خلفية ملوّنة + حدود) بدل نص/فقرات منفصلة.
7. ألوان التقرير تتبع ثيم المشروع الفعلي: PDFExporter تقبل الآن
   theme_colors (dict: primary/accent/bg/text/card) اختيارياً — لو
   لم يُمرَّر، تُستخدم الألوان الافتراضية القديمة (متوافقة مع الإصدار
   السابق بدون أي تغيير مطلوب من المستدعي).

🧭 جدول المحتويات (TOC) الآلي: أُجِّل عمداً — يتطلب إعادة هيكلة أعمق
لتوليد Bookmarks/Outline متوافقة مع تخطيط الأعمدة المتعدد (dash_Ncol)
المستخدَم حالياً لبلوك "dashboard"، وهذا يستحق تصميماً منفصلاً بدل
حشره هنا بسرعة على حساب استقرار بقية التصدير.
"""

import logging
from pathlib import Path
from typing  import Optional
from io      import BytesIO
from datetime import datetime

from reportlab.lib              import colors
from reportlab.lib.pagesizes    import A4
from reportlab.lib.styles       import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units        import cm
from reportlab.platypus         import (
    BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, Table, TableStyle, PageBreak, Image,
    FrameBreak, NextPageTemplate, HRFlowable, KeepTogether
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

# ألوان متسلسلة للرسوم البيانية (متوافقة مع ثيم التطبيق الافتراضي —
# تُستخدم كما هي داخل exporters/chart_renderer.py الذي لا يعرف عن
# نظام الثيمات؛ التحكم بألوان الرسوم نفسها خارج نطاق هذا التحديث)
CHART_PALETTE = ["#2563EB", "#10B981", "#F59E0B", "#A855F7", "#EF4444", "#0EA5E9"]

# ألوان PDF الافتراضية (السلوك القديم قبل دعم ثيمات المشروع) — تُستخدم
# فقط لو لم يُمرَّر theme_colors عند إنشاء PDFExporter.
_DEFAULT_PDF_COLORS = {
    "primary": "#1E3A5F",
    "accent" : "#2563EB",
    "bg"     : "#FFFFFF",
    "text"   : "#1E293B",
    "card"   : "#F1F5F9",
}


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


def _lighten_hex(hex_color: str, amount: float) -> "colors.Color":
    """
    🆕 مزج لون hex مع الأبيض بنسبة amount (0=بدون تغيير، 1=أبيض تماماً)
    وإرجاعه كـ reportlab.lib.colors.Color جاهز للاستخدام مباشرة.

    بديل عن معامل "opacity" غير المدعوم في HRFlowable بهذا الإصدار من
    ReportLab — نفس الأثر البصري المطلوب (خط فاصل خافت) لكن عبر تفتيح
    اللون فعلياً بدل شفافية حقيقية غير متاحة.
    """
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        h = "2563EB"
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    amount = max(0.0, min(1.0, amount))
    r = r + (1 - r) * amount
    g = g + (1 - g) * amount
    b = b + (1 - b) * amount
    return colors.Color(r, g, b)


def _is_numeric_like(value) -> bool:
    """
    هل القيمة رقمية فعلياً أو نص يمثّل رقماً (بعد إزالة فواصل الآلاف)؟
    يُستخدم لتحديد محاذاة عمود جدول كامل (راجع _column_is_numeric).
    """
    if value is None or value == "":
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).replace(",", "").strip())
        return True
    except (TypeError, ValueError):
        return False


def _column_is_numeric(rows: list, col_name: str) -> bool:
    """
    🆕 يفحص عيّنة من قيم عمود جدول (حتى 20 صفاً) ليقرر هل غالبيتها
    رقمية — لمحاذاته لليمين بدل توسيطه القسري كباقي الأعمدة النصية.
    عمود فارغ تماماً يُعتبر غير رقمي (لا فائدة من تمييزه).
    """
    sample = rows[:20]
    if not sample:
        return False
    numeric_count = sum(1 for r in sample if _is_numeric_like(r.get(col_name)))
    return numeric_count >= max(1, len(sample) // 2)


class PDFExporter:
    """
    تصدير تقرير إلى PDF.

    الاستخدام:
        exp = PDFExporter(report_manager, theme_colors=get_theme_colors(settings),
                           project_name=settings.get("project_name"))
        exp.export(report_id, Path("report.pdf"))

    theme_colors: dict اختياري {"primary","accent","bg","text","card"} —
    نفس الشكل الذي تُرجعه ui.common.get_theme_colors(). لو لم يُمرَّر،
    تُستخدم _DEFAULT_PDF_COLORS (السلوك القديم قبل دعم الثيمات).
    project_name: اسم المشروع — يظهر في صفحة الغلاف فقط إن وُجد.
    """

    def __init__(self, report_manager: ReportManager, theme_colors: dict = None,
                 project_name: str = ""):
        self.rm           = report_manager
        self.project_name = project_name or ""
        self._colors      = {**_DEFAULT_PDF_COLORS, **(theme_colors or {})}
        self._font_name = "Helvetica"   # افتراضي لو لم يوجد خط عربي
        self._font_path: Optional[Path] = None
        self._setup_font()
        self._styles = self._build_styles()
        self._report_title_for_footer = ""   # يُضبط داخل export() لاستخدامه في onPage

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
    #  🆕 رأس/تذييل ثابتان على كل صفحة (عدا الغلاف)
    # ──────────────────────────────────────────────────────────

    def _draw_header_footer(self, canvas, doc) -> None:
        """
        onPage callback يُستدعى تلقائياً من ReportLab بعد رسم كل صفحة —
        يُطبَّق على كل PageTemplate (single وdash_Ncol) في
        _build_doc_templates أدناه، فيظهر بشكل متسق بغض النظر عن عدد
        أعمدة الصفحة. لا يُستدعى إطلاقاً على صفحة الغلاف (تُبنى بقالب
        مستقل بدون onPage — راجع _build_cover_page).
        """
        canvas.saveState()
        accent = colors.HexColor(self._colors["accent"])
        muted = colors.HexColor("#94A3B8")

        # خط رفيع أعلى منطقة التذييل يفصله عن المحتوى
        canvas.setStrokeColor(accent)
        canvas.setLineWidth(0.6)
        footer_y = MARGIN - 0.5 * cm
        canvas.line(MARGIN, footer_y + 0.35 * cm, PAGE_WIDTH - MARGIN, footer_y + 0.35 * cm)

        canvas.setFont(self._font_name, 8)
        canvas.setFillColor(muted)

        page_num_text = _prepare_text(f"صفحة {canvas.getPageNumber()}")
        canvas.drawCentredString(PAGE_WIDTH / 2, footer_y, page_num_text)

        if self._report_title_for_footer:
            canvas.drawRightString(PAGE_WIDTH - MARGIN, footer_y, _prepare_text(self._report_title_for_footer))

        export_date = datetime.now().strftime("%Y-%m-%d")
        canvas.drawString(MARGIN, footer_y, export_date)

        canvas.restoreState()

    def _build_doc_templates(self, doc: BaseDocTemplate) -> None:
        """
        قالب 'single': إطار واحد عادي (نفس سلوك SimpleDocTemplate
        الافتراضي) — يُستخدم لكل المحتوى العادي (فقرات، جداول، رسوم
        مستقلة). قوالب 'dash_1col'..'dash_Ncol': عدد أُطر موزّعة أفقياً
        (من اليمين لليسار) — تُستخدم فقط أثناء عرض بلوك "dashboard"
        (راجع _render_dashboard) لإنتاج تخطيط أعمدة حقيقي يدعم تعدد
        الصفحات تلقائياً، خلافاً لمحاولة سابقة بجدول Table واحد كانت
        تفشل بمجرد تجاوز أي عمود ارتفاع صفحة واحدة.

        🆕 كل القوالب (single وdash_Ncol) تمرر onPage=self._draw_header_footer
        الآن، فيظهر رقم الصفحة/اسم التقرير/التاريخ بشكل متسق بغض النظر
        عن تخطيط الصفحة الحالي.

        ⚠️ قيد معروف: لو تجاوز محتوى عمود واحد ارتفاع صفحة كاملة، فائض
        المحتوى ينتقل تلقائياً إلى إطار العمود التالي (وليس لصفحة
        جديدة بنفس العمود) — قيد أساسي في تصميم multi-frame بـ
        ReportLab. مقبول عملياً لأن أعمدة اللوحة عادة محتواها محدود
        (خلايا قليلة نسبياً).
        """
        # هامش تذييل إضافي بسيط حتى لا يتداخل المحتوى مع خط التذييل
        content_top = PAGE_HEIGHT - MARGIN
        content_bottom = MARGIN + 0.5 * cm
        content_w = PAGE_WIDTH - 2 * MARGIN
        content_h = content_top - content_bottom

        single_frame = Frame(MARGIN, content_bottom, content_w, content_h, id="normal")
        templates = [PageTemplate(id="single", frames=[single_frame], onPage=self._draw_header_footer)]

        gap = 0.5 * cm
        for n in range(1, _MAX_DASHBOARD_COLUMNS + 1):
            col_w = (content_w - gap * (n - 1)) / n
            frames = []
            for i in range(n):
                # ترتيب من اليمين لليسار: العمود الأول (i=0) أقصى اليمين
                x = MARGIN + (n - 1 - i) * (col_w + gap)
                frames.append(Frame(x, content_bottom, col_w, content_h, id=f"col_{i}"))
            templates.append(PageTemplate(id=f"dash_{n}col", frames=frames, onPage=self._draw_header_footer))

        doc.addPageTemplates(templates)

    # ──────────────────────────────────────────────────────────
    #  أنماط النص
    # ──────────────────────────────────────────────────────────

    def _build_styles(self) -> dict:
        base = getSampleStyleSheet()
        fn   = self._font_name
        primary = colors.HexColor(self._colors["primary"])
        accent  = colors.HexColor(self._colors["accent"])
        return {
            "cover_title": ParagraphStyle(
                "cover_title",
                fontName = fn,
                fontSize = 28,
                alignment= TA_CENTER,
                spaceAfter= 16,
                textColor= primary,
                leading  = 34,
            ),
            "cover_sub": ParagraphStyle(
                "cover_sub",
                fontName = fn,
                fontSize = 14,
                alignment= TA_CENTER,
                spaceAfter= 8,
                textColor= accent,
            ),
            "cover_meta": ParagraphStyle(
                "cover_meta",
                fontName = fn,
                fontSize = 10,
                alignment= TA_CENTER,
                textColor= colors.HexColor("#64748B"),
            ),
            "title": ParagraphStyle(
                "title",
                fontName = fn,
                fontSize = 18,
                alignment= TA_CENTER,
                spaceAfter= 20,
                textColor= primary,
            ),
            "h1": ParagraphStyle(
                "h1",
                fontName = fn,
                fontSize = 14,
                alignment= TA_RIGHT,
                spaceBefore= 12,
                spaceAfter = 6,
                textColor= accent,
            ),
            "h2": ParagraphStyle(
                "h2",
                fontName = fn,
                fontSize = 12,
                alignment= TA_RIGHT,
                spaceBefore= 8,
                spaceAfter = 4,
                textColor= accent,
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
            "caption": ParagraphStyle(
                "caption",
                fontName = fn,
                fontSize = 8.5,
                textColor= colors.HexColor("#64748B"),
                alignment= TA_CENTER,
                spaceBefore= 4,
            ),
            "kpi_value": ParagraphStyle(
                "kpi_value",
                fontName = fn,
                fontSize = 24,
                leading  = 30,
                alignment= TA_CENTER,
                textColor= primary,
            ),
            "kpi_label": ParagraphStyle(
                "kpi_label",
                fontName = fn,
                fontSize = 10,
                alignment= TA_CENTER,
                textColor= colors.HexColor("#334155"),
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
        self._report_title_for_footer = report["title"]   # يُستخدم في onPage

        try:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # 🆕 BaseDocTemplate بدل SimpleDocTemplate — يسمح بتعريف
            # قوالب صفحة متعددة الأُطر (أعمدة حقيقية) لبلوك "dashboard"،
            # وبتمرير onPage موحّد لرأس/تذييل كل صفحة محتوى.
            doc = BaseDocTemplate(
                str(output_path),
                pagesize    = A4,
                rightMargin = MARGIN,
                leftMargin  = MARGIN,
                topMargin   = MARGIN,
                bottomMargin= MARGIN,
            )
            self._build_doc_templates(doc)

            story = self._build_cover_page(report["title"])
            story.append(NextPageTemplate("single"))
            story.append(PageBreak())
            story.extend(self._build_story(report["title"], blocks))
            doc.build(story)

            logger.info("PDF exported: %s", output_path)
            return {"ok": True, "path": str(output_path)}

        except Exception as e:
            logger.error("PDF export error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  🆕 صفحة الغلاف
    # ──────────────────────────────────────────────────────────

    def _build_cover_page(self, title: str) -> list:
        """
        صفحة غلاف بسيطة: عنوان التقرير، اسم المشروع (إن وُجد)، تاريخ
        التصدير. تُبنى ضمن قالب 'single' الافتراضي (أول صفحة في
        المستند تستخدم أول قالب مُضاف تلقائياً) لكن بدون رأس/تذييل —
        نتجنّب ذلك بعدم استدعاء أي محتوى إضافي هنا، وبما أن onPage
        يُطبَّق على كل صفحات قالب 'single' فعلياً، فإن رقم الصفحة سيظهر
        على الغلاف أيضاً (سلوك مقبول ومألوف في تقارير كثيرة — رقم
        الصفحة الأولى للغلاف). لو أُريد إخفاؤه تحديداً في الغلاف فقط،
        يلزم قالب 'cover' منفصل بلا onPage — أُجِّل ذلك تفادياً لتعقيد
        إضافي غير ضروري حالياً.
        """
        items = []
        items.append(Spacer(1, 6 * cm))
        items.append(Paragraph(_prepare_text(title), self._styles["cover_title"]))
        items.append(Spacer(1, 0.5 * cm))
        if self.project_name:
            items.append(Paragraph(_prepare_text(f"مشروع: {self.project_name}"), self._styles["cover_sub"]))
        items.append(Spacer(1, 1 * cm))
        export_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        items.append(Paragraph(_prepare_text(f"تاريخ التصدير: {export_date}"), self._styles["cover_meta"]))
        items.append(Paragraph(_prepare_text("DataLens — منصة تحليل البيانات"), self._styles["cover_meta"]))
        return items

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

            block_items = []
            if btype == "paragraph":
                block_items = self._render_paragraph(content)
            elif btype == "table":
                block_items = self._render_table(content)
            elif btype == "kpi":
                block_items = self._render_kpi(content)
            elif btype == "gauge":
                block_items = self._render_gauge(content)
            elif btype == "chart":
                block_items = self._render_chart(content)
            elif btype == "dashboard":
                # 🆕 بلوك اللوحة يدير صفحاته وأعمدته بنفسه (PageBreak/
                # NextPageTemplate) — لا نضيف فاصلاً بصرياً عادياً حوله.
                story.extend(self._render_dashboard(content, is_last_block=is_last))
                continue

            story.extend(block_items)

            # 🆕 فاصل بصري رفيع بين البلوكات الرئيسية (وليس آخر بلوك)
            if not is_last:
                story.append(Spacer(1, 0.15 * cm))
                # 🩹 إصلاح: HRFlowable في هذا الإصدار من ReportLab لا
                # يدعم معامل opacity (يرمي خطأ عند البناء) — بدل شفافية
                # حقيقية غير متاحة، نستخدم لوناً أفتح فعلياً (مزج لون
                # accent مع الأبيض يدوياً) للحصول على نفس الأثر البصري
                # (خط رفيع خافت) بدون الاعتماد على ميزة غير مدعومة.
                story.append(HRFlowable(
                    width="100%", thickness=0.6,
                    color=_lighten_hex(self._colors["accent"], 0.65),
                    spaceAfter=8,
                ))
            story.append(Spacer(1, 0.2 * cm))

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
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(self._colors["primary"])),
                ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ]
        tbl.setStyle(TableStyle(style_cmds))
        return tbl

    def _render_table(self, content: dict, width: float = None) -> list:
        """
        تحويل بيانات جدول إلى ReportLab Table.

        🆕 محاذاة الأعمدة الرقمية لليمين (بدل CENTER القسري على كل
        الأعمدة) — يُكتشف العمود الرقمي تلقائياً من غالبية قيمه الفعلية
        عبر _column_is_numeric، فيسهل قراءة ومقارنة الأرقام عمودياً
        بنفس منطق جداول البيانات المألوف. الأعمدة النصية تبقى CENTER
        كما كانت (السلوك القديم).
        """
        data    = content.get("data", [])
        columns = content.get("columns", [])

        if not data or not columns:
            return [Paragraph(_prepare_text("جدول فارغ"), self._styles["body"])]

        numeric_cols = {c for c in columns if _column_is_numeric(data, c)}

        header = [_prepare_text(str(c)) for c in columns]
        rows   = [header]
        for row in data:
            rows.append([_prepare_text(str(row.get(c, ""))) for c in columns])

        available  = width or (PAGE_WIDTH - 2 * MARGIN)
        col_width  = available / len(columns)
        col_widths = [col_width] * len(columns)

        tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        style_cmds = [
            ("BACKGROUND",   (0, 0), (-1, 0),  colors.HexColor(self._colors["primary"])),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",     (0, 0), (-1, -1), self._font_name),
            ("FONTSIZE",     (0, 0), (-1, 0),  9),
            ("FONTSIZE",     (0, 1), (-1, -1), 8),
            ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor(self._colors["card"])]),
            ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]
        for col_idx, col_name in enumerate(columns):
            if col_name in numeric_cols:
                # الرأس يبقى CENTER (اسم العمود)، بيانات العمود نفسها
                # تُحاذى لليمين (خلايا الصفوف من 1 فصاعداً)
                style_cmds.append(("ALIGN", (col_idx, 1), (col_idx, -1), "RIGHT"))
        tbl.setStyle(TableStyle(style_cmds))
        return [tbl]

    def _render_kpi(self, content: dict) -> list:
        """
        🆕 بطاقة KPI حقيقية: خلية جدول واحدة بخلفية ملوّنة (لون card)
        وحدود بلون accent، تحتوي التسمية فوق القيمة فوق سطر الهدف —
        بدل ثلاث فقرات نصية منفصلة بلا إطار مرئي يجمعها كبطاقة واحدة.
        """
        actual = content.get("actual_value", 0)
        target = content.get("target_value", 0)
        label  = content.get("label", "")
        unit   = content.get("unit", "")

        try:
            actual_text = f"{actual:,.2f} {unit}".strip()
        except (TypeError, ValueError):
            actual_text = f"{actual} {unit}".strip()
        try:
            target_text = f"الهدف: {target:,.2f} {unit}".strip()
        except (TypeError, ValueError):
            target_text = f"الهدف: {target} {unit}".strip()

        cell_items = []
        if label:
            cell_items.append(Paragraph(_prepare_text(label), self._styles["kpi_label"]))
            cell_items.append(Spacer(1, 0.15 * cm))
        cell_items.append(Paragraph(_prepare_text(actual_text), self._styles["kpi_value"]))
        cell_items.append(Spacer(1, 0.15 * cm))
        cell_items.append(Paragraph(_prepare_text(target_text), self._styles["label"]))

        card_width = 7 * cm
        card = Table([[cell_items]], colWidths=[card_width])
        card.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, -1), colors.HexColor(self._colors["card"])),
            ("BOX",         (0, 0), (-1, -1), 1, colors.HexColor(self._colors["accent"])),
            ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",  (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 14),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING",(0, 0), (-1, -1), 10),
        ]))
        return [card]

    def _render_gauge(self, content: dict, width: float = None) -> list:
        """
        رسم Gauge فعلي (صورة PNG)، بعرض العمود/الإطار الفعلي المتاح.

        🆕 إطار خفيف حول الصورة + تسمية توضيحية أسفلها (لو label
        موجودة) — بنمط أكاديمي مألوف يفصل الشكل بصرياً عن باقي النص.
        """
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

        framed = self._frame_image(img, available_w)
        items = [framed]
        if label:
            items.append(Paragraph(_prepare_text(f"مقياس: {label}"), self._styles["caption"]))
        return items

    def _render_chart(self, content: dict, width: float = None) -> list:
        """
        توليد صورة رسم بياني وتضمينها بعرض العمود/الإطار الفعلي المتاح.

        🆕 إطار خفيف حول الصورة + تسمية توضيحية أسفلها بصيغة
        "شكل N: العنوان" — رقم الشكل يزيد تلقائياً عبر self._figure_count.
        """
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

        framed = self._frame_image(img, available_w)
        items = [framed]
        if title:
            fig_num = self._next_figure_number()
            items.append(Paragraph(_prepare_text(f"شكل {fig_num}: {title}"), self._styles["caption"]))
        return items

    def _figure_count(self) -> int:
        if not hasattr(self, "_fig_counter"):
            self._fig_counter = 0
        return self._fig_counter

    def _next_figure_number(self) -> int:
        self._fig_counter = getattr(self, "_fig_counter", 0) + 1
        return self._fig_counter

    def _frame_image(self, img: Image, width: float) -> Table:
        """
        🆕 وضع صورة (رسم/مقياس) داخل خلية جدول بإطار خفيف بلون accent —
        يفصلها بصرياً عن النص المحيط بدل صورة عائمة بلا حدود واضحة.
        """
        framed = Table([[img]], colWidths=[width])
        framed.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor(self._colors["accent"])),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return framed

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
            # 🩹 إصلاح: عرض كل الـ Gauges في صف أفقي واحد (page_w / عدد
            # المؤشرات) كان يجعل كل صورة صغيرة جداً — والنص مرسوم داخل
            # الصورة نفسها بدقة بكسل ثابتة (راجع exporters/chart_renderer.py
            # render_gauge)، فتصغير عرض العرض على الصفحة يُصغّر الخط
            # والأرقام معاً بلا حد أدنى مقروء. الحل: توزيع المؤشرات على
            # شبكة (صفّان كحد أقصى لكل صف بدل عدد المؤشرات كاملاً في صف
            # واحد) فيتضاعف عرض كل مؤشر تقريباً ويصبح نصه مقروءاً فعلياً،
            # كما تُستهلك مساحة رأسية إضافية من الصفحة بدل تركها فارغة.
            gauges_per_row = 2 if len(gauges) > 2 else len(gauges)
            page_w = PAGE_WIDTH - 2 * MARGIN
            gauge_col_w = page_w / gauges_per_row

            for row_start in range(0, len(gauges), gauges_per_row):
                row_gauges = gauges[row_start:row_start + gauges_per_row]
                gauge_flowables = [self._render_snapshot_cell(g, width=gauge_col_w) for g in row_gauges]
                # صف غير مكتمل (عدد فردي) — نُبقي الأعمدة الفارغة بلا محتوى
                # بدل تمديد العمود الأخير على كامل العرض (يحافظ على تناسق
                # حجم كل مؤشر بين الصفوف).
                while len(gauge_flowables) < gauges_per_row:
                    gauge_flowables.append("")
                gauge_table = Table([gauge_flowables], colWidths=[gauge_col_w] * gauges_per_row)
                gauge_table.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(gauge_table)
                story.append(Spacer(1, 0.3 * cm))
            story.append(Spacer(1, 0.2 * cm))

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
