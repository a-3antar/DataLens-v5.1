"""
ui/projects.py
==============
إنشاء / فتح / إعادة تسمية / حذف / تصدير / استيراد المشاريع.

عند إنشاء مشروع جديد، تُعبَّأ إعدادات محرك AI (والمفتاح) تلقائياً
بآخر محرك+مفتاح+نموذج استخدمه المستخدم (من users.db عبر AuthManager)
كقيمة افتراضية مبدئية فقط — تُحفظ داخل project.db الخاص بالمشروع
الجديد تماماً كأي إعداد آخر، ويستطيع المستخدم تغييرها أو حتى استخدام
محرك مختلف تماماً لهذا المشروع بدون أي قيد.

🆕 عرض شبكي (بطاقات) بدل الصفوف النصية:
------------------------------------------------------------------
كل مشروع الآن بطاقة (container بحدود) ضمن شبكة من 3 أعمدة بدل صف
أفقي بأربعة أزرار متجاورة. الفعل الرئيسي ("فتح") زر primary بارز
داخل البطاقة، وبقية الإجراءات (تصدير / إعادة تسمية / حذف) مجمّعة
داخل قائمة "⁝" منسدلة واحدة (st.popover) — نفس فلسفة تقليل الأزرار
المتنافسة بصرياً المتّبعة في نظام التصميم الموحّد (راجع ui/common.py).

🆕 الانتقال التلقائي بعد فتح/إنشاء مشروع:
------------------------------------------------------------------
بدل البقاء في صفحة المشاريع بعد فتح مشروع أو إنشائه، نضبط
st.session_state["_jump_to_page"] (تُقرأ من main.py عبر _JUMP_TARGETS)
لتوجيه المستخدم مباشرة:
  • فتح مشروع موجود → "📊 لوحات المعلومات" (المشروع لديه بيانات
    وتحليلات جاهزة، فالانتقال المباشر يوفر خطوة).
  • إنشاء مشروع جديد → "📄 الملفات" بدل لوحات المعلومات، لأن مشروعاً
    جديداً لا يملك أي بيانات بعد؛ عرض لوحات فارغة لا فائدة منه، بينما
    صفحة الملفات هي الخطوة المنطقية التالية فعلياً (رفع أول ملف
    بيانات). لو تغيّر هذا التفضيل مستقبلاً يكفي تبديل القيمة أدناه
    إلى "dashboards".
"""

from pathlib import Path
import tempfile

import streamlit as st

from ui.common import (
    apply_rtl, apply_theme_css, require_login, sidebar_header, get_project_manager,
    temp_export_dir, offer_download, notify,
)
from core.project_db import ProjectDB
from core.auth import AuthManager

_CARDS_PER_ROW = 3


def show_projects():
    apply_rtl()
    require_login()
    sidebar_header()
    pm = get_project_manager()

    # لا مشروع مفتوح بالضرورة في هذه الصفحة؛ نطبّق ثيم آخر مشروع كان
    # مفتوحاً إن وُجد، وإلا الثيم الافتراضي، حتى تبقى الصفحة متسقة بصرياً
    fallback_settings = {}
    if st.session_state.get("db"):
        fallback_settings = st.session_state.db.get_settings()
    apply_theme_css(fallback_settings or {"theme": "ocean_dark"})

    st.title("📁 المشاريع")

    with st.expander("➕ إنشاء مشروع جديد"):
        _render_create_form(pm)

    with st.expander("📥 استيراد مشروع من ملف"):
        _render_import_form(pm)

    st.divider()

    projects = pm.list_projects()
    if not projects:
        st.info("لا توجد مشاريع بعد. أنشئ مشروعاً جديداً للبدء.")
        return

    for row_start in range(0, len(projects), _CARDS_PER_ROW):
        row = projects[row_start:row_start + _CARDS_PER_ROW]
        cols = st.columns(_CARDS_PER_ROW, gap="medium")
        for col, p in zip(cols, row):
            with col:
                _render_project_card(pm, p)


