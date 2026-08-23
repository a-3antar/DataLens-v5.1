"""
ui/dashboards.py
==================
صفحة لوحات المعلومات: معرض اللوحات، إنشاء لوحة من أحد ٦ قوالب،
بناء الخلايا (سؤال طبيعي + نوع عرض)، شريط Slicers قابل للطي على
اليسار، وتحديث كل البيانات بضغطة زر واحدة فقط — لا تحديث تلقائي
أو فوري لأي خلية أو Slicer.

تحديث الخلايا:
----------------
- تحديث اللوحة كاملة أو خلية واحدة يستخدم AI فقط عند الحاجة الفعلية
  (أول توليد لسؤال جديد، أو خلايا Story Telling). التحديثات الأخرى
  تُعاد فقط بتطبيق الفلاتر على SQL محفوظ مسبقاً — أسرع وبدون تكلفة AI.
- كل خلية تعرض badge صغير يوضح للمستخدم هل التحديث القادم سيكون
  "⚡ سريع" أو يحتاج "🤖 AI".

واجهة الخلية:
---------------
أزرار "تحديث/تعديل/إفراغ" مخفية داخل قائمة مطوية (⚙️) بدل ظهورها
كثلاثة أزرار دائمة تحت كل خلية — يقلل الازدحام البصري خصوصاً في
القوالب ذات الخلايا الكثيرة (مثل قالب E بـ ٦ خلايا + ٤ Gauges).
"""

import uuid

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ui.common import apply_rtl, require_login, require_project, sidebar_header
from core.dashboard_templates import DASHBOARD_TEMPLATES, get_template
from core.dashboard_manager import DashboardManager
from ai.ai_manager import AIManager, get_engine
from config import (
    DASHBOARD_SLICER_COUNT, DASHBOARD_GAUGE_COUNT,
    DASHBOARD_SLICER_VALUES_LIMIT, CHART_TYPES,
)

DISPLAY_TYPE_LABELS = {
    "table": "جدول", "chart": "رسم بياني", "gauge": "مقياس (Gauge)",
    "kpi": "بطاقة مؤشر (KPI)", "story": "تحليل نصي (Story Telling)",
}
CHART_TYPE_LABELS = CHART_TYPES

# دعم st.popover ظهر في إصدارات Streamlit الحديثة (>=1.32). بما أن
# requirements.txt يثبّت streamlit>=1.40 فهو متاح دائماً هنا، لكن
# نتحقق دفاعياً لتفادي أي كسر لو شُغِّل التطبيق ببيئة أقدم بالخطأ.
_HAS_POPOVER = hasattr(st, "popover")


def show_dashboards():
    apply_rtl()
    require_login()
    db = require_project()
    sidebar_header()

    if st.session_state.get("current_dashboard_id"):
        _show_dashboard_detail(db)
    else:
        _show_dashboard_gallery(db)


# ══════════════════════════════════════════════════════════════
#  معرض اللوحات
# ══════════════════════════════════════════════════════════════

def _show_dashboard_gallery(db):
    st.title("📊 لوحات المعلومات")

    with st.expander("➕ إنشاء لوحة جديدة", expanded=not db.get_dashboards()):
        title = st.text_input("عنوان اللوحة")
        st.markdown("**اختر قالباً:**")
        for key, tmpl in DASHBOARD_TEMPLATES.items():
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(f"**{key} — {tmpl['name']}**")
                st.caption(tmpl["description"])
            with c2:
                if st.button("اختيار", key=f"pick_tmpl_{key}", width='stretch'):
                    if not title.strip():
                        st.error("الرجاء إدخال عنوان اللوحة أولاً")
                    else:
                        dash_id = str(uuid.uuid4())
                        db.create_dashboard(dash_id, title.strip(), key)
                        st.session_state.current_dashboard_id = dash_id
                        st.rerun()
            st.divider()

    dashboards = db.get_dashboards()
    if not dashboards:
        st.info("لا توجد لوحات بعد. أنشئ لوحتك الأولى أعلاه.")
        return

    st.subheader("لوحاتك")
    for d in dashboards:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            tmpl = get_template(d["template_id"])
            with c1:
                st.markdown(f"**{d['title']}**")
                st.caption(f"القالب: {tmpl['name']} | آخر تحديث: {d.get('updated_at') or 'لم يُحدَّث بعد'}")
            with c2:
                if st.button("📂 فتح", key=f"open_dash_{d['id']}", width='stretch'):
                    st.session_state.current_dashboard_id = d["id"]
                    st.rerun()
            with c3:
                if st.button("📑 تكرار", key=f"dup_dash_{d['id']}", width='stretch'):
                    new_id = str(uuid.uuid4())
                    db.duplicate_dashboard(d["id"], new_id, f"{d['title']} (نسخة)")
                    st.rerun()
            with c4:
                confirm_key = f"confirm_del_dash_{d['id']}"
                if st.session_state.get(confirm_key):
                    if st.button("⚠️ تأكيد الحذف", key=f"confirm_btn_{d['id']}", width='stretch'):
                        db.delete_dashboard(d["id"])
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                else:
                    if st.button("🗑️ حذف", key=f"del_dash_{d['id']}", width='stretch'):
                        st.session_state[confirm_key] = True
                        st.rerun()


