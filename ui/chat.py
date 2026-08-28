"""
ui/chat.py
==========
واجهة المحادثة: كتابة سؤال بلغة طبيعية → SQL من AI → تنفيذ → عرض النتيجة
كـ جدول / رسم بياني / gauge / KPI، مع إمكانية الإرسال للتقرير.

مفتاح API يُقرأ من إعدادات المشروع (project.db) كما كان دائماً — كل
مشروع له مفتاحه الخاص.

🆕 خلفية الرسوم البيانية شفافة ومتوافقة مع لون نص الثيم الحالي، وألوان
الأعمدة/الخطوط/شريط الـ Gauge نفسها تُفرض فعلياً من ألوان الثيم عبر
ui.common.apply_plotly_theme() (وليس فقط الخلفية والنص كما كان سابقاً
— راجع توثيق apply_plotly_theme في ui/common.py للتفاصيل). نص صندوق
Story Telling يأخذ الآن لون نص الثيم الحالي صراحةً عبر get_theme_colors()
بدل الاعتماد فقط على وراثة CSS العامة. الرسائل التوضيحية الثابتة
استُبدلت بإشعارات toast عابرة عبر ui.common.notify.
"""

import uuid

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ui.common import (
    apply_rtl, apply_theme_css, require_login, require_project, sidebar_header,
    format_local_dt, notify, get_chart_theme, apply_plotly_theme, get_theme_colors,
    render_themed_table,
)
from ai.ai_manager import AIManager, get_engine
from core.query_engine import QueryEngine
from config import CHART_TYPES


def show_chat():
    apply_rtl()
    require_login()
    db = require_project()
    settings = db.get_settings()
    apply_theme_css(settings.get("theme", "ocean_dark"))
    sidebar_header()

    st.title("💬 اسأل بياناتك")

    if not db.get_files():
        st.caption("لا توجد جداول بعد. ارفع ملفاً أولاً من صفحة الملفات.")
        return

    engine_name = settings.get("ai_engine", "gemini")
    engine = get_engine(
        engine_name=engine_name,
        api_key=settings.get(f"api_key_{engine_name}", ""),
        model=settings.get("model", ""),
        timeout=settings.get("timeout", 30),
        ollama_url=settings.get("ollama_url", "http://localhost:11434"),
    )
    if engine is None:
        notify("محرك AI غير معروف. راجع الإعدادات.", kind="error")
        return

    ai = AIManager(
        db, engine,
        temperature=settings.get("temperature", 0.1),
        max_tries=settings.get("max_tries", 3),
        retry_delay=settings.get("retry_delay", 10),
        max_total_wait_seconds=settings.get("max_total_wait_seconds", 0),
        story_max_total_wait_seconds=settings.get("story_max_total_wait_seconds", 0),
        story_timeout=settings.get("story_timeout", 45),
    )

    c1, c2 = st.columns([3, 1])
    with c1:
        question = st.text_area("اكتب سؤالك بالعربية أو الإنجليزية", height=90)
    with c2:
        result_type = st.selectbox(
            "نوع النتيجة", ["table", "chart", "gauge", "kpi", "story"],
            format_func=lambda t: {
                "table": "جدول", "chart": "رسم بياني", "gauge": "مقياس (Gauge)",
                "kpi": "بطاقة مؤشر (KPI)", "story": "تحليل نصي (Story Telling)",
            }.get(t, t),
        )
        chart_type = "bar"
        if result_type == "chart":
            chart_type = st.selectbox(
                "نوع الرسم",
                list(CHART_TYPES.keys()),
                format_func=lambda t: CHART_TYPES[t],
            )
        run_clicked = st.button("▶️ إرسال", width='stretch', type="primary")

    if run_clicked:
        if not question.strip():
            notify("الرجاء كتابة سؤال", kind="warning")
        else:
            spinner_msg = (
                "جاري تحليل البيانات وكتابة التقرير..." if result_type == "story"
                else "جاري التفكير..."
            )
            with st.spinner(spinner_msg):
                if result_type == "story":
                    result = ai.tell_story(question, ai_rules=settings.get("ai_rules"))
                else:
                    result = ai.ask(question, result_type=result_type, ai_rules=settings.get("ai_rules"))
            st.session_state.last_result = result
            st.session_state.last_result_type = result_type
            st.session_state.last_chart_type = chart_type
            st.session_state.last_question = question
            chat_id = str(uuid.uuid4())
            db.save_chat_result(
                chat_id, question,
                sql_query=result.get("sql"),
                result_type=result_type,
                result_data={"rows": result.get("rows")} if result["ok"] else None,
                error=None if result["ok"] else result.get("error"),
            )
            if not result["ok"]:
                notify(f"فشل الاستعلام: {result.get('error')}", kind="error")

    result = st.session_state.get("last_result")
    if result:
        _render_result(
            db, settings, result,
            st.session_state.get("last_result_type", "table"),
            st.session_state.get("last_chart_type", "bar"),
        )

    st.divider()
    with st.expander("🕓 سجل المحادثة"):
        history = db.get_chat_history(limit=20)
        for h in history:
            status = "✅" if not h.get("error") else "❌"
            st.markdown(f"{status} **{h['question']}**")
            if h.get("sql_query"):
                st.code(h["sql_query"], language="sql")
            if h.get("error"):
                st.caption(f"خطأ: {h['error']}")
            st.caption(format_local_dt(h["created_at"], settings))
            st.markdown("---")


