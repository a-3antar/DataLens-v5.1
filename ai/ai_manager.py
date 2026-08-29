"""
ai/ai_manager.py
================
المدير المركزي للـ AI:
- اختيار المحرك المناسب
- بناء الـ Prompt
- إرسال السؤال مع retry loop عند الخطأ
- استخراج SQL من الرد
- تنفيذ SQL عبر QueryEngine

إعادة المحاولة عند فشل الاتصال — دائم مقابل مؤقت:
-----------------------------------------------------
كل محرك AI يُرجع الآن (اختيارياً) "error_type" ضمن نتيجة send() الفاشلة:
  - "auth"      : خطأ مصادقة دائم (مفتاح مرفوض/غير صالح/عليه قيود). لا فائدة
                  إطلاقاً من إعادة المحاولة بنفس المفتاح — نتوقف فوراً بدل
                  استهلاك max_tries كاملة و retry_delay على كل واحدة.
  - "rate_limit": تجاوز حصة/معدل الطلبات (429) — يستحق إعادة المحاولة
                  (المشكلة قد تزول خلال ثوانٍ)، فنُبقيه ضمن حلقة الانتظار.
  - "transient" : انقطاع شبكة/مهلة/خطأ سيرفر عابر (5xx) — نفس المعاملة.
  - "other"/غير محدد: نتعامل معه بحذر كأنه قد يكون مؤقتاً.

حد زمني إجمالي (max_total_wait_seconds / story_max_total_wait_seconds):
--------------------------------------------------------------------------
بالإضافة إلى max_tries × retry_delay كحد أقصى نظري، يمكن الآن ضبط
ميزانية زمنية كلية اختيارية لكل عملية:
  - max_total_wait_seconds       : لعملية ask() الواحدة (توليد SQL)
  - story_max_total_wait_seconds : لعملية tell_story() الكاملة (توليد
                                    SQL ثم توليد نص السرد — مرحلتان)
0 أو None = بدون حد (السلوك القديم يبقى كما هو تماماً). لو ضُبطت قيمة
أكبر من صفر، يتوقف الانتظار قبل إعادة أي محاولة لو كان الوقت المُستهلك
حتى الآن + مدة الانتظار القادمة سيتجاوز هذه الميزانية — بدل الانتظار
الكامل ثم اكتشاف تجاوز الوقت لاحقاً بلا فائدة.

مهلة اتصال منفصلة لمرحلة السرد (story_timeout):
--------------------------------------------------
مرحلة توليد نص السرد (المرحلة الثانية من tell_story()) تستخدم الآن
مهلة اتصال (timeout) مستقلة تماماً عن "timeout" العام المستخدم في
توليد SQL، عبر تمرير timeout_override إلى engine.send(). هذا يحل
مشكلة انتظار مهلة طويلة (مثلاً 100 ثانية) على استدعاء أول فاشل قبل
إعادة المحاولة، رغم أن المحاولة الثانية عادة تنجح بسرعة معقولة.

🆕 تطبيق فعلي (وليس نصي فقط) لفلاتر لوحات المعلومات على SQL المولَّد
من AI:
--------------------------------------------------------------------
سابقاً كانت filters تُمرَّر فقط لبناء نص توجيهي داخل الـ prompt يطلب
من AI أن يضيف WHERE بنفسه (PromptBuilder._build_filters) — بدون أي
ضمان فعلي أن AI سيلتزم بذلك عند تنفيذ SQL الناتج (self.qe.run(...)
كانت تُستدعى بدون تمرير filters إطلاقاً). الآن تُمرَّر نفس filters
أيضاً إلى self.qe.run(last_sql, filters=filters) — والتي (راجع
core/query_engine.py) تبني view مفلترة حقيقية فوق كل جدول له فلتر
نشط قبل تنفيذ أي SQL عليه، بغض النظر عمّا كتبه AI. نص الـ prompt يبقى
كسياق توضيحي مفيد لـ AI فقط، والتطبيق الفعلي مضمون من طبقة البيانات.

توحيد محركات AI:
-------------------
"gemini" و"ollama" يبقيان بكلاس منفصل خاص بكل منهما (بروتوكول مختلف).
أي محرك آخر (مثل "groq"، "openrouter"، وأي محرك مستقبلي متوافق مع
OpenAI API) يُبنى ديناميكياً عبر ai.engine_registry +
ai.openai_compatible_engine.OpenAICompatibleEngine — إضافة محرك جديد
تتم بسطر واحد في ai/engine_registry.py فقط، دون أي تعديل هنا.

🆕 build_ai_manager():
------------------------
دالة مساعدة واحدة تبني AIManager جاهزاً مباشرة من إعدادات مشروع
(project.db)، بدل تكرار نفس منطق "قراءة settings → get_engine →
AIManager(...)" في أكثر من مكان (كان مكرراً بين ui/dashboards.py
و core/dashboard_cells/base.py). راجع نهاية هذا الملف.
"""

