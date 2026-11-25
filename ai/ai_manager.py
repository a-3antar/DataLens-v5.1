"""
ai/ai_manager.py
================
المدير المركزي للـ AI:
- اختيار المحرك المناسب
- بناء الـ Prompt
- إرسال السؤال مع retry loop عند الخطأ
- استخراج SQL من الرد
- تنفيذ SQL عبر QueryEngine

🆕 سؤال وجواب (answer_question):
-----------------------------------
نوع نتيجة جديد "qa" — راجع توثيق الدالة أدناه. لا تغيير على ask()
ولا tell_story(): _parse_story_queries فقط حصلت على معامل اختياري
max_queries (افتراضيه STORY_MAX_QUERIES = السلوك القديم بالضبط).

🆕 ذاكرة المحادثة الداخلية (ملخص تراكمي):
-------------------------------------------
- answer_question تستقبل summary (اختياري) — ملخص تراكمي لكل المحادثة
  السابقة، يُمرَّر لمرحلتي الخطة والإجابة مع آخر QA_HISTORY_TURNS
  سؤال/جواب، فيكون كل رد متابعة لما سبق حتى لو خرجت الأسئلة القديمة
  من النافذة.
- summarize_qa_memory: استفسار AI منفصل تماماً عن answer_question —
  تُستدعى من ui/chat.py بعد عرض نتيجة السؤال الأخير. فشلها لا يؤثر
  على أي إجابة: تُسجَّل في اللوج وتُرجع {"ok": False}.
- الذاكرة والملخص داخليان بالكامل: لا يُعرضان ولا يُعدَّلان من المستخدم.

🆕 الذاكرة المعرفية (core/knowledge_manager.py):
-------------------------------------------------
ask/tell_story/answer_question تضيف سياق المعرفة المخزَّن للمشروع إلى الـ prompt
(قسم DATA CONTEXT) عبر _knowledge_context — صامت: لا معرفة أو أي فشل = سلوك
سابق بلا تغيير. طبقتا المعرفة ب وج تستدعيان _run_planned_query بحرارة 0.0 صريحة
(معامل temperature) بدل self.temperature.

🆕 Smart Value Resolver (القيم النصية التي لا تطابق البيانات):
------------------------------------------------------------------
ask(resolve_values=True) — يُفعَّل من صفحة المحادثة فقط — إن نجح تنفيذ
SQL ورجع 0 صف، يُمرَّر الـ SQL إلى core.value_resolver.ValueResolver
(راجع ذلك الملف للمنطق الكامل) لاكتشاف شرط نصي على عمود تصنيفي لا
تطابق قيمته البيانات الفعلية:
  - تصحيح تلقائي مؤكد → يُعاد تنفيذ الـ SQL المصحَّح مرة واحدة،
    وتُضاف للنتيجة "value_fixes" لإظهار التصحيح للمستخدم.
  - أكثر من احتمال → تبقى النتيجة 0 صف وتُضاف "value_resolution"
    (status/items/sql الأصلي) ليعرض ui/chat.py قائمة الاختيار.
    بعد اختيار المستخدم تُنفِّذ apply_value_choices الـ SQL المعدَّل
    (تعديل مواضع الـ literals فقط) دون أي استدعاء AI جديد لتوليد SQL.
  - لا مرشح → "value_resolution" بحالة not_found لإبلاغ المستخدم.
خطأ تنفيذ SQL لا يمر بهذا المسار إطلاقاً (يبقى خطأً كما هو)، وأي
استثناء داخل الـ Resolver يُسجَّل فقط ولا يكسر ask(). الافتراضي
resolve_values=False فلا يتغير سلوك لوحات المعلومات.
الوحيد المستخدم من AI هنا: _suggest_value_variants — استدعاء واحد
قصير عند الحاجة فقط لاقتراح كتابات بديلة (ترجمة/تحويل حروف) تُتحقق
لاحقاً من وجودها فعلاً في العمود.

🆕 Value Resolver داخل خطط الاستعلامات (سؤال وجواب + Story):
-----------------------------------------------------------------
الاستعلامات الفرعية في خطة answer_question/tell_story (تُنفَّذ عبر
_run_planned_query) كانت لا تمر بالـ Resolver، فشرط مثل
ILIKE '%سانا جاز%' على قيمة مخزَّنة "SANA - GAS" يرجع 0 صف بصمت ويُنتج
إجابة متناقضة ("لا توجد بيانات" مع أرقام العميل من استعلام آخر).
الآن _run_planned_query(resolve_values=True) تعالج نتيجة الصفر عبر
_resolve_planned_zero_rows، وبما أنه لا يوجد مستخدم ليختار وسط خطة
متعددة الاستعلامات:
  - تصحيح مؤكد → يُطبَّق ويُعاد التنفيذ، وتُجمَّع "value_fixes" في
    نتيجة العملية (يعرضها ui/chat.py كسطر ملاحظة).
  - غير مؤكد → لا استبدال، بل "value_hint" نصي بأقرب القيم الموجودة
    يُمرَّر لمرحلة كتابة الإجابة (قسم NOTES) كي لا تُبنى خلاصة
    "لا توجد بيانات" على عدم تطابق كتابة لا أكثر.
"""

from core.logger_config import trace_calls

import re
import json
import time
import logging
from difflib import SequenceMatcher
from typing import Optional

from ai.base_engine               import BaseEngine
from ai.gemini                    import GeminiEngine
from ai.ollama                    import OllamaEngine
from ai.openai_compatible_engine  import OpenAICompatibleEngine, get_registry_entry
from ai.prompt_builder            import PromptBuilder
from core.project_db              import ProjectDB
from core.query_engine            import QueryEngine
from core.dashboard_views import DashboardViews
from core.value_resolver import ValueResolver, build_variants_prompt, parse_variants_response
from core.knowledge_manager import KnowledgeManager
from config import (
    OLLAMA_DEFAULT_URL, OLLAMA_TIMEOUT_SECONDS, STORY_SAMPLE_ROWS_IN_PROMPT, AI_RETRY_DELAY_SECONDS,
    STORY_TIMEOUT_SECONDS, STORY_MAX_QUERIES, QA_MAX_QUERIES, QA_HISTORY_TURNS,
    QA_SUMMARY_MAX_CHARS,
)

logger = logging.getLogger(__name__)

_PERMANENT_ERROR_TYPES = {"auth"}

# 🆕 أقصى عدد محاولات لتحديث الملخص التراكمي — عمداً أقل من max_tries:
# التلخيص عملية مساندة تأتي بعد ظهور الإجابة، ولا يجب أن تُطيل انتظار
# المستخدم بإعادة محاولات كثيرة.
_QA_SUMMARY_MAX_ATTEMPTS = 2

# 🆕 أقصى عدد قيم مرشحة تُذكر في تلميح value_hint لكل شرط (يُرسَل للـ AI)
_HINT_MAX_CANDIDATES = 5

