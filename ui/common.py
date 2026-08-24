"""
ui/common.py
============
أدوات مشتركة لكل صفحات الواجهة: RTL، الثيمات، التحقق من تسجيل الدخول،
التأكد من وجود مشروع مفتوح، وتحويل التواريخ للمنطقة الزمنية المحلية.
"""

import shutil
import tempfile
import logging
from pathlib import Path
from datetime import datetime, timezone as _dt_timezone
from contextlib import contextmanager
from zoneinfo import ZoneInfo

import streamlit as st

from core.project_manager import ProjectManager
from core.project_db import ProjectDB

from config import APP_NAME

logger = logging.getLogger(__name__)

RTL_CSS = """
<style>
    .main, .stApp { direction: rtl; text-align: right; }
    .stTextInput input, .stTextArea textarea, .stNumberInput input { direction: rtl; text-align: right; }
    .stSelectbox div[data-baseweb="select"] { direction: rtl; text-align: right; }
    .stButton button { width: 100%; }
    div[data-testid="stMetricValue"] { direction: ltr; }
    .stDataFrame { direction: rtl; }
    thead tr th { text-align: center !important; }

    /* ─────────────────────────────────────────────────────────
       تثبيت الشريط الجانبي على أقصى اليمين (حالتي الفتح والطي).
       Streamlit يضع الشريط افتراضياً على اليسار (left:0) ويُخفيه
       عند الطي عبر transform: translateX(-100%) — وهذا لا ينعكس
       تلقائياً بمجرد ضبط direction:rtl على الحاوية، فيظهر شريط
       الطي عالقاً في المنتصف. نُثبّت الموضع والانزلاق يدوياً هنا.
       ───────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] {
        direction: rtl;
        text-align: right;
        right: 0 !important;
        left: auto !important;
    }
    [data-testid="stSidebar"][aria-expanded="true"] {
        transform: translateX(0) !important;
    }
    [data-testid="stSidebar"][aria-expanded="false"] {
        transform: translateX(100%) !important;
    }
    /* زر إظهار/إخفاء الشريط (السهم الصغير) — يثبت في أقصى اليمين دائماً */
    [data-testid="collapsedControl"] {
        right: 0 !important;
        left: auto !important;
    }
    /* منطقة المحتوى الرئيسي: نضمن عدم بقاء مساحة فارغة يسارية بعد نقل الشريط لليمين */
    section.main {
        direction: rtl;
    }
</style>
"""

# ألوان كل ثيم — القيم الافتراضية. لون النص (text) قابل للتخصيص من
# المستخدم عبر إعدادات المشروع (custom_text_color) — راجع
# get_theme_colors() أدناه التي تدمج الاختيار الشخصي مع ألوان الثيم.
THEME_COLORS = {
    "ocean_dark":     {"primary": "#1E3A5F", "accent": "#2563EB", "bg": "#0F172A", "text": "#F8FAFC", "card": "#1E293B"},
    "arctic_light":   {"primary": "#0EA5E9", "accent": "#38BDF8", "bg": "#F8FAFC", "text": "#0F172A", "card": "#FFFFFF"},
    "desert_warm":    {"primary": "#B45309", "accent": "#F59E0B", "bg": "#FFFBEB", "text": "#451A03", "card": "#FFFFFF"},
    "forest_green":   {"primary": "#065F46", "accent": "#10B981", "bg": "#F0FDF4", "text": "#052E16", "card": "#FFFFFF"},
    "corporate_gray": {"primary": "#374151", "accent": "#6B7280", "bg": "#F9FAFB", "text": "#111827", "card": "#FFFFFF"},
}


def apply_rtl():
    st.markdown(RTL_CSS, unsafe_allow_html=True)


def get_theme_colors(settings: dict) -> dict:
    """
    إرجاع قاموس ألوان الثيم النهائي بعد تطبيق أي تخصيص شخصي.

    settings["theme"]             : مفتاح الثيم المختار (ocean_dark, ...)
    settings["custom_text_color"] : لون نص اختياري يختاره المستخدم بحرية
                                     من صفحة الإعدادات؛ لو موجود يُستخدم
                                     بدل لون النص الافتراضي للثيم فقط —
                                     بقية الألوان (primary/accent/bg/card)
                                     تبقى كما هي محددة في الثيم.

    تُستخدم هذه الدالة في كل مكان يحتاج معرفة "لون النص الحالي" — سواء
    لتطبيق CSS على الواجهة (apply_theme_css) أو لتلوين نصوص الرسوم
    البيانية (Plotly gauge/chart) حتى تتطابق مع باقي نصوص الصفحة.
    """
    theme_key = (settings or {}).get("theme", "ocean_dark")
    colors = dict(THEME_COLORS.get(theme_key, THEME_COLORS["ocean_dark"]))
    custom_text = (settings or {}).get("custom_text_color")
    if custom_text:
        colors["text"] = custom_text
    return colors


