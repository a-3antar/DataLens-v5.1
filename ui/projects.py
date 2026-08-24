"""
ui/projects.py
==============
إنشاء / فتح / إعادة تسمية / حذف / تصدير / استيراد المشاريع.

عند إنشاء مشروع جديد، تُعبَّأ إعدادات محرك AI (والمفتاح) تلقائياً
بآخر محرك+مفتاح+نموذج استخدمه المستخدم (من users.db عبر AuthManager)
كقيمة افتراضية مبدئية فقط — تُحفظ داخل project.db الخاص بالمشروع
الجديد تماماً كأي إعداد آخر، ويستطيع المستخدم تغييرها أو حتى استخدام
محرك مختلف تماماً لهذا المشروع بدون أي قيد.
"""

from pathlib import Path
import tempfile

import streamlit as st

from ui.common import (
    apply_rtl, apply_theme_css, require_login, sidebar_header, get_project_manager,
    temp_export_dir, offer_download,
)
from core.project_db import ProjectDB
from core.auth import AuthManager


def show_projects():
    apply_rtl()
    require_login()
    sidebar_header()
    pm = get_project_manager()

    # لا مشروع مفتوح بالضرورة في هذه الصفحة؛ نطبّق ثيم آخر مشروع كان
    # مفتوحاً إن وُجد، وإلا الثيم الافتراضي، حتى تبقى الصفحة متسقة بصرياً
    fallback_theme = "ocean_dark"
    if st.session_state.get("db"):
        fallback_theme = st.session_state.db.get_settings().get("theme", "ocean_dark")
    apply_theme_css(fallback_theme)

    st.title("📁 المشاريع")

    with st.expander("➕ إنشاء مشروع جديد"):
        with st.form("new_project"):
            name = st.text_input("اسم المشروع")
            if st.form_submit_button("إنشاء"):
                r = pm.create(name)
                if r["ok"]:
                    # 🆕 تعبئة تلقائية بآخر محرك/مفتاح/نموذج AI استخدمه
                    # المستخدم — تُحفظ في project.db الخاص بهذا المشروع
                    # الجديد فقط كنقطة انطلاق، وقابلة للتعديل فوراً من
                    # صفحة الإعدادات دون أي قيد.
                    last = AuthManager().get_last_used_engine(st.session_state.user_id)
                    if last and last.get("engine_name"):
                        updates = {
                            "ai_engine": last["engine_name"],
                            "model": last.get("model", "") or "",
                        }
                        if last["engine_name"] != "ollama" and last.get("api_key"):
                            updates[f"api_key_{last['engine_name']}"] = last["api_key"]
                        r["db"].save_settings(updates)
                    st.success(f"تم إنشاء المشروع: {name}")
                    st.session_state.project_id = r["project_id"]
                    st.session_state.db = r["db"]
                    st.rerun()
                else:
                    st.error(r["error"])

    with st.expander("📥 استيراد مشروع من ملف"):
        uploaded = st.file_uploader("اختر ملف .db", type=["db"])
        if uploaded and st.button("استيراد"):
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                tmp.write(uploaded.getbuffer())
                tmp_path = Path(tmp.name)
            try:
                r = pm.import_project(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
            if r["ok"]:
                st.success("تم استيراد المشروع بنجاح")
                st.rerun()
            else:
                st.error(r["error"])

    st.divider()

    projects = pm.list_projects()
    if not projects:
        st.info("لا توجد مشاريع بعد. أنشئ مشروعاً جديداً للبدء.")
        return

    for p in projects:
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
            with c1:
                st.markdown(f"**{p['name']}**")
                st.caption(
                    f"📄 ملفات: {p['files_count']} | "
                    f"📝 تقارير: {p['reports_count']} | "
                    f"💾 {p['size_mb']} MB"
                )
            with c2:
                if st.button("فتح", key=f"open_{p['project_id']}", width='stretch'):
                    st.session_state.project_id = p["project_id"]
                    st.session_state.db = ProjectDB(st.session_state.user_id, p["project_id"])
                    st.success(f"تم فتح المشروع: {p['name']}")
                    st.rerun()
            with c3:
                if st.button("تصدير", key=f"export_{p['project_id']}", width='stretch'):
                    with temp_export_dir() as out_dir:
                        out_path = out_dir / f"{p['name']}.db"
                        r = pm.export(p["project_id"], out_path)
                        if r["ok"]:
                            offer_download(
                                out_path, "⬇️ تحميل الملف", f"{p['name']}.db",
                                key=f"dl_{p['project_id']}",
                            )
                        else:
                            st.error(r["error"])
            with c4:
                new_name = st.text_input(
                    "اسم جديد", key=f"rename_input_{p['project_id']}",
                    label_visibility="collapsed", placeholder="إعادة تسمية",
                )
                if new_name and st.button("حفظ", key=f"rename_btn_{p['project_id']}", width='stretch'):
                    r = pm.rename(p["project_id"], new_name)
                    if r["ok"]:
                        st.rerun()
                    else:
                        st.error(r["error"])
            with c5:
                confirm_key = f"confirm_delete_{p['project_id']}"
                if st.session_state.get(confirm_key):
                    if st.button("⚠️ تأكيد الحذف", key=f"confirm_{p['project_id']}", width='stretch'):
                        pm.delete(p["project_id"])
                        if st.session_state.get("project_id") == p["project_id"]:
                            st.session_state.project_id = None
                            st.session_state.db = None
                        st.rerun()
                else:
                    if st.button("🗑️ حذف", key=f"delete_{p['project_id']}", width='stretch'):
                        st.session_state[confirm_key] = True
                        st.rerun()