# ══════════════════════════════════════════════════════════════
#  عرض تفصيلي للوحة
# ══════════════════════════════════════════════════════════════

def _build_ai_manager(db):
    settings = db.get_settings()
    engine_name = settings.get("ai_engine", "gemini")
    engine = get_engine(
        engine_name=engine_name,
        api_key=settings.get(f"api_key_{engine_name}", ""),
        model=settings.get("model", ""),
        timeout=settings.get("timeout", 30),
        ollama_url=settings.get("ollama_url", "http://localhost:11434"),
    )
    ai = AIManager(
        db, engine,
        temperature=settings.get("temperature", 0.1),
        max_tries=settings.get("max_tries", 3),
        retry_delay=settings.get("retry_delay", 10),
    )
    return ai, settings


def _show_dashboard_detail(db):
    dashboard_id = st.session_state.current_dashboard_id
    dashboard = db.get_dashboard(dashboard_id)
    if not dashboard:
        st.session_state.current_dashboard_id = None
        st.rerun()
        return

    ai, settings = _build_ai_manager(db)
    dm = DashboardManager(db, ai)

    # ── رأس الصفحة ──
    c1, c2, c3 = st.columns([5, 1.2, 1.5])
    with c1:
        st.title(f"📊 {dashboard['title']}")
        st.caption(f"آخر تحديث: {dashboard.get('updated_at') or 'لم يُحدَّث بعد'}")
    with c2:
        if st.button("↩️ اللوحات", width='stretch'):
            st.session_state.current_dashboard_id = None
            st.rerun()
    with c3:
        if st.button("🔄 تحديث البيانات", type="primary", width='stretch'):
            with st.spinner(
                "جاري تحديث كل خلايا اللوحة... قد يستغرق هذا بعض الوقت "
                "(بما فيها انتظار إعادة المحاولة عند فشل الاتصال بمحرك AI)"
            ):
                result = dm.refresh_dashboard(dashboard_id, ai_rules=settings.get("ai_rules"))
            if not result["ok"]:
                st.error(result.get("error", "فشل التحديث"))
            elif result["errors"] == 0:
                st.success(
                    f"✅ تم تحديث {result['total']} خلية بنجاح "
                    f"(⚡ {result['fast_updates']} سريع بدون AI، 🤖 {result['ai_calls']} عبر AI)"
                )
            else:
                st.warning(
                    f"⚠️ تم التحديث: {result['total'] - result['errors']} نجحت، "
                    f"{result['errors']} فشلت "
                    f"(⚡ {result['fast_updates']} سريع، 🤖 {result['ai_calls']} عبر AI)"
                )
            st.rerun()

    st.divider()

    template = get_template(dashboard["template_id"])
    cells = {c["position"]: c for c in db.get_dashboard_cells(dashboard_id)}
    slicers = {s["position"]: s for s in db.get_dashboard_slicers(dashboard_id)}

    # محتوى اللوحة أولاً (يظهر يميناً في RTL)، ثم شريط الـ Slicers (يظهر يساراً)
    main_col, slicer_col = st.columns([5, 1.4])

    with main_col:
        st.markdown("##### 🎯 المؤشرات الرئيسية")
        gauge_cols = st.columns(DASHBOARD_GAUGE_COUNT)
        for i in range(DASHBOARD_GAUGE_COUNT):
            with gauge_cols[i]:
                _render_dashboard_cell(db, dm, settings, dashboard_id, i, cells.get(i))

        st.divider()

        layout_fn = LAYOUT_REGISTRY[template["layout_fn"]]
        base = DASHBOARD_GAUGE_COUNT

        def render_cell(idx_in_template):
            position = base + idx_in_template
            _render_dashboard_cell(db, dm, settings, dashboard_id, position, cells.get(position))

        layout_fn(render_cell)

    with slicer_col:
        _render_slicer_panel(db, dm, dashboard_id, slicers)


