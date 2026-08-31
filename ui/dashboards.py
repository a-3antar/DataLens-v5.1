"""
ui/dashboards.py
==================
ملخص تحديثات لوحات المعلومات (ui/dashboards.py):
1. هيكلة كائنات الخلايا: فصل منطق العرض والتعديل إلى `core/dashboard_cells/` وتوحيد الواجهات.
2. تحسين واجهة المستخدم: نقل الفلاتر لقائمة مطوية أعلى الصفحة واستبدال الرسائل بإشعارات عابرة (Toast).
3. التحديث الذكي والسريع: استخدام الذكاء الاصطناعي فقط للأسئلة الجديدة أو السرد، وتطبيق الفلاتر محلياً.
4. التنفيذ المتوازي: تشغيل خلايا السرد بالتوازي دائماً، وبقية الخلايا مع المحرك السحابي لتسريع الأداء.
5. الإنشاء التلقائي: إمكانية توليد اللوحات تلقائياً بالذكاء الاصطناعي بناءً على وصف حر للمستخدم.
"""

from core import dashboard_cells
import uuid

import streamlit as st

from ui.common import (
    apply_rtl, apply_theme_css, require_login, require_project, sidebar_header,
    format_local_dt, notify,
)
from core.dashboard_templates import DASHBOARD_TEMPLATES, get_template
from core.dashboard_manager import DashboardManager
from core.dashboard_cells import create_cell
from ai.ai_manager import build_ai_manager as _core_build_ai_manager
from config import DASHBOARD_SLICER_COUNT, DASHBOARD_GAUGE_COUNT, DASHBOARD_SLICER_VALUES_LIMIT

_BUSY_FRAMES = ["⏳", "⌛"]


def show_dashboards():
    apply_rtl()
    require_login()
    db = require_project()
    settings = db.get_settings()
    apply_theme_css(settings.get("theme", "ocean_dark"))
    sidebar_header()

    if st.session_state.get("current_dashboard_id"):
        _show_dashboard_detail(db)
    else:
        _show_dashboard_gallery(db)


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
                        notify("الرجاء إدخال عنوان اللوحة أولاً", kind="warning")
                    else:
                        dash_id = str(uuid.uuid4())
                        db.create_dashboard(dash_id, title.strip(), key)
                        st.session_state.current_dashboard_id = dash_id
                        st.rerun()
            st.divider()

    with st.expander("🤖 إنشاء لوحة تلقائياً بالذكاء الاصطناعي"):
        auto_title = st.text_input("عنوان اللوحة", key="auto_dash_title")
        auto_desc = st.text_area(
            "صف ما تريد متابعته", key="auto_dash_desc", height=90,
            placeholder="مثال: لوحة متابعة أداء المبيعات الشهري حسب المنطقة والمنتج",
        )
        if st.button("🤖 إنشاء الخطة", key="auto_dash_generate"):
            if not auto_title.strip() or not auto_desc.strip():
                notify("الرجاء إدخال العنوان والوصف", kind="warning")
            elif not db.get_files():
                notify("ارفع ملفاً واحداً على الأقل قبل استخدام الإنشاء التلقائي", kind="warning")
            else:
                ai, settings = _build_ai_manager(db)
                dm = DashboardManager(db, ai)
                with st.spinner("⏳ جاري تحليل بياناتك وبناء خطة اللوحة..."):
                    plan = dm.generate_dashboard_plan(auto_desc.strip(), ai_rules=settings.get("ai_rules"))
                if not plan["ok"]:
                    notify(plan["error"], kind="error")
                else:
                    dash_id = str(uuid.uuid4())
                    db.create_dashboard(dash_id, auto_title.strip(), plan["template_id"])
                    for i, g in enumerate(plan["gauges"]):
                        if g.get("question"):
                            db.save_dashboard_cell(dash_id, i, "gauge", g.get("title") or None, g["question"], None)
                    base = DASHBOARD_GAUGE_COUNT
                    for i, c in enumerate(plan["cells"]):
                        if c.get("question"):
                            db.save_dashboard_cell(
                                dash_id, base + i, c.get("display_type") or "table",
                                c.get("title") or None, c["question"], c.get("chart_type"),
                            )
                    notify("تم إنشاء اللوحة بنجاح", kind="success")
                    st.session_state.current_dashboard_id = dash_id
                    st.rerun()

    dashboards = db.get_dashboards()
    if not dashboards:
        st.caption("لا توجد لوحات بعد. أنشئ لوحتك الأولى أعلاه.")
        return

    settings = db.get_settings()
    st.subheader("لوحاتك")
    for d in dashboards:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            tmpl = get_template(d["template_id"])
            with c1:
                st.markdown(f"**{d['title']}**")
                updated_label = format_local_dt(d.get("updated_at"), settings) or "لم يُحدَّث بعد"
                st.caption(f"القالب: {tmpl['name']} | آخر تحديث: {updated_label}")
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