# 🆕 تصحيح أسماء الأعمدة تلقائياً من الـ Schema (راجع _try_schema_column_fix).
# رسالة DuckDB 'Table "t1" does not have a column named "X"' لا تحتوي
# Candidate bindings فلا يلتقطها تصحيح QueryEngine — فنطابق الاسم الخاطئ
# مع أعمدة الـ Schema الفعلية هنا بعد توحيد الإملاء العربي (ه/ة، أ/ا...).
_MISSING_COL_NAMED_RE = re.compile(r'does not have a column named "([^"]+)"', re.IGNORECASE)
_AR_NORMALIZE = str.maketrans({"ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ؤ": "و", "ئ": "ي"})
_SCHEMA_FIX_MIN_RATIO = 0.85
_MAX_SCHEMA_FIX_STEPS = 3


@trace_calls
def get_engine(
    engine_name: str,
    api_key    : str = "",
    model      : str = "",
    timeout    : int = 30,
    ollama_url : str = OLLAMA_DEFAULT_URL,
) -> Optional[BaseEngine]:
    """
    إرجاع محرك AI بناءً على الاسم.
    "gemini" و"ollama": كلاس مخصص لكل منهما (بروتوكول مختلف).
    أي اسم آخر: يُبحث عنه في OPENAI_COMPATIBLE_ENGINES ويُبنى عبر
    OpenAICompatibleEngine — يشمل هذا "groq" و"openrouter" حالياً،
    وأي محرك يُضاف مستقبلاً للسجل بدون تعديل هذه الدالة.
    يرجع None لو الاسم غير معروف في أي من المسارين.
    """
    name = engine_name.lower().strip()
    if name == "gemini":
        return GeminiEngine(api_key=api_key, model=model, timeout=timeout)
    if name == "ollama":
        return OllamaEngine(base_url=ollama_url, model=model, timeout=timeout)

    entry = get_registry_entry(name)
    if entry:
        return OpenAICompatibleEngine(
            base_url    =entry["base_url"],
            api_key     =api_key,
            model       =model or entry.get("default_model", ""),
            timeout     =timeout,
            display_name=entry.get("display_name", name),
        )

    logger.error("Unknown engine: %s", engine_name)
    return None


