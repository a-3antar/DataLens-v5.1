"""
core/file_manager.py
=====================
قراءة ملفات Excel و CSV مباشرة من الذاكرة (bytes) وتحويلها إلى
DataFrames — بدون أي نسخ دائم على قرص السيرفر.

تغيير مهم عن الإصدار السابق:
------------------------------
كنا سابقاً ننسخ كل ملف مرفوع إلى مجلد دائم داخل المشروع (files_dir)،
وهو ما تسبب في تراكم نسخ غير مستخدمة (يتيمة) بمرور الوقت وحاجة لنظام
تنظيف يدوي/تلقائي مستمر. البيانات النظيفة أصلاً تُحفظ في project.db
بعد المعالجة (عبر DataManager)، فلا حاجة إطلاقاً للاحتفاظ بالملف
الخام نفسه على السيرفر — نقرأه من الذاكرة، نستخرج منه ما نحتاج،
ثم لا يتبقى أي أثر له على القرص لا مؤقتاً ولا دائماً.

عند طلب "تحديث البيانات" لاحقاً، يُطلب من المستخدم إعادة اختيار
نفس الملف من جهازه من جديد (عبر ui/files.py)، بدل قراءة نسخة قديمة
مخزّنة على السيرفر.
"""

import io
import logging
from pathlib import Path

import pandas as pd

from config import ALLOWED_EXTENSIONS

logger = logging.getLogger(__name__)


class FileManager:
    """
    قراءة ملفات Excel/CSV من الذاكرة مباشرة، بدون أي كتابة على القرص.
    لا يحتفظ هذا الكائن بأي حالة دائمة على القرص — كل شيء عبر bytes.
    """

    def __init__(self, user_id: str, project_id: str):
        # أُبقيا للتوافق مع استدعاءات قديمة قد تمرر user_id/project_id،
        # لكنهما لم يعودا يُستخدمان لإنشاء أي مسار على القرص.
        self.user_id = user_id
        self.project_id = project_id

    # ──────────────────────────────────────────────────────────
    #  فحص الملف (بدون تحميل كامل البيانات بعد)
    # ──────────────────────────────────────────────────────────

    def inspect(self, file_bytes: bytes, original_name: str) -> dict:
        """
        فحص سريع للملف من الذاكرة: التحقق من الامتداد وإرجاع أسماء
        الشيتات إن كان Excel. لا يكتب أي شيء على القرص.
        """
        ext = Path(original_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            return {"ok": False, "error": f"امتداد غير مدعوم: {ext}"}
        if not file_bytes:
            return {"ok": False, "error": "الملف فارغ أو تعذرت قراءته"}

        sheets = []
        if ext in (".xlsx", ".xls"):
            try:
                sheets = pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names
            except Exception as e:
                return {"ok": False, "error": f"تعذر قراءة الملف: {e}"}

        return {
            "ok": True,
            "original_name": original_name,
            "extension": ext,
            "sheets": sheets,
        }

    # ──────────────────────────────────────────────────────────
    #  قراءة البيانات الفعلية
    # ──────────────────────────────────────────────────────────

    def read_bytes(self, file_bytes: bytes, extension: str,
                    sheet=None, columns=None) -> dict:
        """قراءة DataFrame مباشرة من bytes في الذاكرة."""
        ext = extension.lower()
        try:
            if ext == ".csv":
                df = self._read_csv_bytes(file_bytes)
            else:
                df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet or 0)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        if columns:
            missing = [c for c in columns if c not in df.columns]
            if missing:
                return {"ok": False, "error": f"أعمدة غير موجودة: {missing}"}
            df = df[columns].copy()

        return {"ok": True, "df": df}

    def _read_csv_bytes(self, file_bytes: bytes):
        for enc in ["utf-8", "utf-8-sig", "windows-1256", "latin-1"]:
            try:
                return pd.read_csv(io.BytesIO(file_bytes), encoding=enc)
            except UnicodeDecodeError:
                continue
        raise ValueError("encoding غير معروف")

    def get_columns_from_bytes(self, file_bytes: bytes, extension: str, sheet=None) -> dict:
        r = self.read_bytes(file_bytes, extension, sheet)
        if not r["ok"]:
            return r
        return {"ok": True, "columns": list(r["df"].columns)}
