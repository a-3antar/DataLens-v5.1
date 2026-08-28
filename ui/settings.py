"""
ui/settings.py
==============
إعدادات المشروع: محرك AI، النموذج، مفتاح API، الثيم، auto-run،
عدد المحاولات، مهلة الاتصال (عامة + مستقلة لـ Story Telling)،
مدة الانتظار، المنطقة الزمنية، والحد الزمني الشامل الاختياري.

مفتاح API:
------------
يبقى مخزَّناً في project.db لكل مشروع تحديداً (api_key_{engine})،
تماماً كالسابق — كل مشروع يحتفظ بمفتاحه الخاص وقابل للتغيير بحرية.
بالإضافة لذلك، عند كل حفظ نُخزِّن نسخة "آخر مفتاح استُخدم" في
users.db (عبر core.auth.AuthManager) — تُستخدم فقط كقيمة افتراضية
تُقترح تلقائياً عند إنشاء مشروع جديد (راجع ui/projects.py)، ولا تحل
محل مفتاح المشروع أو تفرض عليه أي شيء.

محركات AI:
-----------
"gemini" و"ollama" لهما معالجة خاصة (بروتوكول مختلف). أي محرك آخر في
config.AI_ENGINES (حالياً "groq" و"openrouter") يُبنى تلقائياً عبر
ai.engine_registry + OpenAICompatibleEngine — لا حاجة لأي كود إضافي
هنا عند إضافة محرك جديد للسجل مستقبلاً.

🆕 الثيم المخصص (custom):
----------------------------
بالإضافة إلى الثيمات الجاهزة، يمكن للمستخدم اختيار "🎨 مخصص" وتحديد
الألوان الخمسة بحرّية كاملة عبر color_picker (أساسي، تمييز، خلفية،
نص، بطاقة) — تُحفظ في project settings تحت مفتاح "custom_theme_colors"
وتُستخدم تلقائياً في كل صفحات المشروع (الجداول، الرسوم البيانية،
عناصر الإدخال، Story Telling...) بمجرد اختيار "مخصص" كثيم نشط.

🆕 بذر الألوان تلقائياً من آخر ثيم نشط:
--------------------------------------------
عند فتح تبويب "🎨 مخصص" لأول مرة (أو عند التبديل إليه من ثيم جاهز لم
يُخصَّص بعد)، تُملأ منتقيات الألوان تلقائياً بنفس ألوان الثيم الذي
كان نشطاً قبل ذلك مباشرة (مثلاً Ocean Dark)، بدل البدء من قيم افتراضية
غير ذات صلة — بحيث يبدأ المستخدم من مظهر مألوف ويُعدّل عليه بدل
البدء من الصفر. راجع _seed_custom_color_pickers أدناه للتفاصيل.
"""

import streamlit as st

from ui.common import (
    apply_rtl, apply_theme_css, require_login, require_project, sidebar_header,
    THEME_COLORS, get_chart_theme, apply_plotly_theme,
)
from ai.ai_manager import get_engine
from core.auth import AuthManager
from config import AI_ENGINES, THEMES, COMMON_TIMEZONES, DEFAULT_SETTINGS


