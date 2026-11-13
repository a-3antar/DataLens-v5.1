"""
ui/files.py
===========
رفع ملفات Excel/CSV، اختيار الشيت والأعمدة، وتحميلها كجداول في المشروع،
وإدارة الملفات المسجَّلة (تحديث من الملف الأصلي / حذف).

سياسة عدم التخزين:
--------------------
لا يُحتفظ بالملف الخام (Excel/CSV) على السيرفر إطلاقاً — يُقرأ من
الذاكرة مباشرة عند الرفع، وتُحفظ البيانات النظيفة فقط في project.db.
عند طلب "تحديث البيانات" لاحقاً، يُطلب من المستخدم إعادة اختيار نفس
الملف من جهازه من جديد بدل قراءة نسخة قديمة على السيرفر — وإن لم
يُعِد اختياره، تظهر رسالة خطأ واضحة بدل تحديث صامت أو فاشل.

نموذج الرفع:
--------------
- file_uploader واختيار الشيت يبقيان خارج st.form عمداً: داخل النموذج
  لا يُعاد تشغيل الصفحة عند تغيير أي عنصر، فكانت قائمة الأعمدة تبقى
  محسوبة للشيت الافتراضي (الأول) حتى بعد اختيار شيت آخر. خارجه، تغيير
  الشيت يُعيد الرسم فتُحسب أعمدة الشيت المختار فعلاً.
- حقول الأعمدة واسم الجدول داخل st.form واحد، فالضغط على Enter في
  حقل "اسم الجدول" يُنفّذ "تحميل البيانات" مباشرة.
- نتيجة الفحص وأعمدة كل شيت تُخزَّن مؤقتاً في session_state (مرتبطة
  بتوقيع الملف المرفوع) حتى لا يُعاد الفحص عند كل إعادة رسم.

🆕 الفحص السريع لملفات xlsx الكبيرة:
------------------------------------------
قراءة أسماء الشيتات والأعمدة عبر pandas تحمّل الشيت كاملاً (ملف 45MB
قد يستغرق دقائق) رغم أن المطلوب أسماء فقط. لذلك لملفات .xlsx تُستخدم
openpyxl في وضع read_only: أسماء الشيتات من workbook.xml مباشرة،
وأسماء الأعمدة من الصف الأول فقط في الشيت المختار (iter_rows بحد
أقصى صف واحد، فلا تُقرأ بقية الصفوف). أي فشل (صيغة غير xlsx، ملف
تالف...) يرجع تلقائياً إلى FileManager.inspect/get_columns_from_bytes
كما كانا، فلا يتغير سلوك .xls و.csv. القراءة الفعلية الكاملة للبيانات
تحدث مرة واحدة فقط عند الضغط على "تحميل البيانات" (DataManager.load_file).

تأكيد الحذف + إشعار Cascade على العلاقات:
------------------------------------------------
حذف ملف يتطلب ضغطتين (نفس نمط تأكيد حذف المشروع في ui/projects.py)،
لأن حذف الجدول قد يُسقط علاقات مبنية عليه في صفحة "تنظيف البيانات".
بعد التنفيذ يُعرض عدد العلاقات التي حُذفت تبعاً (إن وُجدت).

بطاقة الملف الموحّدة + قائمة "⁝":
---------------------------------------
كل ملف يعرض اسمه ومعلوماته المختصرة في سطر، وأفعال الحذف والتحديث
مجمّعة في قائمة منسدلة واحدة (st.popover بأيقونة "⁝") — يتماشى مع نمط
القوائم والأزرار الموحّد في ui/common.py (زر تحذيري أحمر ثابت لتأكيد
الحذف عبر بادئة المفتاح "danger_").
"""

import io
import logging
from pathlib import Path

import streamlit as st

from ui.common import (
    apply_rtl, apply_theme_css, require_login, require_project, sidebar_header,
    notify, format_local_dt,
)
from core.file_manager import FileManager
from core.data_manager import DataManager
from core.dashboard_views import DashboardViews

logger = logging.getLogger(__name__)

# مفتاح التخزين المؤقت لنتائج فحص الملف المرفوع (الشيتات + أعمدة كل شيت)
_UPLOAD_CACHE_KEY = "_upload_inspect_cache"


