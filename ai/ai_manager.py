"""
ai/ai_manager.py
================
المدير المركزي للـ AI:
- اختيار المحرك المناسب
- بناء الـ Prompt
- إرسال السؤال مع retry loop عند الخطأ
- استخراج SQL من الرد
- تنفيذ SQL عبر QueryEngine

إعادة المحاولة عند فشل الاتصال:
---------------------------------
عند فشل الاتصال بمحرك AI نفسه (وليس خطأ SQL أو رد فارغ)، بدل التوقف
فوراً، ننتظر مدة retry_delay ثانية ثم نعيد المحاولة بنفس الـ prompt،
طالما لا تزال هناك محاولات متبقية ضمن max_tries. مدة الانتظار قابلة
للتخصيص لكل مشروع (تُحفظ في إعدادات المشروع)، وتُقرأ افتراضياً من
config.AI_RETRY_DELAY_SECONDS.
"""

import time
import logging
from typing import Optional

from ai.base_engine    import BaseEngine
from ai.gemini         import GeminiEngine
from ai.openrouter     import OpenRouterEngine
from ai.grok           import GrokEngine
from ai.ollama         import OllamaEngine
from ai.prompt_builder import PromptBuilder
from core.project_db   import ProjectDB
from core.query_engine import QueryEngine
from config            import OLLAMA_DEFAULT_URL, STORY_SAMPLE_ROWS_IN_PROMPT, AI_RETRY_DELAY_SECONDS

logger = logging.getLogger(__name__)


def get_engine(
    engine_name: str,
    api_key    : str = "",
    model      : str = "",
    timeout    : int = 30,
    ollama_url : str = OLLAMA_DEFAULT_URL,
) -> Optional[BaseEngine]:
    """
    إرجاع محرك AI بناءً على الاسم.
    يرجع None لو الاسم غير معروف.
    """
    name = engine_name.lower().strip()
    if name == "gemini":
        return GeminiEngine(api_key=api_key, model=model, timeout=timeout)
    elif name == "openrouter":
        return OpenRouterEngine(api_key=api_key, model=model, timeout=timeout)
    elif name == "grok":
        return GrokEngine(api_key=api_key, model=model, timeout=timeout)
    elif name == "ollama":
        return OllamaEngine(base_url=ollama_url, model=model, timeout=timeout)
    else:
        logger.error("Unknown engine: %s", engine_name)
        return None


