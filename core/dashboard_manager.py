"""
core/dashboard_manager.py
===========================
المنطق البرمجي للوحات المعلومات: خيارات الـ Slicers، وتنفيذ
"تحديث البيانات" (الذي يُشغّل كل الخلايا مع تطبيق قيود الفلترة معاً).

لا تحديث تلقائي أو فوري لأي خلية — كل شيء يحدث فقط عند استدعاء
refresh_dashboard() (المرتبط بزر "🔄 تحديث البيانات" في الواجهة).
"""

import logging
from typing import Optional

import pandas as pd

from core.project_db import ProjectDB
from core.query_engine import QueryEngine
from ai.ai_manager import AIManager

logger = logging.getLogger(__name__)


class DashboardManager:
    def __init__(self, db: ProjectDB, ai: AIManager, qe: Optional[QueryEngine] = None):
        self.db = db
        self.ai = ai
        self.qe = qe or QueryEngine(db)

    # ──────────────────────────────────────────────────────────
    #  خيارات الـ Slicers (جداول/أعمدة/قيم المشروع الكامل)
    # ──────────────────────────────────────────────────────────

    def get_available_tables(self) -> list[str]:
        """كل الجداول المتاحة في المشروع (لاختيار الجدول في Slicer)."""
        return list(self.db.get_schema().keys())

    def get_available_columns(self, table_name: str) -> list[str]:
        """كل أعمدة جدول معيّن (لاختيار العمود بعد اختيار الجدول)."""
        schema = self.db.get_schema()
        table_info = schema.get(table_name)
        if not table_info:
            return []
        return list(table_info.get("columns", {}).keys())

    def get_distinct_values(self, table_name: str, column_name: str, limit: int = 200) -> dict:
        """
        القيم الفريدة لعمود معيّن (لقائمة اختيار القيم في Slicer).
        استعلام مباشر بسيط — لا يحتاج AI إطلاقاً (عملية حتمية).
        """
        sql = (
            f'SELECT DISTINCT "{column_name}" AS v FROM "{table_name}" '
            f'WHERE "{column_name}" IS NOT NULL ORDER BY 1 LIMIT {int(limit)}'
        )
        result = self.qe.run(sql)
        if not result["ok"]:
            return {"ok": False, "error": result["error"]}
        values = result["df"]["v"].tolist()
        return {"ok": True, "values": values}

    # ──────────────────────────────────────────────────────────
    #  تحديث البيانات (العملية الوحيدة التي تُنفّذ أي استعلام فعلي)
    # ──────────────────────────────────────────────────────────

    def refresh_dashboard(self, dashboard_id: str, ai_rules: Optional[str] = None) -> dict:
        """
        تنفيذ كل خلايا اللوحة المُهيَّأة من جديد، مع تطبيق قيود كل
        الـ Slicers المُفعَّلة (لها جدول+عمود+قيمة واحدة على الأقل).

        يُستدعى فقط عند ضغط زر "🔄 تحديث البيانات" — لا نداء تلقائياً
        من أي مكان آخر.

        يرجع: {"ok": True, "results": {position: {...}}, "errors": N}
        """
        cells = self.db.get_dashboard_cells(dashboard_id)
        configured = [c for c in cells if c.get("question")]

        if not configured:
            return {"ok": False, "error": "لا توجد خلايا مُهيَّأة في هذه اللوحة بعد"}

        filters = self._build_active_filters(dashboard_id)

        results = {}
        error_count = 0

        for cell in configured:
            position = cell["position"]
            display_type = cell.get("display_type") or "table"
            question = cell["question"]

            try:
                if display_type == "story":
                    r = self.ai.tell_story(question, ai_rules=ai_rules, filters=filters)
                else:
                    r = self.ai.ask(question, result_type=display_type, ai_rules=ai_rules, filters=filters)
            except Exception as e:
                logger.error("Dashboard cell %d execution error: %s", position, e)
                r = {"ok": False, "error": str(e)}

            if r.get("ok"):
                stored = self._serialize_result(display_type, r, cell.get("chart_type"))
                self.db.save_dashboard_cell_result(
                    dashboard_id, position, stored, r.get("sql"), None
                )
                results[position] = stored
            else:
                error_count += 1
                self.db.save_dashboard_cell_result(
                    dashboard_id, position, None, r.get("sql"), r.get("error")
                )
                results[position] = None

        self.db.touch_dashboard(dashboard_id)
        logger.info(
            "Dashboard '%s' refreshed: %d cells, %d errors",
            dashboard_id, len(configured), error_count
        )
        return {"ok": True, "results": results, "errors": error_count, "total": len(configured)}

    def _build_active_filters(self, dashboard_id: str) -> list:
        """يحوّل Slicers المُفعَّلة فعلياً (لها جدول+عمود+قيم) إلى صيغة الفلاتر."""
        slicers = self.db.get_dashboard_slicers(dashboard_id)
        filters = []
        for s in slicers:
            if s.get("table_name") and s.get("column_name") and s.get("selected_values"):
                filters.append({
                    "table" : s["table_name"],
                    "column": s["column_name"],
                    "values": s["selected_values"],
                })
        return filters

    def _serialize_result(self, display_type: str, r: dict, chart_type: Optional[str]) -> dict:
        """تحويل نتيجة ai.ask()/tell_story() إلى شكل قابل للتخزين كـ JSON."""
        df: pd.DataFrame = r.get("df")
        stored = {
            "columns": list(df.columns) if df is not None else [],
            "rows": df.to_dict(orient="records") if df is not None else [],
        }
        if display_type == "chart":
            stored["chart_type"] = chart_type or "bar"
        if display_type == "story":
            stored["story"] = r.get("story", "")
        return stored