def _render_result(db, settings, result: dict, result_type: str, chart_type: str = "bar"):
    if not result["ok"]:
        if result.get("sql"):
            with st.expander("SQL الأخير"):
                st.code(result["sql"], language="sql")
        return

    with st.expander("💻 SQL المُنفذ", expanded=False):
        st.code(result["sql"], language="sql")
    st.caption(f"عدد المحاولات: {result['tries']} | عدد الصفوف: {result['rows']}")

    if result.get("auto_fixes"):
        fixes_text = "، ".join(f"«{f['from']}» → «{f['to']}»" for f in result["auto_fixes"])
        st.caption(f"✏️ تم تصحيح اسم عمود تلقائياً: {fixes_text}")

    df: pd.DataFrame = result["df"]
    chart_theme = get_chart_theme(settings)

    if result_type == "table":
        render_themed_table(df, settings)

    elif result_type == "chart":
        if df.shape[1] < 2:
            st.caption("النتيجة لا تحتوي أعمدة كافية لرسم بياني")
            render_themed_table(df, settings)
        else:
            x_col = df.columns[0]
            y_cols = list(df.columns[1:3])
            try:
                if chart_type == "line":
                    fig = px.line(df, x=x_col, y=y_cols, markers=True)
                elif chart_type == "pie":
                    fig = px.pie(df, names=x_col, values=y_cols[0])
                elif chart_type == "area":
                    fig = px.area(df, x=x_col, y=y_cols)
                elif chart_type == "scatter":
                    fig = px.scatter(df, x=x_col, y=y_cols[0])
                else:
                    fig = px.bar(df, x=x_col, y=y_cols, barmode="group", text_auto=True)
                fig.update_layout(margin=dict(l=10, r=10, t=30, b=10))
                # 🆕 يفرض ألوان الثيم فعلياً على الأعمدة/الخطوط/الشرائح
                # (وليس فقط الخلفية والنص) — راجع ui/common.py للتفاصيل.
                apply_plotly_theme(fig, settings)
                st.plotly_chart(fig, width='stretch')
            except Exception as e:
                st.error(f"تعذر رسم البيانات بنوع «{chart_type}»: {e}")
                st.dataframe(df, width='stretch', hide_index=True)

    elif result_type == "gauge":
        row = df.iloc[0].to_dict() if not df.empty else {}
        current = row.get("current_value", 0)
        mn = row.get("min_value", 0)
        mx = row.get("max_value", 100)
        fig = go.Figure(go.Indicator(
            mode="gauge+number", value=current,
            number={"font": {"color": chart_theme["font_color"]}},
            gauge={
                "axis": {"range": [mn, mx], "tickfont": {"color": chart_theme["font_color"]}},
            },
        ))
        # 🆕 apply_plotly_theme تضبط شريط الـ Gauge نفسه بلون التمييز
        # (accent) الخاص بالثيم الحالي بدل تركه بلون افتراضي ثابت.
        apply_plotly_theme(fig, settings)
        st.plotly_chart(fig, width='stretch')

    elif result_type == "kpi":
        row = df.iloc[0].to_dict() if not df.empty else {}
        actual = row.get("actual_value", 0)
        target = row.get("target_value", 0)
        delta = actual - target
        st.metric("القيمة", actual, delta=round(delta, 2) if isinstance(delta, (int, float)) else None)
        st.caption(f"الهدف: {target}")

    elif result_type == "story":
        story_text = result.get("story", "")
        # 🆕 لون النص يُحدَّد صراحة من ألوان الثيم الحالي (بما فيها
        # الثيم المخصص) بدل الاعتماد فقط على وراثة CSS العامة، حتى
        # يستجيب فوراً لأي تغيير في "لون النص" من صفحة الإعدادات.
        text_color = get_theme_colors(settings)["text"]
        st.markdown(
            f'<div dir="rtl" style="text-align:right; color:{text_color};">{story_text}</div>',
            unsafe_allow_html=True,
        )
        with st.expander("📊 البيانات المستخدمة في التحليل"):
            render_themed_table(df, settings)

    st.divider()
    with st.form("send_to_report_form"):
        st.markdown("**📤 إرسال إلى تقرير**")
        reports = db.get_reports()
        report_options = {r["title"]: r["id"] for r in reports}
        report_choice = st.selectbox("اختر تقريراً", list(report_options.keys()) or ["لا يوجد تقارير"])
        label = st.text_input("عنوان/تسمية (لـ KPI أو Gauge)", value="")
        include_data_table = False
        if result_type == "story":
            include_data_table = st.checkbox("إرفاق جدول البيانات مع التحليل النصي", value=False)
        submitted = st.form_submit_button("إرسال")
        if submitted:
            if not reports:
                notify("أنشئ تقريراً أولاً من صفحة التقارير", kind="warning")
            else:
                from exporters.report_manager import ReportManager
                rm = ReportManager(db)
                report_id = report_options[report_choice]
                result_id = str(uuid.uuid4())
                r = _add_block_for_type(
                    rm, report_id, result_id, result_type, df, label,
                    story_text=result.get("story"), include_data_table=include_data_table,
                    chart_type=chart_type,
                )
                if r["ok"]:
                    notify("تمت الإضافة إلى التقرير", kind="success")
                else:
                    notify(r["error"], kind="error")