def _build_ai_manager(db):
    """مفتاح API يُقرأ من إعدادات المشروع (project.db) كما كان دائماً."""
    return _core_build_ai_manager(db)

def _show_dashboard_detail(db):
    dashboard_id = st.session_state.current_dashboard_id
    dashboard = db.get_dashboard(dashboard_id)
    if not dashboard:
        st.session_state.current_dashboard_id = None
        st.rerun()
        return

    ai, settings = _build_ai_manager(db)
    dm = DashboardManager(db, ai)
    engine_name = settings.get("ai_engine", "gemini")

    c1, c2, c3 = st.columns([5, 1.2, 1.5])
    with c1:
        st.title(f"📊 {dashboard['title']}")
        updated_label = format_local_dt(dashboard.get("updated_at"), settings) or "لم يُحدَّث بعد"
        st.caption(f"آخر تحديث: {updated_label}")
    with c2:
        if st.button("↩️ اللوحات", width='stretch'):
            st.session_state.current_dashboard_id = None
            st.rerun()
    with c3:
        refresh_clicked = st.button("🔄 تحديث البيانات", type="primary", width='stretch')

    if refresh_clicked:
        status_placeholder = st.empty()
        status_placeholder.markdown("⏳ جاري تحديث خلايا اللوحة...")

        def on_progress(done, total):
            frame = _BUSY_FRAMES[done % len(_BUSY_FRAMES)]
            status_placeholder.markdown(f"{frame} جاري التحديث... تم {done} من {total}")

        result = dm.refresh_dashboard(
            dashboard_id,
            ai_rules=settings.get("ai_rules"),
            on_progress=on_progress,
            engine_name=engine_name,
        )
        status_placeholder.empty()

        if not result["ok"]:
            notify(result.get("error", "فشل التحديث"), kind="error")
        elif result["errors"] == 0:
            notify(
                f"تم تحديث {result['total']} خلية بنجاح "
                f"(⚡ {result['fast_updates']} سريع، 🤖 {result['ai_calls']} عبر AI)",
                kind="success",
            )
        else:
            notify(
                f"تم التحديث: {result['total'] - result['errors']} نجحت، "
                f"{result['errors']} فشلت",
                kind="warning",
            )
        st.rerun()

    # الـ Slicers وفلتر التاريخ معاً في قائمة مطوية واحدة — فلتر
    # التاريخ يشغل العمود الرابع (الأخير) ضمن نفس صف الفلاتر بدل قائمة
    # مطوية منفصلة، فيظهر المستخدم كل عوامل التصفية في مكان واحد.
    slicers = {s["position"]: s for s in db.get_dashboard_slicers(dashboard_id)}

    date_filter_position = DASHBOARD_SLICER_COUNT - 1
    date_filter = slicers.pop(date_filter_position, None)
    date_filter_active = bool(
        date_filter and date_filter.get("table_name") and date_filter.get("column_name")
        and date_filter.get("selected_values") and len(date_filter["selected_values"]) == 2
    )
    active_count = sum(
        1 for s in slicers.values()
        if s.get("table_name") and s.get("column_name") and s.get("selected_values")
    ) + (1 if date_filter_active else 0)
    slicer_label = "🔍 عوامل التصفية" + (f" — {active_count} مُفعَّل" if active_count else "")
    with st.expander(slicer_label, expanded=False):
        _render_slicer_panel(db, dm, dashboard_id, slicers, date_filter_position, date_filter)

    st.divider()

    template = get_template(dashboard["template_id"])
    cells = {c["position"]: c for c in db.get_dashboard_cells(dashboard_id)}

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

