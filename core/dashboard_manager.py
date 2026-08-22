"""
core/dashboard_manager.py
===========================
المنطق البرمجي للوحات المعلومات: خيارات الـ Slicers، وتنفيذ
"تحديث البيانات" (الذي يُشغّل كل الخلايا مع تطبيق قيود الفلترة معاً).

سياسة استدعاء AI:
--------------------
- خلية عادية (table/chart/gauge/kpi) ولها base_sql محفوظ مسبقاً:
  يُعاد تطبيق الفلاتر الحالية على نفس الـ SQL في طبقة بايثون/DuckDB
  مباشرة — بدون أي استدعاء AI جديد (أسرع وأرخص وأكثر ثباتاً).
- خلية عادية بدون base_sql (أول مرة، أو بعد تعديل نص السؤال):
  يُستدعى AI لتوليد SQL جديد، ثم يُحفظ كـ base_sql للاستخدام لاحقاً.
- خلية Story Telling: يُستدعى AI دائماً (لأن السرد نص جديد يُبنى فعلياً
  على البيانات الحالية بعد الفلترة — وهذا تحليل، وليس مجرد استعلام).

لا تحديث تلقائي أو فوري لأي خلية — كل شيء يحدث فقط عند استدعاء
refresh_dashboard() أو refresh_single_cell() (المرتبطين بأزرار صريحة
في الواجهة).
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
    #  إعادة تعيين الـ Slicers
    # ──────────────────────────────────────────────────────────

    def reset_slicers(self, dashboard_id: str) -> None:
        """إعادة كل Slicers اللوحة إلى الوضع الافتراضي (بدون تنفيذ أي تحديث)."""
        self.db.reset_dashboard_slicers(dashboard_id)

    # ──────────────────────────────────────────────────────────
    #  تحديث البيانات (كل خلايا اللوحة)
    # ──────────────────────────────────────────────────────────

    def refresh_dashboard(self, dashboard_id: str, ai_rules: Optional[str] = None) -> dict:
        """
        تنفيذ كل خلايا اللوحة المُهيَّأة من جديد، مع تطبيق قيود كل
        الـ Slicers المُفعَّلة (لها جدول+عمود+قيمة واحدة على الأقل).

        يُستدعى فقط عند ضغط زر "🔄 تحديث البيانات" — لا نداء تلقائياً
        من أي مكان آخر.

        يرجع: {"ok": True, "results": {position: {...}}, "errors": N,
               "total": N, "ai_calls": N, "fast_updates": N}
        """
        cells = self.db.get_dashboard_cells(dashboard_id)
        configured = [c for c in cells if c.get("question")]

        if not configured:
            return {"ok": False, "error": "لا توجد خلايا مُهيَّأة في هذه اللوحة بعد"}

        filters = self._build_active_filters(dashboard_id)

        results = {}
        error_count = 0
        ai_calls = 0
        fast_updates = 0

        for cell in configured:
            position = cell["position"]
            display_type = cell.get("display_type") or "table"
            question = cell["question"]
            base_sql = cell.get("base_sql")

            try:
                if display_type == "story":
                    # السرد النصي يحتاج AI دائماً — يُبنى فعلياً على
                    # البيانات بعد الفلترة، وليس مجرد إعادة تشغيل SQL
                    r = self.ai.tell_story(question, ai_rules=ai_rules, filters=filters)
                    ai_calls += 1
                elif base_sql:
                    # لدينا SQL أساسي محفوظ — نطبّق الفلاتر بدون AI
                    r = self._run_fast(base_sql, filters)
                    fast_updates += 1
                else:
                    # أول مرة لهذا السؤال (أو بعد تعديله) — نحتاج AI
                    # لتوليد SQL جديد، ثم نحفظه كـ base_sql للمستقبل
                    r = self.ai.ask(question, result_type=display_type, ai_rules=ai_rules, filters=filters)
                    ai_calls += 1
                    if r.get("ok") and r.get("sql"):
                        self.db.save_dashboard_cell_base_sql(dashboard_id, position, r["sql"])
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
            "Dashboard '%s' refreshed: %d cells, %d errors, %d AI calls, %d fast updates",
            dashboard_id, len(configured), error_count, ai_calls, fast_updates
        )
        return {
            "ok": True, "results": results, "errors": error_count,
            "total": len(configured), "ai_calls": ai_calls, "fast_updates": fast_updates,
        }

    # ──────────────────────────────────────────────────────────
    #  تحديث خلية واحدة فقط
    # ──────────────────────────────────────────────────────────

    def refresh_single_cell(self, dashboard_id: str, position: int, ai_rules: Optional[str] = None) -> dict:
        """
        تحديث خلية واحدة فقط بنفس منطق refresh_dashboard (AI فقط عند
        الحاجة). مفيد أثناء بناء اللوحة أو تصحيح سؤال معين دون الانتظار
        لتحديث كل الخلايا.

        يرجع: {"ok": True/False, "used_ai": True/False, "result": {...}}
               أو {"ok": False, "used_ai": ..., "error": "..."}
        """
        cells = {c["position"]: c for c in self.db.get_dashboard_cells(dashboard_id)}
        cell = cells.get(position)
        if not cell or not cell.get("question"):
            return {"ok": False, "error": "الخلية غير مُهيَّأة"}

        filters = self._build_active_filters(dashboard_id)
        display_type = cell.get("display_type") or "table"
        question = cell["question"]
        base_sql = cell.get("base_sql")
        used_ai = False

        try:
            if display_type == "story":
                r = self.ai.tell_story(question, ai_rules=ai_rules, filters=filters)
                used_ai = True
            elif base_sql:
                r = self._run_fast(base_sql, filters)
            else:
                r = self.ai.ask(question, result_type=display_type, ai_rules=ai_rules, filters=filters)
                used_ai = True
                if r.get("ok") and r.get("sql"):
                    self.db.save_dashboard_cell_base_sql(dashboard_id, position, r["sql"])
        except Exception as e:
            logger.error("Single cell refresh error (%d): %s", position, e)
            r = {"ok": False, "error": str(e)}

        if r.get("ok"):
            stored = self._serialize_result(display_type, r, cell.get("chart_type"))
            self.db.save_dashboard_cell_result(dashboard_id, position, stored, r.get("sql"), None)
            self.db.touch_dashboard(dashboard_id)
            return {"ok": True, "used_ai": used_ai, "result": stored}
        else:
            self.db.save_dashboard_cell_result(dashboard_id, position, None, r.get("sql"), r.get("error"))
            return {"ok": False, "used_ai": used_ai, "error": r.get("error")}

    # ──────────────────────────────────────────────────────────
    #  دوال داخلية
    # ──────────────────────────────────────────────────────────

    def _run_fast(self, base_sql: str, filters: list) -> dict:
        """تنفيذ base_sql مع الفلاتر عبر QueryEngine مباشرة (بدون AI)."""
        result = self.qe.run_with_filters(base_sql, filters)
        if not result["ok"]:
            return {"ok": False, "error": result["error"], "sql": base_sql}
        return {
            "ok": True,
            "sql": result.get("sql", base_sql),
            "df": result["df"],
            "rows": result["rows"],
            "tries": 0,
        }

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
        """تحويل نتيجة ai.ask()/tell_story()/_run_fast() إلى شكل قابل للتخزين كـ JSON."""
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
