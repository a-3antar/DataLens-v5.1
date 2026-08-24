"""
ui/settings.py
==============
إعدادات المشروع: محرك AI، النموذج، مفتاح API، الثيم، auto-run،
عدد المحاولات، مهلة الاتصال، مدة الانتظار، المنطقة الزمنية،
والحد الزمني الشامل الاختياري.

مفتاح API:
------------
يبقى مخزَّناً في project.db لكل مشروع تحديداً (api_key_{engine})،
تماماً كالسابق — كل مشروع يحتفظ بمفتاحه الخاص وقابل للتغيير بحرية.
بالإضافة لذلك، عند كل حفظ نُخزِّن نسخة "آخر مفتاح استُخدم" في
users.db (عبر core.auth.AuthManager) — تُستخدم فقط كقيمة افتراضية
تُقترح تلقائياً عند إنشاء مشروع جديد (راجع ui/projects.py)، ولا تحل
محل مفتاح المشروع أو تفرض عليه أي شيء.

لون النص:
-----------
لون النص الأساسي كان قبل هذا التعديل ثابتاً حسب الثيم المختار فقط
(color_picker معطّل للعرض فقط). الآن المستخدم يستطيع اختيار لون نص
مخصص بحرية عبر color_picker فعلي — يُحفظ في project settings تحت
"custom_text_color" ويُطبَّق في كل الواجهة (عبر apply_theme_css)
وفي نصوص الرسوم البيانية (عبر apply_plotly_theme في ui/common.py).
"""

import streamlit as st

from ui.common import (
    apply_rtl, apply_theme_css, require_login, require_project, sidebar_header,
    THEME_COLORS, get_theme_colors,
)
from ai.ai_manager import get_engine
from core.auth import AuthManager
from config import AI_ENGINES, THEMES, COMMON_TIMEZONES


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
            index=AI_ENGINES.index(settings.get("ai_engine", "gemini")),
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

            # مزامنة نسخة "آخر مفتاح استُخدم" في users.db — فقط كاقتراح
            # افتراضي لمشاريع جديدة مستقبلاً، لا يمس هذا المشروع
            if engine_name != "ollama" and api_key:
                auth.save_api_key(user_id, engine_name, api_key, model)

            st.success("تم الحفظ")

    with tab_general:
        auto_run = st.toggle("تشغيل الكود تلقائياً (Auto Run)", value=bool(settings.get("auto_run", True)))
        max_tries = st.number_input("عدد المحاولات عند الخطأ", 1, 10, int(settings.get("max_tries", 3)))
        timeout = st.number_input("مهلة الاتصال (ثانية)", 5, 300, int(settings.get("timeout", 30)))
        retry_delay = st.number_input(
            "مدة الانتظار قبل إعادة المحاولة عند فشل الاتصال (ثانية)",
            0, 120, int(settings.get("retry_delay", 10)),
            help="يُستخدم فقط عند فشل الاتصال بمحرك AI نفسه (وليس عند خطأ SQL). "
                 "مع كل محاولة فاشلة يُنتظر هذا القدر من الوقت قبل إعادة المحاولة.",
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
                "retry_delay": retry_delay,
                "max_total_wait_seconds": max_total_wait,
                "story_max_total_wait_seconds": story_max_total_wait,
                "timezone": timezone_choice,
            })
            st.success("تم الحفظ")

    with tab_theme:
        theme_key = st.radio(
            "اختر الثيم",
            list(THEMES.keys()),
            format_func=lambda k: THEMES[k],
            index=list(THEMES.keys()).index(settings.get("theme", "ocean_dark")),
        )
        default_colors = THEME_COLORS.get(theme_key, {})

        c1, c2 = st.columns(2)
        c1.color_picker("اللون الأساسي (ثابت حسب الثيم)", default_colors.get("primary", "#1E3A5F"), disabled=True)
        c2.color_picker("لون التمييز (ثابت حسب الثيم)", default_colors.get("accent", "#2563EB"), disabled=True)

        st.markdown("**🖋️ لون النص**")
        st.caption(
            "لون النص الافتراضي يتبع الثيم المختار، لكن يمكنك اختيار لون "
            "مخصص خاص بك بدلاً منه. هذا اللون يُطبَّق أيضاً على نصوص "
            "الرسوم البيانية (Gauges والمخططات) لتبقى متناسقة ومقروءة."
        )
        current_custom = settings.get("custom_text_color") or default_colors.get("text", "#F8FAFC")
        custom_text_color = st.color_picker("لون النص", current_custom)

        use_default_text = st.checkbox(
            "استخدام لون النص الافتراضي للثيم (تجاهل اختياري أعلاه)",
            value=not bool(settings.get("custom_text_color")),
        )

        st.caption("👀 معاينة فورية قبل الحفظ:")
        preview_settings = {
            "theme": theme_key,
            "custom_text_color": None if use_default_text else custom_text_color,
        }
        apply_theme_css(preview_settings)

        if st.button("💾 حفظ الثيم"):
            db.save_settings({
                "theme": theme_key,
                "custom_text_color": None if use_default_text else custom_text_color,
            })
            st.success("تم الحفظ وتطبيقه على كل صفحات التطبيق فوراً (بما فيها الرسوم البيانية)")
            st.rerun()