def show_settings():
    apply_rtl()
    require_login()
    db = require_project()
    settings = db.get_settings()
    apply_theme_css(settings)
    sidebar_header()

    st.title("⚙️ الإعدادات")
    auth = AuthManager()
    user_id = st.session_state.user_id

    tab_ai, tab_general, tab_theme = st.tabs(["🤖 محرك AI", "⚙️ عام", "🎨 الثيم"])

    with tab_ai:
        engine_name = st.selectbox(
            "محرك AI", AI_ENGINES,
            index=AI_ENGINES.index(settings.get("ai_engine", "gemini")) if settings.get("ai_engine", "gemini") in AI_ENGINES else 0,
        )

        if engine_name == "ollama":
            st.warning(
                "⚠️ Ollama محرك محلي يتصل بسيرفر يعمل على جهازك أو شبكتك "
                "المحلية فقط. لن يعمل هذا الخيار إطلاقاً لو كان التطبيق "
                "منشوراً على Streamlit Community Cloud أو أي استضافة "
                "سحابية أخرى — استخدمه فقط عند تشغيل التطبيق محلياً على "
                "جهازك."
            )

        api_key = ""
        ollama_url = settings.get("ollama_url", "http://localhost:11434")
        if engine_name == "ollama":
            ollama_url = st.text_input("عنوان سيرفر Ollama", value=ollama_url)
        else:
            api_key = st.text_input(
                f"مفتاح API لـ {engine_name}",
                value=settings.get(f"api_key_{engine_name}", ""),
                type="password",
                help="خاص بهذا المشروع فقط ويمكن تغييره بحرية. آخر مفتاح "
                     "تحفظه يُقترح تلقائياً كافتراضي عند إنشاء مشروع جديد.",
            )

        c1, c2 = st.columns([3, 1])
        with c2:
            fetch_models = st.button("🔄 جلب النماذج")

        models = []
        if fetch_models:
            engine = get_engine(engine_name, api_key=api_key, ollama_url=ollama_url)
            if engine:
                r = engine.get_models()
                if r["ok"]:
                    models = r["models"]
                    st.session_state["_fetched_models"] = models
                    st.success(f"تم جلب {len(models)} نموذج")
                else:
                    st.error(r["error"])

        models = st.session_state.get("_fetched_models", [])
        current_model = settings.get("model", "")
        if models:
            idx = models.index(current_model) if current_model in models else 0
            model = st.selectbox("النموذج", models, index=idx)
        else:
            model = st.text_input("النموذج (اكتب الاسم يدوياً)", value=current_model)

        temperature = st.slider("درجة الحرارة (Temperature)", 0.0, 2.0, float(settings.get("temperature", 0.1)), 0.05)

        st.markdown("**قواعد AI العامة (System Prompt مخصص، اختياري)**")
        ai_rules = st.text_area("اترك فارغاً لاستخدام القواعد الافتراضية", value=settings.get("ai_rules", "") or "")

        if st.button("💾 حفظ إعدادات AI"):
            updates = {
                "ai_engine": engine_name,
                "model": model,
                "temperature": temperature,
                "ai_rules": ai_rules or None,
            }
            if engine_name == "ollama":
                updates["ollama_url"] = ollama_url
            else:
                updates[f"api_key_{engine_name}"] = api_key
            db.save_settings(updates)

            # مزامنة نسخة "آخر مفتاح استُخدم" في users.db — فقط
            # كاقتراح افتراضي لمشاريع جديدة مستقبلاً، لا يمس هذا المشروع
            if engine_name != "ollama" and api_key:
                auth.save_api_key(user_id, engine_name, api_key, model)

            st.success("تم الحفظ")

    with tab_general:
        auto_run = st.toggle("تشغيل الكود تلقائياً (Auto Run)", value=bool(settings.get("auto_run", True)))
        max_tries = st.number_input("عدد المحاولات عند الخطأ", 1, 10, int(settings.get("max_tries", 3)))
        timeout = st.number_input("مهلة الاتصال لتوليد SQL (ثانية)", 5, 300, int(settings.get("timeout", 30)))

        # 🆕 مهلة منفصلة لمرحلة السرد — مستقلة تماماً عن مهلة SQL أعلاه.
        # الافتراضي أقل عمداً حتى يفشل الاستدعاء الأول بسرعة ويُعاد
        # المحاولة، بدل انتظار طويل قبل الفشل ثم النجاح في المحاولة
        # الثانية (وهو ما كان يجعل Story Telling يبدو معطّلاً عملياً).
        story_timeout = st.number_input(
            "مهلة الاتصال لمرحلة التحليل النصي — Story Telling (ثانية)",
            5, 300, int(settings.get("story_timeout", 45)),
            help="مستقلة تماماً عن مهلة توليد SQL أعلاه. لو كانت مهلة SQL "
                 "مضبوطة على قيمة كبيرة (مثلاً 100 ثانية)، فإن استخدام نفس "
                 "القيمة لمرحلة السرد يجعل أي استدعاء بطيء يبدو متجمداً "
                 "لفترة طويلة قبل إعادة المحاولة تلقائياً. قيمة أصغر هنا "
                 "(30-45 ثانية) تجعل الفشل والمحاولة التالية أسرع بكثير.",
        )

        retry_delay = st.number_input(
            "مدة الانتظار قبل إعادة المحاولة عند فشل الاتصال (ثانية)",
            0, 120, int(settings.get("retry_delay", 10)),
            help="يُستخدم فقط عند فشل الاتصال بمحرك AI نفسه (وليس عند خطأ SQL). "
                 "مع كل محاولة فاشلة يُنتظر هذا القدر من الوقت قبل إعادة المحاولة. "
                 "يُطبَّق على مرحلتي SQL والسرد معاً.",
        )

        st.markdown("**⏱️ حد زمني إجمالي (اختياري)**")
        st.caption(
            "0 = بدون حد (السلوك الافتراضي: تُستهلك كل المحاولات مع كامل مدة "
            "الانتظار بينها). لو ضُبط بقيمة أكبر من صفر، تتوقف إعادة المحاولة "
            "فوراً عند تجاوز هذا الحد الكلي حتى لو تبقّت محاولات ضمن الحد "
            "الأقصى للمحاولات أعلاه."
        )
        max_total_wait = st.number_input(
            "الحد الزمني الإجمالي لتوليد SQL (ثانية، 0 = بدون حد)",
            0, 3600, int(settings.get("max_total_wait_seconds", 0)),
        )
        story_max_total_wait = st.number_input(
            "الحد الزمني الإجمالي للتحليل النصي — Story Telling (ثانية، 0 = بدون حد)",
            0, 3600, int(settings.get("story_max_total_wait_seconds", 0)),
            help="مستقل عن الحد أعلاه لأن السرد النصي يحتاج استدعاءين "
                 "متتاليين (توليد SQL ثم توليد النص) وطبيعته الزمنية مختلفة.",
        )

        st.markdown("**🌍 المنطقة الزمنية**")
        current_tz = settings.get("timezone", "Asia/Riyadh")
        tz_options = COMMON_TIMEZONES if current_tz in COMMON_TIMEZONES else [current_tz] + COMMON_TIMEZONES
        timezone_choice = st.selectbox(
            "تُستخدم لعرض كل التواريخ والأوقات في التطبيق (آخر تحديث للوحات، "
            "سجل المحادثة...). التخزين الداخلي يبقى UTC دائماً.",
            tz_options, index=tz_options.index(current_tz),
        )

        if st.button("💾 حفظ الإعدادات العامة"):
            db.save_settings({
                "auto_run": auto_run,
                "max_tries": max_tries,
                "timeout": timeout,
                "story_timeout": story_timeout,
                "retry_delay": retry_delay,
                "max_total_wait_seconds": max_total_wait,
                "story_max_total_wait_seconds": story_max_total_wait,
                "timezone": timezone_choice,
            })
            st.success("تم الحفظ")

    with tab_theme:
        _render_theme_tab(db, settings)


