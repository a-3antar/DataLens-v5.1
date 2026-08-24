"""
core/dashboard_manager.py
===========================
المنطق البرمجي للوحات المعلومات: خيارات الـ Slicers، تنفيذ "تحديث
البيانات" (متوازٍ عبر Threads)، وبناء خطة لوحة كاملة تلقائياً بالذكاء
الاصطناعي.

سياسة استدعاء AI:
--------------------
- خلية عادية (table/chart/gauge/kpi) ولها base_sql محفوظ مسبقاً:
  يُعاد تطبيق الفلاتر الحالية على نفس الـ SQL في طبقة بايثون/DuckDB
  مباشرة — بدون أي استدعاء AI جديد (أسرع وأرخص وأكثر ثباتاً).
- خلية عادية بدون base_sql (أول مرة، أو بعد تعديل نص السؤال):
  يُستدعى AI لتوليد SQL جديد، ثم يُحفظ كـ base_sql للاستخدام لاحقاً.
- خلية Story Telling: يُستدعى AI دائماً (لأن السرد نص جديد يُبنى فعلياً
  على البيانات الحالية بعد الفلترة — وهذا تحليل، وليس مجرد استعلام).

سياسة التوازي (Threads):
----------------------------
- خلايا Story Telling: تُنفَّذ دائماً عبر thread pool منفصل (متوازية
  فيما بينها) بغض النظر عن المحرك — لأنها الأبطأ (استدعاءين متتاليين
  لكل خلية: توليد SQL ثم توليد السرد).
- بقية الخلايا (SQL عادي / fast update): تُنفَّذ متوازية عبر
  ThreadPoolExecutor فقط لو المحرك != "ollama". Ollama المحلي لا
  يستفيد عملياً من التوازي (يتزاحم على نفس موارد CPU/GPU المحلية بدل
  تسريع حقيقي)، لذا خلاياه تبقى تسلسلية.
- الكتابة الفعلية لنتائج كل خلية في project.db تحدث بعد انتهاء كل
  الـ threads (تجميع النتائج أولاً)، وليس من داخل الـ threads نفسها —
  لتفادي أي تزامن كتابة على نفس ملف SQLite من عدة threads معاً.

لا تحديث تلقائي أو فوري لأي خلية — كل شيء يحدث فقط عند استدعاء
refresh_dashboard() أو refresh_single_cell() (المرتبطين بأزرار صريحة
في الواجهة).
"""

import json
import logging
import threading
import datetime as _dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Callable

import pandas as pd
import numpy as np

