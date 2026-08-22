"""
exporters/chart_renderer.py
============================
رسم المخططات البيانية (bar/line/area/pie/scatter) والـ Gauge كصور PNG
باستخدام Pillow مباشرة، بدلاً من matplotlib.

سبب التحول من matplotlib إلى Pillow:
--------------------------------------
اكتشفنا أن نتيجة معالجة النص العربي (تشكيل + bidi) تعتمد على ما إذا كانت
مكتبة النص المستخدمة تقوم بهذه المعالجة داخلياً أم لا:
  - matplotlib (رسم الحروف مباشرة عبر FreeType بدون تشكيل معقد) يحتاج
    معالجة يدوية (arabic_reshaper + bidi) قبل تمرير النص.
  - Pillow الحديثة (المبنية مع مكتبة raqm) تقوم بكل شيء تلقائياً
    (تشكيل + ترتيب bidi)، فتمرير نص مُعالَج يدوياً يعكسه مرتين.

هذا الفرق قد يختلف بين بيئة وأخرى (حسب إصدار Pillow وتوفر raqm عند
البناء)، وهو ما تسبب في ظهور النص معكوساً عند بعض المستخدمين رغم عمله
بشكل صحيح في بيئة الاختبار. الحل هنا يكتشف تلقائياً توفر raqm ويتصرف
وفقاً لذلك، بدلاً من افتراض سلوك ثابت.
"""

import logging
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

try:
    from PIL import features as _pil_features
    HAS_RAQM = _pil_features.check("raqm")
except Exception:
    HAS_RAQM = False

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _BIDI_AVAILABLE = True
except Exception:
    _BIDI_AVAILABLE = False

logger.info("Pillow raqm support: %s", HAS_RAQM)

# ── ألوان ثيم التطبيق ───────────────────────────────────────
PALETTE = ["#2563EB", "#10B981", "#F59E0B", "#A855F7", "#EF4444", "#0EA5E9"]
COLOR_TEXT = "#1E293B"
COLOR_MUTED = "#64748B"
COLOR_TITLE = "#1E3A5F"
COLOR_GRID = "#E2E8F0"
COLOR_AXIS = "#94A3B8"

FONT_CANDIDATES = [
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
]


def _find_font() -> Optional[str]:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


_FONT_PATH = _find_font()


def _font(size: int):
    if _FONT_PATH:
        return ImageFont.truetype(_FONT_PATH, size)
    return ImageFont.load_default()


def _is_arabic(text: str) -> bool:
    return any("\u0600" <= c <= "\u06FF" for c in str(text))


def prep_text(text: str) -> str:
    """
    تجهيز النص حسب قدرة Pillow المتاحة:
    - لو raqm متاح: نُرجع النص كما هو (Pillow تتولى التشكيل والترتيب).
    - لو غير متاح ونص عربي: نُطبّق reshape + bidi يدوياً.
    """
    text = str(text)
    if not text or not _is_arabic(text):
        return text
    if HAS_RAQM or not _BIDI_AVAILABLE:
        return text
    try:
        return get_display(arabic_reshaper.reshape(text), base_dir="R")
    except Exception:
        return text


def _draw_text(draw: ImageDraw.ImageDraw, xy, text, font, fill, anchor="la", rtl=False):
    """رسم نص مع تمرير direction عندما يكون raqm متاحاً ونصاً عربياً."""
    text = prep_text(text)
    kwargs = {}
    if HAS_RAQM and rtl:
        kwargs["direction"] = "rtl"
    try:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor, **kwargs)
    except Exception:
        # بعض إصدارات Pillow القديمة لا تدعم direction/anchor معاً
        draw.text(xy, text, font=font, fill=fill)


def _text_size(draw, text, font, rtl=False):
    text = prep_text(text)
    kwargs = {"direction": "rtl"} if (HAS_RAQM and rtl) else {}
    try:
        bbox = draw.textbbox((0, 0), text, font=font, **kwargs)
    except Exception:
        bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ══════════════════════════════════════════════════════════════
#  رسم بياني (bar / line / area / pie / scatter)
# ══════════════════════════════════════════════════════════════

