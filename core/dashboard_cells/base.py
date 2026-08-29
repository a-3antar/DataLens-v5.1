"""
core/dashboard_cells/base.py
==============================
الكلاس الأب المجرَّد (Abstract Base Class) لكل أنواع خلايا لوحة
المعلومات (باستثناء EmptyCell — راجع core/dashboard_cells/empty_cell.py
للسبب). يجمع كل السلوك المشترك بين الأنواع:

- بيانات الخلية المشتركة (position, title, question, chart_type,
  base_sql, last_result, last_sql, last_error, last_updated_at).
- التنفيذ الافتراضي (execute) لأي خلية "قائمة على SQL" (كل الأنواع
  عدا StoryCell التي تستدعي AI دائماً وتُعيد تعريف execute بالكامل).
- تحويل النتيجة الخام إلى شكل قابل للتخزين (_base_stored_dict) —
  كل subclass يبني عليها في to_stored_dict.
- عرض حالتي الخطأ/الفراغ (_render_error_or_empty) وتذييل "آخر تحديث"
  (_render_updated_caption) — مشتركتان 100% بين كل الأنواع.
- محرر الخلية الكامل (render_editor): عنوان + سؤال + حقول خاصة بالنوع
  (عبر render_type_specific_fields القابلة للتخصيص) + اختبار + حفظ/إلغاء.
- قائمة الإجراءات (render_actions_menu): تحديث/تعديل/إفراغ — موحّدة
  100% ولا يُعاد تعريفها في أي subclass.
- الحفظ/الإفراغ (save/clear): تتعامل مع core.project_db.ProjectDB كما
  كانت دائماً (بدون أي تعديل على project_db نفسه).

كل subclass concrete (TableCell/ChartCell/GaugeCell/KpiCell/StoryCell)
مسؤول فقط عن: to_stored_dict، render_result، وعند الحاجة
render_type_specific_fields وexecute (StoryCell فقط).
"""

import logging
import datetime as _dt
from abc import ABC, abstractmethod
from typing import Optional

import streamlit as st
import pandas as pd
import numpy as np

from ui.common import render_themed_table, format_local_dt, get_theme_colors, notify
from ai.ai_manager import build_ai_manager

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  تعقيم القيم قبل التخزين كـ JSON (مشتركة بين كل الأنواع)
# ══════════════════════════════════════════════════════════════

def _json_safe(value):
    """
    تحويل قيمة واحدة إلى نوع قابل للتسلسل عبر json.dumps مباشرة —
    نتائج DuckDB/pandas قد تحتوي pandas.Timestamp، numpy.int64/
    float64/bool_، أو NaT/NaN، ولا شيء منها قابل للتسلسل افتراضياً.
    """
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, _dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        f = float(value)
        return None if pd.isna(f) else f
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and pd.isna(value):
        return None
    if value is pd.NaT:
        return None
    return value


def _sanitize_rows(rows: list) -> list:
    """تطبيق _json_safe على كل قيمة في كل صف من صفوف النتيجة."""
    return [{k: _json_safe(v) for k, v in row.items()} for row in rows]


