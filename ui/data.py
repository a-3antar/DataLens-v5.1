"""
ui/data.py
==========
تنظيف البيانات: تغيير الأنواع، تصفية الصفوف، معالجة النصوص والفراغات،
والعلاقات بين الجداول.

العلاقات:
----------
اختيار أسماء الأعمدة يتم الآن عبر قوائم منسدلة (selectbox) مبنية من
أعمدة الجدول الفعلية بدل كتابة الاسم يدوياً — يمنع أخطاء الطباعة
ويضمن أن العمود المختار موجود فعلاً في الجدول.
"""

import streamlit as st

from ui.common import apply_rtl, require_login, require_project, sidebar_header
from core.file_manager import FileManager
from core.data_manager import DataManager


def show_data():
    apply_rtl()
    require_login()
    db = require_project()
    sidebar_header()

    fm = FileManager(st.session_state.user_id, st.session_state.project_id)
    dm = DataManager(db, fm)

    st.title("🧹 تنظيف البيانات")

    files = db.get_files()
    if not files:
        st.info("لا توجد جداول بعد. ارفع ملفاً أولاً من صفحة الملفات.")
        return

    aliases = [f["table_alias"] for f in files]
    table = st.selectbox("اختر الجدول", aliases)

    preview = dm.get_preview(table, rows=10)
    if not preview["ok"]:
        st.error(preview["error"])
        return

    st.markdown(f"عدد الصفوف الكلي: **{preview['total']}**")
    st.dataframe(preview["data"], width='stretch')

    column = st.selectbox("اختر العمود", preview["columns"])

    tab_type, tab_filter, tab_text, tab_nulls, tab_relations = st.tabs(
        ["🔢 نوع البيانات", "🔍 تصفية الصفوف", "🔤 تنظيف النصوص", "🕳️ القيم الفارغة", "🔗 العلاقات"]
    )

    with tab_type:
        new_type = st.selectbox("النوع الجديد", ["int", "float", "str", "date", "bool"])
        if st.button("تطبيق تغيير النوع"):
            r = dm.change_dtype(table, column, new_type)
            _report(r)

    with tab_filter:
        op = st.selectbox("العملية", ["==", "!=", ">", "<", ">=", "<=", "contains"])
        value = st.text_input("القيمة")
        if st.button("تطبيق التصفية"):
            r = dm.filter_rows(table, column, op, value)
            _report(r, extra=lambda r: st.caption(f"{r['before']} → {r['after']} صف"))

    with tab_text:
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("Strip"):
            _report(dm.strip_text(table, column))
        if c2.button("Capitalize"):
            _report(dm.capitalize_text(table, column))
        if c3.button("UPPER"):
            _report(dm.uppercase_text(table, column))
        if c4.button("lower"):
            _report(dm.lowercase_text(table, column))

    with tab_nulls:
        strategy = st.selectbox("الاستراتيجية", ["mean", "median", "mode", "zero", "value"])
        value = None
        if strategy == "value":
            value = st.text_input("القيمة البديلة")
        if st.button("معالجة القيم الفارغة"):
            r = dm.fill_nulls(table, column, strategy, value)
            _report(r, extra=lambda r: st.caption(f"تم ملء {r.get('filled', 0)} قيمة"))

    with tab_relations:
        _render_relations_tab(db, dm, aliases)

    st.divider()
    st.subheader("📊 إحصاءات العمود")
    stats = dm.get_stats(table, column)
    if stats["ok"]:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("عدد القيم", stats["count"])
        c2.metric("فارغ", stats["nulls"])
        c3.metric("أدنى", stats["min"])
        c4.metric("أعلى", stats["max"])
        c5.metric("متوسط", round(stats["mean"], 2) if stats["mean"] else None)
        c6.metric("وسيط", round(stats["median"], 2) if stats["median"] else None)


def _render_relations_tab(db, dm: DataManager, aliases: list[str]):
    """
    تبويب العلاقات: عرض العلاقات الحالية + إضافة علاقة جديدة عبر قوائم
    منسدلة (جدول + عمود) بدل كتابة اسم العمود يدوياً.
    """
    st.caption("العلاقات الحالية:")
    relations = db.get_relations()
    for rel in relations:
        c1, c2 = st.columns([4, 1])
        c1.write(f"{rel['from_table']}.{rel['from_col']} = {rel['to_table']}.{rel['to_col']}")
        if c2.button("حذف", key=f"del_rel_{rel['id']}"):
            db.remove_relation(rel["id"])
            st.rerun()

    st.divider()
    c1, c2, c3, c4 = st.columns(4)

    # ── من جدول / من عمود ──
    from_table = c1.selectbox("من جدول", aliases, key="rel_from_t")
    from_preview = dm.get_preview(from_table, rows=1) if from_table else {"ok": False}
    from_columns = from_preview["columns"] if from_preview.get("ok") else []
    from_col = c2.selectbox(
        "من عمود",
        ["(اختر عموداً)"] + from_columns,
        key="rel_from_c",
    )

    # ── إلى جدول / إلى عمود (نستبعد الجدول نفسه من قائمة "إلى" افتراضياً
    # لتفادي ربط جدول بنفسه بالخطأ، لكن نُبقي الخيار متاحاً لو كان هو
    # الجدول الوحيد في المشروع) ──
    to_table_options = [t for t in aliases if t != from_table] or aliases
    to_table = c3.selectbox("إلى جدول", to_table_options, key="rel_to_t")
    to_preview = dm.get_preview(to_table, rows=1) if to_table else {"ok": False}
    to_columns = to_preview["columns"] if to_preview.get("ok") else []
    to_col = c4.selectbox(
        "إلى عمود",
        ["(اختر عموداً)"] + to_columns,
        key="rel_to_c",
    )

    # تحذير لطيف (وليس منع) لو أنواع العمودين تبدو مختلفة بشكل واضح
    if from_col != "(اختر عموداً)" and to_col != "(اختر عموداً)":
        from_stats = dm.get_stats(from_table, from_col)
        to_stats = dm.get_stats(to_table, to_col)
        from_is_numeric = bool(from_stats.get("ok")) and from_stats.get("count", 0) > 0
        to_is_numeric = bool(to_stats.get("ok")) and to_stats.get("count", 0) > 0
        if from_is_numeric != to_is_numeric:
            st.warning(
                "⚠️ يبدو أن نوعي العمودين مختلفان (رقمي مقابل نصي) — "
                "تحقق من أن الربط صحيح قبل الإضافة."
            )

    if st.button("➕ إضافة علاقة"):
        if from_col != "(اختر عموداً)" and to_col != "(اختر عموداً)":
            db.add_relation(from_table, from_col, to_table, to_col)
            st.success("تمت إضافة العلاقة")
            st.rerun()
        else:
            st.error("الرجاء اختيار عمود من كل جدول")


def _report(r: dict, extra=None):
    if r["ok"]:
        st.success("تم التطبيق بنجاح")
        if extra:
            extra(r)
        st.rerun()
    else:
        st.error(r["error"])
