"""
ui/common.py
============
أدوات مشتركة لكل صفحات الواجهة: RTL، الثيمات، التحقق من تسجيل الدخول،
التأكد من وجود مشروع مفتوح، تحويل التواريخ للمنطقة الزمنية المحلية،
وإشعارات موحّدة عبر Toast بدل الرسائل التوضيحية الثابتة المنتشرة في
الصفحات.

🆕 تنسيق القوائم النقطية والعناوين داخل محتوى Markdown (RTL):
------------------------------------------------------------------
أُضيفت قواعد CSS إلى RTL_CSS أدناه (ul/ol/li/h1-h4/p ضمن .stMarkdown)
لتصحيح شكل النقاط (•) في سياق RTL — المتصفح افتراضياً يضع
padding-left على القوائم حتى مع direction:rtl، فتظهر النقطة بعيدة عن
حافة النص بمسافة كبيرة وغير متسقة. هذا يُصلح عرض أي محتوى Markdown
في التطبيق (وعلى رأسه نص Story Telling المعروض عبر st.markdown في
ui/chat.py وcore/dashboard_cells/cells.py/base.py).

🆕 اسم المستخدم في اللوج:
---------------------------
sidebar_header() تستدعي الآن core.logger_config.set_log_username()
بمجرد معرفة اسم المستخدم من session_state — هذه هي نقطة الدخول
المركزية الوحيدة (تُستدعى في بداية كل صفحة محمية بعد تسجيل الدخول)،
فيكفي ضبطها هنا مرة واحدة حتى يظهر اسم المستخدم تلقائياً في كل سطر
log يُكتب لاحقاً من أي مكان في المشروع (بما في ذلك threads تحديث
لوحات المعلومات، التي ترث نفس القيمة تلقائياً عبر contextvars —
راجع core/logger_config.py للتفاصيل الكاملة). عند الضغط على "تسجيل الخروج"
تُستدعى clear_log_username() لإعادة الحالة إلى الافتراضي ("-").

🆕 الثيمات:
------------
بالإضافة إلى الثيمات الجاهزة (THEME_COLORS)، يدعم التطبيق ثيماً
"مخصصاً" (custom) يختار فيه المستخدم الألوان الخمسة بحرّية كاملة من
صفحة الإعدادات — تُخزَّن في project settings تحت مفتاح
"custom_theme_colors" ولا تُستخدم إلا لو settings["theme"] == "custom"
(راجع _resolve_theme_colors أدناه).

كل الدوال التي تستهلك الثيم (apply_theme_css، get_chart_theme،
apply_plotly_theme) تمر عبر _resolve_theme_colors كنقطة مركزية
واحدة، لذا أي صفحة تستدعيها تستفيد تلقائياً من الثيم المخصص بدون أي
تعديل إضافي فيها.

🆕 تلوين فعلي (وليس فقط شكلي) للرسوم البيانية والـ Gauge:
--------------------------------------------------------------
تمرير colorway إلى fig.update_layout() وحده لا يكفي: Plotly Express
"يخبز" لون كل عنصر (عمود/خط/شريحة) داخل الـ trace نفسه لحظة الإنشاء
اعتماداً على القالب الافتراضي، ولا يُعاد حساب هذا اللون تلقائياً عند
تغيير colorway لاحقاً. لذلك apply_plotly_theme() هنا تفرض اللون
صراحةً على كل trace (bar/line/area/scatter/pie/indicator) بعد
الإنشاء — وهي الطريقة الوحيدة الفعلية لجعل الرسوم متوافقة بصرياً مع
الثيم الحالي (بما فيها الثيم المخصص).

🆕 تخصيص جدول Streamlit (st.dataframe):
-------------------------------------------
الجدول التفاعلي في Streamlit يُرسم فعلياً على <canvas> عبر مكتبة
glide-data-grid الداخلية، وليس عناصر HTML عادية — فلا يتأثر بقواعد
CSS التقليدية (لون خلفية/نص/حدود). المكتبة تقرأ عوضاً عن ذلك مجموعة
متغيرات CSS مخصصة (--gdg-*) عند كل رسم، فنُعيد تعريف هذه المتغيرات
هنا لتطابق ألوان الثيم الحالي (رأس الجدول، الخلفية، النص، الحدود).
ملاحظة: أسماء هذه المتغيرات داخلية وغير موثّقة رسمياً من Streamlit
وقد تتغيّر بين الإصدارات المستقبلية — هذا أفضل حل عملي متاح حالياً.
"""

