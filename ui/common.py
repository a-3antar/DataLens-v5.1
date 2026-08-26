"""
ui/common.py
============
أدوات مشتركة لكل صفحات الواجهة: RTL، الثيمات، التحقق من تسجيل الدخول،
التأكد من وجود مشروع مفتوح، تحويل التواريخ للمنطقة الزمنية المحلية،
وإشعارات موحّدة عبر Toast بدل الرسائل التوضيحية الثابتة المنتشرة في
الصفحات.
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

# ألوان كل ثيم — تُستخدم لعرض color_picker في صفحة الإعدادات، وأيضاً
# لتطبيق الثيم فعلياً على الواجهة عبر apply_theme_css() أدناه، ولتلوين
# الرسوم البيانية (Plotly) بما يطابق الثيم الحالي عبر apply_plotly_theme().
THEME_COLORS = {
    "ocean_dark":     {"primary": "#1E3A5F", "accent": "#2563EB", "bg": "#0F172A", "text": "#F8FAFC", "card": "#1E293B"},
    "arctic_light":   {"primary": "#0EA5E9", "accent": "#38BDF8", "bg": "#F8FAFC", "text": "#0F172A", "card": "#FFFFFF"},
    "desert_warm":    {"primary": "#B45309", "accent": "#F59E0B", "bg": "#FFFBEB", "text": "#451A03", "card": "#FFFFFF"},
    "forest_green":   {"primary": "#065F46", "accent": "#10B981", "bg": "#F0FDF4", "text": "#052E16", "card": "#FFFFFF"},
    "corporate_gray": {"primary": "#374151", "accent": "#6B7280", "bg": "#F9FAFB", "text": "#111827", "card": "#FFFFFF"},
}


def apply_rtl():
    st.markdown(RTL_CSS, unsafe_allow_html=True)


def apply_theme_css(theme_key_or_settings="ocean_dark"):
    """
    تطبيق ألوان الثيم فعلياً على الواجهة (خلفية، أزرار، عناوين، بطاقات،
    تبويبات، روابط، جداول)، بما يشمل جعل خلفية الجداول (st.dataframe)
    شفافة ومتوافقة مع لون نص الثيم بدل الخلفية البيضاء الافتراضية التي
    تكسر التناسق البصري في الثيمات الداكنة.

    تقبل إما اسم ثيم كنص مباشرة ("ocean_dark") أو dict إعدادات مشروع
    كامل (settings) — نفس مرونة get_chart_theme/apply_plotly_theme،
    حتى تعمل بغض النظر عن الشكل الذي تُستدعى به في كل صفحة (بعض
    الصفحات تمرر settings كاملة، وبعضها يمرر settings.get("theme")
    مباشرة).

    ملاحظة تقنية مهمة: الألوان "الرسمية" لـ Streamlit (primaryColor،
    backgroundColor...) تُقرأ من config.toml مرة واحدة فقط عند إقلاع
    السيرفر، ولا توجد طريقة رسمية لتغييرها ديناميكياً لكل مستخدم أثناء
    التشغيل. الحل العملي هنا: نُطبّق نفس الألوان مباشرة عبر CSS على
    أهم العناصر المرئية.
    """
    colors = _resolve_theme_colors(theme_key_or_settings)
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

        /* ─────────────────────────────────────────────────────
           شفافية الجداول (st.dataframe) داخل خلايا اللوحات
           والتقارير — الخلفية البيضاء الافتراضية لعنصر الجدول
           (المبني عبر glide-data-grid) تكسر التناسق مع الثيمات
           الداكنة. نجعل الحاوية والخلفية شفافة، مع إبقاء حدود
           خفيفة بلون التمييز حتى يبقى الجدول مقروءاً بصرياً.
           ───────────────────────────────────────────────────── */
        [data-testid="stDataFrame"],
        [data-testid="stDataFrame"] > div,
        [data-testid="stElementContainer"] [data-testid="stDataFrame"] {{
            background-color: transparent !important;
        }}
        [data-testid="stDataFrame"] [data-testid="stDataFrameResizable"] {{
            background-color: transparent !important;
        }}
        [data-testid="stDataFrame"] {{
            border: 1px solid {accent}40 !important;
            border-radius: 6px;
        }}

        /* ─────────────────────────────────────────────────────
           خلفية شفافة لعناصر Plotly (الرسوم/المقاييس) — طبقة CSS
           احتياطية بالإضافة إلى ضبط paper_bgcolor/plot_bgcolor
           برمجياً عبر apply_plotly_theme() في كل رسم (الطبقة
           الأهم فعلياً، لأن Plotly يرسم داخل SVG/Canvas خاص به).
           ───────────────────────────────────────────────────── */
        [data-testid="stPlotlyChart"] {{
            background-color: transparent !important;
        }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


def apply_page_style(theme_key: str = None):
    """
    اختصار موحّد لبداية أي صفحة: يُطبّق RTL دائماً، ثم يُطبّق ألوان
    الثيم فعلياً لو تم تمرير theme_key (عادة settings.get("theme")).
    الصفحات التي لا يوجد فيها مشروع مفتوح بعد (تسجيل الدخول، معرض
    المشاريع) تستدعيها بدون theme_key فتحصل على RTL فقط.
    """
    apply_rtl()
    if theme_key:
        apply_theme_css(theme_key)


def _resolve_theme_colors(settings_or_theme) -> dict:
    """
    قبول إما dict إعدادات مشروع كامل (settings.get("theme")) أو اسم
    ثيم كنص مباشرة — يُستخدم داخلياً من get_chart_theme/apply_plotly_theme
    حتى تعمل الدالتان بنفس المرونة بغض النظر عمّا يُمرَّر لهما.
    """
    if isinstance(settings_or_theme, dict):
        theme_key = settings_or_theme.get("theme", "ocean_dark")
    else:
        theme_key = settings_or_theme or "ocean_dark"
    return THEME_COLORS.get(theme_key, THEME_COLORS["ocean_dark"])


def get_chart_theme(settings_or_theme="ocean_dark") -> dict:
    """
    إرجاع إعدادات ثيم جاهزة للتطبيق مباشرة على أي Plotly figure عبر
    fig.update_layout(**get_chart_theme(settings)) — تجعل خلفية الرسم
    شفافة تماماً (تتناسب مع أي خلفية خلفها) ولون النص متوافقاً مع لون
    نص الثيم الحالي، بدل الخلفية البيضاء الافتراضية لـ Plotly.

    يقبل إما dict إعدادات المشروع كاملاً أو اسم ثيم كنص مباشرة.
    """
    colors = _resolve_theme_colors(settings_or_theme)
    return {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font_color": colors["text"],
        "legend": {"font": {"color": colors["text"]}},
    }


def apply_plotly_theme(fig, settings_or_theme="ocean_dark"):
    """
    تطبيق ثيم شفاف متوافق مع لون نص الثيم الحالي مباشرة على كائن
    Plotly figure، وإرجاعه (لتسلسل الاستدعاءات إن رغبت).

    الاستخدام:
        fig = px.bar(df, x="x", y="y")
        fig = apply_plotly_theme(fig, settings)
        st.plotly_chart(fig, width='stretch')

    يقبل إما dict إعدادات المشروع كاملاً (settings) أو اسم ثيم كنص
    مباشرة (مثل "ocean_dark") — نفس مرونة get_chart_theme أعلاه، وهما
    في جوهرهما نفس المنطق: get_chart_theme تُرجع dict للدمج اليدوي،
    وapply_plotly_theme تطبّقه مباشرة على fig كاختصار.
    """
    fig.update_layout(**get_chart_theme(settings_or_theme))
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
#  إشعارات موحّدة (Toast) — بديل الرسائل التوضيحية الثابتة
# ══════════════════════════════════════════════════════════════
#
# بدل st.info/st.success/st.warning/st.caption الثابتة المنتشرة في
# الصفحات (تشغل مساحة دائمة وتتراكم بصرياً)، تُستخدم هذه الدالة الواحدة
# لعرض أي حدث (نجاح عملية، فشلها، تنبيه بسيط) كـ toast عابر يظهر
# ويختفي تلقائياً — قناة إشعار واحدة موحّدة لكل أحداث التطبيق.

_KIND_ICONS = {
    "success": "✅",
    "error"  : "❌",
    "warning": "⚠️",
    "info"   : "ℹ️",
}


def notify(message: str, kind: str = "info", icon: str = None) -> None:
    """
    عرض إشعار عابر (toast) موحّد لأي حدث في التطبيق.

    kind: "success" | "error" | "warning" | "info" — يحدد الأيقونة
          الافتراضية فقط؛ Streamlit toast لا يملك تلوينات مختلفة حسب
          النوع، لذا الأيقونة هي وسيلة التمييز البصري الوحيدة المتاحة.
    icon: أيقونة مخصّصة تتجاوز الافتراضية المرتبطة بـ kind.
    """
    final_icon = icon or _KIND_ICONS.get(kind, "ℹ️")
    try:
        st.toast(message, icon=final_icon)
    except Exception:
        # fallback نادر جداً (نسخات Streamlit قديمة لا تدعم toast) —
        # لا نكسر الصفحة، فقط نسجّل الحدث في اللوج بدل عرضه
        logger.info("notify (no toast support): [%s] %s", kind, message)


# ══════════════════════════════════════════════════════════════
#  التواريخ والمنطقة الزمنية
# ══════════════════════════════════════════════════════════════
#
# كل التواريخ تُخزَّن داخلياً بصيغة ISO بتوقيت UTC (راجع
# core/project_db.py::_now()). طبقة العرض فقط هي المسؤولة عن التحويل
# للمنطقة الزمنية المفضّلة للمستخدم (محفوظة في project settings تحت
# مفتاح "timezone")، حتى لا نُغيّر أي شيء في طريقة التخزين نفسها.

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
            # كل الأوقات المخزَّنة عبر project_db._now() هي UTC ضمنياً
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
