"""
ui/reports.py
=============
إنشاء وإدارة التقارير: عنوان، فقرات Markdown، عرض البلوكات
(جداول/رسوم/gauges/KPI)، وتصدير PDF / Excel / Markdown.

🆕 بلوك الجدول يُعرض الآن عبر ui.common.render_themed_table (جدول
HTML مُنسَّق يدوياً بألوان الثيم) بدل st.dataframe التفاعلي — الأخير
يُرسم على <canvas> ولا يلتزم بشكل موثوق بألوان الثيم الحالي.

🆕 بلوك الرسم البياني (chart):
--------------------------------
بناء الرسم استُبدل من px.bar اليدوي المباشر إلى استدعاء
core.dashboard_cells.cells._build_chart_figure/_apply_chart_layout_tweaks
— نفس الدالتين المستخدمتين في خلايا لوحات المعلومات وصفحة المحادثة،
حتى لا يتكرر نفس منطق بناء الرسم (ومشاكله السابقة: legend بعنوان
"variable" وقيمة "y" عند عمود قيمة واحد، وعنوان محور رأسي "value" غير
ضروري) في أكثر من ملف. البلوك المحفوظ هنا قد يحتوي أكثر من عمود قيمة
(chart_type مخزَّن مسبقاً من ai/ai_manager عبر ReportManager.add_chart)،
والدالة المشتركة تتعامل مع كل الحالات (عمود واحد أو أكثر) تلقائياً.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.common import (
    apply_rtl, apply_theme_css, require_login, require_project, sidebar_header,
    temp_export_dir, offer_download, apply_plotly_theme, render_themed_table,
    get_theme_colors,
)
from core.dashboard_cells.cells import _build_chart_figure, _apply_chart_layout_tweaks
from exporters.report_manager import ReportManager
from exporters.pdf_exporter import PDFExporter
from exporters.excel_exporter import ExcelExporter
from exporters.markdown_exporter import MarkdownExporter

def show_reports():
    apply_rtl()
    require_login()
    db = require_project()
    settings = db.get_settings()
    apply_theme_css(settings)
    sidebar_header()

    rm = ReportManager(db)

    st.title("📝 التقارير")

    with st.expander("➕ إنشاء تقرير جديد"):
        with st.form("new_report"):
            title = st.text_input("عنوان التقرير")
            if st.form_submit_button("إنشاء"):
                r = rm.create(title)
                if r["ok"]:
                    st.success("تم إنشاء التقرير")
                    st.session_state.current_report_id = r["report_id"]
                    st.rerun()
                else:
                    st.error(r["error"])

    reports = rm.list_reports()
    if not reports:
        st.info("لا توجد تقارير بعد.")
        return

    titles = {r["title"]: r["id"] for r in reports}
    chosen_title = st.selectbox("اختر تقريراً للتعديل", list(titles.keys()))
    report_id = titles[chosen_title]

    c1, c2, c3 = st.columns(3)
    with c1:
        new_title = st.text_input("إعادة تسمية", value="")
        if new_title and st.button("حفظ العنوان"):
            rm.rename(report_id, new_title)
            st.rerun()
    with c2:
        if st.button("🗑️ حذف التقرير"):
            rm.delete(report_id)
            st.rerun()
    with c3:
        st.write("")

    st.divider()

    with st.expander("➕ إضافة فقرة نصية (Markdown)"):
        text = st.text_area("النص")
        if st.button("إضافة الفقرة"):
            r = rm.add_paragraph(report_id, text)
            if r["ok"]:
                st.rerun()
            else:
                st.error(r["error"])

    st.subheader("محتوى التقرير")
    blocks = rm.get_blocks(report_id)
    if not blocks:
        st.caption("لا يوجد محتوى بعد. أضف نتائج من صفحة المحادثة أو فقرات هنا.")

    for block in blocks:
        with st.container(border=True):
            _render_block(block, settings)
            if st.button("🗑️ حذف هذا البلوك", key=f"del_block_{block['id']}"):
                rm.delete_block(report_id, block["id"])
                st.rerun()

    st.divider()
    st.subheader("⬇️ تصدير التقرير")
    c1, c2, c3 = st.columns(3)

    with c1:
        if st.button("📄 تصدير PDF", width='stretch'):
            
            pdf_exporter = PDFExporter(
                rm,
                theme_colors=get_theme_colors(settings),
                project_name=settings.get("project_name", ""),
            )
            _export_and_offer(pdf_exporter, report_id, "pdf", chosen_title)
    with c2:
        if st.button("📊 تصدير Excel", width='stretch'):
            _export_and_offer(ExcelExporter(rm), report_id, "xlsx", chosen_title)
    with c3:
        if st.button("📝 تصدير Markdown", width='stretch'):
            _export_and_offer(MarkdownExporter(rm), report_id, "md", chosen_title)


def _export_and_offer(exporter, report_id, ext, title):
    with temp_export_dir() as out_dir:
        out_path = out_dir / f"{title}.{ext}"
        r = exporter.export(report_id, out_path)
        if r["ok"]:
            offer_download(
                out_path, f"⬇️ تحميل الملف (.{ext})", f"{title}.{ext}",
                key=f"dl_export_{ext}",
            )
        else:
            st.error(r["error"])


def _render_block(block: dict, settings: dict = None):
    btype = block["block_type"]
    content = block["content"]

    if btype == "paragraph":
        st.markdown(content.get("text", ""))

    elif btype == "table":
        data = content.get("data", [])
        if data:
            render_themed_table(pd.DataFrame(data), settings or "ocean_dark")
        else:
            st.caption("لا توجد بيانات")

    elif btype == "chart":
        data = content.get("data", [])
        x_col = content.get("x_col")
        y_cols = content.get("y_cols", [])
        if data and x_col and y_cols:
            df = pd.DataFrame(data)
            ctype = content.get("chart_type", "bar")
            # 🆕 بناء الرسم عبر الدالة المشتركة — تمرر اسم العمود مباشرة
            # (وليس كقائمة من عنصر واحد) عند وجود عمود قيمة واحد فقط،
            # فيختفي legend الزائد بعنوان "variable" وقيمة "y" تلقائياً.
            fig = _build_chart_figure(df, x_col, y_cols, ctype)
            fig.update_layout(
                margin=dict(l=10, r=10, t=10, b=10),
                title=content.get("title", ""),
            )
            # legend أعلى الرسم أفقياً (عند وجود عمودي قيمة أو أكثر)
            # وإخفاء عنوان المحور الرأسي لتوفير مساحة العرض.
            _apply_chart_layout_tweaks(fig, ctype)
            apply_plotly_theme(fig, settings)
            st.plotly_chart(fig, width='stretch')

    elif btype == "gauge":
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=content.get("current_value", 0),
            title={"text": content.get("label", "")},
            gauge={"axis": {"range": [content.get("min_value", 0), content.get("max_value", 100)]}},
        ))
        apply_plotly_theme(fig, settings)
        st.plotly_chart(fig, width='stretch')

    elif btype == "kpi":
        actual = content.get("actual_value", 0)
        target = content.get("target_value", 0)
        st.metric(content.get("label", "KPI"), actual, delta=round(actual - target, 2))
        st.caption(f"الهدف: {target} {content.get('unit', '')}")
    elif btype == "dashboard":
        _render_dashboard_snapshot_block(content, settings)


def _render_dashboard_snapshot_block(content: dict, settings: dict = None):
    title = content.get("title", "")
    if title:
        st.markdown(f"#### 📊 {title}")

    gauges = content.get("gauges", [])
    if gauges:
        cols = st.columns(len(gauges))
        for col, g in zip(cols, gauges):
            with col:
                _render_snapshot_cell(g, settings)

    columns = content.get("columns", [])
    if columns:
        col_widgets = st.columns(len(columns))
        for col_widget, col_cells in zip(col_widgets, columns):
            with col_widget:
                for cell in col_cells:
                    _render_snapshot_cell(cell, settings)
                    st.markdown("")


def _render_snapshot_cell(cell: dict, settings: dict = None):
    ctype = cell.get("type")
    data = cell.get("content", {}) or {}
    title = cell.get("title")

    with st.container(border=True):
        if title:
            st.markdown(f"**{title}**")

        if ctype == "table":
            render_themed_table(pd.DataFrame(data.get("rows", [])), settings or "ocean_dark")

        elif ctype == "chart":
            cols_list = data.get("columns", [])
            df = pd.DataFrame(data.get("rows", []))
            if df.empty or len(cols_list) < 2:
                st.caption("لا توجد بيانات كافية للرسم")
            else:
                x_col, y_cols = cols_list[0], cols_list[1:3]
                ctype_chart = data.get("chart_type", "bar")
                fig = _build_chart_figure(df, x_col, y_cols, ctype_chart)
                fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=260)
                _apply_chart_layout_tweaks(fig, ctype_chart)
                apply_plotly_theme(fig, settings)
                st.plotly_chart(fig, width='stretch')

        elif ctype == "gauge":
            row = (data.get("rows") or [{}])[0]
            fig = go.Figure(go.Indicator(
                mode="gauge+number", value=row.get("current_value", 0),
                gauge={"axis": {"range": [row.get("min_value", 0), row.get("max_value", 100)]}},
            ))
            fig.update_layout(height=180, margin=dict(l=10, r=10, t=10, b=10))
            apply_plotly_theme(fig, settings)
            st.plotly_chart(fig, width='stretch')

        elif ctype == "kpi":
            row = (data.get("rows") or [{}])[0]
            actual, target = row.get("actual_value", 0), row.get("target_value", 0)
            delta = actual - target if isinstance(actual, (int, float)) and isinstance(target, (int, float)) else None
            st.metric("القيمة", actual, delta=round(delta, 2) if delta is not None else None)
            st.caption(f"الهدف: {target}")

        elif ctype == "story":
            st.markdown(data.get("story", ""))