def _render_create_form(pm):
    """
    نموذج إنشاء مشروع جديد — st.form كامل بحيث Enter في حقل الاسم
    يُنشئ المشروع مباشرة (نفس تأثير زر "إنشاء").
    """
    with st.form("new_project"):
        name = st.text_input("اسم المشروع", placeholder="مثال: تحليل مبيعات ٢٠٢٦")
        submitted = st.form_submit_button("✅ إنشاء", type="primary", width="stretch")

    if submitted:
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

            st.session_state.project_id = r["project_id"]
            st.session_state.db = r["db"]
            # مشروع جديد بلا بيانات بعد → توجيه لصفحة الملفات بدل
            # لوحات معلومات فارغة (راجع الشرح أعلى الملف).
            st.session_state["_jump_to_page"] = "files"
            notify(f"تم إنشاء المشروع: {name}", kind="success")
            st.rerun()
        else:
            st.error(r["error"])


def _render_import_form(pm):
    """
    نموذج استيراد مشروع من ملف .db — st.form كامل يشمل رفع الملف
    وزر الإرسال معاً.
    """
    with st.form("import_project"):
        uploaded = st.file_uploader("اختر ملف .db", type=["db"])
        submitted = st.form_submit_button("📥 استيراد", type="primary", width="stretch")

    if submitted:
        if not uploaded:
            st.error("الرجاء اختيار ملف .db أولاً")
            return
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp.write(uploaded.getbuffer())
            tmp_path = Path(tmp.name)
        try:
            r = pm.import_project(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        if r["ok"]:
            notify("تم استيراد المشروع بنجاح", kind="success")
            st.rerun()
        else:
            st.error(r["error"])


def _render_project_card(pm, p: dict):
    """بطاقة مشروع واحدة: اسم + إحصائيات + فتح (رئيسي) + قائمة ⁝ للإجراءات الأخرى."""
    with st.container(border=True):
        st.markdown(f"**{p['name']}**")
        st.caption(
            f"📄 {p['files_count']} ملفات · "
            f"📝 {p['reports_count']} تقارير · "
            f"💾 {p['size_mb']} MB"
        )

        c_open, c_menu = st.columns([4, 1])
        with c_open:
            if st.button("📂 فتح", key=f"open_{p['project_id']}", type="primary", width="stretch"):
                st.session_state.project_id = p["project_id"]
                st.session_state.db = ProjectDB(st.session_state.user_id, p["project_id"])
                # مشروع موجود مسبقاً وله بيانات على الأرجح → مباشرة للوحات المعلومات.
                st.session_state["_jump_to_page"] = "dashboards"
                notify(f"تم فتح المشروع: {p['name']}", kind="success")
                st.rerun()

        with c_menu:
            with st.popover("⁝", width="stretch"):
                _render_project_actions_menu(pm, p)


def _render_project_actions_menu(pm, p: dict):
    """محتوى قائمة "⁝": تصدير / إعادة تسمية / حذف — مجمّعة بدل أزرار متجاورة."""
    pid = p["project_id"]

    

    st.markdown("**إعادة تسمية**")
    with st.form(f"rename_form_{pid}"):
        new_name = st.text_input(
            "اسم جديد", key=f"rename_input_{pid}",
            label_visibility="collapsed", placeholder="اسم جديد للمشروع",
        )
        rename_submitted = st.form_submit_button("✏️ حفظ الاسم الجديد", width="stretch")
    if rename_submitted:
        if not new_name.strip():
            st.error("الرجاء إدخال اسم جديد")
        else:
            r = pm.rename(pid, new_name)
            if r["ok"]:
                notify("تم تحديث اسم المشروع", kind="success")
                st.rerun()
            else:
                st.error(r["error"])


    if st.button("⬇️ تصدير المشروع", key=f"export_{pid}", width="stretch"):
        with temp_export_dir() as out_dir:
            out_path = out_dir / f"{p['name']}.db"
            r = pm.export(pid, out_path)
            if r["ok"]:
                offer_download(
                    out_path, "⬇️ تحميل الملف", f"{p['name']}.db",
                    key=f"dl_{pid}",
                )
            else:
                st.error(r["error"])

    confirm_key = f"confirm_delete_{pid}"
    if st.session_state.get(confirm_key):
        st.warning("هذا الإجراء لا رجعة فيه.")
        if st.button("⚠️ تأكيد الحذف نهائياً", key=f"danger_confirm_{pid}", width="stretch"):
            pm.delete(pid)
            if st.session_state.get("project_id") == pid:
                st.session_state.project_id = None
                st.session_state.db = None
            notify(f"تم حذف المشروع: {p['name']}", kind="success")
            st.rerun()
    else:
        if st.button("🗑️ حذف المشروع", key=f"danger_delete_{pid}", width="stretch"):
            st.session_state[confirm_key] = True
            st.rerun()
