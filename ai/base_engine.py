"""
ai/base_engine.py
=================
Abstract base class لكل محركات الـ AI.
كل محرك يرث من هذا الـ class ويطبق الدوال المطلوبة.

🆕 معاملان مشتركان في send() (كل المحركات تلتزم بهما):
---------------------------------------------------------
- max_output_tokens: سقف طول الرد لهذا الاستدعاء فقط. الافتراضي لكل
  محرك يبقى كما كان (2048 للسحابي)، وطلبات خطة الاستعلامات (JSON متعدد
  الاستعلامات) ترفعه كي لا يُقطع الرد في منتصف JSON.
- json_mode: طلب رد JSON صالح من المحرك نفسه (Gemini: responseMimeType،
  OpenAI-compatible: response_format، Ollama: format=json). المحرك الذي
  لا يدعمه (supports_json_mode=False) يتجاهل الطلب بصمت، فيبقى المستدعي
  مضطراً لتحليل الرد بنفسه عبر extract_json_object.

🆕 استخراج JSON مع إصلاح الرد المقطوع (extract_json_object):
-------------------------------------------------------------
النماذج تُقطع ردودها أحياناً (سقف توكنات/مهلة) فيصل JSON ناقص الأقواس.
بدل فشل العملية كلها وإعادة طلب كامل، يُرجَع آخر عنصر مكتمل ويُغلَق ما
بقي مفتوحاً، فتُستخرج الاستعلامات المكتملة من خطة مقطوعة.
"""


from core.logger_config import trace_calls

import re
import json
import logging
from abc      import ABC, abstractmethod
from typing   import Optional

logger = logging.getLogger(__name__)

# ── ثوابت مشتركة ─────────────────────────────────────────────
SQL_START_PATTERN = re.compile(
    r"(SELECT|WITH|EXPLAIN)\b",
    re.IGNORECASE
)
CODE_BLOCK_PATTERN = re.compile(
    r"```(?:sql|SQL)?\s*(.*?)```",
    re.DOTALL
)