import colorsys
import html as _html
import shutil
import tempfile
import uuid as _uuid_module
import logging
from pathlib import Path
from datetime import datetime, timezone as _dt_timezone
from contextlib import contextmanager
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd

from core.project_manager import ProjectManager
from core.project_db import ProjectDB
from core.logger_config import set_log_username, clear_log_username

from config import APP_NAME, DEFAULT_SETTINGS

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
       ───────────────────────────────────────────────── */
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

    /* ─────────────────────────────────────────────────────────
       🆕 تنسيق القوائم النقطية (ul/li) والعناوين الفرعية والفقرات
       والجداول داخل أي محتوى Markdown (خصوصاً نص التحليل — Story
       Telling المعروض عبر st.markdown مباشرة في ui/chat.py وcore/
       dashboard_cells/cells.py|base.py) في سياق RTL.

       ⚠️ ملاحظة مهمة: المتصفح/Streamlit يضبطان تباعد القوائم عبر
       خصائص CSS "منطقية" (padding-inline-start/margin-inline-start)
       وليس الفيزيائية (padding-left/right) — وهذه خصائص منفصلة تماماً
       عن padding-left/padding-right؛ ضبط الأخيرة فقط (كما كان في
       محاولة سابقة) لا يُلغي القيمة المنطقية الفعلية، فيبقى هامش
       كبير غير متوقَّع. هنا نُصفّر كل الأشكال الأربعة معاً (منطقية +
       فيزيائية) قبل ضبط القيمة المطلوبة، لضمان تجاوز فعلي.
       ───────────────────────────────────────────────── */
    .stMarkdown ul, .stMarkdown ol {
        padding-inline-start: 1.4em !important;
        padding-inline-end: 0 !important;
        padding-left: 0 !important;
        padding-right: 1.4em !important;
        margin-inline-start: 0 !important;
        margin-inline-end: 0 !important;
        margin-left: 0 !important;
        margin-right: 0 !important;
        margin-top: 0.4em;
        margin-bottom: 0.8em;
        list-style-position: outside;
    }
    .stMarkdown li {
        margin-bottom: 8px;
        line-height: 1.9;
        text-align: right;
        padding-inline-start: 0 !important;
        padding-inline-end: 0 !important;
    }
    .stMarkdown li::marker {
        unicode-bidi: isolate;
    }
    .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 {
        margin-top: 1.1em;
        margin-bottom: 0.5em;
        text-align: right;
    }
    .stMarkdown p {
        line-height: 1.9;
        margin-bottom: 0.6em;
        text-align: right;
    }

    /* ─────────────────────────────────────────────────────────
       🆕 جداول Markdown (| عمود | عمود |) — يستخدمها الآن نص
       التحليل (Story Telling) عند وجود بيانات مقارَنة، بعد تحديث
       قواعد ai/prompt_builder.py::build_story. تنسيق أساسي (حدود،
       تباعد، رأس بارز) هنا؛ ألوان الثيم الفعلية (خلفية/نص/حدود)
       تُطبَّق في apply_theme_css أدناه بحيث تتوافق مع الثيم النشط.
       ───────────────────────────────────────────────── */
    .stMarkdown table {
        width: 100%;
        border-collapse: collapse;
        margin: 0.6em 0 1em 0;
        direction: rtl;
    }
    .stMarkdown th, .stMarkdown td {
        padding: 6px 12px;
        text-align: center;
        border: 1px solid rgba(128,128,128,0.35);
    }
    .stMarkdown th {
        font-weight: 700;
    }

    /* ─────────────────────────────────────────────────────────
       🆕 كتل الكود (```...```) المستخدمة في نص التحليل لعرض رسم
       نصي بسيط (أعمدة بحرف '█') — نُبقيها بخط ثابت العرض (monospace)
       لضمان محاذاة الأعمدة، مع اتجاه محتوى يسار-ليمين داخلياً
       (الأرقام والرموز الرسومية) لكن محاذاة الكتلة نفسها تبقى يمين
       الصفحة حتى لا تنفرد بمظهر مختلف عن باقي السرد.
       ───────────────────────────────────────────────── */
    .stMarkdown pre {
        direction: ltr;
        text-align: left;
        margin: 0.6em 0 1em 0;
        border-radius: 6px;
        overflow-x: auto;
    }
    .stMarkdown pre code {
        font-family: "Consolas", "Courier New", monospace;
        white-space: pre;
    }