# ══════════════════════════════════════════════════════════════
#  شريط الـ Slicers (قوائم قابلة للطي)
# ══════════════════════════════════════════════════════════════

def _render_slicer_panel(db, dm, dashboard_id, slicers):
    header_col, reset_col = st.columns([3, 1.6])
    with header_col:
        st.markdown("##### 🔍 عوامل التصفية (Slicers)")
    with reset_col:
        if st.button("↺ مسح الكل", key=f"reset_slicers_{dashboard_id}", width='stretch'):
            dm.reset_slicers(dashboard_id)
            # تنظيف حالة الـ widgets المؤقتة حتى تعكس الواجهة القيم
            # الفارغة فوراً بعد rerun (وإلا سيبقى selectbox/multiselect
            # عالقاً على القيمة القديمة من session_state)
            for i in range(DASHBOARD_SLICER_COUNT):
                for prefix in ("slicer_table_", "slicer_col_", "slicer_vals_"):
                    st.session_state.pop(f"{prefix}{dashboard_id}_{i}", None)
            st.success("تم مسح كل الفلاتر — اضغط «🔄 تحديث البيانات» لتطبيق ذلك")
            st.rerun()

    tables = dm.get_available_tables()

    for i in range(DASHBOARD_SLICER_COUNT):
        existing = slicers.get(i, {})
        active_label = (
            f"{existing['table_name']}.{existing['column_name']}"
            if existing.get("table_name") and existing.get("column_name")
            else "غير مُفعّل"
        )
        with st.expander(f"Slicer {i + 1} — {active_label}", expanded=False):
            table_options = ["(بدون)"] + tables
            cur_table = existing.get("table_name")
            table_idx = table_options.index(cur_table) if cur_table in table_options else 0
            sel_table = st.selectbox(
                "الجدول", table_options, index=table_idx,
                key=f"slicer_table_{dashboard_id}_{i}",
            )

            sel_column = None
            sel_values = []
            if sel_table != "(بدون)":
                columns = dm.get_available_columns(sel_table)
                col_options = ["(بدون)"] + columns
                cur_col = existing.get("column_name")
                col_idx = col_options.index(cur_col) if cur_col in col_options else 0
                sel_column = st.selectbox(
                    "العمود", col_options, index=col_idx,
                    key=f"slicer_col_{dashboard_id}_{i}",
                )

                if sel_column != "(بدون)":
                    dv = dm.get_distinct_values(sel_table, sel_column, limit=DASHBOARD_SLICER_VALUES_LIMIT)
                    if dv["ok"]:
                        existing_vals = [v for v in (existing.get("selected_values") or []) if v in dv["values"]]
                        sel_values = st.multiselect(
                            "القيم", dv["values"], default=existing_vals,
                            key=f"slicer_vals_{dashboard_id}_{i}",
                        )
                    else:
                        st.error(dv["error"])

            if st.button("💾 حفظ", key=f"slicer_save_{dashboard_id}_{i}", width='stretch'):
                final_table = sel_table if sel_table != "(بدون)" else None
                final_column = sel_column if sel_column and sel_column != "(بدون)" else None
                db.save_dashboard_slicer(
                    dashboard_id, i, final_table, final_column,
                    sel_values if final_column else [],
                )
                st.rerun()

    st.caption("💡 التغييرات تُطبَّق فقط بعد الضغط على «🔄 تحديث البيانات» أعلى الصفحة.")


# ══════════════════════════════════════════════════════════════
#  رسم خلية واحدة (فارغة / تحرير / عرض نتيجة)
# ══════════════════════════════════════════════════════════════

def _render_dashboard_cell(db, dm, settings, dashboard_id, position, cell):
    edit_key = f"editing_cell_{dashboard_id}_{position}"
    is_gauge_row = position < DASHBOARD_GAUGE_COUNT

    with st.container(border=True):
        if cell and cell.get("question") and not st.session_state.get(edit_key):
            _render_cell_result(db, dashboard_id, position, cell)

            # ── badge يوضح هل التحديث القادم سريع أم يحتاج AI ──
            if cell.get("display_type") == "story":
                st.caption("🤖 يحتاج AI عند كل تحديث (تحليل نصي)")
            elif cell.get("base_sql"):
                st.caption("⚡ تحديث سريع (بدون AI)")
            else:
                st.caption("🤖 يحتاج AI (لم يُولَّد SQL بعد)")

            _render_cell_actions_menu(db, dm, settings, dashboard_id, position, edit_key)
        else:
            _render_cell_editor(db, dashboard_id, position, cell, is_gauge_row, edit_key)