def apply_theme_css(theme_key_or_settings):
    """
    تطبيق ألوان الثيم فعلياً على الواجهة (خلفية، أزرار، عناوين، بطاقات،
    تبويبات، روابط). يقبل إما مفتاح ثيم نصي (السلوك القديم، للتوافق)
    أو قاموس settings كامل (يسمح بتطبيق custom_text_color أيضاً).

    ملاحظة تقنية مهمة: الألوان "الرسمية" لـ Streamlit (primaryColor،
    backgroundColor...) تُقرأ من config.toml مرة واحدة فقط عند إقلاع
    السيرفر، ولا توجد طريقة رسمية لتغييرها ديناميكياً لكل مستخدم أثناء
    التشغيل. الحل العملي هنا: نُطبّق نفس الألوان مباشرة عبر CSS على
    أهم العناصر المرئية (خلفية التطبيق، الأزرار، الحاويات ذات الحدود،
    العناوين، الروابط، الشريط الجانبي) — يُغطي عملياً أغلب ما يراه
    المستخدم دون الحاجة لإعادة تشغيل السيرفر لكل تغيير.
    """
    if isinstance(theme_key_or_settings, dict):
        colors = get_theme_colors(theme_key_or_settings)
    else:
        colors = dict(THEME_COLORS.get(theme_key_or_settings, THEME_COLORS["ocean_dark"]))

    primary = colors["primary"]
    accent = colors["accent"]
    bg = colors["bg"]
    text = colors["text"]
    card = colors["card"]

    css = f"""
    <style>
        .stApp {{
            background-color: {bg} !important;
        }}
        .stApp, .stApp p, .stApp span, .stApp label, .stApp li, .stMarkdown {{
            color: {text} !important;
        }}
        h1, h2, h3, h4, h5, h6 {{
            color: {primary} !important;
        }}
        [data-testid="stSidebar"] {{
            background-color: {card} !important;
        }}
        .stButton > button {{
            background-color: {accent} !important;
            color: #FFFFFF !important;
            border: 1px solid {accent} !important;
        }}
        .stButton > button:hover {{
            background-color: {primary} !important;
            border-color: {primary} !important;
            color: #FFFFFF !important;
        }}
        .stButton > button[kind="primary"] {{
            background-color: {primary} !important;
            border-color: {primary} !important;
        }}
        div[data-testid="stMetricValue"] {{
            color: {primary} !important;
        }}
        div[data-testid="stExpander"],
        div[data-testid="stVerticalBlockBorderWrapper"] {{
            background-color: {card} !important;
            border-color: {accent}55 !important;
        }}
        .stTabs [data-baseweb="tab"] {{
            color: {text} !important;
        }}
        .stTabs [aria-selected="true"] {{
            color: {primary} !important;
            border-bottom-color: {primary} !important;
        }}
        a, a:visited {{
            color: {accent} !important;
        }}
        .stCaption, [data-testid="stCaptionContainer"] {{
            color: {text} !important;
            opacity: 0.75;
        }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


def apply_page_style(settings: dict = None):
    """
    اختصار موحّد لبداية أي صفحة: يُطبّق RTL دائماً، ثم يُطبّق ألوان
    الثيم (بما فيها لون النص المخصص إن وُجد) لو تم تمرير settings.
    """
    apply_rtl()
    if settings:
        apply_theme_css(settings)


def apply_plotly_theme(fig, settings: dict):
    """
    توحيد مظهر أي رسم Plotly (gauge أو chart) مع ثيم الصفحة الحالي:
    - خلفية الرسم بالكامل شفافة (paper_bgcolor/plot_bgcolor) بدل الأبيض
      الافتراضي من Plotly، حتى تندمج بصرياً مع بطاقة/خلفية الصفحة أياً
      كان الثيم المختار، بدل تكرار لون الثيم يدوياً (وهو ما قد يتغيّر
      مستقبلاً مع كل ثيم جديد بينما الشفافية تعمل تلقائياً مع أي ثيم).
    - لون كل النصوص داخل الرسم (الأرقام، التسميات، المحاور) يُطابق لون
      النص المختار في الثيم — بما فيه لون النص المخصص الذي يختاره
      المستخدم بحرية من صفحة الإعدادات — حتى لا تبقى الكتابة سوداء
      افتراضياً على خلفية شفافة قد تكون داكنة (مشكلة قراءة).

    يُستدعى دائماً بعد بناء أي fig وقبل st.plotly_chart(fig, ...).
    يُعدّل الـ fig في مكانه ويُرجعه أيضاً لسهولة الاستخدام المتسلسل.
    """
    colors = get_theme_colors(settings)
    text_color = colors["text"]
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=text_color),
    )
    # عناصر الـ gauge (go.Indicator) لا تتبع دائماً font العام أعلاه
    # بنفس القوة لبعض إصدارات Plotly، فنضبطها صراحة لو كانت موجودة.
    try:
        for trace in fig.data:
            if getattr(trace, "type", None) == "indicator":
                trace.update(number=dict(font=dict(color=text_color)))
                if getattr(trace, "gauge", None) is not None:
                    trace.gauge.update(
                        axis=dict(tickfont=dict(color=text_color)),
                    )
    except Exception:
        pass
    return fig


def require_login():
    """يوقف تنفيذ الصفحة لو المستخدم لم يسجل دخول."""
    if "token" not in st.session_state or not st.session_state.get("token"):
        st.switch_page("main.py") if hasattr(st, "switch_page") else None
        st.warning("الرجاء تسجيل الدخول أولاً")
        st.stop()


def require_project() -> ProjectDB:
    """يوقف تنفيذ الصفحة لو لا يوجد مشروع مفتوح، ويرجع ProjectDB."""
    if "project_id" not in st.session_state or not st.session_state.get("project_id"):
        st.warning("الرجاء فتح مشروع أولاً من صفحة المشاريع")
        st.stop()
    if "db" not in st.session_state or st.session_state.db is None:
        st.session_state.db = ProjectDB(st.session_state.user_id, st.session_state.project_id)
    return st.session_state.db


def get_project_manager() -> ProjectManager:
    if "pm" not in st.session_state or st.session_state.pm is None:
        st.session_state.pm = ProjectManager(st.session_state.user_id)
    return st.session_state.pm


def sidebar_header():
    """رأس الشريط الجانبي: اسم المستخدم + المشروع الحالي + خروج."""
    with st.sidebar:
        st.markdown(f"### 👋 {st.session_state.get('username', '')}")
        if st.session_state.get("project_id"):
            settings = st.session_state.db.get_settings() if st.session_state.get("db") else {}
            name = settings.get("project_name", "بدون اسم")
            st.caption(f"📁 المشروع الحالي: **{name}**")
        st.divider()
        if st.button("🚪 تسجيل الخروج", width='stretch'):
            from core.auth import AuthManager
            AuthManager().logout(st.session_state.get("token", ""))
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


# ══════════════════════════════════════════════════════════════
#  التواريخ والمنطقة الزمنية
# ══════════════════════════════════════════════════════════════

def format_local_dt(iso_str, settings: dict, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """
    تحويل وقت مخزَّن بصيغة ISO (UTC دائماً) إلى نص معروض بالمنطقة
    الزمنية المفضّلة للمستخدم (settings["timezone"]).

    لا يرمي استثناءً أبداً — يرجع نصاً بديلاً معقولاً عند أي خلل
    (قيمة فارغة، تنسيق غير متوقع، أو اسم منطقة زمنية غير صالح).
    """
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt_timezone.utc)

        tz_name = (settings or {}).get("timezone", "Asia/Riyadh")
        try:
            local_dt = dt.astimezone(ZoneInfo(tz_name))
        except Exception:
            logger.warning("Invalid timezone '%s' — falling back to Asia/Riyadh", tz_name)
            local_dt = dt.astimezone(ZoneInfo("Asia/Riyadh"))

        return local_dt.strftime(fmt)
    except Exception as e:
        logger.debug("format_local_dt fallback for '%s': %s", iso_str, e)
        return str(iso_str)[:16].replace("T", " ")


# ══════════════════════════════════════════════════════════════
#  دورة حياة الملفات المؤقتة (التصدير)
# ══════════════════════════════════════════════════════════════

_TEMP_PREFIX = APP_NAME + "_export_"


@contextmanager
def temp_export_dir():
    """
    Context manager يُنشئ مجلداً مؤقتاً بادئته مميزة (لسهولة التنظيف
    الاحتياطي لاحقاً)، ويضمن حذفه فور الخروج من الـ block — بغض النظر
    عن نجاح العملية أو فشلها.
    """
    out_dir = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX))
    try:
        yield out_dir
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def offer_download(path: Path, label: str, file_name: str, key: str = None, mime: str = None):
    """
    يقرأ محتوى الملف في الذاكرة ويعرض زر تحميل — يُستخدم فقط داخل
    with temp_export_dir() حتى يُحذف الملف تلقائياً بعد قراءته.
    """
    data = Path(path).read_bytes()
    st.download_button(label, data=data, file_name=file_name, key=key, mime=mime)


def cleanup_stale_temp_dirs(max_age_hours: int = 2) -> int:
    """
    شبكة أمان احتياطية: تمسح أي مجلدات تصدير مؤقتة قديمة تخص التطبيق
    قد تبقّت من تشغيل سابق تعطّل قبل أن يُنفَّذ الحذف التلقائي.
    """
    import time
    removed = 0
    tmp_root = Path(tempfile.gettempdir())
    cutoff = time.time() - max_age_hours * 3600
    try:
        for entry in tmp_root.glob(f"{_TEMP_PREFIX}*"):
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except Exception as e:
                logger.warning("cleanup_stale_temp_dirs: skipped %s (%s)", entry, e)
    except Exception as e:
        logger.error("cleanup_stale_temp_dirs error: %s", e)
    if removed:
        logger.info("cleanup_stale_temp_dirs: removed %d stale export dir(s)", removed)
    return removed
