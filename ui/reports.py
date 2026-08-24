"""
ui/reports.py
=============
إنشاء وإدارة التقارير: عنوان، فقرات Markdown، عرض البلوكات
(جداول/رسوم/gauges/KPI)، وتصدير PDF / Excel / Markdown.
"""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

from ui.common import (
    apply_rtl, apply_theme_css, require_login, require_project, sidebar_header,
    temp_export_dir, offer_download,
)
from exporters.report_manager import ReportManager
from exporters.pdf_exporter import PDFExporter
from exporters.excel_exporter import ExcelExporter
from exporters.markdown_exporter import MarkdownExporter


def show_reports():
    apply_rtl()
    require_login()
    db = require_project()
    apply_theme_css(db.get_settings().get("theme", "ocean_dark"))
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
            _render_block(block)
            if st.button("🗑️ حذف هذا البلوك", key=f"del_block_{block['id']}"):
                rm.delete_block(report_id, block["id"])
                st.rerun()

    st.divider()
    st.subheader("⬇️ تصدير التقرير")
    c1, c2, c3 = st.columns(3)

    with c1:
        if st.button("📄 تصدير PDF", width='stretch'):
            _export_and_offer(PDFExporter(rm), report_id, "pdf", chosen_title)
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


def _render_block(block: dict):
    btype = block["block_type"]
    content = block["content"]

    if btype == "paragraph":
        st.markdown(content.get("text", ""))

    elif btype == "table":
        st.dataframe(content.get("data", []), width='stretch', hide_index=True)

    elif btype == "chart":
        data = content.get("data", [])
        x_col = content.get("x_col")
        y_cols = content.get("y_cols", [])
        if data and x_col and y_cols:
            import pandas as pd
            df = pd.DataFrame(data)
            fig = px.bar(df, x=x_col, y=y_cols, barmode="group", text_auto=True,
                         title=content.get("title", ""))
            st.plotly_chart(fig, width='stretch')

    elif btype == "gauge":
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=content.get("current_value", 0),
            title={"text": content.get("label", "")},
            gauge={"axis": {"range": [content.get("min_value", 0), content.get("max_value", 100)]}},
        ))
        st.plotly_chart(fig, width='stretch')

    elif btype == "kpi":
        actual = content.get("actual_value", 0)
        target = content.get("target_value", 0)
        st.metric(content.get("label", "KPI"), actual, delta=round(actual - target, 2))
        st.caption(f"الهدف: {target} {content.get('unit', '')}")