# ──────────────────────────────────────────────────────────────
#  تبويب الثيم — ثيمات جاهزة + ثيم مخصص بالكامل
# ──────────────────────────────────────────────────────────────

_COLOR_FIELDS = [
    ("primary", "اللون الأساسي (العناوين والأزرار الرئيسية)"),
    ("accent", "لون التمييز (الأزرار، الروابط، الرسوم البيانية)"),
    ("bg", "لون الخلفية"),
    ("text", "لون النص"),
    ("card", "لون البطاقات والشريط الجانبي"),
]

_SEED_MARKER_KEY = "_custom_theme_seed_signature"


def _render_theme_tab(db, settings: dict):
    current_theme = settings.get("theme", "ocean_dark")
    theme_key = st.radio(
        "اختر الثيم",
        list(THEMES.keys()),
        format_func=lambda k: THEMES[k],
        index=list(THEMES.keys()).index(current_theme) if current_theme in THEMES else 0,
    )

    if theme_key == "custom":
        _render_custom_theme_editor(db, settings)
    else:
        _render_preset_theme_preview(db, theme_key)


def _render_preset_theme_preview(db, theme_key: str):
    """معاينة ثيم جاهز (ألوانه ثابتة — للعرض فقط، غير قابلة للتعديل)."""
    colors = THEME_COLORS.get(theme_key, {})
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.color_picker("الأساسي", colors.get("primary", "#1E3A5F"), disabled=True)
    c2.color_picker("التمييز", colors.get("accent", "#2563EB"), disabled=True)
    c3.color_picker("الخلفية", colors.get("bg", "#0F172A"), disabled=True)
    c4.color_picker("النص", colors.get("text", "#F8FAFC"), disabled=True)
    c5.color_picker("البطاقة", colors.get("card", "#FFFFFF"), disabled=True)

    st.caption("👀 معاينة فورية للثيم المختار قبل الحفظ:")
    apply_theme_css(theme_key)
    _render_theme_preview_sample({"theme": theme_key})

    if st.button("💾 حفظ الثيم"):
        db.save_settings({"theme": theme_key})
        st.success("تم الحفظ وتطبيقه على كل صفحات التطبيق فوراً")
        st.rerun()


