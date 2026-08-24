"""
exporters/report_manager.py
============================
إدارة التقارير: إنشاء / تعديل / حذف / قراءة.
يعمل فوق ProjectDB ولا يتعامل مع الـ db مباشرة.

بلوكات التقرير المدعومة:
  paragraph : نص Markdown
  table     : نتيجة جدول  {"result_id": "...", "data": [...], "columns": [...]}
  chart     : نتيجة رسم   {"result_id": "...", "chart_type": "bar", "data": [...]}
  gauge     : مقياس        {"result_id": "...", "current": N, "min": N, "max": N}
  kpi       : مؤشر         {"result_id": "...", "actual": N, "target": N, "label": "..."}
"""

import uuid
import logging
import datetime as _dt
from typing import Optional

import pandas as pd
import numpy as np

from core.project_db import ProjectDB

logger = logging.getLogger(__name__)


def _json_safe(value):
    """
    تحويل قيمة واحدة إلى نوع قابل للتسلسل عبر json.dumps مباشرة.
    يعالج أنواع pandas/numpy الشائعة (Timestamp، NaT، int64...) التي
    تظهر عند تمرير بيانات جاءت من DuckDB/pandas إلى بلوك تقرير، والتي
    كانت تُسبب "Object of type Timestamp is not JSON serializable".
    """
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, _dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        f = float(value)
        return None if pd.isna(f) else f
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and pd.isna(value):
        return None
    if value is pd.NaT:
        return None
    return value


def _sanitize_content(content: dict) -> dict:
    """تطبيق _json_safe على كل قيمة داخل content، بشكل متكرر (dict/list متداخلة)."""
    def walk(obj):
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        return _json_safe(obj)
    return walk(content)


