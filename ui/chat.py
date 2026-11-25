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

🆕 نوع نتيجة جديد "qa" — سؤال وجواب (رؤى):
----------------------------------------------
نوع إضافي بجانب الجدول/الرسم/Gauge/KPI/Story: يسأل المستخدم عن أي
شيء يخص بياناته (والفلاتر يكتبها في نص السؤال نفسه)، فيبحث AI ويفلتر
ويلخّص ويستخرج Insights ويكتب رأيه (ai.ai_manager.AIManager.answer_question).
عند اختيار هذا النوع يظهر في قائمة "⁝" خيار "طول الإجابة" (مختصرة/مفصّلة).
البيانات المستخدمة تظهر كاملة داخل expander (جدول مُنسَّق بالثيم لو
≤ _QA_THEMED_TABLE_MAX_ROWS صفاً، وإلا st.dataframe التفاعلي لسلاسة
التمرير). الإرسال للتقرير يمر كفقرة Markdown مثل Story تماماً.

🆕 ذاكرة المحادثة الداخلية لنوع "qa" (آخر 10 أسئلة + ملخص تراكمي):
------------------------------------------------------------------------
- الأسئلة/الأجوبة: تُبنى من أسئلة/أجوبة "qa" الناجحة في هذه الجلسة
  (_build_qa_history) ويقتطعها AIManager إلى آخر config.QA_HISTORY_TURNS
  (10) — تُرسَل للذكاء الاصطناعي مع أجوبتها.
- الملخص التراكمي: يُحفظ في st.session_state["_qa_summary"] ويُحدَّث
  بعد كل جواب "qa" ناجح عبر AIManager.summarize_qa_memory — استفسار
  AI منفصل تماماً عن الإجابة نفسها: _handle_submit يسجّل العمل المعلَّق
  (_PENDING_SUMMARY_KEY) ثم يُعيد الرسم، فتظهر الإجابة كاملة أولاً،
  وبعد رسم الصفحة (بطاقات + مربع الكتابة) تُنفِّذ _flush_pending_summary
  التلخيص. لو أُرسل سؤال جديد قبل اكتمال التلخيص يُنفَّذ أولاً (يمنع
  تقدّم الملخص خطوة عن الأسئلة). الملخص يُرسَل مع الأسئلة السابقة
  ليحفظ ما سبق الأسئلة العشرة. فشل التلخيص لا يؤثر على أي إجابة
  (يبقى الملخص القديم).
- الذاكرة والملخص داخليان بالكامل: لا يُعرضان ولا يُعدَّلان من المستخدم.
- "🗑️ مسح تاريخ المحادثة" يمسح الذاكرة (بطاقات الجلسة) والملخص معاً.

🆕 مسح خانة السؤال بعد الإجابة:
---------------------------------
st.session_state.pop(مفتاح_الـ widget) بعد إنشاء الـ widget لا يمسح
قيمته فعلياً (الواجهة الأمامية تعيد إرسال القيمة القديمة). الحل: علم
_CLEAR_QUESTION_KEY يُضبط في _handle_submit، ويُقرأ في بداية
_render_input_area قبل رسم st.text_area فيُضبط st.session_state
[مفتاح_الـ widget] = "" — نفس نمط _PREFILL_KEY المستخدم لمرجع الأسئلة.

🆕 Smart Value Resolver في بطاقة السؤال (نتائج الجدول/الرسم/Gauge/KPI):
-------------------------------------------------------------------------
_handle_submit يستدعي ai.ask(..., resolve_values=True) لهذه الأنواع
فقط (لا Story ولا QA). عندما ينجح SQL ويرجع 0 صف بسبب قيمة نصية لا
تطابق البيانات (مثل «باريس» مقابل Paris) تضيف النتيجة أحد مفتاحين
(راجع ai/ai_manager.py وcore/value_resolver.py):
  - value_fixes: تصحيح تلقائي مؤكد طُبِّق وأُعيد التنفيذ — تُعرض
    ملاحظة قصيرة بالقيمة القديمة والجديدة في سطر الحالة.
  - value_resolution: أكثر من احتمال (needs_choice) أو لا شيء
    (not_found) — _render_value_resolution ترسم داخل نفس البطاقة
    رسالة وقائمة اختيار (st.form لكل بطاقة) مرتبة بالثقة مع تصنيف
    المطابقة وسببها.
