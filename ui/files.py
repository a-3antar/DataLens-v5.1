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

🆕 نموذج رفع واحد + Enter للتحميل:
------------------------------------
اختيار الشيت/الأعمدة/اسم الجدول أصبح داخل st.form واحد — الضغط على
Enter وأنت في حقل "اسم الجدول" يُنفّذ "تحميل البيانات" مباشرة، بدل
الحاجة للضغط يدوياً على الزر. الـ file_uploader نفسه يبقى خارج
الـ form (كما تقتضي طبيعته في Streamlit) لأن نتائج inspect/columns
يجب أن تُحسب من محتوى الملف قبل بناء حقول النموذج التي تعتمد عليها.

🆕 بطاقة الملف الموحّدة + قائمة "⁝":
---------------------------------------
بدل عمودين منفصلين (معلومات الملف / زر حذف) يعرض كل ملف الآن اسمه
ومعلوماته المختصرة في سطر واحد، وأفعال الحذف والتحديث مجمّعة في
قائمة منسدلة واحدة (st.popover بأيقونة "⁝") — يتماشى مع نمط القوائم
والأزرار الموحّد في ui/common.py (زر ثانوي هادئ لكل فعل، وزر تحذيري
أحمر ثابت لتأكيد الحذف عبر بادئة المفتاح "danger_").
"""

from pathlib import Path

import streamlit as st

from ui.common import (
    apply_rtl, apply_theme_css, require_login, require_project, sidebar_header,
    notify, format_local_dt,
)
from core.file_manager import FileManager
from core.data_manager import DataManager


def _format_last_updated(f: dict, settings: dict) -> str | None:
    """
    آخر تحديث للملف = source_files.uploaded_at (راجع core/project_db.py)،
    مخزَّن UTC دائماً. نستخدم format_local_dt الموحّدة من common.py
    (بدل تحويل يدوي هنا) لتحويله لتوقيت settings["timezone"] المفضّل
    للمستخدم — بنفس منطق التحويل المستخدم في بقية التطبيق، فأي تعديل
    مستقبلي على سياسة المناطق الزمنية (مثلاً مصدر آخر غير settings)
    يُطبَّق من مكان واحد فقط.
    """
    raw = f.get("uploaded_at")
    if not raw:
        return None
    # fmt بفاصل "|" مؤقت لاستخراج الفترة (ص/م) العربية يدوياً، لأن
    # %p في strftime يعطي AM/PM إنجليزية بغض النظر عن لغة النظام.
    local_str = format_local_dt(raw, settings, fmt="%d/%m/%Y|%I:%M|%p")
    parts = local_str.split("|")
    if len(parts) != 3:
        return None
    date_part, time_part, ampm = parts
    period = "ص" if ampm.upper() == "AM" else "م"
    return f"آخر تحديث {time_part} {date_part} {period}"


def show_files():
    apply_rtl()
    require_login()
    db = require_project()
    settings = db.get_settings()
    apply_theme_css(settings)
    sidebar_header()

    fm = FileManager(st.session_state.user_id, st.session_state.project_id)
    dm = DataManager(db, fm)

    st.title("📄 إدارة الملفات")

    with st.expander("⬆️ رفع ملف جديد", expanded=True):
        _render_upload_form(fm, dm)

    st.divider()
    st.subheader("📚 الملفات المسجلة في المشروع")

    files = db.get_files()
    if not files:
        st.info("لا توجد ملفات محملة بعد.")
        return

    for f in files:
        _render_file_card(db, dm, f, settings)


def _render_upload_form(fm: FileManager, dm: DataManager) -> None:
    """
    اختيار الملف يبقى خارج الـ form (طبيعة file_uploader في
    Streamlit)، لكن بمجرد رفعه تُبنى حقول الشيت/الأعمدة/اسم الجدول
    داخل st.form واحد — فيصبح Enter في حقل اسم الجدول كافياً لتنفيذ
    "تحميل البيانات" دون الحاجة لضغط الزر يدوياً.
    """
    uploaded = st.file_uploader("اختر ملف Excel أو CSV", type=["xlsx", "xls", "csv"])
    if not uploaded:
        return

    file_bytes = uploaded.getvalue()
    info = fm.inspect(file_bytes, uploaded.name)

    if not info["ok"]:
        st.error(info["error"])
        return

    with st.form(key="upload_form", clear_on_submit=False):
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

        submitted = st.form_submit_button("✅ تحميل البيانات إلى المشروع", type="primary", width='stretch')

    if submitted:
        r = dm.load_file(
            file_bytes=file_bytes,
            extension=info["extension"],
            table_alias=table_alias,
            sheet=sheet,
            columns=selected_columns or None,
            original_name=uploaded.name,
        )
        if r["ok"]:
            notify(f"تم تحميل الجدول '{table_alias}' — {r['rows']} صف، {r['cols']} عمود", kind="success")
            st.rerun()
        else:
            st.error(r["error"])


def _render_file_card(db, dm: DataManager, f: dict, settings: dict) -> None:
    """
    بطاقة موحّدة لكل ملف: اسم الجدول ومعلوماته المختصرة في سطر، وقائمة
    أفعال واحدة (⁝) تجمع الحذف والتحديث بدل عمودين منفصلين — الشكل
    الموحّد لأزرار "ثانوية" مقابل الفعل التحذيري الأحمر الثابت يأتي
    تلقائياً من apply_theme_css في common.py.
    """
    with st.container(border=True):
        c1, c2 = st.columns([5, 1])

        with c1:
            header_c1, header_c2 = st.columns([3, 2])
            header_c1.markdown(f"**{f['table_alias']}**  ·  {f['original_name']}")
            last_updated = _format_last_updated(f, settings)
            if last_updated:
                header_c2.caption(last_updated)

            meta_bits = []
            if f.get("selected_sheet"):
                meta_bits.append(f"الشيت: {f['selected_sheet']}")
            meta_bits.append(f"الأعمدة: {', '.join(f['selected_columns']) or 'الكل'}")
            st.caption(" — ".join(meta_bits))

            # تنبيه مسبق بعدد العلاقات المرتبطة بهذا الجدول — حتى يعرف
            # المستخدم أثر الحذف قبل تأكيده، وليس بعده فقط.
            related_count = sum(
                1 for rel in db.get_relations()
                if rel["from_table"] == f["table_alias"] or rel["to_table"] == f["table_alias"]
            )
            if related_count:
                st.caption(f"⚠️ مرتبط بـ {related_count} علاقة — سيتم حذفها تلقائياً عند حذف هذا الجدول")

        with c2:
            has_popover = hasattr(st, "popover")
            menu_ctx = (
                st.popover("⁝", width='stretch') if has_popover
                else st.expander("⁝ خيارات", expanded=False)
            )
            with menu_ctx:
                _render_refresh_widget(dm, f)
                st.divider()
                _render_delete_widget(db, f)


def _render_delete_widget(db, f: dict):
    """
    حذف الملف بتأكيد ثنائي الضغط (نفس نمط ui/projects.py) — يمنع
    حذفاً عرضياً بضغطة واحدة، خصوصاً أن الحذف يُسقط أي علاقة مبنية
    على هذا الجدول تلقائياً معه. زر التأكيد النهائي بمفتاح "danger_"
    فيظهر بلون التحذير الأحمر الثابت المعرَّف في apply_theme_css.
    """
    confirm_key = f"confirm_delete_file_{f['id']}"
    if st.session_state.get(confirm_key):
        st.caption("⚠️ هذا الإجراء لا رجعة فيه.")
        if st.button("⚠️ تأكيد الحذف نهائياً", key=f"danger_confirm_del_file_{f['id']}", width='stretch'):
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
        if st.button("🗑️ حذف الجدول", key=f"del_file_{f['id']}", width='stretch'):
            st.session_state[confirm_key] = True
            st.rerun()


def _render_refresh_widget(dm: DataManager, f: dict):
    """
    تحديث الجدول من الملف الأصلي: يطلب من المستخدم إعادة اختيار نفس
    الملف من جهازه. لو ضغط "تحديث الآن" بدون اختيار ملف، تظهر رسالة
    الخطأ المطلوبة بدل تحديث فاشل أو صامت.
    """
    st.caption(f"🔄 تحديث من الملف الأصلي: **{f['original_name']}**")
    refreshed = st.file_uploader(
        "اختر الملف", type=["xlsx", "xls", "csv"],
        key=f"refresh_uploader_{f['id']}", label_visibility="collapsed",
    )
    if st.button("تحديث الآن", key=f"refresh_btn_{f['id']}", width='stretch'):
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
                notify(f"تم التحديث — {r['rows']} صف", kind="success")
                st.rerun()
            else:
                st.error(r["error"])
