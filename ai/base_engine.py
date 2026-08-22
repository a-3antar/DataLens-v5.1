"""
ai/base_engine.py
=================
Abstract base class لكل محركات الـ AI.
كل محرك يرث من هذا الـ class ويطبق الدوال المطلوبة.
"""

import re
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

    def __init__(self, api_key: str = "", model: str = "", timeout: int = 30):
        self.api_key = api_key
        self.model   = model
        self.timeout = timeout

    # ── دوال مجردة — يجب تطبيقها في كل محرك ─────────────────

    @abstractmethod
    def get_models(self) -> dict:
        """
        إرجاع قائمة النماذج المتاحة.
        يرجع: {"ok": True, "models": ["model1", "model2", ...]}
               أو {"ok": False, "error": "..."}
        """

    @abstractmethod
    def send(self, prompt: str, temperature: float = 0.1) -> dict:
        """
        إرسال prompt وإرجاع الرد.
        يرجع: {"ok": True, "text": "..."} أو {"ok": False, "error": "..."}
        """

    @abstractmethod
    def status(self) -> dict:
        """
        التحقق من أن المحرك متاح ويعمل.
        يرجع: {"ok": True} أو {"ok": False, "error": "..."}
        """

    # ── دوال مشتركة — متاحة لكل المحركات ────────────────────

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

    def clean_response(self, text: str) -> str:
        """تنظيف الرد من markdown وأحرف زائدة."""
        if not text:
            return ""
        # إزالة code blocks markers
        text = re.sub(r"```(?:sql|SQL|python)?", "", text)
        text = re.sub(r"```", "", text)
        return text.strip()