def _seed_custom_color_pickers(settings: dict) -> None:
    """
    🆕 بذر منتقيات الألوان الخمسة تلقائياً بألوان الثيم الذي كان نشطاً
    قبل التبديل إلى "مخصص" — بدل تركها بقيم افتراضية غير ذات صلة.

    القاعدة:
    - لو الثيم النشط حالياً هو "custom" فعلاً (المستخدم خصّصه سابقاً
      وحفظه) → نبدأ من custom_theme_colors المحفوظة كما هي.
    - غير ذلك (الثيم النشط حالياً ثيم جاهز، مثل ocean_dark) → نبدأ من
      ألوان ذلك الثيم الجاهز نفسه، حتى يبدأ المستخدم من مظهر مألوف
      يُعدّل عليه بدل البدء من الصفر.

    التنفيذ يعتمد على "توقيع" (signature) نخزّنه في session_state:
    بما أن مفاتيح الـ widgets ثابتة (custom_theme_primary...)،
    Streamlit تتجاهل معامل value بمجرد وجود قيمة سابقة في
    session_state لنفس المفتاح — لذا نفرض القيمة الجديدة يدوياً في
    session_state فقط عند اكتشاف تغيّر فعلي في مصدر البذر (لا نكرر
    الفرض في كل تكرار سكربت، وإلا سيمحو أي تعديل يدوي يجريه المستخدم
    على الألوان).
    """
    current_theme = settings.get("theme", "ocean_dark")
    saved_custom = settings.get("custom_theme_colors")

    if current_theme == "custom" and saved_custom:
        base_colors = {
            key: saved_custom.get(key) or DEFAULT_SETTINGS["custom_theme_colors"][key]
            for key, _label in _COLOR_FIELDS
        }
    else:
        base_key = current_theme if current_theme in THEME_COLORS else "ocean_dark"
        base_colors = dict(THEME_COLORS[base_key])

    signature = (current_theme, tuple(base_colors.get(k, "") for k, _ in _COLOR_FIELDS))
    if st.session_state.get(_SEED_MARKER_KEY) != signature:
        for key, _label in _COLOR_FIELDS:
            st.session_state[f"custom_theme_{key}"] = base_colors[key]
        st.session_state[_SEED_MARKER_KEY] = signature