class AIManager:
    """
    المدير المركزي لعمليات الـ AI.

    الاستخدام:
        ai = AIManager(db, engine, temperature=0.1, max_tries=3, retry_delay=10)
        result = ai.ask("ما إجمالي المبيعات؟", result_type="chart")
    """

    def __init__(
        self,
        db         : ProjectDB,
        engine     : BaseEngine,
        temperature: float = 0.1,
        max_tries  : int   = 3,
        retry_delay: int   = AI_RETRY_DELAY_SECONDS,
    ):
        self.db          = db
        self.engine      = engine
        self.temperature = temperature
        self.max_tries   = max(1, max_tries)
        self.retry_delay = max(0, retry_delay)
        self.qe          = QueryEngine(db)

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

        يرجع:
        {
            "ok"         : True/False,
            "sql"        : "SELECT ...",
            "df"         : DataFrame,          ← عند النجاح
            "rows"       : N,
            "tries"      : عدد المحاولات الفعلي,
            "error"      : "...",              ← عند الفشل
        }
        """
        question = question.strip()
        if not question:
            return {"ok": False, "error": "السؤال فارغ", "tries": 0}

        # بناء الـ Prompt الأولي
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
        last_sql   = ""
        attempt    = 0

        for attempt in range(1, self.max_tries + 1):
            logger.info("AI attempt %d/%d", attempt, self.max_tries)

            # إرسال Prompt
            send_result = self.engine.send(prompt, self.temperature)
            if not send_result["ok"]:
                last_error = send_result["error"]
                logger.warning("Engine send failed (attempt %d): %s", attempt, last_error)

                # خطأ اتصال — ننتظر ثم نعيد المحاولة بنفس الـ prompt،
                # طالما لدينا محاولات متبقية. لا داعي لإعادة بناء الـ
                # prompt هنا لأن الخطأ من الاتصال نفسه وليس من محتوى الرد.
                if attempt < self.max_tries:
                    logger.info(
                        "Waiting %ds before retry due to connection error...",
                        self.retry_delay
                    )
                    if self.retry_delay > 0:
                        time.sleep(self.retry_delay)
                    continue
                else:
                    break

            # استخراج SQL
            extract_result = self.engine.extract_sql(send_result["text"])
            if not extract_result["ok"]:
                last_error = extract_result["error"]
                logger.warning("SQL extraction failed (attempt %d): %s", attempt, last_error)
                # نحاول مرة أخرى مع نفس الـ prompt
                continue

            last_sql = extract_result["sql"]
            logger.info("SQL extracted (attempt %d): %s...", attempt, last_sql[:60])

            # تنفيذ SQL
            run_result = self.qe.run(last_sql)
            if run_result["ok"]:
                logger.info(
                    "Query succeeded on attempt %d: %d rows",
                    attempt, run_result["rows"]
                )
                # لو صحّح QueryEngine اسم عمود تلقائياً، نُحدّث الـ SQL المعروض
                # للمستخدم ليطابق ما نُفّذ فعلياً، ونُبلغ عن التصحيح.
                executed_sql = run_result.get("sql", last_sql)
                return {
                    "ok"        : True,
                    "sql"       : executed_sql,
                    "df"        : run_result["df"],
                    "rows"      : run_result["rows"],
                    "tries"     : attempt,
                    "auto_fixes": run_result.get("auto_fixes"),
                }

            # SQL فشل — نبني retry prompt
            last_error = run_result["error"]
            logger.warning("SQL execution failed (attempt %d): %s", attempt, last_error)

            if attempt < self.max_tries:
                prompt = builder.build_error_retry(
                    original_prompt = prompt,
                    failed_sql      = last_sql,
                    error_message   = last_error,
                )

        # فشلت كل المحاولات
        logger.error("All attempts failed (%d/%d). Last error: %s", attempt, self.max_tries, last_error)
        return {
            "ok"   : False,
            "sql"  : last_sql,
            "error": last_error,
            "tries": attempt,
        }

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
        # المرحلة ١: نحصل على البيانات الفعلية بنفس آلية ask() المعتادة
        data_result = self.ask(question, result_type="story", ai_rules=ai_rules, filters=filters)
        if not data_result["ok"]:
            return data_result

        df = data_result["df"]
        if df is None or df.empty:
            data_result["ok"] = False
            data_result["error"] = "لم يُرجع الاستعلام أي بيانات لكتابة تحليل عنها"
            return data_result

        # المرحلة ٢: نطلب من AI كتابة سرد نصي بناءً على البيانات الفعلية
        story_builder = PromptBuilder(schema={}, relations=[], ai_rules=ai_rules)
        story_prompt = story_builder.build_story(question, df, max_rows=STORY_SAMPLE_ROWS_IN_PROMPT)

        send_result = None
        for attempt in range(1, self.max_tries + 1):
            send_result = self.engine.send(story_prompt, self.temperature)
            if send_result["ok"]:
                break
            logger.warning(
                "Story engine send failed (attempt %d/%d): %s",
                attempt, self.max_tries, send_result["error"]
            )
            if attempt < self.max_tries:
                logger.info("Waiting %ds before retry (story generation)...", self.retry_delay)
                if self.retry_delay > 0:
                    time.sleep(self.retry_delay)

        if not send_result["ok"]:
            data_result["ok"] = False
            data_result["error"] = f"فشل توليد التحليل النصي: {send_result['error']}"
            return data_result

        story_text = self.engine.clean_response(send_result["text"]).strip()
        if not story_text:
            data_result["ok"] = False
            data_result["error"] = "رد AI فارغ عند توليد التحليل النصي"
            return data_result

        data_result["story"] = story_text
        return data_result