class BaseEngine(ABC):
    """
    الواجهة المشتركة لكل محركات الـ AI.

    كل محرك يجب أن يطبق:
        - get_models()  → قائمة النماذج المتاحة
        - send()        → إرسال prompt وإرجاع رد نصي
        - status()      → التحقق من أن المحرك يعمل
    """

    # هل يدعم المحرك (والنموذج المختار عادةً) وضع JSON الأصلي؟ المحركات
    # الفرعية تضبطه؛ القيمة False تعني أن json_mode في send() يُتجاهل.
    supports_json_mode = False

    @trace_calls
    def __init__(self, api_key: str = "", model: str = "", timeout: int = 30):
        self.api_key = api_key.strip()
        self.model   = model.strip()
        self.timeout = timeout

    # ── دوال مجردة — يجب تطبيقها في كل محرك ─────────────────

    @abstractmethod
    @trace_calls
    def get_models(self) -> dict:
        """
        إرجاع قائمة النماذج المتاحة.
        يرجع: {"ok": True, "models": ["model1", "model2", ...]}
               أو {"ok": False, "error": "..."}
        """

    @abstractmethod
    @trace_calls
    def send(
        self,
        prompt: str,
        temperature: float = 0.1,
        timeout_override: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> dict:
        """
        إرسال prompt وإرجاع الرد.

        timeout_override: لو مُمرَّرة، تُستخدم بدل self.timeout لهذا
        الاستدعاء تحديداً فقط (بدون تعديل self.timeout الدائم للمحرك).
        مفيدة لعمليات لها طبيعة زمنية مختلفة عن باقي استدعاءات نفس
        المحرك — مثل مرحلة توليد نص السرد (Story Telling) التي يُفضَّل
        أن تفشل بسرعة أكبر، أو خطة الاستعلامات التي تحتاج مهلة أطول.

        max_output_tokens: سقف طول الرد لهذا الاستدعاء فقط (None = الافتراضي
        الخاص بالمحرك). راجع توثيق الوحدة.

        json_mode: طلب رد JSON من المحرك نفسه لو يدعمه (راجع توثيق الوحدة).

        يرجع: {"ok": True, "text": "..."} أو {"ok": False, "error": "..."}
        """

    @abstractmethod
    @trace_calls
    def status(self) -> dict:
        """
        التحقق من أن المحرك متاح ويعمل.
        يرجع: {"ok": True} أو {"ok": False, "error": "..."}
        """

    # ── دوال مشتركة — متاحة لكل المحركات ────────────────────

    @trace_calls
    def extract_sql(self, text: str) -> dict:
        """
        استخراج SQL من رد الـ AI.

        الحالات المعالجة:
        1. كود داخل ```sql ... ```
        2. كود داخل ``` ... ```
        3. SQL مباشر بدون كود blocks
        4. SQL مخلوط مع نص

        يرجع: {"ok": True, "sql": "..."} أو {"ok": False, "error": "..."}
        """
        if not text or not text.strip():
            return {"ok": False, "error": "الرد فارغ"}

        # محاولة 1: استخراج من code block
        match = CODE_BLOCK_PATTERN.search(text)
        if match:
            sql = match.group(1).strip()
            if sql:
                logger.debug("SQL extracted from code block")
                return {"ok": True, "sql": sql}

        # محاولة 2: البحث عن SQL مباشر في النص
        lines      = text.strip().splitlines()
        sql_lines  = []
        in_sql     = False

        for line in lines:
            stripped = line.strip()
            if not in_sql and SQL_START_PATTERN.match(stripped):
                in_sql = True
            if in_sql:
                # توقف عند سطر فارغ بعد SQL أو نص غير SQL
                if not stripped and sql_lines:
                    break
                sql_lines.append(line)

        if sql_lines:
            sql = "\n".join(sql_lines).strip()
            logger.debug("SQL extracted from plain text")
            return {"ok": True, "sql": sql}

        # محاولة 3: إرجاع النص كله إذا بدا SQL
        text_stripped = text.strip()
        if SQL_START_PATTERN.match(text_stripped):
            return {"ok": True, "sql": text_stripped}

        logger.warning("Could not extract SQL from response: %s...", text[:100])
        return {"ok": False, "error": "لم يتم العثور على SQL في الرد"}

    @trace_calls
    def clean_response(self, text: str) -> str:
        """تنظيف الرد من markdown وأحرف زائدة."""
        if not text:
            return ""
        # إزالة code blocks markers
        text = re.sub(r"```(?:sql|SQL|python)?", "", text)
        text = re.sub(r"```", "", text)
        return text.strip()

    # ── JSON ────────────────────────────────────────────────

    @staticmethod
    @trace_calls
    def repair_truncated_json(text: str) -> Optional[str]:
        """
        إصلاح JSON كائن (object) مقطوع: يمسح النص بدءاً من أول '{' مع
        تتبّع النصوص والهروب والأقواس، ويحتفظ بآخر موضع اكتمل فيه
        عنصر (بعد '}' أو ']') ثم يُغلق الأقواس المفتوحة وقتها. العنصر
        الناقص في الذيل يُحذف — يرجع النص المُصلَح أو None لو لا يوجد
        عنصر مكتمل يمكن الاحتفاظ به.
        """
        start = text.find("{")
        if start == -1:
            return None

        stack: list = []
        in_str = False
        escaped = False
        safe: Optional[tuple] = None   # (فهرس نهاية آخر عنصر مكتمل, الأقواس الواجب إغلاقها)

        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if not stack:
                    break
                stack.pop()
                if not stack:
                    return text[start:i + 1]   # الكائن الجذر اكتمل فعلاً
                safe = (i + 1, "".join(reversed(stack)))

        if safe is None:
            return None
        return text[start:safe[0]] + safe[1]

    @trace_calls
    def extract_json_object(self, text: str) -> dict:
        """
        استخراج كائن JSON من رد AI (يتحمّل code fences ونصاً قبل/بعد
        الكائن، ويصلح الرد المقطوع عبر repair_truncated_json).

        يرجع: {"ok": True, "data": dict, "repaired": bool}
               أو {"ok": False, "error": "..."}
        """
        cleaned = self.clean_response(text)
        start = cleaned.find("{")
        if start == -1:
            return {"ok": False, "error": "لم يُعثر على JSON في الرد"}

        end = cleaned.rfind("}")
        if end > start:
            try:
                obj = json.loads(cleaned[start:end + 1])
                if isinstance(obj, dict):
                    return {"ok": True, "data": obj, "repaired": False}
            except json.JSONDecodeError:
                pass

        repaired = self.repair_truncated_json(cleaned)
        if repaired:
            try:
                obj = json.loads(repaired)
                if isinstance(obj, dict):
                    logger.warning("Truncated JSON response repaired (%d chars)", len(repaired))
                    return {"ok": True, "data": obj, "repaired": True}
            except json.JSONDecodeError:
                pass

        return {"ok": False, "error": "تعذر تحليل الرد كـ JSON (الرد مقطوع أو غير صالح)"}