بعد اختيار المستخدم يُنفَّذ استعلام واحد معدَّل (ai.apply_value_choices)
ويحلّ نتيجته مكان نتيجة البطاقة نفسها في chat_thread (مع حفظ السجل في
project.db بنفس chat_id)، ثم st.rerun() — فلا يُعاد استدعاء AI ولا
يتكرر تنفيذ السؤال الأصلي مع إعادات الرسم اللاحقة، ويبقى سياق السؤال
وبقية شروطه كما هي. أثناء انتظار الاختيار لا يُرسم جسم النتيجة (لا
بيانات أصلاً)، وحالة not_found تُظهر رسالة بدل اقتراحات وهمية.

لا تغيير على أي من ai.ai_manager.AIManager.tell_story/answer_question ولا
على core.query_engine — فقط طبقة العرض.
"""

import uuid
import html
import hashlib

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.common import (
    apply_rtl, apply_theme_css, require_login, require_project, sidebar_header,
    format_local_dt, notify, get_chart_theme, apply_plotly_theme,
    render_themed_table,
)
from ai.ai_manager import build_ai_manager
from core.dashboard_cells.cells import _build_chart_figure, _apply_chart_layout_tweaks
from core.value_resolver import LEVEL_LABELS
from config import CHART_TYPES, QA_ANSWER_LENGTHS, QA_DEFAULT_ANSWER_LENGTH

_RESULT_TYPE_LABELS = {
    "qa": "💡 سؤال وجواب (رؤى)",
    "table": "جدول",
    "chart": "رسم بياني",
    "gauge": "مقياس (Gauge)",
    "kpi": "بطاقة مؤشر (KPI)",
    "story": "تحليل نصي (Story Telling)",
}

_QUESTION_BOX_KEY = "chat_question_box"
_PREFILL_KEY = "_chat_question_prefill"
_CLEAR_QUESTION_KEY = "_chat_clear_question"
_THREAD_KEY = "chat_thread"
# 🆕 الملخص التراكمي الداخلي لمحادثة "qa" (نص) — لا يُعرض للمستخدم
_SUMMARY_KEY = "_qa_summary"
# 🆕 عمل تلخيص معلَّق {"question", "answer"} ينتظر رسم الإجابة أولاً
_PENDING_SUMMARY_KEY = "_qa_summary_pending"
_SCROLL_TO_ITEM_KEY = "_chat_scroll_to_item"
_ACTIVE_PROJECT_KEY = "_chat_active_project_id"
_SCROLL_TO_BOTTOM_KEY = "_chat_scroll_to_bottom"
_CHAT_PAGE_ENTERED_KEY = "_chat_page_entered"

# 🆕 أقصى عدد صفوف يُعرض به جدول بيانات Q&A كجدول HTML مُنسَّق
# (render_themed_table) — أعلى من ذلك يُستخدم st.dataframe التفاعلي
# لأن بناء HTML يدوي لآلاف الصفوف بطيء (راجع معيار الاستخدام في
# ui/common.py). لا يقتطع أي صف — فقط يغيّر طريقة العرض.
_QA_THEMED_TABLE_MAX_ROWS = 200

# 🆕 أنواع النتائج التي يُفعَّل لها Smart Value Resolver (نتائج SQL مفردة)
_RESOLVER_RESULT_TYPES = ("table", "chart", "gauge", "kpi")


def _resolve_project_id(db) -> str:
    """استخراج معرّف المشروع الحالي بأمان لبدء محادثة جديدة عند تغييره."""
    for attr in ("project_id", "current_project_id", "id"):
        val = getattr(db, attr, None)
        if val:
            return str(val)
    proj = getattr(db, "project", None)
    if isinstance(proj, dict) and proj.get("id"):
        return str(proj["id"])
    return str(id(db))


def _reset_session_thread_state() -> None:
    """تفريغ حالة المحادثة والذاكرة المؤقتة عند تغيير المشروع أو مسح السجل."""
    st.session_state[_THREAD_KEY] = []
    st.session_state.pop(_SUMMARY_KEY, None)
    st.session_state.pop(_PENDING_SUMMARY_KEY, None)
    st.session_state.pop(_SCROLL_TO_ITEM_KEY, None)
    st.session_state["_chat_options_section"] = ""


def _trigger_scroll_to_bottom() -> None:
    """تمرير الحاوية النشطة في Streamlit إلى آخر المحادثة (أحدث رسالة ومنطقة الإدخال)."""
    anchor_id = "chat-bottom-anchor"
    st.markdown(f'<div id="{anchor_id}" style="height:1px;"></div>', unsafe_allow_html=True)
    st.components.v1.html(
        f"""<script>
        (function() {{
            const anchorId = {anchor_id!r};
            const scrollToBottom = () => {{
                try {{
                    const doc = window.parent.document;
                    const anchor = doc.getElementById(anchorId);
                    if (anchor) {{
                        anchor.scrollIntoView({{ behavior: "auto", block: "end" }});
                        let parent = anchor.parentElement;
                        while (parent && parent !== doc.body) {{
                            if (parent.scrollHeight > parent.clientHeight + 40) {{
                                parent.scrollTop = parent.scrollHeight;
                            }}
                            parent = parent.parentElement;
                        }}
                    }}
                    const main = doc.querySelector('[data-testid="stMain"]');
                    if (main) main.scrollTop = main.scrollHeight;
                    const block = doc.querySelector('[data-testid="stMainBlockContainer"]');
                    if (block && block.scrollHeight > block.clientHeight) {{
                        block.scrollTop = block.scrollHeight;
                    }}
                    window.parent.scrollTo({{ top: doc.body.scrollHeight, behavior: "auto" }});
                }} catch (e) {{}}
            }};
            scrollToBottom();
            requestAnimationFrame(scrollToBottom);
            setTimeout(scrollToBottom, 80);
            setTimeout(scrollToBottom, 220);
            setTimeout(scrollToBottom, 500);
        }})();
        </script>""",
        height=0,
        scrolling=False,
    )


def _chat_page_css(settings: dict) -> str:
    """تنسيق محلي لواجهة المحادثة دون الاعتماد على CSS خاص غير موجود في common.py."""
    return """
    <style>
      [data-testid="stMainBlockContainer"] {
        max-width: 1100px;
        padding-top: 1.1rem;
        padding-bottom: 6rem;
      }
      [data-testid="stExpander"] { border-radius: 10px; margin: .25rem 0 .55rem; }
      .chat-page-header { margin: 0 0 1rem; }
      .chat-page-title { display:flex; align-items:center; gap:.55rem; font-size:1.35rem; font-weight:750; }
      .chat-page-subtitle { opacity:.68; font-size:.88rem; margin-top:.25rem; }
      .chat-user-row { display:flex; justify-content:flex-start; margin:.65rem 0 .45rem; }
      .chat-user-bubble {
        max-width:92%; padding:.7rem 1rem; border-radius:14px;
        background:rgba(128,128,128,.13); line-height:1.8;
        overflow-wrap:anywhere; white-space:pre-wrap;
      }
      .chat-user-label { display:block; font-size:.72rem; font-weight:700; opacity:.65; margin-bottom:.2rem; }
      .chat-answer-head { display:flex; align-items:center; gap:.45rem; font-weight:700; margin:.5rem 0 .65rem; }
      .chat-assistant-icon { font-size:1.1rem; }
      .chat-status-ok { color:#16a34a; }
      .chat-status-fail { color:#dc2626; }
      [data-testid="st-key-chat_composer"] {
        padding:.4rem 0 .15rem;
        border-top:1px solid rgba(128,128,128,.22);
      }
      [data-testid="stChatInput"] { border-radius:14px; }
      [data-testid="stChatInput"] textarea { direction:rtl; text-align:right; }
    </style>
    """


def show_chat():
    apply_rtl()
    require_login()
    db = require_project()
    settings = db.get_settings()
    apply_theme_css(settings.get("theme", "ocean_dark"))
    sidebar_header()
    st.markdown(_chat_page_css(settings), unsafe_allow_html=True)

    current_project_id = _resolve_project_id(db)
    if st.session_state.get(_ACTIVE_PROJECT_KEY) != current_project_id:
        st.session_state[_ACTIVE_PROJECT_KEY] = current_project_id
        _reset_session_thread_state()
    elif _THREAD_KEY not in st.session_state:
        st.session_state[_THREAD_KEY] = []

    if not st.session_state.get(_CHAT_PAGE_ENTERED_KEY):
        st.session_state[_CHAT_PAGE_ENTERED_KEY] = True
        if _SCROLL_TO_ITEM_KEY not in st.session_state:
            st.session_state[_SCROLL_TO_BOTTOM_KEY] = True

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
            _render_qa_card(db, ai, settings, item)

    # شريط الكتابة والخيارات في أسفل المحادثة.
    # st.chat_input يدعم Enter للإرسال وShift+Enter لسطر جديد.
    _render_input_area(db, ai, settings)

    if thread and st.session_state.pop(_SCROLL_TO_BOTTOM_KEY, False):
        if _SCROLL_TO_ITEM_KEY not in st.session_state:
            _trigger_scroll_to_bottom()

    # 🆕 بعد أن رُسمت الإجابة ومربع الكتابة بالكامل: تحديث ملخص المحادثة
    # (استفسار AI منفصل، صامت — لا يُعرض للمستخدم).
    _flush_pending_summary(ai)


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
#  منطقة الكتابة — خيارات مختصرة + حقل محادثة مثبت أسفل الصفحة
# ══════════════════════════════════════════════════════════════

def _render_input_area(db, ai, settings: dict) -> None:
    # تطبيق سؤال مختار من المحادثات السابقة قبل إنشاء chat_input.
    if st.session_state.pop(_CLEAR_QUESTION_KEY, False):
        st.session_state[_QUESTION_BOX_KEY] = ""
    if _PREFILL_KEY in st.session_state:
        st.session_state[_QUESTION_BOX_KEY] = st.session_state.pop(_PREFILL_KEY)

    st.session_state.setdefault("_chat_result_type", "qa")
    st.session_state.setdefault("_chat_chart_type", "bar")
    st.session_state.setdefault("_chat_answer_length", QA_DEFAULT_ANSWER_LENGTH)

    # حقل السؤال وزر الخيارات في صف واحد.
    with st.container(key="chat_composer", border=False):
        input_col, options_col = st.columns([0.88, 0.12], vertical_alignment="bottom")
        with input_col:
            question = st.chat_input(
                "اكتب سؤالك... Enter للإرسال، Shift + Enter لسطر جديد",
                key=_QUESTION_BOX_KEY,
                width="stretch",
            )
        with options_col:
            _render_options_popover(db)

    if question:
        _handle_submit(db, ai, settings, question)


def _render_options_popover(db) -> None:
    """قائمة خيارات موحدة؛ اختيار أي بند يعرض إعداداته داخل النافذة نفسها."""
    sections = {
        "type": "نوع المحادثة",
        "reports": "إرسال إلى التقارير",
        "history": "المحادثات السابقة",
        "clear": "مسح المحادثة",
    }
    st.session_state.setdefault("_chat_options_section", "")

    with st.popover("⚙️ خيارات", width="stretch"):
        cols = st.columns(2)
        for idx, (key, label) in enumerate(sections.items()):
            with cols[idx % 2]:
                if st.button(label, key=f"chat_option_{key}", width="stretch"):
                    st.session_state["_chat_options_section"] = key

        section = st.session_state.get("_chat_options_section", "")
        if not section:
            st.caption("اختر أحد الخيارات لعرض إعداداته.")
            return

        st.divider()
        st.markdown(f"**{sections[section]}**")

        if section == "type":
            result_type = st.selectbox(
                "نوع النتيجة", list(_RESULT_TYPE_LABELS.keys()),
                format_func=lambda t: _RESULT_TYPE_LABELS.get(t, t),
                key="_chat_result_type",
            )
            if result_type == "chart":
                st.selectbox(
                    "نوع الرسم", list(CHART_TYPES.keys()),
                    format_func=lambda t: CHART_TYPES[t], key="_chat_chart_type",
                )
            elif result_type == "qa":
                st.selectbox(
                    "طول الإجابة", list(QA_ANSWER_LENGTHS.keys()),
                    format_func=lambda t: QA_ANSWER_LENGTHS[t], key="_chat_answer_length",
                )

        elif section == "reports":
            thread = st.session_state.get(_THREAD_KEY, [])
            latest = next((item for item in reversed(thread)
                           if (item.get("result") or {}).get("ok")), None)
            if latest:
                _render_latest_report_form(db, latest)
            else:
                st.info("لا توجد نتيجة ناجحة في المحادثة لإضافتها إلى تقرير.")

        elif section == "history":
            thread = st.session_state.get(_THREAD_KEY, [])
            seen, entries = set(), []
            for item in reversed(thread):
                q = (item.get("question") or "").strip()
                if q and q not in seen:
                    seen.add(q)
                    entries.append(q)
            if len(entries) < 30:
                for item in db.get_chat_history(limit=30):
                    q = (item.get("question") or "").strip()
                    if q and q not in seen:
                        seen.add(q)
                        entries.append(q)
            if not entries:
                st.caption("لا توجد محادثات سابقة.")
            else:
                for q in entries:
                    label = q if len(q) <= 65 else q[:65].rstrip() + "…"
                    q_hash = hashlib.md5(q.encode("utf-8")).hexdigest()[:10]
                    if st.button(label, key=f"ref_q_{q_hash}", width="stretch"):
                        st.session_state[_PREFILL_KEY] = q
                        st.session_state["_chat_options_section"] = ""
                        st.rerun()

        elif section == "clear":
            _render_clear_history_button(db)


def _render_latest_report_form(db, item: dict) -> None:
    """نموذج إضافة أحدث نتيجة ناجحة إلى أحد التقارير من قائمة الخيارات."""
    result = item["result"]
    reports = db.get_reports()
    if not reports:
        st.info("لا توجد تقارير. أنشئ تقريراً أولاً من صفحة التقارير.")
        return

    result_type = item["result_type"]
    df = result.get("df")
    chart_type = item.get("chart_type", "bar")
    report_options = {r["title"]: r["id"] for r in reports}
    with st.form(f"quick_send_report_{item['id']}"):
        report_choice = st.selectbox("اختر التقرير", list(report_options))
        label = st.text_input("عنوان/تسمية (لـ KPI أو Gauge)", value="")
        include_data_table = False
        if result_type in ("story", "qa"):
            include_data_table = st.checkbox(
                "إرفاق جدول البيانات مع التحليل" if result_type == "story"
                else "إرفاق جداول البيانات مع الإجابة", value=False,
            )
        submitted = st.form_submit_button("إضافة إلى التقرير", width="stretch")
        if submitted:
            from exporters.report_manager import ReportManager
            rm = ReportManager(db)
            response = _add_block_for_type(
                rm, report_options[report_choice], str(uuid.uuid4()), result_type,
                df, label, story_text=result.get("story") or result.get("answer"),
                include_data_table=include_data_table, chart_type=chart_type,
                queries=result.get("queries"),
            )
            if response["ok"]:
                notify("تمت الإضافة إلى التقرير", kind="success")
            else:
                notify(response["error"], kind="error")


def _render_clear_history_button(db) -> None:
    """مسح سجل المحادثة والذاكرة الداخلية بعد تأكيد المستخدم."""
    confirm_key = "confirm_clear_chat_history"
    if st.session_state.get(confirm_key):
        st.warning("سيُحذف سجل المحادثة نهائياً.")
        if st.button("تأكيد مسح المحادثة", key="danger_confirm_clear_chat", width="stretch"):
            db.clear_chat_history()
            _reset_session_thread_state()
            st.session_state.pop(confirm_key, None)
            notify("تم مسح المحادثة", kind="success")
            st.rerun()
        if st.button("إلغاء", key="cancel_clear_chat", width="stretch"):
            st.session_state.pop(confirm_key, None)
            st.rerun()
    elif st.button("مسح المحادثة", key="danger_clear_chat", width="stretch"):
        st.session_state[confirm_key] = True
        st.rerun()


def _build_qa_history() -> list:
    """
    🆕 ذاكرة محادثة نوع "qa": أسئلة/أجوبة "qa" الناجحة فقط من هذه
    الجلسة بالترتيب الزمني. الاقتطاع إلى آخر config.QA_HISTORY_TURNS
    عنصر يتم داخل AIManager.answer_question (مكان واحد لهذا القرار).
    """
    turns = []
    for item in st.session_state.get(_THREAD_KEY, []):
        r = item.get("result") or {}
        if item.get("result_type") == "qa" and r.get("ok") and r.get("answer"):
            turns.append({"question": item["question"], "answer": r["answer"]})
    return turns


def _flush_pending_summary(ai) -> None:
    """
    🆕 تنفيذ تحديث الملخص التراكمي المعلَّق (إن وُجد) كاستفسار AI
    منفصل: الملخص السابق + آخر سؤال/جواب → ملخص جديد في session_state.
    يُستدعى بعد رسم الإجابة (show_chat)، أو قبل معالجة سؤال جديد
    (_handle_submit) لو لم يكتمل. العمل المعلَّق يُحذف فقط بعد انتهاء
    المحاولة، فلو قاطع المستخدم السكربت بإرسال سؤال جديد أثناء
    التلخيص، يُعاد تنفيذه قبل السؤال التالي. فشل التلخيص (أو أي
    استثناء) لا يؤثر على أي إجابة: يبقى الملخص القديم.
    """
    pending = st.session_state.get(_PENDING_SUMMARY_KEY)
    if not pending:
        return
    try:
        r = ai.summarize_qa_memory(
            st.session_state.get(_SUMMARY_KEY, ""),
            pending.get("question", ""), pending.get("answer", ""),
        )
        if r.get("ok") and r.get("summary"):
            st.session_state[_SUMMARY_KEY] = r["summary"]
    except Exception as e:
        # لا نُسقط الصفحة بسبب عملية مساندة
        import logging
        logging.getLogger(__name__).warning("QA summary update failed: %s", e)
    finally:
        st.session_state.pop(_PENDING_SUMMARY_KEY, None)


def _handle_submit(db, ai, settings: dict, question: str) -> None:
    if not question.strip():
        notify("الرجاء كتابة سؤال", kind="warning")
        return

    # 🆕 لو كان تلخيص السؤال السابق لم يكتمل (قاطعه هذا الإرسال)، ننفّذه
    # أولاً حتى يُبنى السؤال الجديد على ملخص محدَّث.
    _flush_pending_summary(ai)

    result_type = st.session_state.get("_chat_result_type", "qa")
    chart_type = st.session_state.get("_chat_chart_type", "bar")
    answer_length = st.session_state.get("_chat_answer_length", QA_DEFAULT_ANSWER_LENGTH)

    if result_type == "story":
        spinner_msg = "جاري تحليل البيانات وكتابة التقرير..."
    elif result_type == "qa":
        spinner_msg = "جاري البحث في البيانات واستخراج الرؤى..."
    else:
        spinner_msg = "جاري التفكير..."

    # 🆕 إظهار الرسالة في مربع طائر (Toast)
    st.toast(f"⏳ {spinner_msg}", icon="💡")

    if result_type == "story":
        result = ai.tell_story(question, ai_rules=settings.get("ai_rules"))
    elif result_type == "qa":
        # 🆕 آخر الأسئلة/الأجوبة + الملخص التراكمي الداخلي يُرسَلان للـ AI
        # فقط (لا يُعرضان) — يُبنى history قبل إضافة هذا السؤال للسجل.
        result = ai.answer_question(
            question, ai_rules=settings.get("ai_rules"),
            answer_length=answer_length, history=_build_qa_history(),
            summary=st.session_state.get(_SUMMARY_KEY, ""),
        )
    else:
        # 🆕 Smart Value Resolver مفعَّل لنتائج SQL المفردة (جدول/رسم/gauge/KPI)
        result = ai.ask(
            question, result_type=result_type, ai_rules=settings.get("ai_rules"),
            resolve_values=result_type in _RESOLVER_RESULT_TYPES,
        )

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
    st.session_state[_SCROLL_TO_ITEM_KEY] = chat_id

    if not result["ok"]:
        notify(f"فشل الاستعلام: {result.get('error')}", kind="error")

    # 🆕 جدولة تحديث الملخص كاستفسار منفصل بعد عرض هذه الإجابة (يُنفَّذ
    # في _flush_pending_summary بعد إعادة الرسم، لا هنا)
    if result_type == "qa" and result.get("ok") and result.get("answer"):
        st.session_state[_PENDING_SUMMARY_KEY] = {
            "question": question.strip(), "answer": result["answer"],
        }

    # 🆕 مسح خانة السؤال عبر العلم (يُنفَّذ في بداية _render_input_area
    # بعد rerun، قبل إنشاء الـ widget) — pop المباشر للمفتاح لا يمسحها.
    st.session_state[_CLEAR_QUESTION_KEY] = True
    st.rerun()


# ══════════════════════════════════════════════════════════════
#  بطاقة سؤال/إجابة واحدة
# ══════════════════════════════════════════════════════════════

def _render_qa_card(db, ai, settings: dict, item: dict) -> None:
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
        if st.session_state.get(_SCROLL_TO_ITEM_KEY) == item["id"]:
            anchor_id = f"chat-new-answer-{item['id']}"
            st.markdown(f'<div id="{anchor_id}"></div>', unsafe_allow_html=True)
            st.components.v1.html(
                f"""<script>
                const targetId = {anchor_id!r};
                const scrollToAnswer = () => {{
                    try {{
                        const target = window.parent.document.getElementById(targetId);
                        if (target) target.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                    }} catch (e) {{}}
                }};
                scrollToAnswer();
                setTimeout(scrollToAnswer, 150);
                </script>""",
                height=0,
                scrolling=False,
            )
            st.session_state.pop(_SCROLL_TO_ITEM_KEY, None)

        # SQL والبيانات والإرسال إلى التقارير في Expander موحّد.
        used_queries = result.get("queries", []) or []
        has_sql = bool(result.get("sql"))
        with st.expander("SQL والبيانات المستخدمة وإرسال إلى التقارير", expanded=False):
            if has_sql:
                st.markdown("**SQL المُنفَّذ**")
                st.code(result["sql"], language="sql")
            elif not result.get("ok"):
                st.caption("لم يُولَّد SQL قبل حدوث الخطأ")

            if used_queries:
                if has_sql:
                    st.divider()
                st.markdown("**البيانات المستخدمة**")
                for query_index, query_item in enumerate(used_queries, start=1):
                    _render_query_data_block(settings, query_item)
                    if query_index < len(used_queries):
                        st.divider()

            _render_send_to_report_form(db, item)

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

        if not result.get("ok"):
            st.error(f"فشل الاستعلام: {result.get('error')}")
        else:
            st.caption(f"عدد المحاولات: {result.get('tries')} | عدد الصفوف: {result.get('rows')}")
            if result.get("auto_fixes"):
                fixes_text = "، ".join(f"«{f['from']}» → «{f['to']}»" for f in result["auto_fixes"])
                st.caption(f"✏️ تم تصحيح اسم عمود تلقائياً: {fixes_text}")
            if result.get("value_fixes"):
                value_text = "، ".join(f"«{f['from']}» → «{f['to']}»" for f in result["value_fixes"])
                st.caption(f"🔎 تم تصحيح قيمة في الفلتر تلقائياً: {value_text}")

            resolution = result.get("value_resolution")
            if resolution:
                _render_value_resolution(db, ai, item, resolution)

            # أثناء انتظار اختيار المستخدم لا توجد بيانات تُعرض (0 صف)
            if not (resolution and resolution.get("status") == "needs_choice"):
                _render_answer_body(settings, result, result_type, chart_type, key_prefix=item["id"])


# ══════════════════════════════════════════════════════════════
#  🆕 Smart Value Resolver — اختيار القيمة المقصودة
# ══════════════════════════════════════════════════════════════

def _candidate_label(cand: dict) -> str:
    """نص خيار واحد في قائمة الاختيار: القيمة · تصنيف المطابقة (الثقة) — السبب."""
    level = LEVEL_LABELS.get(cand["level"], cand["level"])
    return f"{cand['value']}  ·  {level} ({int(round(cand['score'] * 100))}%) — {cand['reason']}"


def _render_value_resolution(db, ai, item: dict, resolution: dict) -> None:
    """
    رسالة + نموذج اختيار داخل بطاقة السؤال نفسها (راجع توثيق الوحدة أعلاه).
    - not_found: رسالة فقط، دون اقتراحات.
    - needs_choice: st.form واحد لكل بطاقة، قائمة لكل قيمة لم تُحسم
      تلقائياً (العناصر المؤكدة تلقائياً تُعرض كسطر ملاحظة وتُطبَّق مع
      الاختيار). الإرسال ينفّذ استعلاماً معدَّلاً واحداً ويستبدل نتيجة
      البطاقة ثم st.rerun().
    """
    if resolution.get("status") == "not_found":
        st.info(
            resolution.get("message")
            or "لم أجد في البيانات قيمة مطابقة لما كتبته — جرّب صياغة أخرى."
        )
        return

    items = resolution.get("items", [])
    pending = [it for it in items if not it.get("auto")]
    wanted_text = "، ".join(f"«{it['wanted']}»" for it in pending) or "القيمة المطلوبة"

    with st.form(f"value_resolution_form_{item['id']}"):
        st.markdown(f"**لم أجد تطابقاً مباشراً مع {wanted_text}. هل تقصد إحدى القيم التالية؟**")

        for it in items:
            if it.get("auto"):
                st.caption(f"✓ «{it['wanted']}» ← «{it['chosen']}» (تطابق مؤكد)")

        selections = {}
        for it in pending:
            options = it["candidates"]
            selections[it["id"]] = st.selectbox(
                f"«{it['wanted']}» — عمود {it['column']}",
                options=list(range(len(options))), index=None,
                format_func=lambda i, opts=options: _candidate_label(opts[i]),
                placeholder="اختر القيمة الصحيحة",
                key=f"vr_{item['id']}_{it['id']}",
            )
        submitted = st.form_submit_button("تطبيق الاختيار", type="primary")

    if not submitted:
        return

    if any(sel is None for sel in selections.values()):
        notify("اختر قيمة لكل عنصر أولاً", kind="warning")
        return

    choices = {
        it["id"]: it["candidates"][selections[it["id"]]]["value"] for it in pending
    }
    with st.spinner("⏳ جاري إعادة تنفيذ الاستعلام بالقيمة المختارة..."):
        new_result = ai.apply_value_choices(resolution["sql"], items, choices)

    if not new_result["ok"]:
        st.error(f"فشل إعادة التنفيذ: {new_result.get('error')}")
        return

    # نفس سياق السؤال الأصلي: نحتفظ بعدد المحاولات ونستبدل النتيجة في مكانها
    new_result["tries"] = item["result"].get("tries", 1)
    item["result"] = new_result
    db.save_chat_result(
        item["id"], item["question"],
        sql_query=new_result.get("sql"),
        result_type=item["result_type"],
        result_data={"rows": new_result.get("rows")},
        error=None,
    )
    notify("تم تنفيذ الاستعلام بالقيمة المختارة", kind="success")
    st.rerun()


def _render_query_data_block(settings, q: dict) -> None:
    """
    🆕 عرض بيانات استعلام واحد (نوع "qa") كاملة بلا اقتطاع — جدول
    مُنسَّق بالثيم لو ≤ _QA_THEMED_TABLE_MAX_ROWS صفاً، وإلا
    st.dataframe التفاعلي (تمرير/فرز سلس لبيانات كبيرة).
    """
    st.markdown(f"**{q.get('title', 'بيانات')}**")
    if not q.get("ok"):
        st.caption(f"⚠️ فشل هذا الاستعلام: {q.get('error')}")
        return

    qdf = q.get("df")
    if qdf is None or qdf.empty:
        st.caption("لا توجد صفوف مطابقة")
        return

    st.caption(f"{len(qdf)} صف")
    if len(qdf) <= _QA_THEMED_TABLE_MAX_ROWS:
        render_themed_table(qdf, settings)
    else:
        st.dataframe(qdf, width="stretch", hide_index=True)


def _render_answer_body(settings, result: dict, result_type: str, chart_type: str, key_prefix: str) -> None:
    df: pd.DataFrame = result.get("df")
    chart_theme = get_chart_theme(settings)

    if result_type == "table":
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

    elif result_type == "qa":
        # 🆕 الإجابة (Markdown: الإجابة / الرؤى / الرأي / ملاحظات) ثم
        # البيانات المستخدمة كاملة داخل expander.
        st.markdown(result.get("answer", ""))


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
        if result_type in ("story", "qa"):
            include_data_table = st.checkbox(
                "إرفاق جدول البيانات مع التحليل النصي" if result_type == "story"
                else "إرفاق جداول البيانات مع الإجابة",
                value=False, key=f"report_incl_{key_base}",
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
                    story_text=result.get("story") or result.get("answer"),
                    include_data_table=include_data_table,
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
    if result_type in ("story", "qa"):
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