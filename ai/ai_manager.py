"""
ai/ai_manager.py
================
المدير المركزي للـ AI:
- اختيار المحرك المناسب
- بناء الـ Prompt
- إرسال السؤال مع retry loop عند الخطأ
- استخراج SQL من الرد
- تنفيذ SQL عبر QueryEngine

🧹 تنظيف: حُذفت get_models()/engine_status() من AIManager — كانتا غير
مستخدمتين في أي مكان بالمشروع (جلب النماذج وفحص حالة المحرك يتمّان
عبر استدعاء engine.get_models()/engine.status() مباشرة في ui/settings.py).

🆕 سؤال وجواب (answer_question):
-----------------------------------
نوع نتيجة جديد "qa" — راجع توثيق الدالة أدناه. لا تغيير على ask()
ولا tell_story(): _parse_story_queries فقط حصلت على معامل اختياري
max_queries (افتراضيه STORY_MAX_QUERIES = السلوك القديم بالضبط).
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
from core.dashboard_views import DashboardViews
from config import (
    OLLAMA_DEFAULT_URL, STORY_SAMPLE_ROWS_IN_PROMPT, AI_RETRY_DELAY_SECONDS,
    STORY_TIMEOUT_SECONDS, STORY_MAX_QUERIES, QA_MAX_QUERIES, QA_HISTORY_TURNS,
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
                                    tell_story() الكاملة (وتُستخدم أيضاً
                                    لعملية answer_question()).
    story_timeout                 : مهلة اتصال (بالثواني) لمرحلة توليد
                                    نص السرد فقط، مستقلة عن "timeout"
                                    الممرَّر للمحرك (المستخدم في SQL)،
                                    وتُستخدم أيضاً لمرحلة كتابة إجابة
                                    answer_question().
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
        dashboard_id: Optional[str] = None,
    ) -> dict:
        """
        إرسال سؤال → SQL → تنفيذ → نتيجة.

        dashboard_id: معرّف لوحة المعلومات (اختياري). عند تمريره يعمل
                 الاستعلام على بيانات اللوحة بعد تطبيق فلاترها (views في
                 core/dashboard_views.py). فلاتر اللوحة لا تُرسَل للذكاء
                 الاصطناعي إطلاقاً ولا تظهر في الـ SQL؛ هو يكتب WHERE فقط
                 لشروط ذكرها المستخدم في سؤال الخلية نفسه.

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
        prompt = builder.build(question, result_type)

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

            # الفلترة تحدث في طبقة البيانات (views اللوحة) وليس في الـ SQL.
            run_result = self.qe.run(last_sql, dashboard_id=dashboard_id)
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
    #  السرد القصصي (Story Telling)
    # ──────────────────────────────────────────────────────────
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
            prompt = builder.build_story_plan(question)

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
            run_result = self.qe.run(sql, dashboard_id=dashboard_id)
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

    # ──────────────────────────────────────────────────────────
    #  schema بعد تطبيق فلاتر لوحة المعلومات (إن وُجدت)
    # ──────────────────────────────────────────────────────────
    def _schema(self, dashboard_id: Optional[str] = None) -> dict:
        """
        schema المشروع (الأعمدة والأنواع) مع عينة صفوف مأخوذة من بيانات
        اللوحة بعد فلاترها — حتى لا يرى الذكاء الاصطناعي أي صف خارج
        الفلتر. بدون dashboard_id: مطابق تماماً لـ db.get_schema().
        """
        return DashboardViews(self.db).get_schema(dashboard_id)

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
    def answer_question(
        self,
        question     : str,
        ai_rules     : Optional[str] = None,
        answer_length: str = "short",
        history      : Optional[list] = None,
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

        سياسة الفشل: تفشل العملية فقط لو لم ينجح أي استعلام؛ لو نجح
        بعضها، تُكتب الإجابة من الناجح فقط ويُبلَّغ AI بالفاشل (قسم
        FAILED QUERIES) ليذكره كقيد بدل بناء أرقام عليه.

        يرجع (نفس شكل tell_story مع "answer" بدل "story"):
        {
            "ok", "tries", "queries": [...], "answer": "Markdown",
            "answer_length", "rows", "sql", "df",
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
        prompt = builder.build_qa_plan(question, history=history_turns)
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
        for q in queries_plan:
            sql = str(q.get("sql", "")).strip()
            title = str(q.get("title") or "بيانات").strip()
            if not sql:
                continue
            run_result = self.qe.run(sql)
            if run_result["ok"]:
                executed.append({
                    "title": title, "sql": run_result.get("sql", sql),
                    "ok": True, "df": run_result["df"], "rows": run_result["rows"],
                })
            else:
                executed.append({
                    "title": title, "sql": sql,
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

        return {
            "ok": True,
            "tries": tries,
            "queries": executed,
            "answer": answer_text,
            "answer_length": answer_length,
            "rows": sum(e["rows"] for e in successful),
            "sql": "\n\n".join(f"-- {e['title']}\n{e['sql']}" for e in successful),
            "df": successful[0]["df"],
        }


# ══════════════════════════════════════════════════════════════
#  🆕 بناء AIManager جاهز من إعدادات مشروع مباشرة
# ══════════════════════════════════════════════════════════════

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
