"""
core/dashboard_cells/cells.py
================================
كل أنواع خلايا لوحة المعلومات المعروفة، في ملف واحد — كل كلاس منها
صغير (لا يتجاوز بضع عشرات من الأسطر) لأنه لا يحمل إلا ما يخصّه فعلياً
(تحويل التخزين + عرض النتيجة + حقول خاصة عند الحاجة)؛ كل المنطق
المشترك (التنفيذ الافتراضي، محرر الخلية، قائمة الإجراءات، الحفظ/
الإفراغ...) يبقى في core/dashboard_cells/base.py.

المحتويات:
    EmptyCell  — خلية بلا "question" بعد (لا ترث DashboardCellBase).
    TableCell  — جدول عادي.
    ChartCell  — رسم بياني (bar/line/pie/area/scatter عبر chart_type).
    GaugeCell  — مقياس (Gauge).
    KpiCell    — بطاقة مؤشر (KPI).
    StoryCell  — تحليل نصي (Story Telling) — الوحيدة التي تُعيد تعريف
                 execute() بالكامل (تستدعي AI دائماً).
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ui.common import render_themed_table, apply_plotly_theme, get_chart_theme, get_theme_colors
from core.dashboard_cells.base import DashboardCellBase
from config import CHART_TYPES


# ══════════════════════════════════════════════════════════════
#  EmptyCell — خلية بلا question بعد، لا ترث DashboardCellBase
# ══════════════════════════════════════════════════════════════

class EmptyCell:
    """
    ليست subclass من DashboardCellBase عمداً — لا معنى لـ execute/
    to_stored_dict/render_result قبل أن يُختار نوع الخلية أصلاً. دورها
    الوحيد: تفويض المحرر بالكامل إلى كائن مؤقت من نوع افتراضي (gauge
    لصف المؤشرات الثابت، وإلا table) — DashboardCellBase.render_editor
    نفسها هي من تعرض اختيار/تغيير النوع فعلياً (نفس المنطق المستخدم
    لاحقاً لتغيير نوع خلية محفوظة مسبقاً)، فلا داعي لتكرار تلك الواجهة هنا.
    """

    display_type = None
    label = "عنصر جديد"

    def __init__(self, position: int):
        self.position = position

    def render_editor(self, db, dm, settings: dict, dashboard_id: str, is_gauge_row: bool, edit_key: str) -> None:
        # الاستيراد هنا (وليس أعلى الملف) لتفادي أي احتمال دورة استيراد
        # مع core/dashboard_cells/__init__.py الذي يستورد هذا الملف نفسه.
        from core.dashboard_cells import CELL_CLASSES

        default_type = "gauge" if is_gauge_row else "table"
        temp_cell = CELL_CLASSES[default_type](position=self.position)
        temp_cell.render_editor(db, dm, settings, dashboard_id, is_gauge_row, edit_key)


# ══════════════════════════════════════════════════════════════
#  TableCell — جدول عادي
# ══════════════════════════════════════════════════════════════

class TableCell(DashboardCellBase):
    """
    خلية "جدول" — أبسط الأنواع: SQL عادي (base_sql أو AI عبر السلوك
    الافتراضي في DashboardCellBase.execute)، يُعرض عبر st.dataframe
    التفاعلي (فرز الأعمدة، تغيير الحجم، تكبير كامل الشاشة، تنزيل CSV)
    بدل الجدول الثابت render_themed_table — الأخير أُبقي مستخدَماً في
    أماكن أخرى (نتيجة زر "اختبار"، بيانات Story Telling) لكن ليس هنا،
    لأن خلية الجدول تحديداً هي المكان الذي يحتاج فيه المستخدم فعلياً
    الفرز والتصفح التفاعلي على بيانات قد تكون طويلة. ألوان الثيم لا
    تزال تُطبَّق عليه عبر متغيرات --gdg-* في ui.common.apply_theme_css
    (تُستدعى دائماً في بداية الصفحة).
    """

    display_type = "table"
    label = "جدول"

    def to_stored_dict(self, raw_result: dict) -> dict:
        return self._base_stored_dict(raw_result)

    def render_result(self, settings: dict, dashboard_id: str) -> None:
        if self._render_error_or_empty():
            return
        df = pd.DataFrame(self.last_result.get("rows", []))
        st.dataframe(df, width='stretch', hide_index=True)
        self._render_updated_caption(settings)


# ══════════════════════════════════════════════════════════════
#  ChartCell — رسم بياني (كل الأنواع الفرعية عبر chart_type)
# ══════════════════════════════════════════════════════════════

class ChartCell(DashboardCellBase):
    """
    خلية "رسم بياني" — تغطي كل الأنواع الفرعية (bar/line/pie/area/
    scatter) عبر خاصية chart_type داخل نفس الكلاس، بدل subclass منفصل
    لكل نوع رسم (التفريق الجوهري هو بين "نتيجة تُعرض كرسم" وليس بين
    أنواع الرسم نفسها).
    """

    display_type = "chart"
    label = "رسم بياني"

    def to_stored_dict(self, raw_result: dict) -> dict:
        stored = self._base_stored_dict(raw_result)
        stored["chart_type"] = self.chart_type or "bar"
        return stored

    def render_type_specific_fields(self, dashboard_id: str) -> dict:
        ctype_options = list(CHART_TYPES.keys())
        cur_ctype = self.chart_type or "bar"
        chart_type = st.selectbox(
            "نوع الرسم", ctype_options,
            index=ctype_options.index(cur_ctype) if cur_ctype in ctype_options else 0,
            format_func=lambda t: CHART_TYPES[t],
            key=f"ctype_{dashboard_id}_{self.position}",
        )
        return {"chart_type": chart_type}

    def render_result(self, settings: dict, dashboard_id: str) -> None:
        if self._render_error_or_empty():
            return

        df = pd.DataFrame(self.last_result.get("rows", []))
        if df.empty or df.shape[1] < 2:
            st.caption("لا توجد بيانات كافية للرسم")
            self._render_updated_caption(settings)
            return

        ctype = self.last_result.get("chart_type", "bar")
        x_col = df.columns[0]
        y_cols = list(df.columns[1:3])
        try:
            if ctype == "line":
                fig = px.line(df, x=x_col, y=y_cols, markers=True)
            elif ctype == "pie":
                fig = px.pie(df, names=x_col, values=y_cols[0])
            elif ctype == "area":
                fig = px.area(df, x=x_col, y=y_cols)
            elif ctype == "scatter":
                fig = px.scatter(df, x=x_col, y=y_cols[0])
            else:
                fig = px.bar(df, x=x_col, y=y_cols, barmode="group", text_auto=True)
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=280)
            apply_plotly_theme(fig, settings)
            st.plotly_chart(fig, width='stretch', key=f"chart_{dashboard_id}_{self.position}")
        except Exception as e:
            st.error(f"تعذر رسم البيانات: {e}")

        self._render_updated_caption(settings)


# ══════════════════════════════════════════════════════════════
#  GaugeCell — مقياس (Gauge)
# ══════════════════════════════════════════════════════════════

class GaugeCell(DashboardCellBase):
    """
    خلية "مقياس" (Gauge) — تُعرض عبر go.Indicator، وشريط التقدم يأخذ
    لون التمييز (accent) الخاص بالثيم فعلياً عبر ui.common.apply_plotly_theme.
    """

    display_type = "gauge"
    label = "مقياس (Gauge)"

    def to_stored_dict(self, raw_result: dict) -> dict:
        return self._base_stored_dict(raw_result)

    def render_result(self, settings: dict, dashboard_id: str) -> None:
        if self._render_error_or_empty():
            return

        df = pd.DataFrame(self.last_result.get("rows", []))
        row = df.iloc[0].to_dict() if not df.empty else {}
        current = row.get("current_value", 0)
        mn = row.get("min_value", 0)
        mx = row.get("max_value", 100)

        chart_theme = get_chart_theme(settings)
        fig = go.Figure(go.Indicator(
            mode="gauge+number", value=current,
            number={"font": {"color": chart_theme["font_color"]}},
            gauge={
                "axis": {"range": [mn, mx], "tickfont": {"color": chart_theme["font_color"]}},
            },
        ))
        fig.update_layout(height=180, margin=dict(l=10, r=10, t=10, b=10))
        apply_plotly_theme(fig, settings)
        st.plotly_chart(fig, width='stretch', key=f"gauge_{dashboard_id}_{self.position}")

        self._render_updated_caption(settings)


# ══════════════════════════════════════════════════════════════
#  KpiCell — بطاقة مؤشر (KPI)
# ══════════════════════════════════════════════════════════════

class KpiCell(DashboardCellBase):
    """خلية "بطاقة مؤشر" (KPI) — تُعرض عبر st.metric."""

    display_type = "kpi"
    label = "بطاقة مؤشر (KPI)"

    def to_stored_dict(self, raw_result: dict) -> dict:
        return self._base_stored_dict(raw_result)

    def render_result(self, settings: dict, dashboard_id: str) -> None:
        if self._render_error_or_empty():
            return

        df = pd.DataFrame(self.last_result.get("rows", []))
        row = df.iloc[0].to_dict() if not df.empty else {}
        actual = row.get("actual_value", 0)
        target = row.get("target_value", 0)
        delta = (
            actual - target
            if isinstance(actual, (int, float)) and isinstance(target, (int, float))
            else None
        )
        st.metric("القيمة", actual, delta=round(delta, 2) if delta is not None else None)
        st.caption(f"الهدف: {target}")

        self._render_updated_caption(settings)


# ══════════════════════════════════════════════════════════════
#  StoryCell — تحليل نصي (Story Telling)
# ══════════════════════════════════════════════════════════════

class StoryCell(DashboardCellBase):
    """
    خلية "تحليل نصي" (Story Telling) — الاستثناء الوحيد الذي يُعيد
    تعريف execute() بالكامل: تستدعي AI دائماً (سرد نصي جديد يُبنى
    فعلياً على البيانات الحالية بعد الفلترة، بخلاف بقية الأنواع التي
    تعتمد على base_sql المحفوظ عند توفره).
    """

    display_type = "story"
    label = "تحليل نصي (Story Telling)"

    def execute(self, ai_manager, query_engine, filters: list, ai_rules) -> dict:
        r = ai_manager.tell_story(self.question, ai_rules=ai_rules, filters=filters)
        r["used_ai"] = True
        return r

    def to_stored_dict(self, raw_result: dict) -> dict:
        stored = self._base_stored_dict(raw_result)
        stored["story"] = raw_result.get("story", "")
        return stored

    def render_result(self, settings: dict, dashboard_id: str) -> None:
        if self._render_error_or_empty():
            return

        story_text = self.last_result.get("story", "")
        text_color = get_theme_colors(settings)["text"]
        st.markdown(
            f'<div dir="rtl" style="text-align:right; color:{text_color};">{story_text}</div>',
            unsafe_allow_html=True,
        )
        df = pd.DataFrame(self.last_result.get("rows", []))
        with st.expander("📊 البيانات المستخدمة"):
            render_themed_table(df, settings)

        self._render_updated_caption(settings)