class ReportManager:
    """
    إدارة التقارير لمشروع محدد.

    الاستخدام:
        rm = ReportManager(db)
        report_id = rm.create("تقرير المبيعات")
        rm.add_paragraph(report_id, "## ملخص تنفيذي")
        rm.add_table(report_id, result_id="c001", data=[...], columns=[...])
        blocks = rm.get_blocks(report_id)
    """

    VALID_BLOCK_TYPES = {"paragraph", "table", "chart", "gauge", "kpi"}

    def __init__(self, db: ProjectDB):
        self.db = db

    # ──────────────────────────────────────────────────────────
    #  إنشاء وحذف التقارير
    # ──────────────────────────────────────────────────────────

    def create(self, title: str) -> dict:
        """
        إنشاء تقرير جديد.
        يرجع: {"ok": True, "report_id": "..."} أو {"ok": False, "error": "..."}
        """
        title = title.strip()
        if not title:
            return {"ok": False, "error": "عنوان التقرير مطلوب"}
        try:
            report_id = str(uuid.uuid4())
            self.db.create_report(report_id, title)
            logger.info("Report created: '%s' (%s)", title, report_id)
            return {"ok": True, "report_id": report_id}
        except Exception as e:
            logger.error("create report error: %s", e)
            return {"ok": False, "error": str(e)}

    def delete(self, report_id: str) -> dict:
        """حذف تقرير وكل بلوكاته."""
        reports = self.db.get_reports()
        if not any(r["id"] == report_id for r in reports):
            return {"ok": False, "error": "التقرير غير موجود"}
        try:
            self.db.delete_report(report_id)
            logger.info("Report deleted: %s", report_id)
            return {"ok": True}
        except Exception as e:
            logger.error("delete report error: %s", e)
            return {"ok": False, "error": str(e)}

    def rename(self, report_id: str, new_title: str) -> dict:
        """إعادة تسمية تقرير."""
        new_title = new_title.strip()
        if not new_title:
            return {"ok": False, "error": "العنوان الجديد مطلوب"}
        reports = self.db.get_reports()
        if not any(r["id"] == report_id for r in reports):
            return {"ok": False, "error": "التقرير غير موجود"}
        try:
            self.db.save_settings({f"_report_title_{report_id}": new_title})
            # تحديث العنوان مباشرة في جدول reports
            import sqlite3
            from config import PROJECTS_DIR
            db_path = (
                PROJECTS_DIR / self.db.user_id / self.db.project_id / "project.db"
            )
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute(
                    "UPDATE reports SET title = ? WHERE id = ?",
                    (new_title, report_id)
                )
                conn.commit()
            logger.info("Report renamed: %s -> '%s'", report_id, new_title)
            return {"ok": True}
        except Exception as e:
            logger.error("rename report error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  قراءة التقارير
    # ──────────────────────────────────────────────────────────

    def list_reports(self) -> list[dict]:
        """قائمة كل التقارير."""
        return self.db.get_reports()

    def get_blocks(self, report_id: str) -> list[dict]:
        """بلوكات تقرير مرتبة حسب الموضع."""
        return self.db.get_report_blocks(report_id)

    # ──────────────────────────────────────────────────────────
    #  إضافة بلوكات
    # ──────────────────────────────────────────────────────────

    def _next_position(self, report_id: str) -> int:
        """حساب الموضع التالي في التقرير."""
        blocks = self.db.get_report_blocks(report_id)
        return len(blocks)

    def add_paragraph(self, report_id: str, text: str) -> dict:
        """إضافة فقرة نصية (Markdown)."""
        if not text.strip():
            return {"ok": False, "error": "النص مطلوب"}
        return self._add_block(report_id, "paragraph", {"text": text})

    def add_table(
        self,
        report_id: str,
        result_id: str,
        data     : list,
        columns  : list,
    ) -> dict:
        """إضافة جدول بيانات."""
        if not data:
            return {"ok": False, "error": "البيانات فارغة"}
        if not columns:
            return {"ok": False, "error": "أسماء الأعمدة مطلوبة"}
        return self._add_block(report_id, "table", {
            "result_id": result_id,
            "data"     : data,
            "columns"  : columns,
        })

    def add_chart(
        self,
        report_id : str,
        result_id : str,
        chart_type: str,
        data      : list,
        x_col     : str,
        y_cols    : list,
        title     : str = "",
    ) -> dict:
        """إضافة رسم بياني."""
        if not data:
            return {"ok": False, "error": "البيانات فارغة"}
        valid_types = {"bar", "line", "pie", "scatter", "area"}
        if chart_type not in valid_types:
            return {"ok": False, "error": f"نوع الرسم غير مدعوم: {chart_type}. المدعوم: {valid_types}"}
        return self._add_block(report_id, "chart", {
            "result_id" : result_id,
            "chart_type": chart_type,
            "data"      : data,
            "x_col"     : x_col,
            "y_cols"    : y_cols,
            "title"     : title,
        })

    def add_gauge(
        self,
        report_id    : str,
        result_id    : str,
        current_value: float,
        min_value    : float,
        max_value    : float,
        label        : str = "",
    ) -> dict:
        """إضافة مقياس Gauge."""
        if min_value >= max_value:
            return {"ok": False, "error": "min_value يجب أن يكون أصغر من max_value"}
        return self._add_block(report_id, "gauge", {
            "result_id"    : result_id,
            "current_value": current_value,
            "min_value"    : min_value,
            "max_value"    : max_value,
            "label"        : label,
        })

    def add_kpi(
        self,
        report_id   : str,
        result_id   : str,
        actual_value: float,
        target_value: float,
        label       : str = "",
        unit        : str = "",
    ) -> dict:
        """إضافة KPI Card."""
        return self._add_block(report_id, "kpi", {
            "result_id"   : result_id,
            "actual_value": actual_value,
            "target_value": target_value,
            "label"       : label,
            "unit"        : unit,
        })

    def _add_block(self, report_id: str, block_type: str, content: dict) -> dict:
        """إضافة بلوك عام للتقرير."""
        reports = self.db.get_reports()
        if not any(r["id"] == report_id for r in reports):
            return {"ok": False, "error": "التقرير غير موجود"}
        try:
            # 🆕 تعقيم المحتوى قبل الحفظ — يمنع "Object of type Timestamp
            # is not JSON serializable" عندما تحتوي البيانات (القادمة من
            # DuckDB/pandas) أعمدة تاريخ أو أنواع numpy غير قابلة للتسلسل
            safe_content = _sanitize_content(content)
            position = self._next_position(report_id)
            self.db.save_report_block(report_id, position, block_type, safe_content)
            logger.info("Block added: %s[%d] type=%s", report_id, position, block_type)
            return {"ok": True, "position": position}
        except Exception as e:
            logger.error("add_block error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  حذف بلوك
    # ──────────────────────────────────────────────────────────

    def delete_block(self, report_id: str, block_id: int) -> dict:
        """حذف بلوك محدد من التقرير."""
        try:
            import sqlite3
            from config import PROJECTS_DIR
            db_path = (
                PROJECTS_DIR / self.db.user_id / self.db.project_id / "project.db"
            )
            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.execute(
                    "DELETE FROM report_blocks WHERE id = ? AND report_id = ?",
                    (block_id, report_id)
                )
                conn.commit()
            if cursor.rowcount == 0:
                return {"ok": False, "error": "البلوك غير موجود"}
            logger.info("Block deleted: id=%d from report %s", block_id, report_id)
            return {"ok": True}
        except Exception as e:
            logger.error("delete_block error: %s", e)
            return {"ok": False, "error": str(e)}