def _add_block_for_type(rm, report_id, result_id, result_type, df: pd.DataFrame, label,
                         story_text: str = None, include_data_table: bool = False,
                         chart_type: str = "bar"):
    if result_type == "table":
        return rm.add_table(report_id, result_id, df.to_dict(orient="records"), list(df.columns))
    if result_type == "chart":
        x_col = df.columns[0]
        y_cols = list(df.columns[1:3])
        return rm.add_chart(
            report_id, result_id, chart_type, df.to_dict(orient="records"), x_col, y_cols, title=label,
        )
    if result_type == "gauge":
        row = df.iloc[0].to_dict() if not df.empty else {}
        return rm.add_gauge(
            report_id, result_id,
            current_value=row.get("current_value", 0),
            min_value=row.get("min_value", 0),
            max_value=row.get("max_value", 100),
            label=label,
        )
    if result_type == "kpi":
        row = df.iloc[0].to_dict() if not df.empty else {}
        return rm.add_kpi(
            report_id, result_id,
            actual_value=row.get("actual_value", 0),
            target_value=row.get("target_value", 0),
            label=label,
        )
    if result_type == "story":
        text = story_text or ""
        if label:
            text = f"## {label}\n\n{text}"
        result = rm.add_paragraph(report_id, text)
        if result["ok"] and include_data_table:
            rm.add_table(report_id, result_id, df.to_dict(orient="records"), list(df.columns))
        return result
    return {"ok": False, "error": "نوع نتيجة غير مدعوم"}
