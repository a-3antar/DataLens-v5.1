"""
ai/ai_manager.py
================
المدير المركزي للـ AI:
- اختيار المحرك المناسب
- بناء الـ Prompt
- إرسال السؤال مع retry loop عند الخطأ
- استخراج SQL من الرد
- تنفيذ SQL عبر QueryEngine
"""
import json
import time
import logging
from typing import Optional

from ai.base_engine               import BaseEngine
from ai.gemini                    import GeminiEngine
from ai.ollama                    import OllamaEngine
from ai.openai_compatible_engine  import OpenAICompatibleEngine, get_registry_entry
from ai.prompt_builder            import PromptBuilder
from core.project_db              import ProjectDB
from core.query_engine            import QueryEngine
from config import (
    OLLAMA_DEFAULT_URL, STORY_SAMPLE_ROWS_IN_PROMPT, AI_RETRY_DELAY_SECONDS,
    STORY_TIMEOUT_SECONDS, STORY_MAX_QUERIES,
)

logger = logging.getLogger(__name__)

_PERMANENT_ERROR_TYPES = {"auth"}


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
                                    tell_story() الكاملة.
    story_timeout                 : مهلة اتصال (بالثواني) لمرحلة توليد
                                    نص السرد فقط، مستقلة عن "timeout"
                                    الممرَّر للمحرك (المستخدم في SQL).
    """

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

    def _budget_exceeded(self, start_time: float, budget: Optional[int]) -> bool:
        """
        هل تجاوزنا (أو سنتجاوز بعد الانتظار القادم) الميزانية الزمنية
        الكلية لهذه العملية؟ بدون budget (None) دائماً False.
        """
        if not budget:
            return False
        elapsed = time.monotonic() - start_time
        return (elapsed + self.retry_delay) > budget

    # ──────────────────────────────────────────────────────────
    #  الدالة الرئيسية
    # ──────────────────────────────────────────────────────────

    def ask(
        self,
        question   : str,
        result_type: Optional[str] = None,
        ai_rules   : Optional[str] = None,
        filters    : Optional[list] = None,
    ) -> dict:
        """
        إرسال سؤال → SQL → تنفيذ → نتيجة.

        filters: قيود إضافية (من Slicers لوحة معلومات)، بصيغة
                 [{"table": "sales", "column": "المنطقة", "values": [...]}]
                 تُستخدم في مكانين معاً:
                 (١) كسياق توضيحي في الـ prompt المُرسَل إلى AI (عبر
                     PromptBuilder._build_filters) — مفيد لكن غير مُلزم.
                 (٢) 🆕 تُمرَّر فعلياً إلى self.qe.run() عند تنفيذ الـ
                     SQL الناتج — وهي الضمانة الحقيقية: QueryEngine
                     يبني view مفلترة فوق كل جدول له فلتر نشط قبل
                     التنفيذ، بغض النظر عمّا كتبه AI بالضبط.

        يرجع:
        {
            "ok"         : True/False,
            "sql"        : "SELECT ...",
            "df"         : DataFrame,          ← عند النجاح
            "rows"       : N,
            "tries"      : عدد المحاولات الفعلي,
            "error"      : "...",              ← عند الفشل
            "error_type" : "auth"|"rate_limit"|"transient"|"other",
        }
        """
        question = question.strip()
        if not question:
            return {"ok": False, "error": "السؤال فارغ", "tries": 0}

        schema    = self.db.get_schema()
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
        prompt = builder.build(question, result_type, filters=filters)

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
                continue

            last_sql = extract_result["sql"]
            logger.info("SQL extracted (attempt %d): %s...", attempt, last_sql[:60])

            # 🆕 filters تُمرَّر هنا فعلياً — QueryEngine يبني الـ views
            # المفلترة قبل تنفيذ last_sql، بغض النظر عن محتواه.
            run_result = self.qe.run(last_sql, filters=filters)
            if run_result["ok"]:
                logger.info(
                    "Query succeeded on attempt %d: %d rows",
                    attempt, run_result["rows"]
                )
                executed_sql = run_result.get("sql", last_sql)
                return {
                    "ok"        : True,
                    "sql"       : executed_sql,
                    "df"        : run_result["df"],
                    "rows"      : run_result["rows"],
                    "tries"     : attempt,
                    "auto_fixes": run_result.get("auto_fixes"),
                }

            last_error = run_result["error"]
            last_error_type = None
            logger.warning("SQL execution failed (attempt %d): %s", attempt, last_error)

            if attempt < self.max_tries:
                prompt = builder.build_error_retry(
                    original_prompt = prompt,
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
    #  دوال مساعدة
    # ──────────────────────────────────────────────────────────

    def get_models(self) -> dict:
        """إرجاع النماذج المتاحة من المحرك الحالي."""
        return self.engine.get_models()

    def engine_status(self) -> dict:
        """فحص حالة المحرك الحالي."""
        return self.engine.status()


    # ──────────────────────────────────────────────────────────
    #  السرد القصصي (Story Telling)
    # ──────────────────────────────────────────────────────────
    def tell_story(
        self,
        question: str,
        ai_rules: Optional[str] = None,
        filters : Optional[list] = None,
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
            "base_queries_json": (فقط لو تم توليد خطة جديدة الآن) — نص
                JSON جاهز للحفظ كـ base_sql لهذه الخلية،
            "error"   : "..."  ← عند الفشل (أي مرحلة)
        }
        """
        story_start = time.monotonic()
        question = question.strip()
        if not question:
            return {"ok": False, "error": "السؤال فارغ", "tries": 0}

        schema = self.db.get_schema()
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
            prompt = builder.build_story_plan(question, filters=filters)

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
        for q in queries_plan:
            sql = str(q.get("sql", "")).strip()
            title = str(q.get("title") or "بيانات").strip()
            if not sql:
                continue
            run_result = self.qe.run(sql, filters=filters)
            if run_result["ok"]:
                ok_count += 1
                executed.append({
                    "title": title, "sql": run_result.get("sql", sql),
                    "ok": True, "df": run_result["df"], "rows": run_result["rows"],
                })
            else:
                executed.append({
                    "title": title, "sql": sql,
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
        if generated_plan:
            result["base_queries_json"] = json.dumps(
                [{"title": q.get("title") or "بيانات", "sql": str(q.get("sql", "")).strip()}
                 for q in queries_plan if str(q.get("sql", "")).strip()],
                ensure_ascii=False,
            )
        return result

    def _parse_story_queries(self, text: str) -> dict:
        """تحليل رد AI (خطة الاستعلامات) كـ JSON، مع تقييد العدد بحد STORY_MAX_QUERIES."""
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
        for q in queries[:STORY_MAX_QUERIES]:
            if not isinstance(q, dict):
                continue
            sql = str(q.get("sql", "")).strip()
            title = str(q.get("title", "")).strip() or "بيانات"
            if sql:
                cleaned.append({"title": title, "sql": sql})

        if not cleaned:
            return {"ok": False, "error": "لا يوجد استعلام SQL صالح ضمن الخطة"}

        return {"ok": True, "queries": cleaned}


# ══════════════════════════════════════════════════════════════
#  🆕 بناء AIManager جاهز من إعدادات مشروع مباشرة
# ══════════════════════════════════════════════════════════════

def build_ai_manager(db: ProjectDB):
    """
    بناء AIManager جاهز من إعدادات مشروع (project.db) مباشرة — نقطة
    مشتركة واحدة يستخدمها كل من ui/dashboards.py (معرض اللوحات،
    الإنشاء التلقائي بالذكاء الاصطناعي) وcore/dashboard_cells/base.py
    (زر "اختبار" داخل محرر الخلية)، بدل تكرار نفس منطق قراءة
    الإعدادات وبناء المحرك في أكثر من مكان.

    يرجع: (ai_manager: AIManager, settings: dict)
    """
    settings = db.get_settings()
    engine_name = settings.get("ai_engine", "gemini")
    engine = get_engine(
        engine_name=engine_name,
        api_key=settings.get(f"api_key_{engine_name}", ""),
        model=settings.get("model", ""),
        timeout=settings.get("timeout", 30),
        ollama_url=settings.get("ollama_url", "http://localhost:11434"),
    )
    ai = AIManager(
        db, engine,
        temperature=settings.get("temperature", 0.1),
        max_tries=settings.get("max_tries", 3),
        retry_delay=settings.get("retry_delay", 10),
        max_total_wait_seconds=settings.get("max_total_wait_seconds", 0),
        story_max_total_wait_seconds=settings.get("story_max_total_wait_seconds", 0),
        story_timeout=settings.get("story_timeout", 45),
    )
    return ai, settings