def _render_custom_theme_editor(db, settings: dict):
    """محرر الثيم المخصص — المستخدم يختار كل الألوان الخمسة بحرّية."""
    st.caption(
        "اختر الألوان الخمسة بحرّية كاملة. تُطبَّق فوراً على كل عناصر "
        "الواجهة (العناوين، الأزرار، الجداول، عناصر الإدخال)، على لوحة "
        "ألوان الرسوم البيانية والـ Gauge، وعلى نص التحليل "
        "(Story Telling) في كل صفحات المشروع بمجرد الحفظ."
    )

    # 🆕 بذر تلقائي من آخر ثيم نشط (أو من الثيم المخصص المحفوظ سابقاً)
    _seed_custom_color_pickers(settings)

    cols = st.columns(5)
    picked = {}
    for (key, label), col in zip(_COLOR_FIELDS, cols):
        with col:
            # لا نمرر value= هنا عمداً — القيمة الابتدائية تأتي فقط من
            # session_state التي بذرناها في _seed_custom_color_pickers،
            # حتى لا تتضارب مع تعديلات المستخدم اللاحقة على كل تكرار.
            picked[key] = st.color_picker(label, key=f"custom_theme_{key}")

    # معاينة حية بالألوان المختارة الآن (حتى قبل الضغط على "حفظ")
    preview_settings = {"theme": "custom", "custom_theme_colors": picked}
    st.caption("👀 معاينة فورية للثيم المخصص بالألوان المختارة أعلاه:")
    apply_theme_css(preview_settings)
    _render_theme_preview_sample(preview_settings)

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("💾 حفظ الثيم المخصص", type="primary", width='stretch'):
            db.save_settings({
                "theme": "custom",
                "custom_theme_colors": picked,
            })
            st.success("تم حفظ الثيم المخصص وتطبيقه على كل صفحات التطبيق فوراً")
            st.rerun()
    with c2:
        if st.button("↺ إعادة تعيين لثيم Ocean Dark", width='stretch'):
            reset_colors = dict(THEME_COLORS["ocean_dark"])
            for key, _label in _COLOR_FIELDS:
                st.session_state[f"custom_theme_{key}"] = reset_colors[key]
            # توقيع فريد يمنع _seed_custom_color_pickers من الكتابة
            # فوقه مباشرة في التكرار التالي للسكربت
            st.session_state[_SEED_MARKER_KEY] = ("__manual_reset__", tuple(reset_colors.items()))
            st.rerun()


def _render_theme_preview_sample(preview_settings: dict):
    """
    عيّنة معاينة مصغّرة تشمل: نص عادي، جدول، رسم بياني، مقياس (Gauge)،
    ومربع نص (textarea) — حتى يرى المستخدم فوراً أثر الثيم على كل هذه
    العناصر معاً (الرسم البياني والـ Gauge يتبعان لوحة ألوان الثيم
    فعلياً عبر apply_plotly_theme، تماماً كما يظهران في صفحات
    المحادثة ولوحات المعلومات) قبل حفظ أي تعديل.
    """
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go

    sample_df = pd.DataFrame({
        "الفئة": ["أ", "ب", "ج", "د"],
        "القيمة": [40, 65, 30, 80],
    })

    p1, p2 = st.columns([1, 1])
    with p1:
        st.text_area(
            "مثال مربع نص (Story Telling / أسئلة)",
            value="هذا نص تجريبي لمعاينة لون الكتابة داخل مربعات النص.",
            height=90, key="_theme_preview_textarea",
        )
        st.dataframe(sample_df, width='stretch', hide_index=True)
    with p2:
        fig = px.bar(sample_df, x="الفئة", y="القيمة", text_auto=True)
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=180)
        apply_plotly_theme(fig, preview_settings)
        st.plotly_chart(fig, width='stretch', key="_theme_preview_chart")

        gauge_fig = go.Figure(go.Indicator(
            mode="gauge+number", value=68,
            gauge={"axis": {"range": [0, 100]}},
        ))
        gauge_fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=180)
        apply_plotly_theme(gauge_fig, preview_settings)
        st.plotly_chart(gauge_fig, width='stretch', key="_theme_preview_gauge")
