"""
core/data_manager.py
====================
تنظيف ومعالجة البيانات وحفظها في project.db.
يعمل فوق FileManager و ProjectDB.

الإصلاحات:
- load_file يستدعي db.add_file() لتسجيل الملف في source_files
  حتى يتمكن QueryEngine من تحميل الجداول عبر get_files()
- fill_nulls يفحص صحة strategy قبل فحص null_count == 0
"""

import logging
from typing import Optional

import pandas as pd

from core.file_manager import FileManager
from core.project_db   import ProjectDB

logger = logging.getLogger(__name__)


class DataManager:
    """
    تنظيف البيانات وحفظها في project.db.

    الاستخدام:
        dm = DataManager(db, file_manager)
        dm.load_file("f001", ".xlsx", "sales", sheet="Sheet1")
        dm.change_dtype("sales", "amount", "float")
        dm.fill_nulls("sales", "amount", "mean")
    """

    # الـ strategies الصحيحة — نعرفها هنا لإعادة الاستخدام
    VALID_STRATEGIES = {"mean", "median", "mode", "zero", "value"}

    def __init__(self, db: ProjectDB, fm: FileManager):
        self.db = db
        self.fm = fm

    # ──────────────────────────────────────────────────────────
    #  تحميل ملف وحفظ البيانات النظيفة
    # ──────────────────────────────────────────────────────────

    def load_file(
        self,
        file_bytes : bytes,
        extension  : str,
        table_alias: str,
        sheet      : Optional[str] = None,
        columns    : Optional[list] = None,
        original_name: str = "",
        file_id    : Optional[str] = None,
    ) -> dict:
        """
        قراءة ملف من الذاكرة (بدون أي نسخة على القرص) + تنظيف أولي +
        حفظ في project.db. يُستخدم لكل من الرفع الأول وإعادة التحديث
        (بتمرير نفس file_id في حالة التحديث لتفادي تسجيل مكرر).

        يرجع: {"ok": True, "rows": N, "cols": M}
        """
        result = self.fm.read_bytes(file_bytes, extension, sheet, columns)
        if not result["ok"]:
            return result

        df = self._basic_clean(result["df"])

        try:
            self.db.save_clean_data(table_alias, df)
        except Exception as e:
            logger.error("load_file save_clean_data error: %s", e)
            return {"ok": False, "error": str(e)}

        import uuid as _uuid
        record_id = file_id or str(_uuid.uuid4())
        try:
            self.db.add_file(
                file_id         = record_id,
                original_name   = original_name or f"{table_alias}{extension}",
                table_alias     = table_alias,
                selected_sheet  = sheet,
                selected_columns= columns or list(df.columns),
            )
        except Exception as e:
            logger.debug("add_file skipped (already exists?): %s", e)

        logger.info("load_file done: '%s' — %d rows, %d cols", table_alias, len(df), len(df.columns))
        return {"ok": True, "rows": len(df), "cols": len(df.columns), "file_id": record_id}

    def _basic_clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """تنظيف أولي تلقائي لكل DataFrame."""
        df = df.dropna(how="all")
        df = df.dropna(axis=1, how="all")
        df.columns = [
            str(c).strip().replace(" ", "_").lower()
            for c in df.columns
        ]
        return df.reset_index(drop=True)

    # ──────────────────────────────────────────────────────────
    #  تغيير نوع البيانات
    # ──────────────────────────────────────────────────────────

    def change_dtype(self, table_alias: str, column: str, new_type: str) -> dict:
        """
        تغيير نوع عمود وحفظ التعديل.
        الأنواع المدعومة: int | float | str | date | bool
        """
        df = self.db.get_clean_data(table_alias)
        if df is None:
            return {"ok": False, "error": f"الجدول '{table_alias}' غير موجود"}
        if column not in df.columns:
            return {"ok": False, "error": f"العمود '{column}' غير موجود"}

        try:
            if new_type == "int":
                df[column] = pd.to_numeric(df[column], errors="coerce").astype("Int64")
            elif new_type == "float":
                df[column] = pd.to_numeric(df[column], errors="coerce").astype(float)
            elif new_type == "str":
                df[column] = df[column].astype(str)
            elif new_type == "date":
                # نُبقي النوع datetime64 فعلياً (وليس نصاً منسقاً) حتى
                # تعمل دوال التاريخ في DuckDB (مثل DATE_TRUNC) مباشرة
                # بدون الحاجة لأي CAST يدوي من الذكاء الاصطناعي.
                df[column] = pd.to_datetime(df[column], errors="coerce")
            elif new_type == "bool":
                df[column] = df[column].astype(bool)
            else:
                return {"ok": False, "error": f"نوع غير مدعوم: {new_type}"}

            self.db.save_clean_data(table_alias, df)
            logger.info("dtype changed: %s.%s -> %s", table_alias, column, new_type)
            return {"ok": True}

        except Exception as e:
            logger.error("change_dtype error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  تصفية الصفوف
    # ──────────────────────────────────────────────────────────

    def filter_rows(
        self,
        table_alias: str,
        column     : str,
        operator   : str,
        value      : object,
    ) -> dict:
        """
        تصفية الصفوف وحفظ النتيجة.
        العمليات المدعومة: == | != | > | < | >= | <= | contains
        """
        df = self.db.get_clean_data(table_alias)
        if df is None:
            return {"ok": False, "error": f"الجدول '{table_alias}' غير موجود"}
        if column not in df.columns:
            return {"ok": False, "error": f"العمود '{column}' غير موجود"}

        before = len(df)
        try:
            if operator == "==":
                df = df[df[column] == value]
            elif operator == "!=":
                df = df[df[column] != value]
            elif operator == ">":
                df = df[df[column] > value]
            elif operator == "<":
                df = df[df[column] < value]
            elif operator == ">=":
                df = df[df[column] >= value]
            elif operator == "<=":
                df = df[df[column] <= value]
            elif operator == "contains":
                df = df[df[column].astype(str).str.contains(str(value), na=False)]
            else:
                return {"ok": False, "error": f"عملية غير مدعومة: {operator}"}

            df = df.reset_index(drop=True)
            self.db.save_clean_data(table_alias, df)
            logger.info("filter_rows: %s — %d -> %d rows", table_alias, before, len(df))
            return {"ok": True, "before": before, "after": len(df)}

        except Exception as e:
            logger.error("filter_rows error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  تنظيف النصوص
    # ──────────────────────────────────────────────────────────

    def strip_text(self, table_alias: str, column: str) -> dict:
        return self._text_op(table_alias, column, "strip")

    def capitalize_text(self, table_alias: str, column: str) -> dict:
        return self._text_op(table_alias, column, "capitalize")

    def uppercase_text(self, table_alias: str, column: str) -> dict:
        return self._text_op(table_alias, column, "upper")

    def lowercase_text(self, table_alias: str, column: str) -> dict:
        return self._text_op(table_alias, column, "lower")

    def _text_op(self, table_alias: str, column: str, op: str) -> dict:
        df = self.db.get_clean_data(table_alias)
        if df is None:
            return {"ok": False, "error": f"الجدول '{table_alias}' غير موجود"}
        if column not in df.columns:
            return {"ok": False, "error": f"العمود '{column}' غير موجود"}
        try:
            s = df[column].astype(str)
            if op == "strip":
                df[column] = s.str.strip()
            elif op == "capitalize":
                df[column] = s.str.capitalize()
            elif op == "upper":
                df[column] = s.str.upper()
            elif op == "lower":
                df[column] = s.str.lower()
            self.db.save_clean_data(table_alias, df)
            return {"ok": True}
        except Exception as e:
            logger.error("text_op error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  معالجة القيم الفارغة
    # ──────────────────────────────────────────────────────────

    def fill_nulls(
        self,
        table_alias: str,
        column     : str,
        strategy   : str,
        value      : object = None,
    ) -> dict:
        """
        معالجة القيم الفارغة.
        الـ strategies المدعومة: mean | median | mode | zero | value

        ✅ الإصلاح: نفحص صحة strategy أولاً قبل فحص null_count
        حتى لا يمر strategy خاطئ عندما تكون nulls == 0
        """
        # ✅ فحص strategy أولاً — قبل أي شيء آخر
        if strategy not in self.VALID_STRATEGIES:
            return {"ok": False, "error": f"strategy غير مدعوم: {strategy}"}

        if strategy == "value" and value is None:
            return {"ok": False, "error": "يجب تحديد value مع strategy='value'"}

        df = self.db.get_clean_data(table_alias)
        if df is None:
            return {"ok": False, "error": f"الجدول '{table_alias}' غير موجود"}
        if column not in df.columns:
            return {"ok": False, "error": f"العمود '{column}' غير موجود"}

        null_count = int(df[column].isna().sum())
        if null_count == 0:
            return {"ok": True, "filled": 0}

        try:
            if strategy == "mean":
                fill_val = df[column].mean()
            elif strategy == "median":
                fill_val = df[column].median()
            elif strategy == "mode":
                fill_val = df[column].mode()[0]
            elif strategy == "zero":
                fill_val = 0
            else:  # value
                fill_val = value

            df[column] = df[column].fillna(fill_val)
            self.db.save_clean_data(table_alias, df)
            logger.info(
                "fill_nulls: %s.%s — %d nulls filled (%s)",
                table_alias, column, null_count, strategy
            )
            return {"ok": True, "filled": null_count}

        except Exception as e:
            logger.error("fill_nulls error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  معلومات الجدول
    # ──────────────────────────────────────────────────────────

    def get_preview(self, table_alias: str, rows: int = 10) -> dict:
        """أول N صفوف من الجدول للمعاينة."""
        df = self.db.get_clean_data(table_alias)
        if df is None:
            return {"ok": False, "error": f"الجدول '{table_alias}' غير موجود"}
        return {
            "ok"     : True,
            "data"   : df.head(rows).to_dict(orient="records"),
            "columns": list(df.columns),
            "total"  : len(df),
        }

    def get_stats(self, table_alias: str, column: str) -> dict:
        """إحصاءات أساسية لعمود."""
        df = self.db.get_clean_data(table_alias)
        if df is None:
            return {"ok": False, "error": f"الجدول '{table_alias}' غير موجود"}
        if column not in df.columns:
            return {"ok": False, "error": f"العمود '{column}' غير موجود"}
        try:
            s = pd.to_numeric(df[column], errors="coerce")
            n = s.count()
            return {
                "ok"    : True,
                "count" : int(n),
                "nulls" : int(s.isna().sum()),
                "min"   : float(s.min())    if n > 0 else None,
                "max"   : float(s.max())    if n > 0 else None,
                "mean"  : float(s.mean())   if n > 0 else None,
                "median": float(s.median()) if n > 0 else None,
            }
        except Exception as e:
            logger.error("get_stats error: %s", e)
            return {"ok": False, "error": str(e)}

    def refresh_from_bytes(
        self,
        file_id    : str,
        file_bytes : bytes,
        extension  : str,
        table_alias: str,
        original_name: str = "",
        sheet      : Optional[str] = None,
        columns    : Optional[list] = None,
    ) -> dict:
        """
        إعادة تحميل البيانات: يُطلب من المستخدم إعادة اختيار نفس الملف
        من جهازه (لا يُحتفظ بأي نسخة سابقة على السيرفر)، ثم تُحفظ
        البيانات الجديدة بنفس file_id لتحديث السجل بدل تكراره.
        """
        logger.info("Refreshing table '%s' from freshly re-uploaded file", table_alias)
        return self.load_file(
            file_bytes, extension, table_alias,
            sheet=sheet, columns=columns,
            original_name=original_name, file_id=file_id,
        )
