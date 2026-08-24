"""
ui/files.py
===========
رفع ملفات Excel/CSV، اختيار الشيت والأعمدة، وتحميلها كجداول في المشروع.

سياسة عدم التخزين:
--------------------
لا يُحتفظ بالملف الخام (Excel/CSV) على السيرفر إطلاقاً — يُقرأ من
الذاكرة مباشرة عند الرفع، وتُحفظ البيانات النظيفة فقط في project.db.
عند طلب "تحديث البيانات" لاحقاً، يُطلب من المستخدم إعادة اختيار نفس
الملف من جهازه من جديد بدل قراءة نسخة قديمة على السيرفر — وإن لم
يُعِد اختياره، تظهر رسالة خطأ واضحة بدل تحديث صامت أو فاشل.
"""

from pathlib import Path

import streamlit as st

from ui.common import apply_rtl, apply_theme_css, require_login, require_project, sidebar_header
from core.file_manager import FileManager
from core.data_manager import DataManager


def show_files():
    apply_rtl()
    require_login()
    db = require_project()
    apply_theme_css(db.get_settings().get("theme", "ocean_dark"))
    sidebar_header()

    fm = FileManager(st.session_state.user_id, st.session_state.project_id)
    dm = DataManager(db, fm)

    st.title("📄 إدارة الملفات")

    with st.expander("⬆️ رفع ملف جديد", expanded=True):
        uploaded = st.file_uploader("اختر ملف Excel أو CSV", type=["xlsx", "xls", "csv"])
        if uploaded:
            file_bytes = uploaded.getvalue()
            info = fm.inspect(file_bytes, uploaded.name)

            if not info["ok"]:
                st.error(info["error"])
            else:
                sheet = None
                if info["sheets"]:
                    sheet = st.selectbox("اختر الشيت", info["sheets"])

                cols_result = fm.get_columns_from_bytes(file_bytes, info["extension"], sheet)
                selected_columns = None
                if cols_result["ok"]:
                    selected_columns = st.multiselect(
                        "اختر الأعمدة (اتركها فارغة لاختيار الكل)",
                        cols_result["columns"],
                    )

                table_alias = st.text_input(
                    "اسم الجدول (بالإنجليزية، بدون مسافات)",
                    value=Path(uploaded.name).stem.strip().replace(" ", "_").lower(),
                )

                if st.button("✅ تحميل البيانات إلى المشروع"):
                    r = dm.load_file(
                        file_bytes=file_bytes,
                        extension=info["extension"],
                        table_alias=table_alias,
                        sheet=sheet,
                        columns=selected_columns or None,
                        original_name=uploaded.name,
                    )
                    if r["ok"]:
                        st.success(f"تم تحميل الجدول '{table_alias}' — {r['rows']} صف، {r['cols']} عمود")
                        st.rerun()
                    else:
                        st.error(r["error"])

    st.divider()
    st.subheader("📚 الملفات المسجلة في المشروع")

    files = db.get_files()
    if not files:
        st.info("لا توجد ملفات محملة بعد.")
        return

    for f in files:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"**{f['table_alias']}**  ({f['original_name']})")
                if f.get("selected_sheet"):
                    st.caption(f"الشيت: {f['selected_sheet']}")
                st.caption(f"الأعمدة: {', '.join(f['selected_columns']) or 'الكل'}")
            with c2:
                if st.button("🗑️ حذف", key=f"del_file_{f['id']}", width='stretch'):
                    db.remove_file(f["id"])
                    st.rerun()

            _render_refresh_widget(dm, f)


def _render_refresh_widget(dm: DataManager, f: dict):
    """
    عنصر تحديث مضغوط لكل ملف: يطلب من المستخدم إعادة اختيار نفس الملف
    من جهازه. لو ضغط "تحديث الآن" بدون اختيار ملف، تظهر رسالة الخطأ
    المطلوبة بدل تحديث فاشل أو صامت.
    """
    with st.expander(f"🔄 تحديث «{f['table_alias']}» من الملف الأصلي"):
        st.caption(f"اختر نفس الملف من جهازك لإعادة تحميل أحدث نسخة منه: **{f['original_name']}**")
        refreshed = st.file_uploader(
            "اختر الملف", type=["xlsx", "xls", "csv"],
            key=f"refresh_uploader_{f['id']}", label_visibility="collapsed",
        )
        if st.button("تحديث الآن", key=f"refresh_btn_{f['id']}"):
            if refreshed is None:
                st.error(f"لا يمكن التحديث الآن: الملف غير موجود {f['original_name']}")
            else:
                file_bytes = refreshed.getvalue()
                ext = Path(refreshed.name).suffix.lower()
                r = dm.refresh_from_bytes(
                    file_id=f["id"],
                    file_bytes=file_bytes,
                    extension=ext,
                    table_alias=f["table_alias"],
                    original_name=refreshed.name,
                    sheet=f.get("selected_sheet"),
                    columns=f.get("selected_columns") or None,
                )
                if r["ok"]:
                    st.success(f"تم التحديث — {r['rows']} صف")
                    st.rerun()
                else:
                    st.error(r["error"])
