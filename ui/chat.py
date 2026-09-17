"""
ui/chat.py
==========
واجهة المحادثة بأسلوب برامج AI Chat: كل سؤال وإجابته يظهران كبطاقة
مستقلة في سجل عمودي يسهل التمرير فيه، ومنطقة كتابة السؤال في أسفل
الصفحة (آخر عنصر يُرسم دائماً) مع عمودين: عمود واسع لمربع النص، وعمود
ضيق بجانبه يحتوي صفين — قائمة "⁝" منسدلة (نوع النتيجة/نوع الرسم/مسح
المحادثة) في الأعلى، وزر "▶️ إرسال" الأساسي أسفلها مباشرة.

🆕 هذا الإصدار (Chat UX — 5):
------------------------------------------------------------------
- كل الأسئلة في هذه الجلسة تُعرض كبطاقات متتالية (st.session_state
  ["chat_thread"]) بدل استبدال آخر نتيجة فقط بكل سؤال جديد — تماماً
  كسجل محادثة حقيقي يسهل الرجوع لأي سؤال سابق فيه بالتمرير لأعلى.
- السؤال الكامل + الـ SQL المُنفَّذ داخل expander مطوي فوق الإجابة
  مباشرة في كل بطاقة (بدل expander منفصل للـ SQL فقط كما كان سابقاً).
- منطقة الكتابة بلا st.form هذه المرة عمداً: st.popover/st.button غير
  مسموحين داخل st.form في Streamlit، وبما أن نوع النتيجة/الرسم أصبحا
  داخل قائمة "⁝" منسدلة (popover)، لا بد أن تكون خارج أي form. مربع
  النص (st.text_area) لا يُرسل تلقائياً بالضغط على Enter (فقط ينشئ
  سطراً جديداً)، لذا لا خطر "إرسال عرضي" حتى بدون form — الإرسال
  الوحيد الممكن يبقى الضغط الصريح على زر "▶️ إرسال". ميزة إضافية غير
  مقصودة: قائمة "نوع الرسم" أصبحت تظهر فقط فعلياً عند اختيار "رسم
  بياني" (كانت تظهر دائماً سابقاً بسبب قيود st.form).
- 🆕 مرجع الأسئلة: st.popover أعلى الصفحة يسرد كل أسئلة الجلسة الحالية
  + سجل المحادثة المحفوظ سابقاً (db.get_chat_history) — الضغط على أي
  سؤال يعيد نصّه إلى مربع الكتابة مباشرة (لا يوجد Scroll-to-element
  موثوق في Streamlit، فهذا أقرب سلوك عملي متاح: يمكن تعديل السؤال أو
  إرساله كما هو من جديد).
- 🆕 زر "🗑️ مسح تاريخ المحادثة" داخل نفس قائمة "⁝" — يمسح كلاً من بطاقات
  الجلسة الحالية (session_state) وسجل project.db (db.clear_chat_history)
  معاً، بتأكيد ثنائي الضغط (نفس نمط "danger_" المتّبع في بقية المشروع).

لا تغيير على أي من ai.ai_manager.AIManager.ask/tell_story ولا على
core.query_engine — فقط طبقة العرض.
"""

import uuid
import html
import hashlib

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from contextlib import contextmanager

# 🆕 st.bottom في إصدارات Streamlit الحديثة كائن حاوية جاهز (proxy) —
# يُستخدم مباشرة كـ context manager (`with st.bottom:` بدون استدعاء/
# أقواس)، على عكس streamlit_extras.bottom_container.bottom() القديمة
# التي هي دالة تُستدعى فترجع الحاوية (`with bottom():`). نوحّد الاثنين
# هنا خلف نفس الواجهة (`with bottom():`) حتى لا يتغيّر باقي الكود.
if hasattr(st, "bottom"):
    @contextmanager
    def bottom():
        with st.bottom:
            yield
else:
    from streamlit_extras.bottom_container import bottom

from ui.common import (
    apply_rtl, apply_theme_css, chat_ui_css, require_login, require_project, sidebar_header,
    format_local_dt, notify, get_chart_theme, apply_plotly_theme,
    render_themed_table,
)
from ai.ai_manager import build_ai_manager
from core.dashboard_cells.cells import _build_chart_figure, _apply_chart_layout_tweaks
from config import CHART_TYPES

_RESULT_TYPE_LABELS = {
    "table": "جدول", "chart": "رسم بياني", "gauge": "مقياس (Gauge)",
    "kpi": "بطاقة مؤشر (KPI)", "story": "تحليل نصي (Story Telling)",
}