from core.project_db import ProjectDB
from core.query_engine import QueryEngine
from core.dashboard_templates import DASHBOARD_TEMPLATES
from ai.ai_manager import AIManager
from config import DASHBOARD_GAUGE_COUNT

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
        يُستدعى فقط عند طلب صريح من المستخدم (زر "تحميل القيم" في
        الواجهة) وليس تلقائياً بمجرد اختيار عمود.
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
    #  تنفيذ خلية واحدة (تُستدعى داخل thread — لا تكتب في DB)
    # ──────────────────────────────────────────────────────────

    def _process_cell(self, cell: dict, filters: list, ai_rules: Optional[str]) -> dict:
        """
        تنفيذ منطق خلية واحدة فعلياً (AI أو fast-update حسب الحالة).
        لا يكتب أي شيء في project.db — فقط يُرجع النتيجة الخام، حتى
        يمكن استدعاؤه بأمان من داخل thread منفصل. الكتابة الفعلية
        تحدث لاحقاً تسلسلياً بعد تجميع كل نتائج الـ threads.
        """
        position = cell["position"]
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
        except Exception as e:
            logger.error("Dashboard cell %d execution error: %s", position, e)
            r = {"ok": False, "error": str(e)}

        return {
            "cell": cell, "position": position, "display_type": display_type,
            "result": r, "used_ai": used_ai,
        }

    # ──────────────────────────────────────────────────────────
    #  تحديث البيانات (كل خلايا اللوحة) — متوازٍ عبر Threads
    # ──────────────────────────────────────────────────────────

    def refresh_dashboard(
        self,
        dashboard_id: str,
        ai_rules: Optional[str] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        engine_name: str = "gemini",
        max_workers: int = 4,
    ) -> dict:
        """
        تنفيذ كل خلايا اللوحة المُهيَّأة من جديد، مع تطبيق قيود كل
        الـ Slicers المُفعَّلة، وبالتوازي عبر ThreadPoolExecutor حيثما
        كان ذلك آمناً ومفيداً (راجع توثيق الوحدة أعلاه).

        يُستدعى فقط عند ضغط زر "🔄 تحديث البيانات" — لا نداء تلقائياً
        من أي مكان آخر.

        on_progress(done, total): callback اختياري يُستدعى من هذه
        الدالة (main thread، بعد جمع كل نتيجة عبر as_completed) —
        مناسب لتحديث مؤشر بصري بسيط في الواجهة أثناء التنفيذ.

        engine_name: اسم محرك AI الحالي — يُستخدم فقط لتقرير هل نُفعّل
        التوازي لبقية الخلايا (غير story)؛ Ollama يبقى تسلسلياً لها.

        يرجع: {"ok": True, "results": {position: {...}}, "errors": N,
               "total": N, "ai_calls": N, "fast_updates": N}
        """
        cells = self.db.get_dashboard_cells(dashboard_id)
        configured = [c for c in cells if c.get("question")]

        if not configured:
            return {"ok": False, "error": "لا توجد خلايا مُهيَّأة في هذه اللوحة بعد"}

        filters = self._build_active_filters(dashboard_id)
        total = len(configured)

        done_lock = threading.Lock()
        done_count = [0]

        def run_and_track(cell):
            res = self._process_cell(cell, filters, ai_rules)
            with done_lock:
                done_count[0] += 1
                if on_progress:
                    try:
                        on_progress(done_count[0], total)
                    except Exception as e:
                        logger.warning("on_progress callback failed: %s", e)
            return res

        story_cells = [c for c in configured if (c.get("display_type") or "table") == "story"]
        story_positions = {c["position"] for c in story_cells}
        other_cells = [c for c in configured if c["position"] not in story_positions]

        all_outcomes = []

        # ── خلايا Story Telling: متوازية دائماً بغض النظر عن المحرك ──
        if story_cells:
            workers = min(max_workers, max(1, len(story_cells)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="story") as ex:
                futures = [ex.submit(run_and_track, c) for c in story_cells]
                for f in as_completed(futures):
                    all_outcomes.append(f.result())

        # ── بقية الخلايا: متوازية فقط لو المحرك ليس Ollama ──
        if other_cells:
            if engine_name != "ollama":
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cell") as ex:
                    futures = [ex.submit(run_and_track, c) for c in other_cells]
                    for f in as_completed(futures):
                        all_outcomes.append(f.result())
            else:
                # Ollama محلي — لا فائدة حقيقية من التوازي، نبقيه تسلسلياً
                for c in other_cells:
                    all_outcomes.append(run_and_track(c))

        # ── كتابة النتائج تسلسلياً في project.db بعد انتهاء كل الـ threads ──
        # (تجنّباً لأي تزامن كتابة SQLite من عدة threads في وقت واحد)
        results = {}
        error_count = 0
        ai_calls = 0
        fast_updates = 0

        for outcome in all_outcomes:
            position = outcome["position"]
            display_type = outcome["display_type"]
            cell = outcome["cell"]
            r = outcome["result"]
            used_ai = outcome["used_ai"]

            if used_ai:
                ai_calls += 1
            else:
                fast_updates += 1

            if r.get("ok"):
                stored = self._serialize_result(display_type, r, cell.get("chart_type"))
                self.db.save_dashboard_cell_result(
                    dashboard_id, position, stored, r.get("sql"), None
                )
                # لو كانت هذه أول مرة تُنتج SQL ناجح لهذه الخلية (بدون
                # base_sql محفوظ مسبقاً)، نحفظه الآن ليُستخدم في
                # التحديثات السريعة القادمة بدون AI
                if display_type != "story" and not cell.get("base_sql") and r.get("sql"):
                    self.db.save_dashboard_cell_base_sql(dashboard_id, position, r["sql"])
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
            dashboard_id, total, error_count, ai_calls, fast_updates
        )
        return {
            "ok": True, "results": results, "errors": error_count,
            "total": total, "ai_calls": ai_calls, "fast_updates": fast_updates,
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
        outcome = self._process_cell(cell, filters, ai_rules)
        display_type = outcome["display_type"]
        r = outcome["result"]
        used_ai = outcome["used_ai"]

        if r.get("ok"):
            stored = self._serialize_result(display_type, r, cell.get("chart_type"))
            self.db.save_dashboard_cell_result(dashboard_id, position, stored, r.get("sql"), None)
            if display_type != "story" and not cell.get("base_sql") and r.get("sql"):
                self.db.save_dashboard_cell_base_sql(dashboard_id, position, r["sql"])
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

    @staticmethod
    def _json_safe(value):
        """
        تحويل قيمة واحدة إلى نوع قابل للتسلسل عبر json.dumps مباشرة.

        السبب: نتائج DuckDB/pandas قد تحتوي أنواعاً مثل pandas.Timestamp
        (أعمدة التاريخ)، numpy.int64/float64/bool_، أو NaT/NaN — لا شيء
        منها قابل للتسلسل بـ json افتراضياً، وهذا كان يُسبب خطأ
        "Object of type Timestamp is not JSON serializable" عند حفظ
        نتيجة خلية تحتوي عمود تاريخ (شائع في اللوحات المُنشأة تلقائياً
        بالذكاء الاصطناعي، حيث الأسئلة كثيراً ما تتضمن تجميعاً زمنياً).
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

    def _sanitize_rows(self, rows: list) -> list:
        """تطبيق _json_safe على كل قيمة في كل صف من صفوف النتيجة."""
        return [
            {k: self._json_safe(v) for k, v in row.items()}
            for row in rows
        ]

    def _serialize_result(self, display_type: str, r: dict, chart_type: Optional[str]) -> dict:
        """تحويل نتيجة ai.ask()/tell_story()/_run_fast() إلى شكل قابل للتخزين كـ JSON."""
        df: pd.DataFrame = r.get("df")
        raw_rows = df.to_dict(orient="records") if df is not None else []
        stored = {
            "columns": list(df.columns) if df is not None else [],
            "rows": self._sanitize_rows(raw_rows),
        }
        if display_type == "chart":
            stored["chart_type"] = chart_type or "bar"
        if display_type == "story":
            stored["story"] = r.get("story", "")
        return stored

    # ──────────────────────────────────────────────────────────
    #  🆕 بناء خطة لوحة كاملة تلقائياً بالذكاء الاصطناعي
    # ──────────────────────────────────────────────────────────

    def generate_dashboard_plan(self, description: str, ai_rules: Optional[str] = None) -> dict:
        """
        يطلب من AI اختيار أحد القوالب الستة الموجودة فعلياً في
        DASHBOARD_TEMPLATES + اقتراح عنوان وسؤال لكل خلية (٤ Gauges
        أعلى دائماً + خلايا القالب المختار)، بناءً على وصف حر من
        المستخدم وschema المشروع الفعلي.

        هذه الدالة لا تُنشئ اللوحة مباشرة — تُرجع فقط خطة (dict) تُعرض
        للمراجعة في الواجهة. الإنشاء الفعلي في ui/dashboards.py يستخدم
        بالضبط نفس db.save_dashboard_cell() المُستخدَمة في الإنشاء
        اليدوي — أي أن اللوحة الناتجة هنا مطابقة تماماً للوحة عادية
        وقابلة للتعديل لاحقاً بلا أي قيد إضافي؛ الفرق الوحيد هو لحظة
        الإنشاء الأولى فقط.

        يرجع:
        {
            "ok": True,
            "template_id": "A",
            "gauges": [{"title": "...", "question": "..."}, ...],  # طولها دائماً DASHBOARD_GAUGE_COUNT
            "cells": [{"title": "...", "question": "...", "display_type": "...", "chart_type": None|"..."}, ...],
        }
        أو {"ok": False, "error": "..."}
        """
        schema = self.db.get_schema()
        if not schema:
            return {"ok": False, "error": "لا توجد جداول محملة في المشروع بعد"}

        templates_desc = "\n".join(
            f'- {k}: {v["name"]} — {v["description"]} (cell_count={v["cell_count"]})'
            for k, v in DASHBOARD_TEMPLATES.items()
        )
        schema_lines = []
        for alias, info in schema.items():
            cols = ", ".join(info.get("columns", {}).keys())
            schema_lines.append(f"{alias}: {cols}")
        schema_text = "\n".join(schema_lines)

        prompt = f"""أنت مساعد لبناء لوحات معلومات (Dashboards). اختر قالباً واحداً فقط من
القوالب المتاحة أدناه بناءً على طلب المستخدم، ثم اقترح عنواناً وسؤالاً
طبيعياً واضحاً لكل خلية (٤ مؤشرات Gauges أعلى دائماً + خلايا القالب المختار).

القوالب المتاحة (اختر id واحداً فقط بالضبط كما هو مكتوب):
{templates_desc}

الجداول والأعمدة المتاحة في المشروع:
{schema_text}

طلب المستخدم: {description}

أجب فقط بصيغة JSON صالحة بدون أي شرح أو نص إضافي، بالضبط بهذا الشكل:
{{
  "template_id": "A",
  "gauges": [
    {{"title": "عنوان قصير", "question": "سؤال طبيعي واضح"}},
    {{"title": "...", "question": "..."}},
    {{"title": "...", "question": "..."}},
    {{"title": "...", "question": "..."}}
  ],
  "cells": [
    {{"title": "...", "question": "...", "display_type": "table", "chart_type": null}}
  ]
}}
- عدد عناصر "gauges" يجب أن يكون ٤ بالضبط دائماً.
- عدد عناصر "cells" يجب أن يطابق تماماً cell_count الخاص بالقالب المختار.
- display_type يجب أن يكون واحداً من: table, chart, gauge, kpi, story.
- لو display_type = "chart"، حدد chart_type من: bar, line, pie, area, scatter. غير ذلك اجعله null."""

        send_result = self.ai.engine.send(prompt, self.ai.temperature)
        if not send_result["ok"]:
            return {"ok": False, "error": send_result["error"]}

        text = self.ai.engine.clean_response(send_result["text"])
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            data = json.loads(text[start:end])
        except Exception as e:
            logger.error("generate_dashboard_plan parse error: %s | text=%s", e, text[:300])
            return {"ok": False, "error": f"تعذر تحليل رد الذكاء الاصطناعي كـ JSON: {e}"}

        template_id = data.get("template_id")
        if template_id not in DASHBOARD_TEMPLATES:
            return {"ok": False, "error": f"قالب غير صالح من الذكاء الاصطناعي: {template_id}"}

        tmpl = DASHBOARD_TEMPLATES[template_id]
        cells = data.get("cells") or []
        gauges = data.get("gauges") or []

        if len(cells) != tmpl["cell_count"]:
            return {
                "ok": False,
                "error": f"عدد الخلايا المُقترحة ({len(cells)}) لا يطابق القالب '{template_id}' "
                         f"الذي يحتاج {tmpl['cell_count']} خلية",
            }

        # نضمن وجود ٤ gauges بالضبط (نُكمّل ببطاقات فارغة أو نقصّ الزائد بأمان)
        gauges = (gauges + [{"title": "", "question": ""}] * DASHBOARD_GAUGE_COUNT)[:DASHBOARD_GAUGE_COUNT]

        return {"ok": True, "template_id": template_id, "gauges": gauges, "cells": cells}
