"""
core/dashboard_manager.py
===========================
المنطق البرمجي للوحات المعلومات: خيارات الـ Slicers، تنفيذ "تحديث
البيانات" (متوازٍ عبر Threads)، بناء خطة لوحة كاملة تلقائياً بالذكاء
الاصطناعي، وتغيير قالب لوحة موجودة بعد إنشائها.

🆕 إعادة هيكلة (خلايا OOP):
------------------------------
منطق كل نوع خلية (تنفيذ AI/fast-update، تحويل النتيجة لشكل قابل
للتخزين) انتقل بالكامل إلى core/dashboard_cells/*.py — كل خلية الآن
كائن (TableCell/ChartCell/GaugeCell/KpiCell/StoryCell) يُبنى عبر
core.dashboard_cells.create_cell() من الصف الخام في project_db، ويعرف
بنفسه كيف يُنفَّذ (execute) وكيف يُخزَّن (to_stored_dict). هذا الملف
لم يعد يحتوي أي فرع "if display_type == ..." — فقط يستدعي الدوال
المتعارف عليها على أي كائن خلية بغض النظر عن نوعها الفعلي.

سياسة استدعاء AI (تبقى كما كانت، الآن داخل كل كلاس خلية):
--------------------------------------------------------------
- خلية عادية (table/chart/gauge/kpi) ولها base_sql محفوظ مسبقاً:
  يُعاد تطبيق الفلاتر الحالية على نفس الـ SQL في طبقة بايثون/DuckDB
  مباشرة — بدون أي استدعاء AI جديد (أسرع وأرخص وأكثر ثباتاً).
- خلية عادية بدون base_sql (أول مرة، أو بعد تعديل نص السؤال):
  يُستدعى AI لتوليد SQL جديد، ثم يُحفظ كـ base_sql للاستخدام لاحقاً.
- خلية Story Telling: يُستدعى AI دائماً (لأن السرد نص جديد يُبنى فعلياً
  على البيانات الحالية بعد الفلترة — وهذا تحليل، وليس مجرد استعلام).

سياسة التوازي (Threads) — بدون أي تغيير:
----------------------------------------------
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

🆕 نقل سياق logging (contextvars) إلى الـ threads العاملة:
------------------------------------------------------------------
core/logger_config.py يضبط اسم المستخدم الحالي عبر
contextvars.ContextVar — وهذه لا تنتقل تلقائياً إلى أي thread جديد
يُنشأ عبر threading.Thread/ThreadPoolExecutor (خلافاً لـ asyncio الذي
ينسخها تلقائياً)؛ الـ thread الجديد يبدأ بسياق افتراضي فارغ، فيظهر
اسم المستخدم كـ "-" في كل سطر log يُكتب من داخل _process_cell رغم
تنفيذه فعلياً لصالح مستخدم معروف — وهذا ما لوحظ فعلياً في اللوج
(أسطر ai.ai_manager/core.query_engine الناتجة عن خلايا اللوحة تظهر
دائماً بـ "-" رغم ظهور اسم المستخدم بشكل صحيح في الأسطر المجاورة
الآتية من الـ main thread).

الحل: عند كل submit إلى ThreadPoolExecutor هنا، نلتقط السياق الحالي
في الـ thread الرئيسي عبر contextvars.copy_context() (قبل بدء الـ
thread، حيث لا تزال القيمة الصحيحة متاحة)، ثم ننفّذ _process_cell
داخل نفس هذا السياق عبر ctx.run(...) بدل استدعائه مباشرة — فينتقل
معه أي ContextVar مضبوط وقتها (اسم المستخدم وأي سياق مشابه مستقبلاً)
بدون أي حاجة لمعرفة تفاصيله هنا (هذا الملف لا يستورد ui/ أو
streamlit، ويبقى كذلك — راجع _submit_with_context أدناه).

🆕 تغيير قالب لوحة موجودة (update_dashboard_template):
------------------------------------------------------------
core/project_db.py ممنوع تعديله بموجب قرار معماري صريح، ولا توجد فيه
دالة لتحديث template_id للوحة موجودة (create_dashboard يضبطها فقط
وقت الإنشاء). لذا — بنفس النمط المُستخدَم فعلياً في
exporters/report_manager.py::rename وcore/project_manager.py::rename
لأعمدة لا تغطيها ProjectDB — نُحدّث عمود dashboards.template_id مباشرة
عبر sqlite3 على ملف project.db، دون أي تعديل على project_db.py نفسه.

الخلايا التي تقع خارج نطاق القالب الجديد (position >=
DASHBOARD_GAUGE_COUNT + cell_count الجديد) لا تُحذف ولا تُعدَّل أبداً —
تبقى مخزَّنة كما هي بالكامل (سؤالها، base_sql، آخر نتيجة محفوظة). هي
فقط تتوقف عن الظهور في الواجهة (ui/dashboards.py يعرض فقط الخلايا ضمن
نطاق القالب الحالي عبر layout_fn) ولا تُشمَل ضمن "تحديث البيانات"
(راجع الفلترة في refresh_dashboard أدناه) — توفيراً حقيقياً لوقت
الحسابات واستدعاءات AI على خلايا غير مرئية أصلاً. لو أُعيد اختيار
قالب أكبر لاحقاً (أو نفس القالب القديم)، تعود هذه الخلايا للظهور
ببياناتها المحفوظة فوراً بدون أي إعادة حساب.

لا تحديث تلقائي أو فوري لأي خلية — كل شيء يحدث فقط عند استدعاء
refresh_dashboard() أو refresh_single_cell() (المرتبطين بأزرار صريحة
في الواجهة).
"""