_QUESTION_BOX_KEY = "chat_question_box"
_PREFILL_KEY = "_chat_question_prefill"
_THREAD_KEY = "chat_thread"






def show_chat():
    apply_rtl()
    require_login()
    db = require_project()
    settings = db.get_settings()
    apply_theme_css(settings.get("theme", "ocean_dark"))
    sidebar_header()
    st.markdown(chat_ui_css(settings), unsafe_allow_html=True)

    if _THREAD_KEY not in st.session_state:
        st.session_state[_THREAD_KEY] = []

    st.markdown(
        """
        <div class="chat-page-header">
            <div class="chat-page-title"><span class="chat-logo">✨</span><span>اسأل بياناتك</span></div>
            <div class="chat-page-subtitle">اسأل بيانات مشروعك بلغة طبيعية واحصل على إجابة مدعومة بالبيانات.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not db.get_files():
        st.caption("لا توجد جداول بعد. ارفع ملفاً أولاً من صفحة الملفات.")
        return

    ai, settings = build_ai_manager(db)
    if ai.engine is None:
        notify("محرك AI غير معروف. راجع الإعدادات.", kind="error")
        return

    _render_question_reference(db)

    # ── سجل الأسئلة/الإجابات (بطاقات متتالية) ──────────────────
    thread = st.session_state[_THREAD_KEY]
    if not thread:
        st.markdown(
            """
            <div style="text-align:center; padding:4rem 1rem 7rem 1rem; opacity:.72;">
                <div style="font-size:2rem; margin-bottom:.5rem;">✨</div>
                <div style="font-size:1.05rem; font-weight:650;">ابدأ بسؤال عن بياناتك</div>
                <div style="font-size:.88rem; margin-top:.35rem;">مثال: ما إجمالي الإنتاج لكل شهر خلال هذا العام؟</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        for item in thread:
            _render_qa_card(db, settings, item)

    # ── منطقة الكتابة — مثبَّتة فعلياً أسفل الصفحة عبر
    # streamlit_extras.bottom.bottom()، بدل الاعتماد على كونها آخر
    # عنصر في تدفق الصفحة (الذي لا يُبقيها ظاهرة إن طال السجل أعلاها).
    # يتطلب أن تكون حزمة streamlit-extras مضافة إلى requirements.txt.
    with bottom():
        _render_input_area(db, ai, settings)


# ══════════════════════════════════════════════════════════════
#  🆕 مرجع الأسئلة — popover يسرد أسئلة الجلسة + السجل المحفوظ
# ══════════════════════════════════════════════════════════════

def _render_question_reference(db) -> None:
    has_popover = hasattr(st, "popover")
    with st.container(key="chat_history_ref", border=False):
        menu_ctx = (
            st.popover("🕘 المحادثات السابقة", width="stretch") if has_popover
            else st.expander("🕘 المحادثات السابقة", expanded=False)
        )
        with menu_ctx:
            thread = st.session_state.get(_THREAD_KEY, [])

            seen = set()
            entries = []
            for item in reversed(thread):
                q = item["question"].strip()
                if q and q not in seen:
                    seen.add(q)
                    entries.append(q)

            # 🆕 تفادي استعلام db.get_chat_history (يُنفَّذ في كل rerun
            # لأن محتوى popover يُبنى دائماً بغض النظر عن كونه مفتوحاً
            # فعلياً أم لا — قيد Streamlit في طريقة عمل الـ popover) لو
            # أسئلة الجلسة الحالية وحدها كافية أصلاً لملء الحد الأقصى.
            if len(entries) < 30:
                history = db.get_chat_history(limit=30)
                for h in history:
                    q = (h.get("question") or "").strip()
                    if q and q not in seen:
                        seen.add(q)
                        entries.append(q)

            if not entries:
                st.caption("لا توجد أسئلة سابقة بعد")
                return

            st.caption("اضغط على أي سؤال لإعادته إلى مربع الكتابة")
            for q in entries:
                label = q if len(q) <= 60 else q[:60].rstrip() + "…"
                # 🆕 مفتاح ثابت مبني على hash نص السؤال بدل index وحده
                # — يمنع أي التباس بصري لو تغيّر ترتيب/محتوى القائمة
                # بين إعادتي رسم متتاليتين (مثلاً سؤال جديد يُضاف أعلى
                # القائمة فيزيح كل الـ index القديمة بمقدار واحد).
                q_hash = hashlib.md5(q.encode("utf-8")).hexdigest()[:10]
                if st.button(label, key=f"ref_q_{q_hash}", width="stretch"):
                    st.session_state[_PREFILL_KEY] = q
                    st.rerun()


# ══════════════════════════════════════════════════════════════
#  منطقة الكتابة — عمودان: مربع النص | (قائمة ⁝ + زر إرسال)
# ══════════════════════════════════════════════════════════════

def _render_input_area(db, ai, settings: dict) -> None:
    # تطبيق أي سؤال مُختار من "مرجع الأسئلة" قبل رسم مربع النص
    if _PREFILL_KEY in st.session_state:
        st.session_state[_QUESTION_BOX_KEY] = st.session_state.pop(_PREFILL_KEY)

    st.session_state.setdefault("_chat_result_type", "table")
    st.session_state.setdefault("_chat_chart_type", "bar")

    with st.container(key="chat_composer", border=False):
        try:
            col_text, col_side = st.columns([5, 1.25], vertical_alignment="bottom")
        except TypeError:
            col_text, col_side = st.columns([5, 1.25])

        with col_text:
            question = st.text_area(
                "اكتب سؤالك بالعربية أو الإنجليزية", height=100,
                placeholder="اسأل عن المبيعات، الإنتاج، المخزون، الأداء...",
                key=_QUESTION_BOX_KEY, 
                label_visibility="collapsed",
            )

        with col_side:
            _render_options_popover(db)
            run_clicked = st.button("إرسال  ➤", type="primary", width="stretch", key="chat_send")

    if run_clicked:
        _handle_submit(db, ai, settings, question)


def _render_options_popover(db) -> None:
    """
    قائمة "⁝" منسدلة: نوع النتيجة، نوع الرسم (فقط عند اختيار "رسم
    بياني")، ثم زر مسح تاريخ المحادثة. القيم المُختارة تُخزَّن في
    session_state مباشرة (بدون form) فتُقرأ وقت الإرسال.
    """
    has_popover = hasattr(st, "popover")
    # 🆕 fallback الـ expander (نسخ Streamlit القديمة بلا st.popover)
    # فقط: نحافظ على حالة الفتح عبر session_state، وإلا فأي ضغط على
    # زر تأكيد المسح بالأسفل يُعيد رسم الصفحة والـ expander يرتد
    # مغلقاً تلقائياً (expanded ثابتة False) قبل أن يرى المستخدم نتيجة
    # الضغط. غير مطلوب مع st.popover لأنه لا "يُغلق" بنفس المنطق.
    _EXPANDED_KEY = "_chat_options_expander_open"
    ctx = (
        st.popover("⁝ خيارات", width="stretch") if has_popover
        else st.expander("⁝ خيارات", expanded=st.session_state.get(_EXPANDED_KEY, False))
    )
    with ctx:
        result_type = st.selectbox(
            "نوع النتيجة", list(_RESULT_TYPE_LABELS.keys()),
            format_func=lambda t: _RESULT_TYPE_LABELS.get(t, t),
            key="_chat_result_type",
        )
        if result_type == "chart":
            st.selectbox(
                "نوع الرسم", list(CHART_TYPES.keys()),
                format_func=lambda t: CHART_TYPES[t],
                key="_chat_chart_type",
            )

        st.divider()
        _render_clear_history_button(db)


def _render_clear_history_button(db) -> None:
    """
    🆕 كل مسار يؤدي لإعادة رسم القائمة (فتح التأكيد، تنفيذه، أو
    الإلغاء) يضبط _chat_options_expander_open=True قبل rerun — فقط
    ليبقى fallback الـ expander (نسخ Streamlit القديمة بلا popover)
    مفتوحاً عبر كل هذه الخطوات؛ لا تأثير له مع st.popover الحديث.
    """
    confirm_key = "confirm_clear_chat_history"
    if st.session_state.get(confirm_key):
        st.caption("⚠️ سيُحذف كل سجل المحادثة نهائياً.")
        if st.button("⚠️ تأكيد المسح", key="danger_confirm_clear_chat", width="stretch"):
            db.clear_chat_history()
            st.session_state[_THREAD_KEY] = []
            st.session_state.pop(confirm_key, None)
            notify("تم مسح تاريخ المحادثة", kind="success")
            st.rerun()
        if st.button("إلغاء", key="cancel_clear_chat", width="stretch"):
            st.session_state.pop(confirm_key, None)
            st.session_state["_chat_options_expander_open"] = True
            st.rerun()
    else:
        if st.button("🗑️ مسح تاريخ المحادثة", key="danger_clear_chat", width="stretch"):
            st.session_state[confirm_key] = True
            st.session_state["_chat_options_expander_open"] = True
            st.rerun()

def _handle_submit(db, ai, settings: dict, question: str) -> None:
    if not question.strip():
        notify("الرجاء كتابة سؤال", kind="warning")
        return

    result_type = st.session_state.get("_chat_result_type", "table")
    chart_type = st.session_state.get("_chat_chart_type", "bar")

    spinner_msg = (
        "جاري تحليل البيانات وكتابة التقرير..." if result_type == "story"
        else "جاري التفكير..."
    )
    
    # 🆕 إظهار الرسالة في مربع طائر (Toast)
    st.toast(f"⏳ {spinner_msg}", icon="💡")

    if result_type == "story":
        result = ai.tell_story(question, ai_rules=settings.get("ai_rules"))
    else:
        result = ai.ask(question, result_type=result_type, ai_rules=settings.get("ai_rules"))

    chat_id = str(uuid.uuid4())
    db.save_chat_result(
        chat_id, question,
        sql_query=result.get("sql"),
        result_type=result_type,
        result_data={"rows": result.get("rows")} if result["ok"] else None,
        error=None if result["ok"] else result.get("error"),
    )

    st.session_state[_THREAD_KEY].append({
        "id": chat_id,
        "question": question.strip(),
        "result_type": result_type,
        "chart_type": chart_type,
        "result": result,
    })

    if not result["ok"]:
        notify(f"فشل الاستعلام: {result.get('error')}", kind="error")

    st.session_state.pop(_QUESTION_BOX_KEY, None)
    st.rerun()

# ══════════════════════════════════════════════════════════════
#  بطاقة سؤال/إجابة واحدة
# ══════════════════════════════════════════════════════════════

def _render_qa_card(db, settings: dict, item: dict) -> None:
    result = item["result"]
    result_type = item["result_type"]
    chart_type = item.get("chart_type", "bar")
    # 🆕 حالة البطاقة بلون ثابت (chat-status-ok/fail معرَّفة في
    # ui/common.py::chat_ui_css) بدل رمز نصي بلا لون — الفرق بين ✓ و !
    # لا يكفي وحده للتمييز السريع عند تصفّح سجل طويل بالتمرير.
    status_class = "chat-status-ok" if result.get("ok") else "chat-status-fail"
    status_icon = "✓ تم" if result.get("ok") else "! فشل"
    safe_question = html.escape(item["question"].strip())

    with st.container(key=f"chat_item_{item['id']}", border=False):
        st.markdown(
            f"""
            <div class="chat-user-row">
                <div class="chat-user-bubble">
                    <span class="chat-user-label">أنت</span>{safe_question}
                </div>
            </div>
            <div class="chat-answer-head">
                <span class="chat-assistant-icon">✨</span>
                <span>DataLens</span>
                <span class="{status_class}" style="opacity:.9;">{status_icon}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander("السؤال وSQL", expanded=False):
            st.markdown(f"**السؤال الكامل:**\n\n{item['question']}")
            if result.get("sql"):
                st.code(result["sql"], language="sql")
            elif not result.get("ok"):
                st.caption("لم يُولَّد SQL قبل حدوث الخطأ")

        if not result.get("ok"):
            st.error(f"فشل الاستعلام: {result.get('error')}")
        else:
            st.caption(f"عدد المحاولات: {result.get('tries')} | عدد الصفوف: {result.get('rows')}")
            if result.get("auto_fixes"):
                fixes_text = "، ".join(f"«{f['from']}» → «{f['to']}»" for f in result["auto_fixes"])
                st.caption(f"✏️ تم تصحيح اسم عمود تلقائياً: {fixes_text}")

            _render_answer_body(settings, result, result_type, chart_type, key_prefix=item["id"])

        _render_send_to_report_form(db, item)


def _render_answer_body(settings, result: dict, result_type: str, chart_type: str, key_prefix: str) -> None:
    df: pd.DataFrame = result.get("df")
    chart_theme = get_chart_theme(settings)

    if result_type == "table":
        # 🆕 احتياط: result.get("df") قد يعود list[dict] وليس DataFrame
        # جاهزاً في بعض حالات ai.ask() — render_themed_table تتوقع
        # DataFrame حصراً (تستدعي .empty/.columns/.iterrows)، وبدون هذا
        # التحويل كان أي شكل غير DataFrame يُسقط الجدول المُنسَّق ويظهر
        # بدلاً منه كنص/خطأ خام بدل جدول HTML مُلوَّن بالثيم.
        table_df = df if isinstance(df, pd.DataFrame) else pd.DataFrame(df or [])
        render_themed_table(table_df, settings)

    elif result_type == "chart":
        if df is None or df.shape[1] < 2:
            st.caption("النتيجة لا تحتوي أعمدة كافية لرسم بياني")
            if df is not None:
                render_themed_table(df, settings)
        else:
            x_col = df.columns[0]
            y_cols = list(df.columns[1:3])
            try:
                fig = _build_chart_figure(df, x_col, y_cols, chart_type)
                fig.update_layout(margin=dict(l=10, r=10, t=30, b=10))
                _apply_chart_layout_tweaks(fig, chart_type)
                apply_plotly_theme(fig, settings)
                st.plotly_chart(fig, width="stretch", key=f"chart_{key_prefix}")
            except Exception as e:
                st.error(f"تعذر رسم البيانات بنوع «{chart_type}»: {e}")
                st.dataframe(df, width="stretch", hide_index=True)

    elif result_type == "gauge":
        row = df.iloc[0].to_dict() if df is not None and not df.empty else {}
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
        apply_plotly_theme(fig, settings)
        st.plotly_chart(fig, width="stretch", key=f"gauge_{key_prefix}")

    elif result_type == "kpi":
        row = df.iloc[0].to_dict() if df is not None and not df.empty else {}
        actual = row.get("actual_value", 0)
        target = row.get("target_value", 0)
        delta = actual - target if isinstance(actual, (int, float)) and isinstance(target, (int, float)) else None
        st.metric("القيمة", actual, delta=round(delta, 2) if delta is not None else None)
        st.caption(f"الهدف: {target}")

    elif result_type == "story":
        story_text = result.get("story", "")
        st.markdown(story_text)

        queries = result.get("queries", [])
        if queries:
            with st.expander("📊 البيانات المستخدمة"):
                for q in queries:
                    st.markdown(f"**{q.get('title', 'بيانات')}**")
                    if q.get("ok") and q.get("df") is not None:
                        render_themed_table(q["df"], settings)
                    elif not q.get("ok"):
                        st.caption(f"⚠️ فشل هذا الاستعلام: {q.get('error')}")
                    st.divider()


# ══════════════════════════════════════════════════════════════
#  إرسال إلى تقرير — نموذج (form) مستقل لكل بطاقة
# ══════════════════════════════════════════════════════════════

def _render_send_to_report_form(db, item: dict) -> None:
    result = item["result"]
    if not result.get("ok"):
        return

    result_type = item["result_type"]
    chart_type = item.get("chart_type", "bar")
    df = result.get("df")

    reports = db.get_reports()
    key_base = item["id"]

    with st.form(f"send_to_report_form_{key_base}"):
        st.markdown("**📤 إرسال إلى تقرير**")
        report_options = {r["title"]: r["id"] for r in reports}
        report_choice = st.selectbox(
            "اختر تقريراً", list(report_options.keys()) or ["لا يوجد تقارير"],
            key=f"report_choice_{key_base}",
        )
        label = st.text_input("عنوان/تسمية (لـ KPI أو Gauge)", value="", key=f"report_label_{key_base}")
        include_data_table = False
        if result_type == "story":
            include_data_table = st.checkbox(
                "إرفاق جدول البيانات مع التحليل النصي", value=False, key=f"report_incl_{key_base}",
            )
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
                    chart_type=chart_type, queries=result.get("queries"),
                )
                if r["ok"]:
                    notify("تمت الإضافة إلى التقرير", kind="success")
                else:
                    notify(r["error"], kind="error")


def _add_block_for_type(rm, report_id, result_id, result_type, df: pd.DataFrame, label,
                         story_text: str = None, include_data_table: bool = False,
                         chart_type: str = "bar", queries: list = None):
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
            if queries:
                for q in queries:
                    if q.get("ok") and q.get("df") is not None and not q["df"].empty:
                        rm.add_table(
                            report_id, str(uuid.uuid4()),
                            q["df"].to_dict(orient="records"), list(q["df"].columns),
                        )
            elif df is not None and not df.empty:
                rm.add_table(report_id, result_id, df.to_dict(orient="records"), list(df.columns))
        return result
    return {"ok": False, "error": "نوع نتيجة غير مدعوم"}