</style>
"""

# ألوان كل ثيم جاهز — تُستخدم لعرض color_picker في صفحة الإعدادات،
# وأيضاً لتطبيق الثيم فعلياً على الواجهة عبر apply_theme_css() أدناه،
# ولتلوين الرسوم البيانية (Plotly) بما يطابق الثيم الحالي عبر
# apply_plotly_theme(). الثيم "custom" ليس له إدخال هنا عمداً — ألوانه
# تُقرأ ديناميكياً من project settings (راجع _resolve_theme_colors).
THEME_COLORS = {
    "ocean_dark":     {"primary": "#1E3A5F", "accent": "#2563EB", "bg": "#0F172A", "text": "#F8FAFC", "card": "#1E293B"},
    "arctic_light":   {"primary": "#0EA5E9", "accent": "#38BDF8", "bg": "#F8FAFC", "text": "#0F172A", "card": "#FFFFFF"},
    "desert_warm":    {"primary": "#B45309", "accent": "#F59E0B", "bg": "#FFFBEB", "text": "#451A03", "card": "#FFFFFF"},
    "forest_green":   {"primary": "#065F46", "accent": "#10B981", "bg": "#F0FDF4", "text": "#052E16", "card": "#FFFFFF"},
    "corporate_gray": {"primary": "#374151", "accent": "#6B7280", "bg": "#F9FAFB", "text": "#111827", "card": "#FFFFFF"},
}

# نسخة افتراضية من ألوان الثيم المخصص — تُستخدم فقط لو لم يوجد شيء
# محفوظ بعد في project settings (أول استخدام لـ "custom" قبل أي حفظ).
_DEFAULT_CUSTOM_COLORS = dict(DEFAULT_SETTINGS["custom_theme_colors"])

_REQUIRED_COLOR_KEYS = ("primary", "accent", "bg", "text", "card")


def apply_rtl():
    st.markdown(RTL_CSS, unsafe_allow_html=True)


def apply_theme_css(theme_key_or_settings="ocean_dark"):
    """
    تطبيق ألوان الثيم فعلياً على الواجهة (خلفية، أزرار، عناوين، بطاقات،
    تبويبات، روابط، جداول، عناصر الإدخال، أشرطة الأدوات)، بما يشمل
    جعل خلفية الجداول (st.dataframe) شفافة ومتوافقة مع لون نص الثيم
    بدل الخلفية البيضاء الافتراضية التي تكسر التناسق البصري في
    الثيمات الداكنة.

    تقبل إما اسم ثيم كنص مباشرة ("ocean_dark") أو dict إعدادات مشروع
    كامل (settings) — نفس مرونة get_chart_theme/apply_plotly_theme،
    حتى تعمل بغض النظر عن الشكل الذي تُستدعى به في كل صفحة. تمرير
    settings كاملة هو الشكل الوحيد الذي يسمح بتفعيل الثيم "المخصص"
    (custom) فعلياً — لأن ألوانه مخزَّنة داخل settings نفسها.

    ملاحظة تقنية مهمة: الألوان "الرسمية" لـ Streamlit (primaryColor،
    backgroundColor...) تُقرأ من config.toml مرة واحدة فقط عند إقلاع
    السيرفر، ولا توجد طريقة رسمية لتغييرها ديناميكياً لكل مستخدم أثناء
    التشغيل. الحل العملي هنا: نُطبّق نفس الألوان مباشرة عبر CSS على
    أهم العناصر المرئية، وعبر متغيرات CSS داخلية لجدول Streamlit
    التفاعلي (راجع توثيق الوحدة أعلاه).
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
            /* ─────────────────────────────────────────────────
               متغيرات جدول Streamlit الداخلية (glide-data-grid).
               st.dataframe يُرسم على <canvas> ولا يتأثر بـ CSS
               التقليدي — لكن المكتبة تقرأ هذه المتغيرات عند كل
               رسم لتحديد ألوان رأس الجدول، الخلفية، النص، والحدود.
               ───────────────────────────────────────────────── */
            --gdg-bg-cell: {card};
            --gdg-bg-cell-medium: {card};
            --gdg-bg-header: {primary};
            --gdg-bg-header-has-focus: {primary};
            --gdg-bg-header-hovered: {accent};
            --gdg-text-dark: {text};
            --gdg-text-light: {text};
            --gdg-text-medium: {text};
            --gdg-text-header: #FFFFFF;
            --gdg-border-color: {accent}66;
            --gdg-horizontal-border-color: {accent}33;
            --gdg-accent-color: {accent};
            --gdg-accent-light: {accent}33;
            --gdg-bg-bubble: {card};
            --gdg-bg-bubble-selected: {accent};
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
           🆕 جداول Markdown (نص التحليل — Story Telling وأي محتوى
           آخر يستخدم صيغة | عمود | عمود |) — رأس بلون primary ونص
           أبيض، خلايا بلون card ونص بلون text، حدود بلون accent —
           بنفس منطق render_themed_table لكن عبر الماركداون العادي
           (Streamlit يحوّل جدول Markdown القياسي إلى <table> HTML
           فعلي تلقائياً، فيستجيب لهذه القواعد مباشرة).
           ───────────────────────────────────────────────────── */
        .stMarkdown table {{
            border: 1px solid {accent}55 !important;
        }}
        .stMarkdown thead tr {{
            background-color: {primary} !important;
        }}
        .stMarkdown th {{
            color: #FFFFFF !important;
            border: 1px solid {accent}55 !important;
        }}
        .stMarkdown td {{
            color: {text} !important;
            background-color: {card} !important;
            border: 1px solid {accent}33 !important;
        }}
        .stMarkdown tbody tr:nth-child(even) td {{
            background-color: {bg} !important;
        }}

        /* ─────────────────────────────────────────────────────
           عناصر الإدخال (نص/فقرة/رقم) وقوائم الاختيار — خلفية
           بطاقة الثيم ولون نص الثيم بدل الخلفية البيضاء/الرمادية
           الافتراضية لـ Streamlit، مع حدود بلون التمييز لإبقائها
           مقروءة بصرياً على أي خلفية.
           ───────────────────────────────────────────────────── */
        .stTextInput input,
        .stTextArea textarea,
        .stNumberInput input {{
            background-color: {card} !important;
            color: {text} !important;
            border: 1px solid {accent}55 !important;
        }}
        .stTextInput input::placeholder,
        .stTextArea textarea::placeholder {{
            color: {text} !important;
            opacity: 0.5;
        }}
        .stSelectbox div[data-baseweb="select"] > div,
        .stMultiSelect div[data-baseweb="select"] > div {{
            background-color: {card} !important;
            color: {text} !important;
            border-color: {accent}55 !important;
        }}
        [data-baseweb="popover"] li,
        [data-baseweb="menu"] li {{
            background-color: {card} !important;
            color: {text} !important;
        }}
        .stMultiSelect span[data-baseweb="tag"] {{
            background-color: {accent} !important;
            color: #FFFFFF !important;
        }}

        /* ─────────────────────────────────────────────────────
           🆕 إصلاح: عناصر الإدخال المعطّلة (disabled) — مثل صناديق
           المعاينة — تتجاهل لون النص العادي في بعض المتصفحات لأن
           الخاصية الداخلية -webkit-text-fill-color على الحقول
           المعطّلة لها أولوية أعلى من color، فيبقى النص بلون باهت
           غير مقروء رغم ضبط اللون أعلاه. نفرضها صراحة هنا.
           ───────────────────────────────────────────────────── */
        .stTextArea textarea:disabled,
        .stTextInput input:disabled,
        .stTextArea textarea[disabled],
        .stTextInput input[disabled] {{
            color: {text} !important;
            -webkit-text-fill-color: {text} !important;
            opacity: 1 !important;
        }}

        /* ─────────────────────────────────────────────────────
           🆕 أزرار القوائم المنبثقة (⁝ خيارات الخلية) وأشرطة أدوات
           الجداول/الرسوم (تحميل CSV، بحث، ملء الشاشة) — نفس مبدأ
           الأزرار العادية أعلاه، حتى لا تبقى بألوان Streamlit
           الافتراضية الرمادية غير المتناسقة مع الثيم.
           ───────────────────────────────────────────────────── */
        [data-testid="stPopover"] > button {{
            background-color: {accent} !important;
            color: #FFFFFF !important;
            border: 1px solid {accent} !important;
        }}
        [data-testid="stPopover"] > button:hover {{
            background-color: {primary} !important;
            border-color: {primary} !important;
        }}
        [data-testid="stElementToolbar"] {{
            background-color: {card}CC !important;
            border-radius: 6px;
        }}
        [data-testid="stElementToolbarButton"] {{
            color: {text} !important;
        }}
        [data-testid="stElementToolbarButton"]:hover {{
            background-color: {accent}33 !important;
        }}

        /* ─────────────────────────────────────────────────────
           شفافية الجداول (st.dataframe) داخل خلايا اللوحات
           والتقارير — طبقة احتياطية بالإضافة لمتغيرات --gdg-*
           أعلاه؛ نُبقي الحاوية شفافة مع حدود خفيفة بلون التمييز.
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
           وألوان كل trace برمجياً عبر apply_plotly_theme() (الطبقة
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

    ملاحظة: تمرير theme_key كنص فقط (وليس settings كاملة) لا يفعّل
    الثيم المخصص (custom) لأن ألوانه مخزَّنة داخل settings — الصفحات
    التي تحتاج دعم custom بالكامل يجب أن تستدعي apply_theme_css
    مباشرة وتمرر settings الكاملة (وهو ما تفعله كل صفحات المشروع
    الحالية فعلياً).
    """
    apply_rtl()
    if theme_key:
        apply_theme_css(theme_key)


def _normalize_custom_colors(raw: dict) -> dict:
    """
    ضمان وجود كل مفاتيح الألوان الخمسة المطلوبة بقيمة نصية صالحة،
    حتى لو كانت settings["custom_theme_colors"] محفوظة جزئياً (مثلاً
    من إصدار قديم أضاف بعض المفاتيح فقط) أو غير موجودة إطلاقاً بعد.
    القيم الناقصة تُستكمل من ثيم ocean_dark كافتراضي معقول.
    """
    raw = raw or {}
    return {
        key: raw.get(key) or _DEFAULT_CUSTOM_COLORS[key]
        for key in _REQUIRED_COLOR_KEYS
    }


def _resolve_theme_colors(settings_or_theme) -> dict:
    """
    قبول إما dict إعدادات مشروع كامل (settings) أو اسم ثيم كنص مباشرة
    — يُستخدم داخلياً من apply_theme_css/get_chart_theme/
    apply_plotly_theme حتى تعمل الدوال الثلاث بنفس المرونة بغض النظر
    عمّا يُمرَّر لها.

    حالة الثيم "المخصص" (custom):
    - لو مُرِّر settings كاملة وكان settings["theme"] == "custom"،
      تُقرأ الألوان الفعلية من settings["custom_theme_colors"].
    - لو مُرِّر اسم الثيم كنص فقط ("custom") بدون settings كاملة، لا
      توجد طريقة لمعرفة الألوان المخصصة الفعلية — نُرجع ألوان
      ocean_dark كافتراضي آمن بدل الفشل.
    """
    if isinstance(settings_or_theme, dict):
        theme_key = settings_or_theme.get("theme", "ocean_dark")
        if theme_key == "custom":
            return _normalize_custom_colors(settings_or_theme.get("custom_theme_colors"))
        return THEME_COLORS.get(theme_key, THEME_COLORS["ocean_dark"])

    theme_key = settings_or_theme or "ocean_dark"
    if theme_key == "custom":
        return dict(_DEFAULT_CUSTOM_COLORS)
    return THEME_COLORS.get(theme_key, THEME_COLORS["ocean_dark"])


def get_theme_colors(settings_or_theme="ocean_dark") -> dict:
    """
    واجهة عامة (public) لقراءة ألوان الثيم الخام الخمسة
    (primary/accent/bg/text/card) — مفيدة لأي كود يحتاج لوناً محدداً
    مباشرة (مثل تلوين نص Story Telling أو عنصر Gauge يُبنى يدوياً)
    بدل الاعتماد فقط على get_chart_theme (التي تُرجع قاموساً مخصصاً
    لـ Plotly Layout فقط ولا يجوز إضافة مفاتيح أخرى له).
    """
    return _resolve_theme_colors(settings_or_theme)


# ══════════════════════════════════════════════════════════════
#  توليد لوحة ألوان (colorway) للرسوم البيانية من ألوان الثيم
# ══════════════════════════════════════════════════════════════

def _hex_to_rgb01(h: str) -> tuple[float, float, float]:
    h = (h or "").lstrip("#")
    if len(h) != 6:
        h = "2563EB"   # احتياط لو جاء لون غير صالح
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _rgb01_to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (max(0.0, min(1.0, c)) for c in rgb)
    return "#{:02X}{:02X}{:02X}".format(int(r * 255), int(g * 255), int(b * 255))


def _generate_chart_colorway(colors: dict, n: int = 6) -> list[str]:
    """
    بناء لوحة ألوان متناسقة للرسوم البيانية (Plotly) انطلاقاً من لون
    "accent" في الثيم الحالي، عبر تدوير Hue بمقادير متساوية — بدل
    الاعتماد على ألوان Plotly الافتراضية العشوائية التي لا تمت بصلة
    لثيم المشروع. أول لون في اللوحة يطابق دائماً لون التمييز (accent)
    نفسه تقريباً، وبقية الألوان متناسقة معه ومتمايزة بصرياً بما يكفي
    للتفريق بين السلاسل. n قابلة للتحديد (مثلاً لعدد شرائح Pie).
    """
    n = max(1, n)
    base_hex = colors.get("accent", "#2563EB")
    r, g, b = _hex_to_rgb01(base_hex)
    h, l, s = colorsys.rgb_to_hls(r, g, b)

    # نضمن تشبعاً وإضاءة معقولين حتى لو كان لون accent المُدخَل من
    # المستخدم شديد الفتوح أو شديد الإعتام (مما قد يُنتج ألواناً غير
    # مقروءة عند تدوير الـ Hue فقط بدون تعديل هذين المكوّنين).
    s = max(s, 0.45)
    l = min(max(l, 0.35), 0.65)

    palette = []
    for i in range(n):
        nh = (h + i * (1.0 / n)) % 1.0
        nr, ng, nb = colorsys.hls_to_rgb(nh, l, s)
        palette.append(_rgb01_to_hex((nr, ng, nb)))
    return palette


def _apply_chart_colors(fig, colors: dict) -> None:
    """
    🆕 فرض ألوان الثيم صراحةً على كل trace في الرسم، بعد إنشائه.

    السبب: Plotly Express يُحدّد لون كل عمود/خط/شريحة لحظة إنشاء الرسم
    اعتماداً على القالب الافتراضي (وليس ديناميكياً من layout.colorway
    لاحقاً) — فتمرير colorway وحده عبر update_layout لا يُغيّر شكل
    رسم بمصفوفة ألوان مختلفة، خصوصاً في الحالة الشائعة لعمود واحد
    (trace واحد) حيث يبقى بلون Plotly الأزرق الافتراضي دائماً. هذه
    الدالة تتجاوز المشكلة بضبط marker/line/fillcolor لكل trace يدوياً.

    تغطي: bar (بما فيها الأعمدة المجمّعة)، line/scatter/area (جميعها
    من نوع "scatter" داخلياً في Plotly)، pie (لون مستقل لكل شريحة)،
    وindicator (شريط الـ Gauge نفسه يأخذ لون accent).
    """
    base_colorway = _generate_chart_colorway(colors, n=6)
    accent = colors.get("accent", base_colorway[0])
    trace_idx = 0

    for trace in fig.data:
        ttype = getattr(trace, "type", None)

        if ttype == "pie":
            # ⚠️ إصلاح: trace.labels عبارة عن مصفوفة (numpy array)، لا
            # قيمة مفردة — استخدام "if trace.labels" مباشرة يرمي
            # "truth value of an array... is ambiguous" بمجرد وجود أكثر
            # من عنصر فيها. الفحص الصحيح هو "is not None" ثم len().
            labels = getattr(trace, "labels", None)
            n_slices = len(labels) if labels is not None else 6
            try:
                trace.marker.colors = _generate_chart_colorway(colors, n=max(n_slices, 1))
            except Exception as e:
                logger.debug("pie coloring skipped: %s", e)
            continue

        if ttype == "indicator":
            # شريط الـ Gauge نفسه يأخذ لون التمييز (accent) — النصوص
            # (الرقم/المحور) تُلوَّن بالفعل عبر layout.font في الأعلى.
            try:
                trace.gauge.bar.color = accent
            except Exception as e:
                logger.debug("gauge coloring skipped: %s", e)
            continue

        color = base_colorway[trace_idx % len(base_colorway)]

        try:
            if ttype == "bar":
                trace.marker.color = color
            elif ttype == "scatter":
                mode = trace.mode or "lines"
                if trace.fill and trace.fill != "none":
                    trace.fillcolor = color
                if "lines" in mode or not mode:
                    trace.line.color = color
                if "markers" in mode:
                    trace.marker.color = color
        except Exception as e:
            logger.debug("trace coloring skipped (type=%s): %s", ttype, e)

        trace_idx += 1


def get_chart_theme(settings_or_theme="ocean_dark") -> dict:
    """
    إرجاع إعدادات ثيم جاهزة للتطبيق مباشرة على أي Plotly figure عبر
    fig.update_layout(**get_chart_theme(settings)) — تجعل خلفية الرسم
    شفافة تماماً، لون النص متوافقاً مع لون نص الثيم، ولوحة ألوان
    السلاسل (colorway) مبنية من لون التمييز (accent) في نفس الثيم.

    ⚠️ ملاحظة: هذا القاموس يُدمَج مباشرة كـ **kwargs في
    fig.update_layout() في أكثر من مكان بالمشروع — لذا يجب أن يحتوي
    فقط على مفاتيح Plotly Layout الصالحة (paper_bgcolor, plot_bgcolor,
    font_color, colorway, legend...). أي مفتاح إضافي غير معروف
    لـ Plotly سيرمي استثناءً. لقراءة الألوان الخام (accent/text/...)
    مباشرة، استخدم get_theme_colors() بدل هذه الدالة.

    يقبل إما dict إعدادات المشروع كاملاً أو اسم ثيم كنص مباشرة.
    """
    colors = _resolve_theme_colors(settings_or_theme)
    return {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font_color": colors["text"],
        "colorway": _generate_chart_colorway(colors),
        "legend": {"font": {"color": colors["text"]}},
    }


def apply_plotly_theme(fig, settings_or_theme="ocean_dark"):
    """
    تطبيق ثيم شفاف متوافق مع لون نص الثيم الحالي مباشرة على كائن
    Plotly figure، بما يشمل فرض ألوان الثيم فعلياً على كل عناصر
    الرسم (أعمدة/خطوط/مساحات/شرائح/شريط Gauge) — وليس فقط الخلفية
    والنص — ثم إرجاعه (لتسلسل الاستدعاءات إن رغبت).

    الاستخدام:
        fig = px.bar(df, x="x", y="y")
        fig = apply_plotly_theme(fig, settings)
        st.plotly_chart(fig, width='stretch')

    يقبل إما dict إعدادات المشروع كاملاً (settings) أو اسم ثيم كنص
    مباشرة (مثل "ocean_dark") — نفس مرونة get_chart_theme أعلاه.
    """
    colors = _resolve_theme_colors(settings_or_theme)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=colors["text"],
        colorway=_generate_chart_colorway(colors),
        legend={"font": {"color": colors["text"]}},
    )
    _apply_chart_colors(fig, colors)
    return fig


# ══════════════════════════════════════════════════════════════
#  🆕 جدول HTML مُنسَّق يدوياً — بديل مضمون التلوين عن st.dataframe
# ══════════════════════════════════════════════════════════════
#
# st.dataframe التفاعلي في Streamlit يُرسم فعلياً على <canvas> عبر
# مكتبة glide-data-grid الداخلية، وليس عناصر HTML عادية. رغم تعريف
# متغيرات CSS (--gdg-*) في apply_theme_css أعلاه كمحاولة أولى، تبيّن
# عملياً أن الجدول التفاعلي لا يلتزم بها بشكل موثوق ويبقى بالألوان
# الافتراضية البيضاء بغض النظر عن الثيم. الحل الموثوق 100% هو بناء
# جدول HTML يدوياً بألوان الثيم مباشرة كنص Markdown — على حساب فقدان
# ميزات التفاعل (الفرز، تغيير حجم الأعمدة، تحميل CSV من الجدول نفسه)
# التي تبقى متاحة فقط في st.dataframe (المُستخدَم في صفحات إدارة
# البيانات مثل ui/data.py وui/files.py التي لم تُعدَّل هنا).

def render_themed_table(df, settings_or_theme="ocean_dark", max_rows: int = None, key: str = None) -> None:
    """
    عرض DataFrame كجدول HTML ملوَّن فعلياً بألوان الثيم الحالي (رأس
    بلون primary ونص أبيض، خلفية الخلايا بلون card، حدود بلون accent،
    ولون نص الخلايا من الثيم) — يُستخدم بدل st.dataframe في أي مكان
    يظهر فيه الجدول كنتيجة عرض نهائية (خلايا اللوحات، نتائج المحادثة،
    بلوكات التقارير) حيث التلوين الصحيح أهم من التفاعل الكامل.

    key: معرّف فريد اختياري لتفادي تصادم أسماء أصناف CSS لو ظهر أكثر
         من جدول مُنسَّق في نفس الصفحة (وإلا يُولَّد تلقائياً).
    """
    colors = _resolve_theme_colors(settings_or_theme)
    show_df = df.head(max_rows) if (max_rows and df is not None) else df

    if show_df is None or show_df.empty:
        st.caption("لا توجد بيانات")
        return

    cls = f"themed-tbl-{key or _uuid_module.uuid4().hex[:8]}"

    header_cells = "".join(f"<th>{_html.escape(str(c))}</th>" for c in show_df.columns)
    body_rows = []
    for _, row in show_df.iterrows():
        cells = "".join(
            f"<td>{_html.escape('' if pd.isna(row[c]) else str(row[c]))}</td>"
            for c in show_df.columns
        )
        body_rows.append(f"<tr>{cells}</tr>")

    st.markdown(
        f"""
        <style>
        .{cls}-wrap {{
            overflow-x: auto;
            border: 1px solid {colors['accent']}40;
            border-radius: 6px;
        }}
        .{cls} {{
            width: 100%;
            border-collapse: collapse;
            direction: rtl;
            text-align: right;
            font-size: 0.9rem;
        }}
        .{cls} thead tr {{ background-color: {colors['primary']}; }}
        .{cls} th {{
            color: #FFFFFF;
            padding: 8px 12px;
            text-align: center;
            border: 1px solid {colors['accent']}55;
            white-space: nowrap;
        }}
        .{cls} td {{
            color: {colors['text']};
            background-color: {colors['card']};
            padding: 6px 12px;
            text-align: center;
            border: 1px solid {colors['accent']}33;
        }}
        .{cls} tbody tr:nth-child(even) td {{
            background-color: {colors['bg']};
        }}
        </style>
        <div class="{cls}-wrap">
        <table class="{cls}">
            <thead><tr>{header_cells}</tr></thead>
            <tbody>{''.join(body_rows)}</tbody>
        </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


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
    """
    رأس الشريط الجانبي: قائمة حساب منسدلة (اسم المستخدم) + المشروع
    الحالي + خروج.

    🆕 اسم المستخدم أصبح زراً/Popover يفتح قائمة حساب سريعة (حالة
    البريد الإلكتروني + رابط مباشر لصفحة إعدادات الحساب) بدل عنوان
    ثابت فقط — راجع _render_account_quick_menu أدناه. الظهور قبل
    المشروع الحالي كما هو مطلوب.

    🆕 هذه الدالة تُستدعى في بداية كل صفحة محمية بعد تسجيل الدخول —
    نقطة الدخول الطبيعية لضبط set_log_username() لهذا الـ context،
    حتى يظهر اسم المستخدم تلقائياً في كل سطر log لاحق (راجع
    core/logger_config.py للتفاصيل الكاملة).
    """
    from core.auth import AuthManager

    username = st.session_state.get("username", "")
    set_log_username(username)

    with st.sidebar:
        has_popover = hasattr(st, "popover")
        menu_ctx = (
            st.popover(f"👋 {username}", width='stretch') if has_popover
            else st.expander(f"👋 {username}", expanded=False)
        )
        with menu_ctx:
            _render_account_quick_menu(AuthManager())

        if st.session_state.get("project_id"):
            settings = st.session_state.db.get_settings() if st.session_state.get("db") else {}
            name = settings.get("project_name", "بدون اسم")
            st.caption(f"📁 المشروع الحالي: **{name}**")
        st.divider()
        if st.button("🚪 تسجيل الخروج", width='stretch'):
            AuthManager().logout(st.session_state.get("token", ""))
            clear_log_username()
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


def _render_account_quick_menu(auth) -> None:
    """
    محتوى قائمة الحساب السريعة المنسدلة من اسم المستخدم في الشريط
    الجانبي: حالة البريد الإلكتروني (موثّق/غير موثّق/غير موجود) وزر
    ينقل مباشرة إلى صفحة الإعدادات — عبر علم "_jump_to_page" في
    session_state يقرأه main.py لتحديد الصفحة المفتوحة افتراضياً عند
    إعادة تشغيل السكربت (لا يوجد routing حقيقي عبر URL في هذا التطبيق).
    """
    user_id = st.session_state.get("user_id")
    info = auth.get_user_info(user_id) if user_id else None

    if info:
        email = info.get("email") or ""
        if email:
            badge = "✅ موثّق" if info.get("email_verified") else "⚠️ غير موثّق"
            st.caption(f"📧 {email} — {badge}")
        else:
            st.caption("📧 لا يوجد بريد إلكتروني مسجَّل")

    if st.button("⚙️ إعدادات الحساب", key="_goto_account_settings", width='stretch'):
        st.session_state["_jump_to_page"] = "settings"
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