class DashboardCellBase(ABC):
    """
    الكلاس الأب لكل خلايا لوحة المعلومات المُهيَّأة (لها question).

    display_type: خاصية صنفية ثابتة لكل subclass — نفس القيمة النصية
    المخزَّنة في عمود dashboard_cells.display_type (لا تغيير في الـ
    schema). label: نص العرض المستخدم في العناوين وقوائم اختيار النوع.
    """

    display_type: str = "table"
    label: str = "خلية"

    def __init__(
        self,
        position: int,
        title: Optional[str] = None,
        question: Optional[str] = None,
        chart_type: Optional[str] = None,
        base_sql: Optional[str] = None,
        last_result: Optional[dict] = None,
        last_sql: Optional[str] = None,
        last_error: Optional[str] = None,
        last_updated_at: Optional[str] = None,
    ):
        self.position = position
        self.title = title
        self.question = question
        self.chart_type = chart_type
        self.base_sql = base_sql
        self.last_result = last_result
        self.last_sql = last_sql
        self.last_error = last_error
        self.last_updated_at = last_updated_at

    # ──────────────────────────────────────────────────────────
    #  بناء من صف قاعدة بيانات خام
    # ──────────────────────────────────────────────────────────

    @classmethod
    def from_row(cls, row: dict) -> "DashboardCellBase":
        """بناء كائن من صف كما يُرجعه db.get_dashboard_cells()."""
        return cls(
            position=row["position"],
            title=row.get("title"),
            question=row.get("question"),
            chart_type=row.get("chart_type"),
            base_sql=row.get("base_sql"),
            last_result=row.get("last_result"),
            last_sql=row.get("last_sql"),
            last_error=row.get("last_error"),
            last_updated_at=row.get("last_updated_at"),
        )

    # ──────────────────────────────────────────────────────────
    #  التنفيذ — افتراضي (SQL: base_sql أو AI)، StoryCell يُعيد تعريفه
    # ──────────────────────────────────────────────────────────

    def execute(self, ai_manager, query_engine, filters: list, ai_rules: Optional[str]) -> dict:
        """
        تنفيذ منطق الخلية وإرجاع نتيجة خام — لا يكتب في project_db
        إطلاقاً (آمن للاستدعاء داخل thread، تماماً كفلسفة _process_cell
        الحالية). الافتراضي هنا يغطي كل الأنواع "القائمة على SQL"
        (Table/Chart/Gauge/Kpi): base_sql محفوظ → تحديث سريع بدون AI،
        غير ذلك → استدعاء AI. StoryCell وحدها تُعيد تعريف هذه الدالة
        بالكامل لأنها تستدعي AI دائماً بمرحلتين (SQL ثم سرد نصي).
        """
        if self.base_sql:
            return self._run_fast(query_engine, filters)

        r = ai_manager.ask(
            self.question, result_type=self.display_type,
            ai_rules=ai_rules, filters=filters,
        )
        r["used_ai"] = True
        return r

    def _run_fast(self, query_engine, filters: list) -> dict:
        """تنفيذ base_sql مع الفلاتر عبر QueryEngine مباشرة (بدون AI)."""
        result = query_engine.run_with_filters(self.base_sql, filters)
        if not result["ok"]:
            return {"ok": False, "error": result["error"], "sql": self.base_sql, "used_ai": False}
        return {
            "ok": True,
            "sql": result.get("sql", self.base_sql),
            "df": result["df"],
            "rows": result["rows"],
            "used_ai": False,
        }

    # ──────────────────────────────────────────────────────────
    #  تحويل النتيجة الخام إلى شكل قابل للتخزين — مجرّد
    # ──────────────────────────────────────────────────────────

    @abstractmethod
    def to_stored_dict(self, raw_result: dict) -> dict:
        """
        تحويل نتيجة execute() الخام إلى dict قابل للتخزين مباشرة عبر
        db.save_dashboard_cell_result() — كل subclass يبني على
        _base_stored_dict ويضيف حقوله الخاصة عند الحاجة (chart_type
        لـ ChartCell، story لـ StoryCell).
        """
        raise NotImplementedError

    def _base_stored_dict(self, raw_result: dict) -> dict:
        """الجزء المشترك من التخزين: أعمدة وصفوف مُعقَّمة للتسلسل."""
        df = raw_result.get("df")
        raw_rows = df.to_dict(orient="records") if df is not None else []
        return {
            "columns": list(df.columns) if df is not None else [],
            "rows": _sanitize_rows(raw_rows),
        }

    # ──────────────────────────────────────────────────────────
    #  عرض النتيجة المخزَّنة — مجرّد، كل نوع مسؤول عن ثيمه الخاص
    # ──────────────────────────────────────────────────────────

    @abstractmethod
    def render_result(self, settings: dict, dashboard_id: str) -> None:
        """
        عرض self.last_result (أو self.last_error/الحالة الفارغة) في
        واجهة Streamlit. يستقبل settings الكاملة ويستدعي داخلياً
        get_theme_colors/apply_plotly_theme/get_chart_theme بنفسه —
        لا ألوان خام تُمرَّر من الخارج. dashboard_id مطلوب فقط لبناء
        مفاتيح widgets فريدة (رسوم Plotly).

        كل subclass يبدأ عادة بـ:
            if self._render_error_or_empty(): return
        """
        raise NotImplementedError

    def _render_error_or_empty(self) -> bool:
        """
        معالجة موحّدة لحالتي "آخر تحديث فشل" و"لم يُحدَّث بعد" — مشتركة
        بين كل الأنواع. ترجع True لو تم التعامل مع الحالة بالكامل (لا
        شيء إضافي للعرض)، أو False لو توجد نتيجة فعلية يجب عرضها.
        """
        if self.last_error:
            st.error(f"فشل آخر تحديث: {self.last_error}")
            return True
        if not self.last_result:
            st.caption("لم يُحدَّث بعد")
            return True
        return False

    def _render_updated_caption(self, settings: dict) -> None:
        """تذييل "آخر تحديث" — مشترك بين كل الأنواع."""
        if self.last_updated_at:
            st.caption(f"آخر تحديث: {format_local_dt(self.last_updated_at, settings)}")

    # ──────────────────────────────────────────────────────────
    #  محرر الخلية — مشترك بالكامل، الحقول الخاصة عبر hook
    # ──────────────────────────────────────────────────────────

    def render_type_specific_fields(self, dashboard_id: str) -> dict:
        """
        Hook قابل للتخصيص: افتراضياً لا حقول إضافية (Table/Gauge/Kpi/
        Story لا تحتاج شيئاً هنا). ChartCell فقط يُعيد تعريفها لعرض
        اختيار نوع الرسم، وتُرجع dict بالقيم المُختارة — تُطبَّق على
        الكائن تلقائياً (setattr) عند الحفظ في render_editor أدناه.
        """
        return {}

    def render_editor(self, db, dm, settings: dict, dashboard_id: str, is_gauge_row: bool, edit_key: str) -> None:
        """
        نقطة الدخول لمحرر الخلية: تعرض اختيار/تغيير نوع الخلية أولاً
        (معطَّل لصف الـ Gauges — نوعها ثابت "gauge" دائماً)، ثم تُفوّض
        بقية المحرر (عنوان/سؤال/حقول خاصة/اختبار/حفظ) إلى
        _render_editor_body — إما على self مباشرة لو لم يتغيّر النوع،
        أو على كائن مؤقت من الكلاس الجديد لو غيّر المستخدم النوع
        (يحمل نفس العنوان/السؤال الحاليين، ويُحفظ بالنوع الجديد فعلياً
        عند الضغط على "حفظ"). هذا يسمح بتغيير نوع خلية موجودة مسبقاً
        تماماً كما يسمح باختيار نوع خلية جديدة.
        """
        label = "➕ إضافة مقياس (Gauge)" if is_gauge_row else "➕ إضافة عنصر"
        st.markdown(f"**{label}**")

        if is_gauge_row:
            chosen_type = "gauge"
        else:
            from core.dashboard_cells import CELL_CLASSES, DISPLAY_TYPE_LABELS

            type_options = list(CELL_CLASSES.keys())
            cur_type = self.display_type if self.display_type in type_options else "table"
            chosen_type = st.selectbox(
                "نوع العرض", type_options,
                index=type_options.index(cur_type),
                format_func=lambda t: DISPLAY_TYPE_LABELS[t],
                key=f"dtype_{dashboard_id}_{self.position}",
            )

        if chosen_type != self.display_type:
            from core.dashboard_cells import CELL_CLASSES
            active_cell = CELL_CLASSES[chosen_type](
                position=self.position, title=self.title, question=self.question,
            )
        else:
            active_cell = self

        active_cell._render_editor_body(db, dm, settings, dashboard_id, edit_key)

    def _render_editor_body(self, db, dm, settings: dict, dashboard_id: str, edit_key: str) -> None:
        """
        الجزء المشترك من المحرر (عنوان، سؤال، حقول خاصة بالنوع، اختبار،
        حفظ/إلغاء) — يُستدعى من render_editor بعد تحديد النوع النهائي
        (سواء بقي كما هو أو تغيّر للتو).
        """
        title = st.text_input(
            "عنوان الخلية (اختياري)", value=self.title or "",
            key=f"title_{dashboard_id}_{self.position}",
        )
        question = st.text_area(
            "السؤال بلغة طبيعية", value=self.question or "",
            key=f"question_{dashboard_id}_{self.position}", height=80,
        )

        extra_fields = self.render_type_specific_fields(dashboard_id)

        has_popover = hasattr(st, "popover")
        test_menu_ctx = (
            st.popover("⁝ خيارات الاختبار") if has_popover
            else st.expander("⁝ خيارات الاختبار", expanded=False)
        )
        test_key = f"cell_test_result_{dashboard_id}_{self.position}"
        with test_menu_ctx:
            if st.button("🔍 اختبار", key=f"test_cell_{dashboard_id}_{self.position}", width='stretch'):
                if not question.strip():
                    notify("الرجاء كتابة سؤال أولاً", kind="warning")
                else:
                    filters = dm._build_active_filters(dashboard_id)
                    with st.spinner("⏳ جاري الاختبار..."):
                        r = self._run_test(question.strip(), db, settings, filters)
                    st.session_state[test_key] = r

        if st.session_state.get(test_key):
            self._render_test_result(st.session_state[test_key], settings)

        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("💾 حفظ", key=f"save_cell_{dashboard_id}_{self.position}", width='stretch', type="primary"):
                if not question.strip():
                    notify("الرجاء كتابة سؤال", kind="warning")
                else:
                    self.title = title.strip() or None
                    self.question = question.strip()
                    for field_name, value in extra_fields.items():
                        setattr(self, field_name, value)

                    self.save(db, dashboard_id)
                    st.session_state.pop(edit_key, None)
                    st.session_state.pop(test_key, None)

                    with st.spinner("⏳ جاري تحديث الخلية..."):
                        r = dm.refresh_single_cell(dashboard_id, self.position, ai_rules=settings.get("ai_rules"))
                    if r["ok"]:
                        notify("تم الحفظ والتحديث" + (" (عبر AI)" if r["used_ai"] else ""), kind="success")
                    else:
                        notify(f"تم الحفظ لكن فشل التحديث: {r.get('error')}", kind="warning")
                    st.rerun()
        with bc2:
            if self.question and st.button("إلغاء", key=f"cancel_cell_{dashboard_id}_{self.position}", width='stretch'):
                st.session_state.pop(edit_key, None)
                st.session_state.pop(test_key, None)
                st.rerun()

    def _run_test(self, question: str, db, settings: dict, filters: list) -> dict:
        """تشغيل "اختبار" على السؤال الحالي بدون حفظ — نفس منطق ai.ask/tell_story."""
        ai, _ = build_ai_manager(db)
        if self.display_type == "story":
            return ai.tell_story(question, ai_rules=settings.get("ai_rules"), filters=filters)
        return ai.ask(question, result_type=self.display_type, ai_rules=settings.get("ai_rules"), filters=filters)

    def _render_test_result(self, r: dict, settings: dict) -> None:
        """عرض نتيجة زر "اختبار" — مشترك بين كل الأنواع."""
        if r.get("ok"):
            if r.get("sql"):
                with st.expander("SQL", expanded=False):
                    st.code(r["sql"], language="sql")
            if r.get("df") is not None:
                render_themed_table(r["df"], settings)
            if r.get("story"):
                text_color = get_theme_colors(settings)["text"]
                st.markdown(
                    f'<div dir="rtl" style="text-align:right; color:{text_color};">{r["story"]}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.error(f"فشل الاختبار: {r.get('error')}")

    # ──────────────────────────────────────────────────────────
    #  قائمة الإجراءات — موحّدة 100%، لا تُعاد كتابتها في أي subclass
    # ──────────────────────────────────────────────────────────

    def render_actions_menu(self, db, dm, settings: dict, dashboard_id: str, edit_key: str) -> None:
        """تحديث الخلية / تعديل السؤال / إفراغ الخلية."""
        has_popover = hasattr(st, "popover")
        menu_ctx = (
            st.popover("⁝", use_container_width=True) if has_popover
            else st.expander("⁝", expanded=False)
        )
        with menu_ctx:
            if st.button("🔄 تحديث هذه الخلية", key=f"refresh_one_{dashboard_id}_{self.position}", width='stretch'):
                with st.spinner("⏳ جاري التحديث..."):
                    r = dm.refresh_single_cell(dashboard_id, self.position, ai_rules=settings.get("ai_rules"))
                if r["ok"]:
                    notify("تم التحديث" + (" (عبر AI)" if r["used_ai"] else ""), kind="success")
                else:
                    notify(r.get("error", "فشل التحديث"), kind="error")
                st.rerun()

            if st.button("✏️ تعديل السؤال", key=f"edit_{dashboard_id}_{self.position}", width='stretch'):
                st.session_state[edit_key] = True
                st.rerun()

            if st.button("🗑️ إفراغ الخلية", key=f"clear_{dashboard_id}_{self.position}", width='stretch'):
                self.clear(db, dashboard_id)
                st.rerun()

    # ──────────────────────────────────────────────────────────
    #  الحفظ / الإفراغ — تتعامل مع ProjectDB كما هي الآن
    # ──────────────────────────────────────────────────────────

    def save(self, db, dashboard_id: str) -> None:
        """
        استدعاء db.save_dashboard_cell() بنفس التوقيع الحالي —
        display_type يُؤخَذ من الخاصية الصنفية بدل تمريره من الخارج.
        """
        db.save_dashboard_cell(
            dashboard_id, self.position, self.display_type,
            self.title, self.question, self.chart_type,
        )

    def clear(self, db, dashboard_id: str) -> None:
        """استدعاء db.clear_dashboard_cell() مباشرة."""
        db.clear_dashboard_cell(dashboard_id, self.position)