class AIManager:
    """
    المدير المركزي لعمليات الـ AI.

    الاستخدام:
        ai = AIManager(
            db, engine, temperature=0.1, max_tries=3, retry_delay=10,
            max_total_wait_seconds=0, story_max_total_wait_seconds=0,
            story_timeout=45,
        )
        result = ai.ask("ما إجمالي المبيعات؟", result_type="chart")

    max_total_wait_seconds       : 0/None = بدون حد. خلاف ذلك، ميزانية
                                    زمنية كلية لعملية ask() الواحدة.
    story_max_total_wait_seconds : نفس الفكرة لكن مستقلة لعملية
                                    tell_story() الكاملة (وتُستخدم أيضاً
                                    لعملية answer_question()).
    story_timeout                 : مهلة اتصال (بالثواني) لمرحلة توليد
                                    نص السرد فقط، مستقلة عن "timeout"
                                    الممرَّر للمحرك (المستخدم في SQL)،
                                    وتُستخدم أيضاً لمرحلة كتابة إجابة
                                    answer_question() ولاقتراح الكتابات
                                    البديلة في Smart Value Resolver.
    """

    @trace_calls
    def __init__(
        self,
        db         : ProjectDB,
        engine     : BaseEngine,
        temperature: float = 0.1,
        max_tries  : int   = 3,
        retry_delay: int   = AI_RETRY_DELAY_SECONDS,
        max_total_wait_seconds: Optional[int] = 0,
        story_max_total_wait_seconds: Optional[int] = 0,
        story_timeout: Optional[int] = None,
    ):
        self.db          = db
        self.engine      = engine
        self.temperature = temperature
        self.max_tries   = max(1, max_tries)
        self.retry_delay = max(0, retry_delay)
        self.max_total_wait_seconds = max_total_wait_seconds or None
        self.story_max_total_wait_seconds = story_max_total_wait_seconds or None
        self.story_timeout = story_timeout or STORY_TIMEOUT_SECONDS
        self.qe          = QueryEngine(db)

    # ──────────────────────────────────────────────────────────
    #  ميزانية الوقت الإجمالية
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def _budget_exceeded(self, start_time: float, budget: Optional[int]) -> bool:
        """
        هل تجاوزنا (أو سنتجاوز بعد الانتظار القادم) الميزانية الزمنية
        الكلية لهذه العملية؟ بدون budget (None) دائماً False.
        """
        if not budget:
            return False
        elapsed = time.monotonic() - start_time
        return (elapsed + self.retry_delay) > budget

    @trace_calls
    def _knowledge_context(self, question: str, schema: Optional[dict], mode: str,
                           dashboard_id: Optional[str] = None) -> Optional[str]:
        """سياق معرفة المشروع للـ prompt (None بصمت عند غيابه أو أي فشل)."""
        try:
            return KnowledgeManager(self.db).build_context(
                question, schema, mode=mode, filtered=bool(dashboard_id)) or None
        except Exception as e:
            logger.debug("knowledge context skipped: %s", e)
            return None

    # ──────────────────────────────────────────────────────────
    #  الدالة الرئيسية
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def ask(
        self,
        question   : str,
        result_type: Optional[str] = None,
        ai_rules   : Optional[str] = None,
        dashboard_id: Optional[str] = None,
        resolve_values: bool = False,
    ) -> dict:
        """
        إرسال سؤال → SQL → تنفيذ → نتيجة.

        dashboard_id: معرّف لوحة المعلومات (اختياري). عند تمريره يعمل
                 الاستعلام على بيانات اللوحة بعد تطبيق فلاترها (views في
                 core/dashboard_views.py). فلاتر اللوحة لا تُرسَل للذكاء
                 الاصطناعي إطلاقاً ولا تظهر في الـ SQL؛ هو يكتب WHERE فقط
                 لشروط ذكرها المستخدم في سؤال الخلية نفسه.

        resolve_values: 🆕 تفعيل Smart Value Resolver عندما ينجح التنفيذ
                 ويرجع 0 صف (راجع توثيق الوحدة أعلاه). الافتراضي False.

        يرجع:
        {
            "ok"         : True/False,
            "sql"        : "SELECT ...",
            "df"         : DataFrame,          ← عند النجاح
            "rows"       : N,
            "tries"      : عدد المحاولات الفعلي,
            "value_fixes"     : [...]  ← (اختياري) قيم صُحّحت تلقائياً,
            "value_resolution": {...}  ← (اختياري) اقتراحات تنتظر اختيار المستخدم,
            "error"      : "...",              ← عند الفشل
            "error_type" : "auth"|"rate_limit"|"transient"|"other",
        }
        """
        question = question.strip()
        if not question:
            return {"ok": False, "error": "السؤال فارغ", "tries": 0}

        schema    = self._schema(dashboard_id)
        relations = self.db.get_relations()

        if not schema:
            return {
                "ok"   : False,
                "error": "لا توجد جداول محملة في المشروع",
                "tries": 0,
            }

        builder = PromptBuilder(
            schema    = schema,
            relations = relations,
            ai_rules  = ai_rules,
        )
        prompt = builder.build(
            question, result_type,
            knowledge_context=self._knowledge_context(question, schema, "sql", dashboard_id),
        )
        # نحتفظ بالـ prompt الأساسي؛ كل محاولة تصحيح تبدأ منه ولا تراكم
        # رسائل الأخطاء السابقة داخل سياق يتضخم مع كل محاولة.
        base_prompt = prompt

        last_error = ""
        last_error_type = None
        last_sql   = ""
        attempt    = 0
        start_time = time.monotonic()

        for attempt in range(1, self.max_tries + 1):
            logger.info("AI attempt %d/%d", attempt, self.max_tries)

            send_result = self.engine.send(prompt, self.temperature)
            if not send_result["ok"]:
                last_error = send_result["error"]
                last_error_type = send_result.get("error_type")
                logger.warning("Engine send failed (attempt %d): %s", attempt, last_error)

                if last_error_type in _PERMANENT_ERROR_TYPES:
                    logger.error(
                        "Permanent error (%s) — stopping without further retries",
                        last_error_type
                    )
                    break

                if attempt < self.max_tries:
                    if self._budget_exceeded(start_time, self.max_total_wait_seconds):
                        logger.warning(
                            "Total wait budget (%ds) exceeded — stopping retries early",
                            self.max_total_wait_seconds
                        )
                        last_error = (
                            f"{last_error} "
                            f"(تم إيقاف إعادة المحاولة بسبب تجاوز الحد الزمني الإجمالي "
                            f"المحدد: {self.max_total_wait_seconds} ثانية)"
                        )
                        break

                    logger.info(
                        "Waiting %ds before retry due to %s error...",
                        self.retry_delay, last_error_type or "connection"
                    )
                    if self.retry_delay > 0:
                        time.sleep(self.retry_delay)
                    continue
                else:
                    break

            extract_result = self.engine.extract_sql(send_result["text"])
            if not extract_result["ok"]:
                last_error = extract_result["error"]
                last_error_type = None
                logger.warning("SQL extraction failed (attempt %d): %s", attempt, last_error)
                if attempt < self.max_tries:
                    prompt = builder.build_error_retry(
                        original_prompt=base_prompt,
                        failed_sql=send_result.get("text", "")[:4000],
                        error_message=last_error,
                    )
                continue

            last_sql = extract_result["sql"]
            logger.info("SQL extracted (attempt %d): %s...", attempt, last_sql[:60])

            # الفلترة تحدث في طبقة البيانات (views اللوحة) وليس في الـ SQL.
            run_result = self.qe.run(last_sql, dashboard_id=dashboard_id)
            if run_result["ok"]:
                logger.info(
                    "Query succeeded on attempt %d: %d rows",
                    attempt, run_result["rows"]
                )
                executed_sql = run_result.get("sql", last_sql)
                result = {
                    "ok"        : True,
                    "sql"       : executed_sql,
                    "df"        : run_result["df"],
                    "rows"      : run_result["rows"],
                    "tries"     : attempt,
                    "auto_fixes": run_result.get("auto_fixes"),
                }
                # 🆕 نجاح التنفيذ بصفر صفوف (وليس خطأ SQL) → Smart Value Resolver
                if resolve_values and run_result["rows"] == 0:
                    result = self._resolve_zero_rows(result, dashboard_id)
                return result

            last_error = run_result["error"]
            last_error_type = None
            logger.warning("SQL execution failed (attempt %d): %s", attempt, last_error)

            if attempt < self.max_tries:
                prompt = builder.build_error_retry(
                    original_prompt = base_prompt,
                    failed_sql      = last_sql,
                    error_message   = last_error,
                )

        logger.error("All attempts failed (%d/%d). Last error: %s", attempt, self.max_tries, last_error)
        result = {
            "ok"   : False,
            "sql"  : last_sql,
            "error": last_error,
            "tries": attempt,
        }
        if last_error_type:
            result["error_type"] = last_error_type
        return result

    # ──────────────────────────────────────────────────────────
    #  🆕 Smart Value Resolver — التكامل مع ask()
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def _resolver_table_loader(self, dashboard_id: Optional[str] = None):
        """
        دالة تحميل الجداول التي يستهلكها ValueResolver: تقرأ الجدول بنفس
        الطريقة التي يراه بها QueryEngine (views اللوحة إن وُجد
        dashboard_id)، وتقتصر على جداول المشروع الفعلية فقط — أي اسم
        آخر وارد في SQL (عمود، CTE...) يُرجع None دون أي قراءة أو لوج خطأ.
        """
        aliases = {f["table_alias"].lower(): f["table_alias"] for f in self.db.get_files()}

        @trace_calls
        def loader(name: str):
            real = aliases.get(str(name).lower())
            if real is None:
                return None
            return self.qe.views.read_table(real, dashboard_id)

        return loader

    @trace_calls
    def _suggest_value_variants(self, wanted: str, table: str, column: str, samples: list) -> list:
        """
        اقتراح كتابات بديلة لقيمة (ترجمة/تحويل حروف) عبر استدعاء AI واحد
        قصير بلا إعادة محاولة: الفشل غير مؤثر (يبقى الـ Resolver على
        نتائجه المحلية). يُرسَل فقط النص المطلوب واسم الجدول/العمود وبضع
        قيم نموذجية — راجع core.value_resolver.build_variants_prompt.
        """
        prompt = build_variants_prompt(wanted, table, column, samples)
        r = self.engine.send(
            prompt, min(self.temperature, 0.2), timeout_override=self.story_timeout,
        )
        if not r["ok"]:
            logger.warning("Value variants request failed: %s", r.get("error"))
            return []
        return parse_variants_response(r["text"])

    @trace_calls
    def _resolve_zero_rows(self, result: dict, dashboard_id: Optional[str] = None) -> dict:
        """
        معالجة نتيجة ناجحة بصفر صفوف عبر ValueResolver (راجع توثيق الوحدة).
        لا ترمي استثناءً أبداً: أي خلل يُسجَّل وتُرجع النتيجة الأصلية كما هي.
        """
        try:
            resolver = ValueResolver(
                self._resolver_table_loader(dashboard_id), self._suggest_value_variants,
            )
            res = resolver.resolve(result["sql"])
            status = res["status"]

            if status == "auto_fixed":
                rerun = self.qe.run(res["sql"], dashboard_id=dashboard_id)
                if rerun["ok"]:
                    logger.info("Value auto-fixed: %s", res["fixes"])
                    result.update({
                        "sql": rerun.get("sql", res["sql"]),
                        "df": rerun["df"],
                        "rows": rerun["rows"],
                        "value_fixes": res["fixes"],
                    })
                else:
                    logger.warning("Re-run after value auto-fix failed: %s", rerun["error"])
            elif status in ("needs_choice", "not_found"):
                result["value_resolution"] = {
                    "status": status,
                    "items": res["items"],
                    "sql": result["sql"],
                    "message": res.get("message", ""),
                }
        except Exception as e:
            logger.warning("Smart Value Resolver failed (ignored): %s", e)
        return result

    @staticmethod
    @trace_calls
    def _format_value_hint(items: list) -> str:
        """
        🆕 صياغة تلميح نصي (للـ AI فقط) من عناصر ValueResolver غير المحسومة:
        لكل شرط لم يطابق البيانات تُذكر القيمة المكتوبة وأقرب القيم الموجودة
        فعلاً في العمود. يُستخدم في قسم NOTES بمرحلة كتابة الإجابة.
        """
        lines = []
        for it in items:
            cands = [c["value"] for c in (it.get("candidates") or [])[:_HINT_MAX_CANDIDATES]]
            if cands:
                lines.append(
                    f"الشرط «{it['wanted']}» على العمود {it['column']} لم يطابق أي صف حرفياً؛ "
                    f"أقرب قيم موجودة فعلاً في العمود: " + "، ".join(f"«{c}»" for c in cands)
                )
            else:
                lines.append(
                    f"الشرط «{it['wanted']}» على العمود {it['column']} لم يطابق أي صف "
                    f"ولا توجد قيمة قريبة منه في العمود"
                )
        return "؛ ".join(lines)

    @trace_calls
    def _resolve_planned_zero_rows(self, out: dict, sql: str, dashboard_id: Optional[str] = None) -> dict:
        """
        🆕 معالجة استعلام فرعي (من خطة QA/Story) رجع بصفر صفوف عبر
        ValueResolver. على عكس ask() لا يوجد مستخدم ليختار وسط خطة
        متعددة الاستعلامات، لذا:
          - تصحيح مؤكد → إعادة تنفيذ الـ SQL المصحَّح واستبدال النتيجة
            وإضافة "value_fixes".
          - غير ذلك → لا استبدال؛ تُضاف "value_hint" نصية بأقرب القيم
            الموجودة لتصل إلى مرحلة كتابة الإجابة.
        لا ترمي استثناءً أبداً: أي خلل يُسجَّل وتُرجع النتيجة كما هي.
        """
        try:
            resolver = ValueResolver(
                self._resolver_table_loader(dashboard_id), self._suggest_value_variants,
            )
            res = resolver.resolve(sql)
            status = res["status"]

            if status == "auto_fixed":
                rerun = self.qe.run(res["sql"], dashboard_id=dashboard_id)
                if rerun["ok"]:
                    logger.info("Planned query value auto-fixed: %s", res["fixes"])
                    out.update({
                        "sql": rerun.get("sql", res["sql"]),
                        "df": rerun["df"],
                        "rows": rerun["rows"],
                        "value_fixes": res["fixes"],
                    })
                else:
                    logger.warning("Re-run after planned value auto-fix failed: %s", rerun["error"])
            elif status in ("needs_choice", "not_found") and res.get("items"):
                hint = self._format_value_hint(res["items"])
                if hint:
                    out["value_hint"] = hint
        except Exception as e:
            logger.warning("Planned query value resolver failed (ignored): %s", e)
        return out

    @trace_calls
    def apply_value_choices(
        self, sql: str, items: list, choices: dict, dashboard_id: Optional[str] = None,
    ) -> dict:
        """
        تنفيذ SQL الأصلي بعد تطبيق اختيارات المستخدم لقيم الشروط
        (ValueResolver.apply_choices — يغيّر مواضع الـ literals المقصودة
        فقط، فتبقى بقية الشروط كما هي). لا يستدعي AI لتوليد SQL ولا
        يعيد تنفيذ أي استعلام غير الاستعلام المعدَّل نفسه.

        يرجع نفس شكل ask() الناجح: {"ok", "sql", "df", "rows", "value_fixes"}
        أو {"ok": False, "error": ...}.
        """
        applied = ValueResolver.apply_choices(sql, items, choices)
        if not applied["ok"]:
            return {"ok": False, "error": applied["error"]}

        run_result = self.qe.run(applied["sql"], dashboard_id=dashboard_id)
        if not run_result["ok"]:
            return {"ok": False, "sql": applied["sql"], "error": run_result["error"]}

        logger.info("Value choices applied: %s", applied["fixes"])
        return {
            "ok": True,
            "sql": run_result.get("sql", applied["sql"]),
            "df": run_result["df"],
            "rows": run_result["rows"],
            "auto_fixes": run_result.get("auto_fixes"),
            "value_fixes": applied["fixes"],
        }

    # ──────────────────────────────────────────────────────────
    #  السرد القصصي (Story Telling)
    # ──────────────────────────────────────────────────────────
    @trace_calls
    def tell_story(
        self,
        question: str,
        ai_rules: Optional[str] = None,
        dashboard_id: Optional[str] = None,
        base_queries: Optional[list] = None,
    ) -> dict:
        """
        سؤال → خطة استعلامات SQL (عبر AI أو مُعادة استخدام base_queries
        المحفوظة مسبقاً) → تنفيذها كلها → تحليل نصي واحد بالعربية يربط
        بين نتائجها مجتمعة.

        base_queries: قائمة [{"title": "...", "sql": "..."}] محفوظة
        مسبقاً (base_sql لخلية لوحة معلومات من نوع story). لو مُمرَّرة،
        تُتخطّى مرحلة توليد الخطة عبر AI بالكامل — فقط تُنفَّذ الاستعلامات
        مع الفلاتر الحالية، ثم يُطلب من AI كتابة السرد فقط. هذا يجعل
        تحديث خلية Story في لوحة معلومات (بعد أول مرة) أسرع بكثير: AI
        call واحد فقط (السرد) بدل اثنين (خطة + سرد).

        عند تعديل نص السؤال لخلية لوحة معلومات، core.project_db.
        ProjectDB.save_dashboard_cell يُفرغ base_sql تلقائياً (نفس آلية
        بقية الأنواع) — فتُعاد توليد خطة استعلامات جديدة تلقائياً في
        أول تحديث تالٍ، دون أي تعديل إضافي مطلوب هنا.

        🆕 الاستعلامات التي ترجع بصفر صفوف بسبب قيمة نصية لا تطابق
        البيانات تُصحَّح تلقائياً عند التأكد (راجع _run_planned_query)؛
        ما صُحِّح يُعاد في "value_fixes".

        يرجع:
        {
            "ok"      : True/False,
            "queries" : [
                {"title": "...", "sql": "...", "ok": True, "df": DataFrame, "rows": N}
                أو
                {"title": "...", "sql": "...", "ok": False, "error": "..."},
                ...
            ],
            "story"   : "النص التحليلي بالعربية",   ← عند النجاح
            "sql"     : نص تجميعي لكل الاستعلامات الناجحة (للعرض فقط),
            "df"      : DataFrame أول استعلام ناجح (توافق خلفي مع بقية الكود),
            "rows"    : مجموع صفوف الاستعلامات الناجحة,
            "tries"   : عدد محاولات آخر مرحلة AI استُدعيت,
            "value_fixes": [...]  ← (اختياري) قيم صُحّحت تلقائياً,
            "base_queries_json": (فقط لو تم توليد خطة جديدة الآن) — نص
                JSON جاهز للحفظ كـ base_sql لهذه الخلية،
            "error"   : "..."  ← عند الفشل (أي مرحلة)
        }
        """
        story_start = time.monotonic()
        question = question.strip()
        if not question:
            return {"ok": False, "error": "السؤال فارغ", "tries": 0}

        schema = self._schema(dashboard_id)
        if not schema:
            return {"ok": False, "error": "لا توجد جداول محملة في المشروع", "tries": 0}

        generated_plan = False
        tries = 0
        queries_plan = base_queries

        # ── المرحلة ١: خطة الاستعلامات (تُتخطّى لو base_queries مُمرَّرة) ──
        if not queries_plan:
            generated_plan = True
            relations = self.db.get_relations()
            builder = PromptBuilder(schema=schema, relations=relations, ai_rules=ai_rules)
            prompt = builder.build_story_plan(
                question,
                knowledge_context=self._knowledge_context(question, schema, "sql", dashboard_id),
            )

            last_error = ""
            last_error_type = None

            for attempt in range(1, self.max_tries + 1):
                tries = attempt
                send_result = self.engine.send(prompt, self.temperature)

                if not send_result["ok"]:
                    last_error = send_result["error"]
                    last_error_type = send_result.get("error_type")
                    logger.warning("Story plan send failed (attempt %d): %s", attempt, last_error)

                    if last_error_type in _PERMANENT_ERROR_TYPES:
                        break
                    if attempt < self.max_tries:
                        if self._budget_exceeded(story_start, self.story_max_total_wait_seconds):
                            break
                        if self.retry_delay > 0:
                            time.sleep(self.retry_delay)
                        continue
                    break

                parsed = self._parse_story_queries(send_result["text"])
                if not parsed["ok"]:
                    last_error = parsed["error"]
                    last_error_type = None
                    logger.warning("Story plan parse failed (attempt %d): %s", attempt, last_error)
                    if attempt < self.max_tries:
                        prompt = builder.build_error_retry(
                            original_prompt=prompt,
                            failed_sql=send_result["text"][:800],
                            error_message=last_error,
                        )
                        continue
                    break

                queries_plan = parsed["queries"]
                break

            if not queries_plan:
                return {
                    "ok": False,
                    "error": last_error or "تعذر توليد خطة استعلامات السرد",
                    "tries": tries,
                    "error_type": last_error_type,
                }

        # ── المرحلة ٢: تنفيذ كل استعلام في الخطة ──
        executed = []
        ok_count = 0
        all_value_fixes = []
        # 🆕 تصحيح موحّد للاستعلامات الفاشلة (راجع _run_planned_query). تصحيح AI
        # فقط لخطة وُلِّدت الآن، لا لـ base_sql محفوظ لخلية لوحة.
        fix_builder = PromptBuilder(
            schema=schema, relations=self.db.get_relations(), ai_rules=ai_rules,
        )
        for q in queries_plan:
            sql = str(q.get("sql", "")).strip()
            title = str(q.get("title") or "بيانات").strip()
            if not sql:
                continue
            run_result = self._run_planned_query(
                sql, title, question, fix_builder, schema,
                dashboard_id=dashboard_id, allow_ai_fix=generated_plan,
                start_time=story_start, budget=self.story_max_total_wait_seconds,
                resolve_values=True,
            )
            if run_result["ok"]:
                ok_count += 1
                all_value_fixes.extend(run_result.get("value_fixes") or [])
                executed.append({
                    "title": title, "sql": run_result.get("sql", sql),
                    "ok": True, "df": run_result["df"], "rows": run_result["rows"],
                })
            else:
                executed.append({
                    "title": title, "sql": run_result.get("sql", sql),
                    "ok": False, "error": run_result["error"],
                })

        total = len(executed)
        if total == 0:
            return {"ok": False, "error": "لم يُرجع الذكاء الاصطناعي أي استعلامات صالحة", "tries": tries}

        fail_count = total - ok_count
        # 🆕 فشل العملية بالكامل لو فشل أكثر من نصف الاستعلامات الفعلية
        if fail_count > ok_count:
            errors_text = "؛ ".join(
                f"{e['title']}: {e.get('error', '')}" for e in executed if not e["ok"]
            )
            return {
                "ok": False,
                "error": f"فشل أكثر من نصف الاستعلامات ({fail_count}/{total}): {errors_text}",
                "tries": tries,
                "queries": executed,
            }

        successful = [e for e in executed if e["ok"]]

        # ── المرحلة ٣: السرد النصي المبني على كل نتائج الاستعلامات الناجحة ──
        story_builder = PromptBuilder(schema={}, relations=[], ai_rules=ai_rules)
        story_prompt = story_builder.build_story_multi(
            question,
            [{"title": e["title"], "df": e["df"]} for e in successful],
            max_rows=STORY_SAMPLE_ROWS_IN_PROMPT,
            knowledge_context=self._knowledge_context(question, schema, "answer", dashboard_id),
        )

        send_result = None
        for attempt in range(1, self.max_tries + 1):
            tries = attempt
            send_result = self.engine.send(
                story_prompt, self.temperature, timeout_override=self.story_timeout,
            )
            if send_result["ok"]:
                break

            error_type = send_result.get("error_type")
            logger.warning(
                "Story narration send failed (attempt %d/%d): %s",
                attempt, self.max_tries, send_result["error"],
            )
            if error_type in _PERMANENT_ERROR_TYPES:
                break
            if attempt < self.max_tries:
                if self._budget_exceeded(story_start, self.story_max_total_wait_seconds):
                    break
                if self.retry_delay > 0:
                    time.sleep(self.retry_delay)

        if not send_result or not send_result["ok"]:
            return {
                "ok": False,
                "error": f"فشل توليد التحليل النصي: {(send_result or {}).get('error', 'unknown')}",
                "tries": tries,
                "queries": executed,
                "error_type": (send_result or {}).get("error_type"),
            }

        story_text = self.engine.clean_response(send_result["text"]).strip()
        if not story_text:
            return {"ok": False, "error": "رد AI فارغ عند توليد التحليل النصي", "tries": tries, "queries": executed}

        result = {
            "ok": True,
            "tries": tries,
            "queries": executed,
            "story": story_text,
            "rows": sum(e["rows"] for e in successful),
            "sql": "\n\n".join(f"-- {e['title']}\n{e['sql']}" for e in successful),
            "df": successful[0]["df"],
        }
        if all_value_fixes:
            result["value_fixes"] = all_value_fixes
        if generated_plan:
            result["base_queries_json"] = json.dumps(
                [{"title": q.get("title") or "بيانات", "sql": str(q.get("sql", "")).strip()}
                 for q in queries_plan if str(q.get("sql", "")).strip()],
                ensure_ascii=False,
            )
        return result

    # ──────────────────────────────────────────────────────────
    #  schema بعد تطبيق فلاتر لوحة المعلومات (إن وُجدت)
    # ──────────────────────────────────────────────────────────
    @trace_calls
    def _schema(self, dashboard_id: Optional[str] = None) -> dict:
        """
        schema المشروع (الأعمدة والأنواع) مع عينة صفوف مأخوذة من بيانات
        اللوحة بعد فلاترها — حتى لا يرى الذكاء الاصطناعي أي صف خارج
        الفلتر. بدون dashboard_id: مطابق تماماً لـ db.get_schema().
        """
        return DashboardViews(self.db).get_schema(dashboard_id)

    # ──────────────────────────────────────────────────────────
    #  🆕 تنفيذ استعلام خطة (Story / Q&A) مع تصحيح — دالة موحّدة
    # ──────────────────────────────────────────────────────────

    @staticmethod
    @trace_calls
    def _norm_col(name) -> str:
        """توحيد اسم عمود للمقارنة: حروف صغيرة + توحيد الإملاء العربي (ه/ة، أ/ا...)."""
        return str(name).strip().lower().translate(_AR_NORMALIZE)

    @trace_calls
    def _try_schema_column_fix(self, sql: str, error_msg: str, schema: dict) -> Optional[str]:
        """
        تصحيح تلقائي (بدون AI) لاسم عمود غير موجود، بمطابقته مع أعمدة
        الـ Schema الفعلية. يغطي رسائل DuckDB التي لا تحمل Candidate
        bindings (مثل: Table "t1" does not have a column named "الحالة")
        فلا يلتقطها تصحيح QueryEngine.

        الثقة: مطابقة تامة بعد توحيد الإملاء (الحالة ← الحاله) لعمود وحيد،
        أو تشابه عالٍ (>= _SCHEMA_FIX_MIN_RATIO) بأفضل مرشح غير متعادل مع
        غيره — وإلا لا تصحيح (نتيجة خاطئة بصمت أخطر من فشل واضح يُصحَّح
        عبر AI). يرجع SQL مُصحَّحاً أو None.
        """
        match = _MISSING_COL_NAMED_RE.search(error_msg or "")
        if not match:
            return None
        wrong = match.group(1).strip()

        all_cols = {c for info in schema.values() for c in info.get("columns", {})}
        if not wrong or wrong in all_cols:
            return None

        wrong_norm = self._norm_col(wrong)
        exact = sorted({c for c in all_cols if self._norm_col(c) == wrong_norm})
        if len(exact) == 1:
            best = exact[0]
        else:
            scored = sorted(
                ((SequenceMatcher(None, wrong_norm, self._norm_col(c)).ratio(), c) for c in all_cols),
                reverse=True,
            )
            if not scored or scored[0][0] < _SCHEMA_FIX_MIN_RATIO:
                return None
            if len(scored) > 1 and scored[1][0] == scored[0][0]:
                return None   # غامض: مرشحان متساويان
            best = scored[0][1]

        quoted = re.compile(r'"' + re.escape(wrong) + r'"')
        unquoted = re.compile(r'(?<![\w"])' + re.escape(wrong) + r'(?![\w"])')
        fixed = quoted.sub(f'"{best}"', sql)
        fixed = unquoted.sub(f'"{best}"', fixed)
        if fixed == sql:
            return None

        logger.info("Schema column auto-fix: '%s' -> '%s'", wrong, best)
        return fixed

    @trace_calls
    def _run_planned_query(
        self,
        sql          : str,
        title        : str,
        question     : str,
        builder      : PromptBuilder,
        schema       : dict,
        dashboard_id : Optional[str] = None,
        allow_ai_fix : bool = True,
        start_time   : Optional[float] = None,
        budget       : Optional[int] = None,
        temperature  : Optional[float] = None,
        resolve_values: bool = False,
    ) -> dict:
        """
        تنفيذ استعلام واحد من خطة (Story/Q&A) مع نفس مستويات التصحيح
        المستخدمة في ask():
          ١) QueryEngine.run (يصحّح أسماء الأعمدة تلقائياً من Candidate bindings).
          ٢) _try_schema_column_fix: تصحيح من الـ Schema (حتى
             _MAX_SCHEMA_FIX_STEPS مرات) — مجاني، لا يستهلك محاولة.
          ٣) لو بقي الخطأ وallow_ai_fix: يُعاد الـ SQL الخاطئ مع رسالة الخطأ
             للـ AI عبر builder.build + build_error_retry، ثم يُنفَّذ
             الناتج. العدد الكلي للتنفيذات = self.max_tries (مثل ask()).
          ٤) 🆕 resolve_values: لو نجح التنفيذ ورجع 0 صف تُعالَج القيم
             النصية غير المطابقة عبر _resolve_planned_zero_rows (تصحيح
             مؤكد أو value_hint). افتراضياً False (طبقتا المعرفة ب وج
             لا تحتاجانه).

        يرجع: {"ok", "sql", "df", "rows", "ai_fixes"} عند النجاح
        (+ "value_fixes"/"value_hint" اختيارياً)، أو
        {"ok": False, "sql": آخر SQL جُرِّب, "error", "ai_fixes"}.
        """
        start_time = start_time or time.monotonic()
        current = sql
        executed_sql = None
        run = {"ok": False, "error": ""}
        base_prompt = None
        ai_fixes = 0

        for attempt in range(1, self.max_tries + 1):
            if current != executed_sql:
                run = self.qe.run(current, dashboard_id=dashboard_id)
                for _ in range(_MAX_SCHEMA_FIX_STEPS):
                    if run["ok"]:
                        break
                    fixed = self._try_schema_column_fix(current, run["error"], schema)
                    if not fixed:
                        break
                    current = fixed
                    run = self.qe.run(current, dashboard_id=dashboard_id)
                executed_sql = current

            if run["ok"]:
                out = {
                    "ok": True, "sql": run.get("sql", current),
                    "df": run["df"], "rows": run["rows"], "ai_fixes": ai_fixes,
                }
                if resolve_values and run["rows"] == 0:
                    out = self._resolve_planned_zero_rows(out, out["sql"], dashboard_id)
                return out

            logger.warning(
                "Planned query '%s' failed (attempt %d/%d): %s",
                title, attempt, self.max_tries, run["error"],
            )
            if not allow_ai_fix or attempt >= self.max_tries:
                break
            if self._budget_exceeded(start_time, budget):
                logger.warning("Total wait budget exceeded — skipping AI fix for '%s'", title)
                break

            if base_prompt is None:
                base_prompt = builder.build(f"{question}\n(الاستعلام المطلوب الآن: {title})")
            retry_prompt = builder.build_error_retry(
                original_prompt=base_prompt, failed_sql=current, error_message=run["error"],
            )

            send_result = self.engine.send(
                retry_prompt, temperature if temperature is not None else self.temperature,
            )
            if not send_result["ok"]:
                logger.warning("AI fix send failed for '%s': %s", title, send_result["error"])
                if send_result.get("error_type") in _PERMANENT_ERROR_TYPES:
                    break
                if self.retry_delay > 0:
                    time.sleep(self.retry_delay)
                continue

            extracted = self.engine.extract_sql(send_result["text"])
            if not extracted["ok"]:
                logger.warning("AI fix SQL extraction failed for '%s': %s", title, extracted["error"])
                continue

            current = extracted["sql"]
            ai_fixes += 1
            logger.info("AI fix %d for planned query '%s': %s...", ai_fixes, title, current[:60])

        return {"ok": False, "sql": current, "error": run["error"], "ai_fixes": ai_fixes}


    @trace_calls
    def _parse_story_queries(self, text: str, max_queries: int = STORY_MAX_QUERIES) -> dict:
        """
        تحليل رد AI (خطة الاستعلامات) كـ JSON، مع تقييد العدد بـ max_queries.
        الافتراضي STORY_MAX_QUERIES (سلوك Story Telling القديم بالضبط)؛
        answer_question تمرّر QA_MAX_QUERIES.
        """
        text = self.engine.clean_response(text)
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            data = json.loads(text[start:end])
        except Exception as e:
            return {"ok": False, "error": f"تعذر تحليل خطة الاستعلامات كـ JSON: {e}"}

        queries = data.get("queries")
        if not isinstance(queries, list) or not queries:
            return {"ok": False, "error": "لم يتم إرجاع أي استعلامات في الخطة"}

        cleaned = []
        for q in queries[:max_queries]:
            if not isinstance(q, dict):
                continue
            sql = str(q.get("sql", "")).strip()
            title = str(q.get("title", "")).strip() or "بيانات"
            if sql:
                cleaned.append({"title": title, "sql": sql})

        if not cleaned:
            return {"ok": False, "error": "لا يوجد استعلام SQL صالح ضمن الخطة"}

        return {"ok": True, "queries": cleaned}

    # ──────────────────────────────────────────────────────────
    #  🆕 سؤال وجواب (Q&A / رؤى)
    # ──────────────────────────────────────────────────────────
    @trace_calls
    def answer_question(
        self,
        question     : str,
        ai_rules     : Optional[str] = None,
        answer_length: str = "short",
        history      : Optional[list] = None,
        summary      : Optional[str] = None,
    ) -> dict:
        """
        سؤال حر عن البيانات → خطة استعلامات (بحث/فلترة/تجميع) → تنفيذها
        كلها → إجابة مباشرة + رؤى + رأي AI (Markdown).

        الفلاتر يحددها المستخدم داخل نص السؤال نفسه (يترجمها AI إلى
        WHERE) — لا Slicers هنا. لا سقف لعدد صفوف نتائج الاستعلامات
        المُرسَلة لـ AI (تظهر كاملة)؛ الـ prompt يطلب التجميع عند
        الحاجة (راجع PromptBuilder._qa_plan_rules).

        answer_length: "short" | "long" (راجع config.QA_ANSWER_LENGTHS).
        history: [{"question": "...", "answer": "..."}] — يُقتطع هنا
                 إلى آخر QA_HISTORY_TURNS عنصر فقط (config.py)؛ 0 =
                 بدون ذاكرة.
        summary: 🆕 ملخص تراكمي (نص) لكل المحادثة السابقة — يُمرَّر لمرحلتي
                 الخطة والإجابة ليحفظ ما سبق الأسئلة العشرة الأخيرة. داخلي
                 بالكامل (لا يُعرض للمستخدم).

        سياسة الفشل: تفشل العملية فقط لو لم ينجح أي استعلام؛ لو نجح
        بعضها، تُكتب الإجابة من الناجح فقط ويُبلَّغ AI بالفاشل (قسم
        FAILED QUERIES) ليذكره كقيد بدل بناء أرقام عليه.

        🆕 الاستعلامات التي رجعت بصفر صفوف بسبب قيمة نصية لا تطابق
        البيانات (مثل «سانا جاز» مقابل SANA - GAS) تمر بالـ Value
        Resolver: التصحيح المؤكد يُطبَّق ويُعاد في "value_fixes"، وغير
        المؤكد يصل كتلميح لقسم NOTES في مرحلة الإجابة.

        يرجع (نفس شكل tell_story مع "answer" بدل "story"):
        {
            "ok", "tries", "queries": [...], "answer": "Markdown",
            "answer_length", "rows", "sql", "df",
            "value_fixes" ← (اختياري),
            "error"/"error_type" ← عند الفشل
        }
        """
        qa_start = time.monotonic()
        question = question.strip()
        if not question:
            return {"ok": False, "error": "السؤال فارغ", "tries": 0}

        schema = self.db.get_schema()
        if not schema:
            return {"ok": False, "error": "لا توجد جداول محملة في المشروع", "tries": 0}

        history_turns = []
        if QA_HISTORY_TURNS > 0 and history:
            history_turns = list(history)[-QA_HISTORY_TURNS:]

        relations = self.db.get_relations()
        builder = PromptBuilder(schema=schema, relations=relations, ai_rules=ai_rules)

        # ── المرحلة ١: خطة الاستعلامات ──
        prompt = builder.build_qa_plan(
            question, history=history_turns, summary=summary,
            knowledge_context=self._knowledge_context(question, schema, "sql"),
        )
        queries_plan = None
        tries = 0
        last_error = ""
        last_error_type = None

        for attempt in range(1, self.max_tries + 1):
            tries = attempt
            send_result = self.engine.send(prompt, self.temperature)

            if not send_result["ok"]:
                last_error = send_result["error"]
                last_error_type = send_result.get("error_type")
                logger.warning("QA plan send failed (attempt %d): %s", attempt, last_error)

                if last_error_type in _PERMANENT_ERROR_TYPES:
                    break
                if attempt < self.max_tries:
                    if self._budget_exceeded(qa_start, self.story_max_total_wait_seconds):
                        break
                    if self.retry_delay > 0:
                        time.sleep(self.retry_delay)
                    continue
                break

            parsed = self._parse_story_queries(send_result["text"], max_queries=QA_MAX_QUERIES)
            if not parsed["ok"]:
                last_error = parsed["error"]
                last_error_type = None
                logger.warning("QA plan parse failed (attempt %d): %s", attempt, last_error)
                if attempt < self.max_tries:
                    prompt = builder.build_error_retry(
                        original_prompt=prompt,
                        failed_sql=send_result["text"][:800],
                        error_message=last_error,
                    )
                    continue
                break

            queries_plan = parsed["queries"]
            break

        if not queries_plan:
            return {
                "ok": False,
                "error": last_error or "تعذر توليد خطة استعلامات الإجابة",
                "tries": tries,
                "error_type": last_error_type,
            }

        # ── المرحلة ٢: تنفيذ كل استعلام (بدون Slicers) ──
        executed = []
        all_value_fixes = []
        value_notes = []
        # 🆕 كل استعلام يمر عبر الدالة الموحّدة _run_planned_query: تصحيح تلقائي
        # للأعمدة ثم إعادة الـ SQL الفاشل للـ AI (حتى self.max_tries) مثل ask()،
        # ثم Value Resolver لو رجع بصفر صفوف (resolve_values=True).
        for q in queries_plan:
            sql = str(q.get("sql", "")).strip()
            title = str(q.get("title") or "بيانات").strip()
            if not sql:
                continue
            run_result = self._run_planned_query(
                sql, title, question, builder, schema,
                start_time=qa_start, budget=self.story_max_total_wait_seconds,
                resolve_values=True,
            )
            if run_result["ok"]:
                all_value_fixes.extend(run_result.get("value_fixes") or [])
                if run_result.get("value_hint"):
                    value_notes.append(f"استعلام «{title}» رجع 0 صف: {run_result['value_hint']}")
                executed.append({
                    "title": title, "sql": run_result.get("sql", sql),
                    "ok": True, "df": run_result["df"], "rows": run_result["rows"],
                })
            else:
                executed.append({
                    "title": title, "sql": run_result.get("sql", sql),
                    "ok": False, "error": run_result["error"],
                })

        successful = [e for e in executed if e["ok"]]
        failed = [e for e in executed if not e["ok"]]

        if not executed:
            return {"ok": False, "error": "لم يُرجع الذكاء الاصطناعي أي استعلامات صالحة", "tries": tries}
        if not successful:
            errors_text = "؛ ".join(f"{e['title']}: {e.get('error', '')}" for e in failed)
            return {
                "ok": False,
                "error": f"فشلت كل الاستعلامات: {errors_text}",
                "tries": tries,
                "queries": executed,
            }

        # ── المرحلة ٣: كتابة الإجابة (بيانات كاملة بلا اقتطاع) ──
        answer_prompt = builder.build_qa_answer(
            question,
            [{"title": e["title"], "df": e["df"]} for e in successful],
            answer_length=answer_length,
            history=history_turns,
            failed=[{"title": e["title"], "error": e.get("error", "")} for e in failed],
            summary=summary,
            knowledge_context=self._knowledge_context(question, schema, "answer"),
            notes=value_notes,
        )

        send_result = None
        for attempt in range(1, self.max_tries + 1):
            tries = attempt
            send_result = self.engine.send(
                answer_prompt, self.temperature, timeout_override=self.story_timeout,
            )
            if send_result["ok"]:
                break

            error_type = send_result.get("error_type")
            logger.warning(
                "QA answer send failed (attempt %d/%d): %s",
                attempt, self.max_tries, send_result["error"],
            )
            if error_type in _PERMANENT_ERROR_TYPES:
                break
            if attempt < self.max_tries:
                if self._budget_exceeded(qa_start, self.story_max_total_wait_seconds):
                    break
                if self.retry_delay > 0:
                    time.sleep(self.retry_delay)

        if not send_result or not send_result["ok"]:
            return {
                "ok": False,
                "error": f"فشل توليد الإجابة: {(send_result or {}).get('error', 'unknown')}",
                "tries": tries,
                "queries": executed,
                "error_type": (send_result or {}).get("error_type"),
            }

        answer_text = self.engine.clean_response(send_result["text"]).strip()
        if not answer_text:
            return {"ok": False, "error": "رد AI فارغ عند توليد الإجابة", "tries": tries, "queries": executed}

        result = {
            "ok": True,
            "tries": tries,
            "queries": executed,
            "answer": answer_text,
            "answer_length": answer_length,
            "rows": sum(e["rows"] for e in successful),
            "sql": "\n\n".join(f"-- {e['title']}\n{e['sql']}" for e in successful),
            "df": successful[0]["df"],
        }
        if all_value_fixes:
            result["value_fixes"] = all_value_fixes
        return result


    # ──────────────────────────────────────────────────────────
    #  🆕 تحديث الملخص التراكمي لمحادثة سؤال وجواب (استفسار منفصل)
    # ──────────────────────────────────────────────────────────
    @trace_calls
    def summarize_qa_memory(
        self,
        old_summary: Optional[str],
        question   : str,
        answer     : str,
    ) -> dict:
        """
        دمج الملخص التراكمي السابق مع آخر سؤال وجواب في ملخص محدَّث
        (حد أقصى config.QA_SUMMARY_MAX_CHARS حرف) — استفسار AI منفصل
        تماماً عن answer_question، يُستدعى من ui/chat.py بعد عرض نتيجة
        السؤال الأخير ويُخزَّن ناتجه في session_state.

        مهلة الاتصال = story_timeout، وعدد المحاولات محدود
        (_QA_SUMMARY_MAX_ATTEMPTS) حتى لا يُطيل الانتظار. الفشل لا يؤثر
        على أي إجابة: يُسجَّل في اللوج ويُرجع {"ok": False} فيبقى
        المستدعي على الملخص القديم دون تغيير.

        يرجع: {"ok": True, "summary": "..."} أو {"ok": False, "error": "..."}
        """
        question = (question or "").strip()
        answer = (answer or "").strip()
        if not question or not answer:
            return {"ok": False, "error": "لا يوجد سؤال/جواب لتلخيصه"}

        builder = PromptBuilder(schema={}, relations=[])
        prompt = builder.build_qa_summary(old_summary, question, answer)

        attempts = min(self.max_tries, _QA_SUMMARY_MAX_ATTEMPTS)
        last_error = ""
        for attempt in range(1, attempts + 1):
            send_result = self.engine.send(
                prompt, self.temperature, timeout_override=self.story_timeout,
            )
            if send_result["ok"]:
                text = self.engine.clean_response(send_result["text"]).strip()
                if not text:
                    last_error = "رد AI فارغ عند تحديث الملخص"
                    logger.warning("QA summary empty (attempt %d/%d)", attempt, attempts)
                    continue

                if len(text) > QA_SUMMARY_MAX_CHARS:
                    # حماية: لو تجاوز النموذج الحد رغم التعليمات نحتفظ
                    # بالنهاية (الأحدث) ونقتطع من البداية (الأقدم)
                    text = text[-QA_SUMMARY_MAX_CHARS:]
                    newline_pos = text.find("\n")
                    if 0 <= newline_pos < len(text) // 2:
                        text = text[newline_pos + 1:]
                    text = text.strip()

                return {"ok": True, "summary": text}

            last_error = send_result.get("error", "unknown")
            logger.warning(
                "QA summary send failed (attempt %d/%d): %s", attempt, attempts, last_error,
            )
            if send_result.get("error_type") in _PERMANENT_ERROR_TYPES:
                break

        return {"ok": False, "error": last_error}