def _render_slicer_panel(db, dm, dashboard_id, slicers, date_filter_position, date_filter):
    tables = dm.get_available_tables()
    slicer_count = DASHBOARD_SLICER_COUNT - 1  # آخر عمود محجوز لفلتر التاريخ
    slicer_cols = st.columns(DASHBOARD_SLICER_COUNT)

    # نجمع اختيارات كل فلتر أولاً (بدون حفظ فردي) — الحفظ الفعلي يحدث
    # مرة واحدة عبر زر موحّد في نهاية الدالة، بعد قراءة كل العناصر.
    pending_slicers = []  # [(position, table, column, values)]

    for i in range(slicer_count):
        existing = slicers.get(i, {})
        with slicer_cols[i]:
            table_options = ["(بدون)"] + tables
            cur_table = existing.get("table_name")
            table_idx = table_options.index(cur_table) if cur_table in table_options else 0
            sel_table = st.selectbox(
                f"الجدول (فلتر {i + 1})", table_options, index=table_idx,
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
                    # عرض قيم العمود فوراً بمجرد اختياره — بدون زر
                    # "تحميل القيم" أو "إعادة تحميل القيم". يُخزَّن الجلب
                    # في session_state باسم يشمل (الجدول, العمود) بحيث
                    # يُعاد الجلب تلقائياً فقط عند تغيّر الاختيار فعلياً.
                    values_key = f"slicer_values_cache_{dashboard_id}_{i}"
                    cache_sig_key = f"{values_key}_sig"
                    current_sig = (sel_table, sel_column)

                    if st.session_state.get(cache_sig_key) != current_sig:
                        dv = dm.get_distinct_values(sel_table, sel_column, limit=DASHBOARD_SLICER_VALUES_LIMIT)
                        if dv["ok"]:
                            st.session_state[values_key] = dv["values"]
                            st.session_state[cache_sig_key] = current_sig
                        else:
                            notify(dv["error"], kind="error")
                            st.session_state[values_key] = []
                            st.session_state[cache_sig_key] = current_sig

                    available_values = st.session_state.get(values_key, [])
                    existing_vals = [v for v in (existing.get("selected_values") or []) if v in available_values]
                    sel_values = st.multiselect(
                        "القيم", available_values, default=existing_vals,
                        key=f"slicer_vals_{dashboard_id}_{i}",
                    )

            final_table = sel_table if sel_table != "(بدون)" else None
            final_column = sel_column if sel_column and sel_column != "(بدون)" else None
            pending_slicers.append((i, final_table, final_column, sel_values if final_column else []))

    # العمود الرابع (الأخير) داخل نفس صف الفلاتر — فلتر التاريخ
    with slicer_cols[slicer_count]:
        date_table, date_column, date_values = _render_date_filter_fields(db, dm, dashboard_id, date_filter)
    pending_slicers.append((date_filter_position, date_table, date_column, date_values))

    st.markdown("")  # مسافة بسيطة قبل صف الأزرار
    reset_col, save_col, _spacer = st.columns([1.2, 1.2, 3.6])

    
    with save_col:
        if st.button("💾 حفظ الكل", key=f"save_all_slicers_{dashboard_id}", width='stretch', type="primary"):
            for position, final_table, final_column, values in pending_slicers:
                db.save_dashboard_slicer(dashboard_id, position, final_table, final_column, values)
            notify("تم حفظ كل الفلاتر", kind="success")
            st.rerun()

    with reset_col:
        if st.button("↺ مسح الكل", key=f"reset_slicers_{dashboard_id}", width='stretch'):
            # reset_dashboard_slicers يحذف كل صفوف dashboard_slicers لهذه
            # اللوحة دفعة واحدة — بما فيها موضع فلتر التاريخ نفسه، لأنه
            # مخزَّن في نفس الجدول. لا حاجة لاستدعاء منفصل لمسحه.
            dm.reset_slicers(dashboard_id)
            for i in range(slicer_count):
                for prefix in ("slicer_table_", "slicer_col_", "slicer_vals_",
                               "slicer_values_cache_"):
                    st.session_state.pop(f"{prefix}{dashboard_id}_{i}", None)
            for key in (f"date_filter_table_{dashboard_id}", f"date_filter_col_{dashboard_id}",
                        f"date_filter_start_{dashboard_id}", f"date_filter_end_{dashboard_id}"):
                st.session_state.pop(key, None)
            notify("تم مسح كل الفلاتر", kind="success")
            st.rerun()

    
def _render_date_filter_fields(db, dm, dashboard_id, existing):
    """
    فلتر تاريخ مبسّط: جدول + عمود تاريخ + نطاق (من/إلى) مباشرة — بدون
    خيارات "فترة سريعة" وبدون زر حفظ خاص به (الحفظ موحّد الآن عبر زر
    "💾 حفظ الكل" في _render_slicer_panel). يُرجع (table, column, values)
    ليُجمَع مع بقية الفلاتر ويُحفظ دفعة واحدة.

    values عند الحفظ = [start_iso, end_iso] بالضبط — راجع
    DashboardManager._build_active_filters للتمييز عن Slicer عادي.
    """
    import datetime as _dt

    existing = existing or {}
    tables = dm.get_available_tables()
    settings = db.get_settings()
    tables_with_dates = [t for t in tables if settings.get(f"_date_cols_{t}", [])]

    if not tables_with_dates:
        st.caption("لا توجد أعمدة تاريخ في المشروع")
        return None, None, []

    table_options = ["(بدون)"] + tables_with_dates
    cur_table = existing.get("table_name")
    table_idx = table_options.index(cur_table) if cur_table in table_options else 0
    sel_table = st.selectbox(
        "فلتر التاريخ", table_options, index=table_idx,
        key=f"date_filter_table_{dashboard_id}",
    )

    if sel_table == "(بدون)":
        return None, None, []

    date_columns = settings.get(f"_date_cols_{sel_table}", [])
    col_options = ["(بدون)"] + date_columns
    cur_col = existing.get("column_name")
    col_idx = col_options.index(cur_col) if cur_col in col_options else 0
    sel_column = st.selectbox(
        "العمود", col_options, index=col_idx,
        key=f"date_filter_col_{dashboard_id}",
    )

    if sel_column == "(بدون)":
        return sel_table, None, []

    existing_values = existing.get("selected_values") or []
    default_start = None
    default_end = None
    if len(existing_values) == 2:
        try:
            default_start = _dt.date.fromisoformat(str(existing_values[0])[:10])
            default_end = _dt.date.fromisoformat(str(existing_values[1])[:10])
        except ValueError:
            pass

    start_date = st.date_input("من", value=default_start, key=f"date_filter_start_{dashboard_id}")
    end_date = st.date_input("إلى", value=default_end, key=f"date_filter_end_{dashboard_id}")

    if start_date and end_date:
        if start_date > end_date:
            notify("تاريخ البداية يجب أن يكون قبل تاريخ النهاية — لن يُحفظ هذا الفلتر", kind="warning")
            return sel_table, sel_column, []
        return sel_table, sel_column, [start_date.isoformat(), end_date.isoformat()]

    return sel_table, sel_column, []

def _render_dashboard_cell(db, dm, settings, dashboard_id, position, cell_row):
    """
    نقطة الدخول الموحّدة لعرض/تحرير خلية واحدة — تبني كائن الخلية
    المناسب عبر core.dashboard_cells.create_cell() (بما فيها EmptyCell
    لو لم تُهيَّأ الخلية بعد) وتستدعي عليه الدوال الموحّدة بغض النظر عن
    نوعها الفعلي.
    """
    edit_key = f"editing_cell_{dashboard_id}_{position}"
    is_gauge_row = position < DASHBOARD_GAUGE_COUNT

    row = cell_row or {"position": position}
    cell_obj = create_cell(row)

    with st.container(border=True):
        if cell_row and cell_row.get("question") and not st.session_state.get(edit_key):
            # زر "⁝" أعلى الخلية (بجانب عنوانها) بدل ظهوره أسفل النتيجة
            title_col, menu_col = st.columns([5, 1])
            with title_col:
                title = cell_obj.title or cell_obj.label
                st.markdown(f"**{title}**")
            with menu_col:
                cell_obj.render_actions_menu(db, dm, settings, dashboard_id, edit_key)

            cell_obj.render_result(settings, dashboard_id)
        else:
            cell_obj.render_editor(db, dm, settings, dashboard_id, is_gauge_row, edit_key)


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