def _render_cell_actions_menu(db, dm, settings, dashboard_id, position, edit_key):
    """
    قائمة مطوية واحدة تجمع أزرار (تحديث / تعديل / إفراغ) بدل عرضها
    كثلاثة أزرار دائمة تحت كل خلية — تقلل الازدحام البصري خصوصاً في
    القوالب ذات الخلايا الكثيرة. تُستخدم st.popover عند توفرها
    (تجربة أقرب لقائمة منسدلة حقيقية)، مع fallback إلى st.expander
    في حال تشغيل التطبيق على إصدار Streamlit أقدم لا يدعم popover.
    """
    if _HAS_POPOVER:
        menu_ctx = st.popover("⚙️ خيارات")
    else:
        menu_ctx = st.expander("⚙️ خيارات", expanded=False)

    with menu_ctx:
        if st.button("🔄 تحديث هذه الخلية", key=f"refresh_one_{dashboard_id}_{position}", width='stretch'):
            with st.spinner("جاري التحديث..."):
                r = dm.refresh_single_cell(dashboard_id, position, ai_rules=settings.get("ai_rules"))
            if r["ok"]:
                st.success("✅ تم" + (" (عبر AI)" if r["used_ai"] else " (سريع بدون AI)"))
            else:
                st.error(r.get("error", "فشل التحديث"))
            st.rerun()

        if st.button("✏️ تعديل السؤال", key=f"edit_{dashboard_id}_{position}", width='stretch'):
            st.session_state[edit_key] = True
            st.rerun()

        if st.button("🗑️ إفراغ الخلية", key=f"clear_{dashboard_id}_{position}", width='stretch'):
            db.clear_dashboard_cell(dashboard_id, position)
            st.rerun()


def _render_cell_editor(db, dashboard_id, position, cell, is_gauge_row, edit_key):
    label = "➕ إضافة مقياس (Gauge)" if is_gauge_row else "➕ إضافة عنصر"
    st.markdown(f"**{label}**")

    title = st.text_input(
        "عنوان الخلية (اختياري)", value=(cell or {}).get("title", ""),
        key=f"title_{dashboard_id}_{position}",
    )
    question = st.text_area(
        "السؤال بلغة طبيعية", value=(cell or {}).get("question", ""),
        key=f"question_{dashboard_id}_{position}", height=80,
    )

    if is_gauge_row:
        display_type = "gauge"
        chart_type = None
    else:
        type_options = list(DISPLAY_TYPE_LABELS.keys())
        cur_type = (cell or {}).get("display_type") or "table"
        display_type = st.selectbox(
            "نوع العرض", type_options,
            index=type_options.index(cur_type) if cur_type in type_options else 0,
            format_func=lambda t: DISPLAY_TYPE_LABELS[t],
            key=f"dtype_{dashboard_id}_{position}",
        )
        chart_type = None
        if display_type == "chart":
            ctype_options = list(CHART_TYPE_LABELS.keys())
            cur_ctype = (cell or {}).get("chart_type") or "bar"
            chart_type = st.selectbox(
                "نوع الرسم", ctype_options,
                index=ctype_options.index(cur_ctype) if cur_ctype in ctype_options else 0,
                format_func=lambda t: CHART_TYPE_LABELS[t],
                key=f"ctype_{dashboard_id}_{position}",
            )

    if display_type == "story":
        st.caption("🤖 هذا النوع يحتاج استدعاء AI عند كل تحديث دائماً (تحليل نصي فعلي).")
    else:
        st.caption("ℹ️ عند الحفظ سيُستدعى AI مرة واحدة لتوليد SQL؛ التحديثات اللاحقة (بفلاتر مختلفة) ستكون سريعة بدون AI طالما لم يتغيّر نص السؤال.")

    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("💾 حفظ", key=f"save_cell_{dashboard_id}_{position}", width='stretch', type="primary"):
            if not question.strip():
                st.error("الرجاء كتابة سؤال")
            else:
                db.save_dashboard_cell(
                    dashboard_id, position, display_type,
                    title.strip() or None, question.strip(), chart_type,
                )
                st.session_state.pop(edit_key, None)
                st.info("تم الحفظ. اضغط «🔄 تحديث البيانات» أعلى الصفحة أو «⚙️ خيارات → تحديث هذه الخلية» لعرض النتيجة.")
                st.rerun()
    with bc2:
        if cell and st.button("إلغاء", key=f"cancel_cell_{dashboard_id}_{position}", width='stretch'):
            st.session_state.pop(edit_key, None)
            st.rerun()