import time
import logging
from typing import Optional

from ai.base_engine               import BaseEngine
from ai.gemini                    import GeminiEngine
from ai.ollama                    import OllamaEngine
from ai.openai_compatible_engine  import OpenAICompatibleEngine
from ai.engine_registry           import get_registry_entry
from ai.prompt_builder            import PromptBuilder
from core.project_db              import ProjectDB
from core.query_engine            import QueryEngine
from config import OLLAMA_DEFAULT_URL, STORY_SAMPLE_ROWS_IN_PROMPT, AI_RETRY_DELAY_SECONDS, STORY_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# error_type التي لا فائدة من إعادة المحاولة عليها بنفس الإعدادات
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
    أي اسم آخر: يُبحث عنه في ai.engine_registry ويُبنى عبر
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
    ) -> dict:
        """
        سؤال → SQL → تنفيذ → تحليل نصي (سرد) بالعربية بناءً على البيانات
        الفعلية الناتجة، بدل عرضها كجدول/رسم فقط.

        المرحلة الأولى (توليد SQL عبر ask()) تستخدم "timeout" العادي
        كما هو، وتُطبَّق فيها filters فعلياً (راجع ask() أعلاه) — لذا
        البيانات التي يُبنى عليها السرد نفسه مفلترة مسبقاً بشكل مضمون.
        المرحلة الثانية (توليد نص السرد) تستخدم self.story_timeout
        المستقل تماماً — يُمرَّر إلى engine.send() عبر timeout_override
        بدل تعديل self.timeout الدائم للمحرك، حتى لا يتأثر أي استدعاء
        آخر (مثل ask() لخلايا أخرى تستخدم نفس instance المحرك).

        يستخدم أيضاً ميزانية زمنية مستقلة (story_max_total_wait_seconds)
        عن عملية ask() الداخلية، لأن العملية الكاملة هنا مرحلتان متتاليتان.

        يرجع نفس بنية ask() تقريباً + مفتاح إضافي "story":
        {
            "ok"    : True/False,
            "sql"   : "...",
            "df"    : DataFrame,
            "rows"  : N,
            "tries" : عدد محاولات SQL,
            "story" : "النص التحليلي بالعربية",
            "error" : "..."  ← عند الفشل (في أي مرحلة)
        }
        """
        story_start = time.monotonic()

        # المرحلة ١: نحصل على البيانات الفعلية بنفس آلية ask() المعتادة
        # (بما في ذلك تطبيق filters فعلياً على التنفيذ)
        data_result = self.ask(question, result_type="story", ai_rules=ai_rules, filters=filters)
        if not data_result["ok"]:
            return data_result

        df = data_result["df"]
        if df is None or df.empty:
            data_result["ok"] = False
            data_result["error"] = "لم يُرجع الاستعلام أي بيانات لكتابة تحليل عنها"
            return data_result

        # المرحلة ٢: نطلب من AI كتابة سرد نصي بناءً على البيانات الفعلية
        # (المفلترة مسبقاً) — بمهلة اتصال مستقلة (story_timeout) عن
        # مرحلة SQL أعلاه.
        story_builder = PromptBuilder(schema={}, relations=[], ai_rules=ai_rules)
        story_prompt = story_builder.build_story(question, df, max_rows=STORY_SAMPLE_ROWS_IN_PROMPT)

        send_result = None
        for attempt in range(1, self.max_tries + 1):
            send_result = self.engine.send(
                story_prompt, self.temperature,
                timeout_override=self.story_timeout,
            )
            if send_result["ok"]:
                break

            error_type = send_result.get("error_type")
            logger.warning(
                "Story engine send failed (attempt %d/%d): %s",
                attempt, self.max_tries, send_result["error"]
            )

            if error_type in _PERMANENT_ERROR_TYPES:
                logger.error("Permanent error (%s) during story generation — stopping", error_type)
                break

            if attempt < self.max_tries:
                if self._budget_exceeded(story_start, self.story_max_total_wait_seconds):
                    logger.warning(
                        "Story total wait budget (%ds) exceeded — stopping retries early",
                        self.story_max_total_wait_seconds
                    )
                    break
                logger.info("Waiting %ds before retry (story generation)...", self.retry_delay)
                if self.retry_delay > 0:
                    time.sleep(self.retry_delay)

        if not send_result["ok"]:
            data_result["ok"] = False
            data_result["error"] = f"فشل توليد التحليل النصي: {send_result['error']}"
            if send_result.get("error_type"):
                data_result["error_type"] = send_result["error_type"]
            return data_result

        story_text = self.engine.clean_response(send_result["text"]).strip()
        if not story_text:
            data_result["ok"] = False
            data_result["error"] = "رد AI فارغ عند توليد التحليل النصي"
            return data_result

        data_result["story"] = story_text
        return data_result


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