def _format_last_updated(f: dict, settings: dict) -> str | None:
    """
    آخر تحديث للملف = source_files.uploaded_at (راجع core/project_db.py)،
    مخزَّن UTC دائماً. نستخدم format_local_dt الموحّدة من common.py
    (بدل تحويل يدوي هنا) لتحويله لتوقيت settings["timezone"] المفضّل
    للمستخدم — بنفس منطق التحويل المستخدم في بقية التطبيق.
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


# ──────────────────────────────────────────────────────────────
#  🆕 فحص سريع لملفات xlsx (openpyxl read_only)
# ──────────────────────────────────────────────────────────────

def _dedupe_headers(names: list[str]) -> list[str]:
    """
    معالجة تكرار أسماء الأعمدة بنفس أسلوب pandas ("x" ثم "x.1" ثم
    "x.2")، حتى تتطابق الأسماء المعروضة هنا مع ما تقرأه pandas فعلياً
    عند التحميل (وإلا فشل اختيار العمود المكرر في read_bytes).
    """
    seen: dict[str, int] = {}
    result = []
    for name in names:
        if name in seen:
            seen[name] += 1
            result.append(f"{name}.{seen[name]}")
        else:
            seen[name] = 0
            result.append(name)
    return result


def _fast_inspect(fm: FileManager, file_bytes: bytes, file_name: str) -> dict:
    """
    فحص الملف المرفوع: أسماء الشيتات + الامتداد، بنفس شكل fm.inspect:
    {"ok", "extension", "sheets", "error"}.
    لـ .xlsx: openpyxl read_only تقرأ أسماء الشيتات من workbook.xml
    فقط (لا تحمّل أي صف). غير ذلك، أو عند أي فشل: fm.inspect الأصلية.
    """
    ext = Path(file_name).suffix.lower()
    if ext == ".xlsx":
        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            try:
                sheets = list(wb.sheetnames)
            finally:
                wb.close()
            return {"ok": True, "extension": ext, "sheets": sheets}
        except Exception as e:
            logger.warning("fast inspect failed (%s) — falling back to FileManager.inspect", e)
    return fm.inspect(file_bytes, file_name)


def _fast_columns(fm: FileManager, file_bytes: bytes, extension: str, sheet) -> dict:
    """
    أسماء أعمدة شيت واحد بنفس شكل fm.get_columns_from_bytes:
    {"ok", "columns", "error"}.
    لـ .xlsx: يُقرأ الصف الأول فقط من الشيت المختار (iter_rows بحد أقصى
    صف واحد) فلا تُحمَّل بقية الصفوف — الفرق بين ثوانٍ ودقائق على ملف
    كبير. الخلايا الفارغة في صف العناوين تُتجاهل (عمود بلا اسم لا
    يمكن اختياره، وأعمدة الملف الفارغة كلياً تُحذف أصلاً في _basic_clean).
    غير ذلك، أو عند أي فشل: fm.get_columns_from_bytes الأصلية.
    """
    if extension == ".xlsx":
        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            try:
                ws = wb[sheet] if sheet else wb.worksheets[0]
                header = next(
                    ws.iter_rows(min_row=1, max_row=1, values_only=True), None,
                )
            finally:
                wb.close()

            if header:
                names = [str(v).strip() for v in header if v is not None and str(v).strip()]
                if names:
                    return {"ok": True, "columns": _dedupe_headers(names)}
        except Exception as e:
            logger.warning("fast columns failed (%s) — falling back to FileManager", e)
    return fm.get_columns_from_bytes(file_bytes, extension, sheet)


# ──────────────────────────────────────────────────────────────
#  رفع ملف جديد
# ──────────────────────────────────────────────────────────────

def _get_upload_cache(uploaded) -> dict:
    """
    تخزين مؤقت لفحص الملف المرفوع الحالي: توقيعه (الاسم + الحجم) يحدد
    صلاحية الكاش، فعند رفع ملف مختلف يُبنى كاش جديد تلقائياً.
    الشكل: {"sig": ..., "info": نتيجة الفحص, "columns": {sheet: نتيجة}}
    """
    sig = (uploaded.name, uploaded.size)
    cache = st.session_state.get(_UPLOAD_CACHE_KEY)
    if not cache or cache.get("sig") != sig:
        cache = {"sig": sig, "info": None, "columns": {}}
        st.session_state[_UPLOAD_CACHE_KEY] = cache
    return cache


def _render_upload_form(fm: FileManager, dm: DataManager) -> None:
    """
    اختيار الملف والشيت خارج الـ form (ليُعاد حساب الأعمدة عند تغيير
    الشيت)، ثم الأعمدة واسم الجدول داخل st.form واحد — فيصبح Enter في
    حقل اسم الجدول كافياً لتنفيذ "تحميل البيانات".
    """
    uploaded = st.file_uploader("اختر ملف Excel أو CSV", type=["xlsx", "xls", "csv"])
    if not uploaded:
        return

    file_bytes = uploaded.getvalue()
    cache = _get_upload_cache(uploaded)

    if cache["info"] is None:
        with st.spinner("⏳ جاري قراءة الملف..."):
            cache["info"] = _fast_inspect(fm, file_bytes, uploaded.name)
    info = cache["info"]

    if not info["ok"]:
        st.error(info["error"])
        return

    # اختيار الشيت خارج النموذج: تغييره يُعيد الرسم فتُحسب أعمدته
    sheet = None
    if info["sheets"]:
        sheet = st.selectbox("اختر الشيت", info["sheets"], key="upload_sheet_choice")

    sheet_key = sheet or "__single__"
    if sheet_key not in cache["columns"]:
        with st.spinner("⏳ جاري قراءة أعمدة الشيت..."):
            cache["columns"][sheet_key] = _fast_columns(fm, file_bytes, info["extension"], sheet)
    cols_result = cache["columns"][sheet_key]

    with st.form(key="upload_form", clear_on_submit=False):
        selected_columns = None
        if cols_result["ok"]:
            # المفتاح مرتبط باسم الشيت حتى لا يبقى اختيار قديم من شيت آخر
            selected_columns = st.multiselect(
                "اختر الأعمدة (اتركها فارغة لاختيار الكل)",
                cols_result["columns"],
                key=f"upload_cols_{sheet_key}",
            )
        else:
            st.warning(f"تعذّرت قراءة أعمدة هذا الشيت: {cols_result.get('error')}")

        table_alias = st.text_input(
            "اسم الجدول (بالإنجليزية، بدون مسافات)",
            value=Path(uploaded.name).stem.strip().replace(" ", "_").lower(),
        )

        submitted = st.form_submit_button("✅ تحميل البيانات إلى المشروع", type="primary", width='stretch')

    if submitted:
        with st.spinner("⏳ جاري تحميل البيانات (قد يستغرق وقتاً مع الملفات الكبيرة)..."):
            r = dm.load_file(
                file_bytes=file_bytes,
                extension=info["extension"],
                table_alias=table_alias,
                sheet=sheet,
                columns=selected_columns or None,
                original_name=uploaded.name,
            )
        if r["ok"]:
            st.session_state.pop(_UPLOAD_CACHE_KEY, None)
            notify(f"تم تحميل الجدول '{table_alias}' — {r['rows']} صف، {r['cols']} عمود", kind="success")
            st.rerun()
        else:
            st.error(r["error"])


# ──────────────────────────────────────────────────────────────
#  بطاقات الملفات المسجَّلة
# ──────────────────────────────────────────────────────────────

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
    على هذا الجدول تلقائياً معه، وتُحذف معه views اللوحات المبنية عليه.
    زر التأكيد النهائي بمفتاح "danger_" فيظهر بلون التحذير الأحمر الثابت.
    """
    confirm_key = f"confirm_delete_file_{f['id']}"
    if st.session_state.get(confirm_key):
        st.caption("⚠️ هذا الإجراء لا رجعة فيه.")
        if st.button("⚠️ تأكيد الحذف نهائياً", key=f"danger_confirm_del_file_{f['id']}", width='stretch'):
            # حذف views اللوحات المبنية على هذا الجدول قبل حذفه
            DashboardViews(db).drop_for_table(f["table_alias"])
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
            with st.spinner("⏳ جاري تحديث البيانات..."):
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