"""
ui/data.py
==========
تنظيف البيانات: تغيير الأنواع، تصفية الصفوف، معالجة النصوص والفراغات،
حذف الصفوف الفارغة، والعلاقات بين الجداول.

العلاقات:
----------
اختيار أسماء الأعمدة يتم الآن عبر قوائم منسدلة (selectbox) مبنية من
أعمدة الجدول الفعلية بدل كتابة الاسم يدوياً — يمنع أخطاء الطباعة
ويضمن أن العمود المختار موجود فعلاً في الجدول.

🆕 تصفية الصفوف:
------------------
القيمة المُدخلة (نصية دائماً من st.text_input) تُحوَّل الآن في
core.data_manager.DataManager._coerce_filter_value إلى نوع العمود
الفعلي (رقمي/تاريخ/منطقي) قبل المقارنة — يمنع خطأ
"Invalid comparison between dtype=int64 and str" على الأعمدة الرقمية
وأعمدة التاريخ.

🆕 القيم الفارغة:
-------------------
بالإضافة لتعبئة القيم الفارغة لعمود واحد، أصبح بالإمكان حذف الصفوف
الفارغة كاملة (كل الأعمدة فارغة معاً، أو أي عمود فارغ، أو عمود محدد
فقط) عبر core.data_manager.DataManager.drop_empty_rows.

🆕 إحصاءات العمود:
--------------------
تُعرض الآن حسب نوع العمود الفعلي (رقمي/تاريخ/نصي) بدل فرض إحصاءات
رقمية على كل الأعمدة (كانت تُظهر أرقاماً وهمية/فارغة على الأعمدة
النصية وأعمدة التاريخ).
"""

import streamlit as st

from ui.common import apply_rtl, apply_theme_css, require_login, require_project, sidebar_header
from core.file_manager import FileManager
from core.data_manager import DataManager


def show_data():
    apply_rtl()
    require_login()
    db = require_project()
    apply_theme_css(db.get_settings())
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
        st.caption("القيمة تُحوَّل تلقائياً لنوع العمود (رقم/تاريخ) قبل المقارنة.")
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
        st.markdown("**تعبئة القيم الفارغة في عمود واحد**")
        strategy = st.selectbox("الاستراتيجية", ["mean", "median", "mode", "zero", "value"])
        value = None
        if strategy == "value":
            value = st.text_input("القيمة البديلة")
        if st.button("معالجة القيم الفارغة"):
            r = dm.fill_nulls(table, column, strategy, value)
            _report(r, extra=lambda r: st.caption(f"تم ملء {r.get('filled', 0)} قيمة"))

        st.divider()
        st.markdown("**🆕 حذف الصفوف الفارغة**")
        drop_mode = st.selectbox(
            "طريقة الحذف",
            ["all", "any", "column"],
            format_func=lambda m: {
                "all": "حذف الصف فقط لو كل أعمدته فارغة معاً",
                "any": "حذف الصف لو أي عمود فيه فارغ (أكثر صرامة)",
                "column": f"حذف الصف لو العمود المختار «{column}» فارغ فقط",
            }[m],
        )
        if st.button("🗑️ حذف الصفوف الفارغة"):
            r = dm.drop_empty_rows(table, mode=drop_mode, column=column if drop_mode == "column" else None)
            _report(r, extra=lambda r: st.caption(f"{r['before']} → {r['after']} صف"))

    with tab_relations:
        _render_relations_tab(db, dm, aliases)

    st.divider()
    st.subheader("📊 إحصاءات العمود")
    stats = dm.get_stats(table, column)
    if not stats["ok"]:
        st.error(stats["error"])
    else:
        _render_column_stats(stats)


def _render_column_stats(stats: dict):
    """
    🆕 عرض إحصاءات مناسبة لنوع العمود الفعلي بدل قالب رقمي واحد يُفرض
    على كل الأعمدة — راجع DataManager.get_stats للتفاصيل.
    """
    kind = stats.get("kind", "numeric")

    if kind == "numeric":
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("عدد القيم", stats["count"])
        c2.metric("فارغ", stats["nulls"])
        c3.metric("أدنى", stats["min"])
        c4.metric("أعلى", stats["max"])
        c5.metric("متوسط", round(stats["mean"], 2) if stats["mean"] is not None else None)
        c6.metric("وسيط", round(stats["median"], 2) if stats["median"] is not None else None)

    elif kind == "date":
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("عدد القيم", stats["count"])
        c2.metric("فارغ", stats["nulls"])
        c3.metric("أقدم تاريخ", stats["min"] or "—")
        c4.metric("أحدث تاريخ", stats["max"] or "—")

    else:  # text
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("عدد القيم", stats["count"])
        c2.metric("فارغ", stats["nulls"])
        c3.metric("قيم فريدة", stats["unique"])
        c4.metric("الأكثر تكراراً", stats["most_common"] or "—")


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

    from_table = c1.selectbox("من جدول", aliases, key="rel_from_t")
    from_preview = dm.get_preview(from_table, rows=1) if from_table else {"ok": False}
    from_columns = from_preview["columns"] if from_preview.get("ok") else []
    from_col = c2.selectbox(
        "من عمود",
        ["(اختر عموداً)"] + from_columns,
        key="rel_from_c",
    )

    to_table_options = [t for t in aliases if t != from_table] or aliases
    to_table = c3.selectbox("إلى جدول", to_table_options, key="rel_to_t")
    to_preview = dm.get_preview(to_table, rows=1) if to_table else {"ok": False}
    to_columns = to_preview["columns"] if to_preview.get("ok") else []
    to_col = c4.selectbox(
        "إلى عمود",
        ["(اختر عموداً)"] + to_columns,
        key="rel_to_c",
    )

    if from_col != "(اختر عموداً)" and to_col != "(اختر عموداً)":
        from_stats = dm.get_stats(from_table, from_col)
        to_stats = dm.get_stats(to_table, to_col)
        from_is_numeric = from_stats.get("ok") and from_stats.get("kind") == "numeric"
        to_is_numeric = to_stats.get("ok") and to_stats.get("kind") == "numeric"
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