import json
import sqlite3
import logging
import contextvars
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Callable

from core.project_db import ProjectDB
from core.query_engine import QueryEngine
from core.dashboard_templates import DASHBOARD_TEMPLATES, get_template
from core.dashboard_cells import create_cell
from ai.ai_manager import AIManager
from config import DASHBOARD_GAUGE_COUNT, PROJECTS_DIR

logger = logging.getLogger(__name__)


def _now() -> str:
    """الوقت الحالي بصيغة ISO — نفس تنسيق core.project_db._now()."""
    return datetime.utcnow().isoformat()


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
    #  🆕 تغيير قالب لوحة موجودة — بدون حذف أو تحديث الخلايا المخفية
    # ──────────────────────────────────────────────────────────

    def _visible_cell_limit(self, dashboard: dict) -> int:
        """
        عدد المواضع المرئية فعلياً حسب قالب اللوحة الحالي: ٤ Gauges
        ثابتة دائماً + عدد خلايا القالب (cell_count). أي خلية بموضع
        (position) أكبر من أو يساوي هذا الحد تُعتبر "مخفية" — موجودة
        في project.db لكن غير معروضة وغير مُحدَّثة ضمن "تحديث الكل".
        """
        template = get_template(dashboard.get("template_id", "A"))
        return DASHBOARD_GAUGE_COUNT + template["cell_count"]

    def update_dashboard_template(self, dashboard_id: str, new_template_id: str) -> dict:
        """
        تغيير قالب لوحة موجودة مسبقاً — لا يحذف ولا يُعدِّل أي خلية
        مخزَّنة، فقط يُبدِّل template_id الذي يتحكم في عدد/تخطيط
        الخلايا المعروضة (راجع توثيق الوحدة أعلاه للتفاصيل الكاملة).

        core/project_db.py لا يُلمس هنا — التحديث مباشر عبر sqlite3
        على نفس ملف project.db، بنفس نمط exporters/report_manager.py
        ::rename وcore/project_manager.py::rename.

        يرجع: {"ok": True} أو {"ok": False, "error": "..."}
        """
        if new_template_id not in DASHBOARD_TEMPLATES:
            return {"ok": False, "error": f"قالب غير معروف: {new_template_id}"}

        dashboard = self.db.get_dashboard(dashboard_id)
        if not dashboard:
            return {"ok": False, "error": "اللوحة غير موجودة"}

        try:
            db_path = PROJECTS_DIR / self.db.user_id / self.db.project_id / "project.db"
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute(
                    "UPDATE dashboards SET template_id = ?, updated_at = ? WHERE id = ?",
                    (new_template_id, _now(), dashboard_id)
                )
                conn.commit()
            logger.info(
                "Dashboard template changed: %s (%s -> %s) — no cells deleted or modified",
                dashboard_id, dashboard.get("template_id"), new_template_id
            )
            return {"ok": True}
        except Exception as e:
            logger.error("update_dashboard_template error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  تنفيذ خلية واحدة (تُستدعى داخل thread — لا تكتب في DB)
    # ──────────────────────────────────────────────────────────

    def _process_cell(self, cell_obj, filters: list, ai_rules: Optional[str]) -> dict:
        """
        تنفيذ خلية واحدة فعلياً عبر cell_obj.execute() — بغض النظر عن
        نوعها (كل كلاس خلية يعرف منطقه الخاص، راجع core/dashboard_cells).
        لا يكتب أي شيء في project.db — فقط يُرجع النتيجة الخام، حتى
        يمكن استدعاؤه بأمان من داخل thread منفصل. الكتابة الفعلية
        تحدث لاحقاً تسلسلياً بعد تجميع كل نتائج الـ threads.
        """
        try:
            r = cell_obj.execute(self.ai, self.qe, filters, ai_rules)
        except Exception as e:
            logger.error("Dashboard cell %d execution error: %s", cell_obj.position, e)
            r = {"ok": False, "error": str(e), "used_ai": False}

        return {"cell_obj": cell_obj, "position": cell_obj.position, "result": r}

    def _submit_with_context(self, executor: ThreadPoolExecutor, cell_obj, filters: list, ai_rules: Optional[str]):
        """
        🆕 إرسال _process_cell إلى الـ thread pool مع نقل سياق
        contextvars الحالي (بما فيه اسم المستخدم لأغراض اللوج — راجع
        core/logger_config.py) إلى الـ thread العامل.

        السبب: contextvars.ContextVar لا تنتقل تلقائياً لـ threads
        جديدة (خلافاً لـ asyncio) — كل thread يبدأ بسياق افتراضي فارغ.
        contextvars.copy_context() هنا يُستدعى في الـ thread الرئيسي
        (وقت الإرسال، حيث السياق الصحيح لا يزال متاحاً)، فيُنشئ نسخة
        من كل قيم السياق الحالية؛ ثم executor.submit(ctx.run, ...)
        ينفّذ _process_cell داخل تلك النسخة بالضبط داخل الـ thread
        العامل. هذا حل عام لا يحتاج معرفة أي تفاصيل عمّا هو مخزَّن في
        السياق (لا استيراد لـ ui/ أو streamlit هنا) — أي ContextVar
        حالي أو مستقبلي ينتقل تلقائياً بنفس الطريقة.
        """
        ctx = contextvars.copy_context()
        return executor.submit(ctx.run, self._process_cell, cell_obj, filters, ai_rules)

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
        تنفيذ كل خلايا اللوحة المُهيَّأة والمرئية فعلياً حسب القالب
        الحالي من جديد، مع تطبيق قيود كل الـ Slicers المُفعَّلة،
        وبالتوازي عبر ThreadPoolExecutor حيثما كان ذلك آمناً ومفيداً
        (راجع توثيق الوحدة أعلاه).

        🆕 الخلايا المخفية (position خارج نطاق القالب الحالي — راجع
        _visible_cell_limit وupdate_dashboard_template أعلاه) تُستبعَد
        بالكامل من هذا التحديث: لا تُنفَّذ، لا تُستهلَك عليها استدعاءات
        AI، ولا تُكتب لها نتيجة جديدة — بياناتها المحفوظة سابقاً تبقى
        كما هي دون لمس حتى تعود للظهور لو أُعيد اختيار قالب يشملها.

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
        dashboard = self.db.get_dashboard(dashboard_id)
        if not dashboard:
            return {"ok": False, "error": "اللوحة غير موجودة"}

        visible_limit = self._visible_cell_limit(dashboard)

        rows = self.db.get_dashboard_cells(dashboard_id)
        configured_rows = [
            r for r in rows
            if r.get("question") and r["position"] < visible_limit
        ]

        if not configured_rows:
            return {"ok": False, "error": "لا توجد خلايا مُهيَّأة (ومرئية حسب القالب الحالي) في هذه اللوحة بعد"}

        cell_objs = [create_cell(r) for r in configured_rows]

        filters = self._build_active_filters(dashboard_id)
        total = len(cell_objs)
        done_count = [0]

        def track_progress():
            """يُستدعى فقط من الـ main thread (داخل حلقة as_completed أدناه)
            حتى لا تُستدعى أي دالة st.* من داخل worker thread — استدعاء
            Streamlit من ثريد غير الرئيسي يُصدر تحذير missing ScriptRunContext."""
            done_count[0] += 1
            if on_progress:
                try:
                    on_progress(done_count[0], total)
                except Exception as e:
                    logger.warning("on_progress callback failed: %s", e)

        story_cells = [c for c in cell_objs if c.display_type == "story"]
        other_cells = [c for c in cell_objs if c.display_type != "story"]

        all_outcomes = []

        # ── خلايا Story Telling: متوازية دائماً بغض النظر عن المحرك ──
        # ملاحظة: _process_cell لا يستدعي أي دالة st.* — فقط منطق بيانات
        # بحت (AI/DuckDB)، لذا تشغيله داخل thread آمن تماماً. التحديث
        # البصري (track_progress) يحدث فقط بعد استلام النتيجة في الـ
        # main thread عبر as_completed، وليس من داخل الـ thread نفسها.
        # 🆕 الإرسال يمر عبر _submit_with_context لنقل سياق اللوج الحالي
        # (اسم المستخدم) إلى كل thread عامل — راجع توثيق تلك الدالة أعلاه.
        if story_cells:
            workers = min(max_workers, max(1, len(story_cells)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="story") as ex:
                futures = {
                    self._submit_with_context(ex, c, filters, ai_rules): c
                    for c in story_cells
                }
                for f in as_completed(futures):
                    all_outcomes.append(f.result())
                    track_progress()

        # ── بقية الخلايا: متوازية فقط لو المحرك ليس Ollama ──
        if other_cells:
            if engine_name != "ollama":
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cell") as ex:
                    futures = {
                        self._submit_with_context(ex, c, filters, ai_rules): c
                        for c in other_cells
                    }
                    for f in as_completed(futures):
                        all_outcomes.append(f.result())
                        track_progress()
            else:
                # Ollama محلي — لا فائدة حقيقية من التوازي، نبقيه تسلسلياً
                # (تنفيذ مباشر في الـ main thread — لا حاجة لنقل سياق هنا)
                for c in other_cells:
                    all_outcomes.append(self._process_cell(c, filters, ai_rules))
                    track_progress()

        # ── كتابة النتائج تسلسلياً في project.db بعد انتهاء كل الـ threads ──
        # (تجنّباً لأي تزامن كتابة SQLite من عدة threads في وقت واحد)
        results = {}
        error_count = 0
        ai_calls = 0
        fast_updates = 0

        for outcome in all_outcomes:
            cell_obj = outcome["cell_obj"]
            position = outcome["position"]
            r = outcome["result"]

            if r.get("used_ai"):
                ai_calls += 1
            else:
                fast_updates += 1

            if r.get("ok"):
                stored = cell_obj.to_stored_dict(r)
                self.db.save_dashboard_cell_result(
                    dashboard_id, position, stored, r.get("sql"), None
                )
                # 🆕 لأي نوع خلية (بما فيها story): لو لا يوجد base_sql محفوظ بعد،
                # نحفظ ما يصلح كأساس للتحديث السريع القادم — SQL مفرد لبقية
                # الأنواع، أو JSON خطة الاستعلامات لـ story (base_queries_json).
                if not cell_obj.base_sql:
                    base_to_save = r.get("base_queries_json") or r.get("sql")
                    if base_to_save:
                        self.db.save_dashboard_cell_base_sql(dashboard_id, position, base_to_save)
                results[position] = stored
            else:
                error_count += 1
                self.db.save_dashboard_cell_result(
                    dashboard_id, position, None, r.get("sql"), r.get("error")
                )
                results[position] = None

        self.db.touch_dashboard(dashboard_id)
        logger.info(
            "Dashboard '%s' refreshed: %d visible cells, %d errors, %d AI calls, %d fast updates",
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
        لتحديث كل الخلايا. تُستدعى فقط من أزرار صريحة على خلايا مرئية
        فعلياً في الواجهة (ui/dashboards.py لا تعرض أزرار لخلايا مخفية
        أصلاً)، فلا حاجة لفحص visible_limit هنا بشكل منفصل.

        تُنفَّذ مباشرة في الـ main thread (بدون ThreadPoolExecutor) —
        لا حاجة لنقل سياق هنا لأن السياق الحالي (اسم المستخدم) هو
        السياق الصحيح بالفعل.

        يرجع: {"ok": True/False, "used_ai": True/False, "result": {...}}
               أو {"ok": False, "used_ai": ..., "error": "..."}
        """
        rows = {r["position"]: r for r in self.db.get_dashboard_cells(dashboard_id)}
        row = rows.get(position)
        if not row or not row.get("question"):
            return {"ok": False, "error": "الخلية غير مُهيَّأة"}

        cell_obj = create_cell(row)
        filters = self._build_active_filters(dashboard_id)
        outcome = self._process_cell(cell_obj, filters, ai_rules)
        r = outcome["result"]
        used_ai = bool(r.get("used_ai"))

        if r.get("ok"):
            stored = cell_obj.to_stored_dict(r)
            self.db.save_dashboard_cell_result(dashboard_id, position, stored, r.get("sql"), None)
            if not cell_obj.base_sql:
                base_to_save = r.get("base_queries_json") or r.get("sql")
                if base_to_save:
                    self.db.save_dashboard_cell_base_sql(dashboard_id, position, base_to_save)
            self.db.touch_dashboard(dashboard_id)
            return {"ok": True, "used_ai": used_ai, "result": stored}
        else:
            self.db.save_dashboard_cell_result(dashboard_id, position, None, r.get("sql"), r.get("error"))
            return {"ok": False, "used_ai": used_ai, "error": r.get("error")}

    # ──────────────────────────────────────────────────────────
    #  دوال داخلية
    # ──────────────────────────────────────────────────────────
    def _build_active_filters(self, dashboard_id: str) -> list:
        """
        يحوّل Slicers المُفعَّلة فعلياً (لها جدول+عمود+قيم) إلى صيغة
        الفلاتر التي يفهمها QueryEngine._build_where_clause.

        الآن مجرد غلاف رقيق فوق filters_from_slicer_rows (راجعها
        للتفاصيل الكاملة) — استُخرج المنطق منها ليكون قابلاً لإعادة
        الاستخدام مع صفوف Slicer لم تُحفظ بعد في project_db (مثل
        فلاتر شاشة "الإنشاء التلقائي" قبل وجود dashboard_id فعلي).
        """
        slicers = self.db.get_dashboard_slicers(dashboard_id)
        return self.filters_from_slicer_rows(slicers)

    def filters_from_slicer_rows(self, slicer_rows: list) -> list:
        """
        🆕 يحوّل قائمة صفوف Slicer خام (كل صف: {"table_name", "column_name",
        "selected_values"}) إلى صيغة الفلاتر التي يفهمها
        QueryEngine._build_where_clause — بدون أي اعتماد على وجود
        dashboard_id محفوظ في project_db، حتى يمكن استخدامها لكل من:
          (١) Slicers لوحة موجودة فعلاً (عبر _build_active_filters أعلاه)
          (٢) فلاتر مُختارة في شاشة "الإنشاء التلقائي" قبل إنشاء أي لوحة.

        فلتر التاريخ يُستخدم فيه نفس تخزين Slicer العادي (جدول + عمود +
        selected_values) — التمييز يعتمد فقط على كون العمود مُسجَّلاً
        كتاريخ فعلياً (_date_cols_{table}). في هذه الحالة selected_values
        تحمل عنصرين بالضبط [start, end] بدل قائمة قيم مفتوحة، فتُبنى
        كـ "date_range" بدل "values".
        """
        settings = self.db.get_settings()
        filters = []
        for s in slicer_rows:
            table = s.get("table_name")
            column = s.get("column_name")
            values = s.get("selected_values")
            if not table or not column or not values:
                continue

            date_cols = settings.get(f"_date_cols_{table}", [])
            if column in date_cols and len(values) == 2:
                filters.append({
                    "table": table,
                    "column": column,
                    "date_range": {"start": values[0], "end": values[1]},
                })
            else:
                filters.append({
                    "table": table,
                    "column": column,
                    "values": values,
                })
        return filters
    
    
    # ──────────────────────────────────────────────────────────
    #  🆕 بناء خطة لوحة كاملة تلقائياً بالذكاء الاصطناعي
    # ──────────────────────────────────────────────────────────
    def generate_dashboard_plan(self, description: str, ai_rules: Optional[str] = None,
                                 filters: Optional[list] = None) -> dict:
        """
        (نفس التوثيق السابق)

        🆕 filters (اختياري): فلاتر بصيغة QueryEngine القياسية (نفس
        شكل filters_from_slicer_rows) مُختارة من المستخدم في شاشة
        الإنشاء التلقائي قبل توليد الخطة — تُضاف كسياق للـ prompt حتى
        يقترح AI عناوين/أسئلة تتماشى مع نطاق البيانات المُصفّى فعلياً
        (مثلاً فترة زمنية محددة أو حالة معينة)، تماماً كما تُستخدم في
        PromptBuilder._build_filters لصفحة المحادثة. الفلاتر الفعلية
        تبقى تُطبَّق لاحقاً كـ Slicers حقيقية على اللوحة بعد إنشائها
        (راجع ui/dashboards.py) — هذا السياق هنا استرشادي فقط لتوليد
        أسئلة أكثر دقة، وليس تنفيذاً فعلياً للفلترة في هذه المرحلة.
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

        filters_text = ""
        if filters:
            filter_lines = ["الفلاتر النشطة التالية ستُطبَّق فعلياً على بيانات هذه اللوحة "
                             "(كل الاستعلامات ستُقيَّد بها تلقائياً) — اجعل الأسئلة والعناوين "
                             "منسجمة مع هذا النطاق دون تكرار ذكره حرفياً في كل سؤال:"]
            for f in filters:
                if f.get("date_range"):
                    filter_lines.append(
                        f'- الجدول "{f["table"]}"، العمود "{f["column"]}": من '
                        f'{f["date_range"]["start"]} إلى {f["date_range"]["end"]}'
                    )
                else:
                    vals = "، ".join(str(v) for v in (f.get("values") or []))
                    filter_lines.append(f'- الجدول "{f["table"]}"، العمود "{f["column"]}": {vals}')
            filters_text = "\n\nقيود الفلترة الحالية:\n" + "\n".join(filter_lines)

        prompt = f"""أنت مساعد لبناء لوحات معلومات (Dashboards). اختر قالباً واحداً فقط من
القوالب المتاحة أدناه بناءً على طلب المستخدم، ثم اقترح عنواناً وسؤالاً
طبيعياً واضحاً لكل خلية (٤ مؤشرات Gauges أعلى دائماً + خلايا القالب المختار).

القوالب المتاحة (اختر id واحداً فقط بالضبط كما هو مكتوب):
{templates_desc}

الجداول والأعمدة المتاحة في المشروع:
{schema_text}{filters_text}

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

        gauges = (gauges + [{"title": "", "question": ""}] * DASHBOARD_GAUGE_COUNT)[:DASHBOARD_GAUGE_COUNT]

        return {"ok": True, "template_id": template_id, "gauges": gauges, "cells": cells}
