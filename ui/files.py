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

🆕 تأكيد الحذف + إشعار Cascade على العلاقات:
------------------------------------------------
حذف ملف لم يعد فورياً بضغطة واحدة — يتطلب الآن ضغطتين (نفس نمط تأكيد
حذف المشروع في ui/projects.py)، لأن حذف الجدول قد يُسقط علاقات مبنية
عليه في صفحة "تنظيف البيانات". بعد التنفيذ الفعلي، يُعرض للمستخدم
عدد العلاقات التي حُذفت تبعاً (إن وُجدت) بدل حذف صامت قد يفاجئه لاحقاً
عند فتح صفحة العلاقات أو المحادثة.
"""

from pathlib import Path

import streamlit as st

from ui.common import apply_rtl, apply_theme_css, require_login, require_project, sidebar_header, notify
from core.file_manager import FileManager
from core.data_manager import DataManager


def show_files():
    apply_rtl()
    require_login()
    db = require_project()
    apply_theme_css(db.get_settings())
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

                # 🆕 تنبيه مسبق بعدد العلاقات المرتبطة بهذا الجدول —
                # حتى يعرف المستخدم أثر الحذف قبل تأكيده، وليس بعده فقط.
                related_count = sum(
                    1 for rel in db.get_relations()
                    if rel["from_table"] == f["table_alias"] or rel["to_table"] == f["table_alias"]
                )
                if related_count:
                    st.caption(f"⚠️ مرتبط بـ {related_count} علاقة — سيتم حذفها تلقائياً عند حذف هذا الجدول")

            with c2:
                _render_delete_widget(db, f)

            _render_refresh_widget(dm, f)


def _render_delete_widget(db, f: dict):
    """
    🆕 حذف الملف بتأكيد ثنائي الضغط (نفس نمط ui/projects.py) — يمنع
    حذفاً عرضياً بضغطة واحدة، خصوصاً أن الحذف يُسقط أي علاقة مبنية
    على هذا الجدول تلقائياً معه.
    """
    confirm_key = f"confirm_delete_file_{f['id']}"
    if st.session_state.get(confirm_key):
        if st.button("⚠️ تأكيد الحذف", key=f"confirm_del_file_{f['id']}", width='stretch'):
            result = db.remove_file(f["id"])
            st.session_state.pop(confirm_key, None)
            removed_relations = (result or {}).get("removed_relations", 0)
            if removed_relations:
                notify(
                    f"تم حذف الجدول '{f['table_alias']}' مع {removed_relations} علاقة مرتبطة به",
                    kind="warning",
                )
            else:
                notify(f"تم حذف الجدول '{f['table_alias']}'", kind="success")
            st.rerun()
        if st.button("إلغاء", key=f"cancel_del_file_{f['id']}", width='stretch'):
            st.session_state.pop(confirm_key, None)
            st.rerun()
    else:
        if st.button("🗑️ حذف", key=f"del_file_{f['id']}", width='stretch'):
            st.session_state[confirm_key] = True
            st.rerun()


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