def render_chart(data, x_col, y_cols, chart_type="bar", title="",
                  width=1000, height=560, scale=2) -> BytesIO:
    """
    يبني صورة PNG لرسم بياني ويُرجعها كـ BytesIO.
    data: قائمة dict (صفوف)، x_col: عمود الفئات، y_cols: أعمدة القيم.
    """
    W, H = width * scale, height * scale
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    title_font = _font(20 * scale)
    label_font = _font(12 * scale)
    tick_font = _font(11 * scale)
    legend_font = _font(12 * scale)

    margin_top = 70 * scale
    margin_bottom = 130 * scale
    margin_left = 70 * scale
    margin_right = 40 * scale

    if title:
        _draw_text(d, (W / 2, 30 * scale), title, title_font, COLOR_TITLE, anchor="mm", rtl=True)

    categories = [str(row.get(x_col, "")) for row in data]
    n = len(categories)

    if chart_type == "pie":
        _render_pie(d, data, y_cols[0], categories, W, H, margin_top, label_font)
        buf = BytesIO()
        img = img.resize((width, height), Image.LANCZOS)
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    plot_x0 = margin_left
    plot_x1 = W - margin_right
    plot_y0 = margin_top
    plot_y1 = H - margin_bottom

    all_values = [row.get(yc, 0) or 0 for row in data for yc in y_cols]
    max_val = max(all_values) if all_values else 1
    max_val = max_val * 1.15 if max_val > 0 else 1

    # ── محاور وخطوط شبكة ──
    n_gridlines = 5
    for i in range(n_gridlines + 1):
        y = plot_y1 - (plot_y1 - plot_y0) * i / n_gridlines
        val = max_val * i / n_gridlines
        d.line([(plot_x0, y), (plot_x1, y)], fill=COLOR_GRID, width=1)
        _draw_text(d, (plot_x0 - 10 * scale, y), f"{val:,.0f}", tick_font, COLOR_MUTED, anchor="rm")

    d.line([(plot_x0, plot_y1), (plot_x1, plot_y1)], fill=COLOR_AXIS, width=2)

    def val_to_y(v):
        return plot_y1 - (plot_y1 - plot_y0) * (v / max_val if max_val else 0)

    plot_w = plot_x1 - plot_x0
    slot_w = plot_w / max(n, 1)

    # ── رسم السلاسل ──
    if chart_type == "bar":
        n_series = max(len(y_cols), 1)
        bar_w = slot_w * 0.7 / n_series
        for si, yc in enumerate(y_cols):
            color = _hex_to_rgb(PALETTE[si % len(PALETTE)])
            for i, row in enumerate(data):
                v = row.get(yc, 0) or 0
                cx = plot_x0 + slot_w * i + slot_w / 2
                x0 = cx - (n_series * bar_w) / 2 + si * bar_w
                x1 = x0 + bar_w * 0.9
                y_top = val_to_y(v)
                d.rectangle([x0, y_top, x1, plot_y1], fill=color)
    elif chart_type in ("line", "scatter", "area"):
        for si, yc in enumerate(y_cols):
            color = _hex_to_rgb(PALETTE[si % len(PALETTE)])
            points = []
            for i, row in enumerate(data):
                v = row.get(yc, 0) or 0
                cx = plot_x0 + slot_w * i + slot_w / 2
                cy = val_to_y(v)
                points.append((cx, cy))
            if chart_type == "area":
                poly = points + [(points[-1][0], plot_y1), (points[0][0], plot_y1)]
                fill_color = color + (90,)
                overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                odraw = ImageDraw.Draw(overlay)
                odraw.polygon(poly, fill=fill_color)
                img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
                d = ImageDraw.Draw(img)
            if chart_type in ("line", "area"):
                d.line(points, fill=color, width=3 * scale, joint="curve")
            if chart_type in ("line", "scatter", "area") and chart_type != "area":
                for (px, py) in points:
                    r = 5 * scale
                    d.ellipse([px - r, py - r, px + r, py + r], fill=color)
            elif chart_type == "scatter":
                for (px, py) in points:
                    r = 6 * scale
                    d.ellipse([px - r, py - r, px + r, py + r], fill=color)

    # ── تصنيفات المحور السيني ──
    for i, cat in enumerate(categories):
        cx = plot_x0 + slot_w * i + slot_w / 2
        _draw_rotated_label(img, d, cx, plot_y1 + 10 * scale, cat, tick_font, COLOR_TEXT)

    # ── مفتاح السلاسل (Legend) ──
    if len(y_cols) > 1:
        legend_y = H - 30 * scale
        total_w = sum(_text_size(d, str(yc), legend_font, rtl=True)[0] + 40 * scale for yc in y_cols)
        lx = W / 2 + total_w / 2
        for si, yc in enumerate(y_cols):
            color = _hex_to_rgb(PALETTE[si % len(PALETTE)])
            tw, th = _text_size(d, str(yc), legend_font, rtl=True)
            lx -= tw
            _draw_text(d, (lx, legend_y), str(yc), legend_font, COLOR_TEXT, anchor="lm", rtl=True)
            lx -= 24 * scale
            d.rectangle([lx, legend_y - 7 * scale, lx + 16 * scale, legend_y + 7 * scale], fill=color)
            lx -= 20 * scale

    img = img.resize((width, height), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _draw_rotated_label(img, d, cx, top_y, text, font, color):
    """رسم تصنيف مائل (لتفادي التداخل) بلصق صورة نص مُدارة."""
    text = prep_text(text)
    tmp = Image.new("RGBA", (400, 60), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(tmp)
    kwargs = {"direction": "rtl"} if (HAS_RAQM and _is_arabic(text)) else {}
    try:
        tdraw.text((0, 0), text, font=font, fill=color, **kwargs)
    except Exception:
        tdraw.text((0, 0), text, font=font, fill=color)
    bbox = tmp.getbbox()
    if not bbox:
        return
    tmp = tmp.crop(bbox)
    rotated = tmp.rotate(30, expand=True, resample=Image.BICUBIC)
    paste_x = int(cx - rotated.width / 2 + rotated.width * 0.25)
    paste_y = int(top_y)
    img.paste(rotated, (paste_x, paste_y), rotated)


def _render_pie(d, data, y_col, categories, W, H, margin_top, label_font):
    cx, cy = W / 2, margin_top + (H - margin_top) / 2 - 20
    radius = min(W, H - margin_top) * 0.28
    values = [row.get(y_col, 0) or 0 for row in data]
    total = sum(values) or 1
    start = -90
    for i, v in enumerate(values):
        extent = 360 * v / total
        color = _hex_to_rgb(PALETTE[i % len(PALETTE)])
        d.pieslice([cx - radius, cy - radius, cx + radius, cy + radius],
                   start, start + extent, fill=color)
        mid_angle = start + extent / 2
        import math
        lx = cx + (radius + 40) * math.cos(math.radians(mid_angle))
        ly = cy + (radius + 40) * math.sin(math.radians(mid_angle))
        pct = f"{v / total * 100:.0f}%"
        label = f"{categories[i]} ({pct})"
        _draw_text(d, (lx, ly), label, label_font, COLOR_TEXT, anchor="mm", rtl=True)
        start += extent


# ══════════════════════════════════════════════════════════════
#  Gauge (نصف دائري)
# ══════════════════════════════════════════════════════════════

def render_gauge(current_value, min_value, max_value, label="",
                  width=900, height=560, scale=2) -> BytesIO:
    W, H = width * scale, height * scale
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    title_font = _font(20 * scale)
    value_font = _font(46 * scale)
    tick_font = _font(13 * scale)

    if label:
        _draw_text(d, (W / 2, 30 * scale), label, title_font, COLOR_TITLE, anchor="mm", rtl=True)

    cx, cy = W / 2, H * 0.62
    radius = min(W * 0.38, H * 0.42)
    thickness = radius * 0.22

    span = max_value - min_value or 1
    pct = max(0.0, min(1.0, (current_value - min_value) / span))

    # المسار الكامل (رمادي فاتح)
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    d.arc(bbox, 180, 360, fill=_hex_to_rgb("#E2E8F0"), width=int(thickness))

    # اللون: أحمر تحت 50%، برتقالي حتى 80%، أخضر بعدها
    if pct < 0.5:
        color = _hex_to_rgb("#EF4444")
    elif pct < 0.8:
        color = _hex_to_rgb("#F59E0B")
    else:
        color = _hex_to_rgb("#10B981")

    end_angle = 180 + 180 * pct
    if pct > 0:
        d.arc(bbox, 180, end_angle, fill=color, width=int(thickness))

    # علامات القياس
    n_ticks = 5
    import math
    for i in range(n_ticks + 1):
        angle = 180 + 180 * i / n_ticks
        val = min_value + span * i / n_ticks
        tx = cx + (radius + thickness / 2 + 22 * scale) * math.cos(math.radians(angle))
        ty = cy + (radius + thickness / 2 + 22 * scale) * math.sin(math.radians(angle))
        _draw_text(d, (tx, ty), _format_number(val), tick_font, COLOR_MUTED, anchor="mm")

    # القيمة الحالية في المنتصف
    _draw_text(d, (cx, cy - 10 * scale), _format_number(current_value), value_font, COLOR_TITLE, anchor="mm")

    img = img.resize((width, height), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _format_number(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v/1_000:.1f}K"
    if v == int(v):
        return f"{int(v)}"
    return f"{v:.2f}"