# ══════════════════════════════════════════════════════════════
#  🆕 بناء AIManager جاهز من إعدادات مشروع مباشرة
# ══════════════════════════════════════════════════════════════

@trace_calls
def build_ai_manager(db: ProjectDB):
    """
    بناء AIManager جاهز من إعدادات مشروع (project.db) مباشرة — نقطة
    مشتركة واحدة يستخدمها كل من ui/dashboards.py (معرض اللوحات،
    الإنشاء التلقائي بالذكاء الاصطناعي)، core/dashboard_cells/base.py
    (زر "اختبار" داخل محرر الخلية)، وui/chat.py (صفحة المحادثة) —
    بدل تكرار نفس منطق قراءة الإعدادات وبناء المحرك في أكثر من مكان.

    يرجع: (ai_manager: AIManager, settings: dict)
    """
    settings = db.get_settings()
    engine_name = settings.get("ai_engine", "gemini")
    # مهلة Ollama مستقلة عن مهلة المحركات السحابية، حتى لا يرث إعداداً
    # قصيراً (30 ثانية) لا يناسب التحميل والتوليد المحلي.
    effective_timeout = (
        settings.get("ollama_timeout", OLLAMA_TIMEOUT_SECONDS)
        if engine_name == "ollama" else settings.get("timeout", 30)
    )
    effective_story_timeout = settings.get("story_timeout", 45)
    if engine_name == "ollama":
        effective_story_timeout = max(effective_story_timeout, effective_timeout)

    engine = get_engine(
        engine_name=engine_name,
        api_key=settings.get(f"api_key_{engine_name}", ""),
        model=settings.get("model", ""),
        timeout=effective_timeout,
        ollama_url=settings.get("ollama_url", "http://localhost:11434"),
    )
    ai = AIManager(
        db, engine,
        temperature=settings.get("temperature", 0.1),
        max_tries=settings.get("max_tries", 3),
        retry_delay=settings.get("retry_delay", 10),
        max_total_wait_seconds=settings.get("max_total_wait_seconds", 0),
        story_max_total_wait_seconds=settings.get("story_max_total_wait_seconds", 0),
        story_timeout=effective_story_timeout,
    )
    return ai, settings