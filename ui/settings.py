"""
ui/settings.py
==============
إعدادات المشروع: محرك AI، النموذج، مفاتيح API، الثيم، auto-run،
عدد المحاولات، مهلة الاتصال، ومدة الانتظار قبل إعادة المحاولة.
"""

import streamlit as st

from ui.common import apply_rtl, require_login, require_project, sidebar_header, THEME_COLORS
from ai.ai_manager import get_engine
from config import AI_ENGINES, THEMES


def show_settings():
    apply_rtl()
    require_login()
    db = require_project()
    sidebar_header()

    st.title("⚙️ الإعدادات")
    settings = db.get_settings()

    tab_ai, tab_general, tab_theme = st.tabs(["🤖 محرك AI", "⚙️ عام", "🎨 الثيم"])

    with tab_ai:
        engine_name = st.selectbox(
            "محرك AI", AI_ENGINES,
            index=AI_ENGINES.index(settings.get("ai_engine", "gemini")),
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
        if st.button("💾 حفظ الإعدادات العامة"):
            db.save_settings({
                "auto_run": auto_run,
                "max_tries": max_tries,
                "timeout": timeout,
                "retry_delay": retry_delay,
            })
            st.success("تم الحفظ")

    with tab_theme:
        theme_key = st.radio(
            "اختر الثيم",
            list(THEMES.keys()),
            format_func=lambda k: THEMES[k],
            index=list(THEMES.keys()).index(settings.get("theme", "ocean_dark")),
        )
        colors = THEME_COLORS.get(theme_key, {})
        c1, c2 = st.columns(2)
        c1.color_picker("اللون الأساسي", colors.get("primary", "#1E3A5F"), disabled=True)
        c2.color_picker("لون التمييز", colors.get("accent", "#2563EB"), disabled=True)
        if st.button("💾 حفظ الثيم"):
            db.save_settings({"theme": theme_key})
            st.success("تم الحفظ — سيُطبق الثيم عند إعادة التحميل")