def _render_cell_result(db, dashboard_id, position, cell):
    title = cell.get("title") or DISPLAY_TYPE_LABELS.get(cell.get("display_type"), "")
    st.markdown(f"**{title}**")

    if cell.get("last_error"):
        st.error(f"فشل آخر تحديث: {cell['last_error']}")
        return

    stored = cell.get("last_result")
    if not stored:
        st.info("لم يُحدَّث بعد. اضغط «🔄 تحديث البيانات» أعلى الصفحة أو «⚙️ خيارات» أدناه.")
        return

    display_type = cell.get("display_type")
    df = pd.DataFrame(stored.get("rows", []))

    if display_type == "table":
        st.dataframe(df, width='stretch', hide_index=True)

    elif display_type == "chart":
        if df.empty or df.shape[1] < 2:
            st.warning("لا توجد بيانات كافية للرسم")
        else:
            ctype = stored.get("chart_type", "bar")
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
                st.plotly_chart(fig, width='stretch', key=f"chart_{dashboard_id}_{position}")
            except Exception as e:
                st.error(f"تعذر رسم البيانات: {e}")

    elif display_type == "gauge":
        row = df.iloc[0].to_dict() if not df.empty else {}
        current = row.get("current_value", 0)
        mn = row.get("min_value", 0)
        mx = row.get("max_value", 100)
        fig = go.Figure(go.Indicator(
            mode="gauge+number", value=current,
            gauge={"axis": {"range": [mn, mx]}},
        ))
        fig.update_layout(height=180, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width='stretch', key=f"gauge_{dashboard_id}_{position}")

    elif display_type == "kpi":
        row = df.iloc[0].to_dict() if not df.empty else {}
        actual = row.get("actual_value", 0)
        target = row.get("target_value", 0)
        delta = (actual - target) if isinstance(actual, (int, float)) and isinstance(target, (int, float)) else None
        st.metric("القيمة", actual, delta=round(delta, 2) if delta is not None else None)
        st.caption(f"الهدف: {target}")

    elif display_type == "story":
        story_text = stored.get("story", "")
        st.markdown(story_text)
        with st.expander("📊 البيانات المستخدمة"):
            st.dataframe(df, width='stretch', hide_index=True)

    updated = cell.get("last_updated_at")
    if updated:
        st.caption(f"آخر تحديث: {updated[:16].replace('T', ' ')}")


# ══════════════════════════════════════════════════════════════
#  دوال التخطيط (Layout Functions) — قابلة للتوسعة
# ══════════════════════════════════════════════════════════════
# كل دالة تستقبل render_cell(idx) وتستدعيها بالترتيب الصحيح ضمن
# التخطيط المطلوب، مستخدمة st.columns/st.container فقط.

def layout_2x2(render_cell):
    """القالب A: عمودان × صفان (٤ خلايا متساوية)."""
    row1 = st.columns(2)
    with row1[0]:
        render_cell(0)
    with row1[1]:
        render_cell(1)
    row2 = st.columns(2)
    with row2[0]:
        render_cell(2)
    with row2[1]:
        render_cell(3)


def layout_main_and_split(render_cell):
    """القالب B: عمود كبير + عمود جانبي مقسوم لصفين."""
    left, right = st.columns([2, 1])
    with left:
        render_cell(0)
    with right:
        render_cell(1)
        render_cell(2)


def layout_3col(render_cell):
    """القالب C: ٣ أعمدة متساوية."""
    cols = st.columns(3)
    for i in range(3):
        with cols[i]:
            render_cell(i)


def layout_full(render_cell):
    """القالب D: خلية واحدة بعرض الصفحة."""
    render_cell(0)


def layout_2x3(render_cell):
    """القالب E: ٣ أعمدة × صفان (٦ خلايا)."""
    row1 = st.columns(3)
    for i in range(3):
        with row1[i]:
            render_cell(i)
    row2 = st.columns(3)
    for i in range(3):
        with row2[i]:
            render_cell(3 + i)


def layout_2col_big(render_cell):
    """القالب F: عمودان كبيران متساويان."""
    cols = st.columns(2)
    with cols[0]:
        render_cell(0)
    with cols[1]:
        render_cell(1)


LAYOUT_REGISTRY = {
    "layout_2x2": layout_2x2,
    "layout_main_and_split": layout_main_and_split,
    "layout_3col": layout_3col,
    "layout_full": layout_full,
    "layout_2x3": layout_2x3,
    "layout_2col_big": layout_2col_big,
}
