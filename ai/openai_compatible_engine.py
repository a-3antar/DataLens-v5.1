"""
ai/openai_compatible_engine.py
================================
كلاس موحّد لأي محرك AI يتبع بروتوكول OpenAI (/models و
/chat/completions بنفس الصيغة) — يغطي حالياً Groq وOpenRouter،
وأي محرك مستقبلي بنفس البروتوكول (OpenAI نفسه، Together، Fireworks...)

سبب التوحيد:
------------
كانت ai/grok.py وai/openrouter.py متطابقتين تقريباً سطراً بسطر
(نفس شكل الطلب، نفس معالجة الأخطاء 401/403/429/5xx)، والفرق الوحيد
الفعلي بينهما هو الـ base_url والموديل الافتراضي. هذا الكلاس يلتقط
المنطق المشترك مرة واحدة، ويأخذ base_url/default headers كمعامل.
(الملفان القديمان ai/grok.py وai/openrouter.py حُذفا نهائياً بعد أن
أصبحا كوداً ميتاً بالكامل — كل شيء يمر الآن عبر هذا الكلاس + السجل
أدناه.)

🆕 وضع JSON وسقف الرد (راجع ai/base_engine.py):
------------------------------------------------
- json_mode يضيف response_format={"type": "json_object"} للطلب، لكن فقط
  حين يعلن السجل دعم المزوّد له (مفتاح "json_mode" في
  OPENAI_COMPATIBLE_ENGINES → supports_json_mode في الكلاس). OpenRouter
  مثلاً يعتمد على النموذج المختار فلا يُفعَّل افتراضياً.
- حتى مع الدعم المعلن، لو رفض المزوّد الطلب بـ HTTP 400 (نموذج لا
  يدعم الوضع) يُعاد الإرسال مرة واحدة بدونه بدل فشل العملية.
- max_output_tokens يغيّر max_tokens لهذا الاستدعاء فقط (الافتراضي 2048).

🧹 تنظيف: حُذفت all_openai_compatible_names() — لم تكن مستخدمة في أي
مكان بالمشروع (config.AI_ENGINES هو المصدر الفعلي المستخدَم في
ui/settings.py لقائمة المحركات المعروضة).
"""


from core.logger_config import trace_calls

import logging
from typing import Optional

import httpx

from ai.base_engine import BaseEngine

logger = logging.getLogger(__name__)

# سقف الرد الافتراضي حين لا يمرّر المستدعي max_output_tokens
DEFAULT_MAX_OUTPUT_TOKENS = 2048


class OpenAICompatibleEngine(BaseEngine):
    """
    محرك عام لأي مزوّد متوافق مع OpenAI Chat Completions API.

    الاستخدام:
        engine = OpenAICompatibleEngine(
            base_url="https://api.groq.com/openai/v1",
            api_key="gsk_...",
            model="openai/gpt-oss-120b",
            display_name="Groq",
            supports_json_mode=True,
        )
        models = engine.get_models()
        result = engine.send("SELECT * FROM sales")
    """

    @trace_calls
    def __init__(
        self,
        base_url    : str,
        api_key     : str,
        model       : str = "",
        timeout     : int = 30,
        display_name: str = "",
        supports_json_mode: bool = False,
    ):
        super().__init__(api_key, model, timeout)
        self.base_url     = base_url.rstrip("/")
        self.display_name = display_name or base_url
        self.supports_json_mode = supports_json_mode

    @property
    @trace_calls
    def models_url(self) -> str:
        return f"{self.base_url}/models"

    @property
    @trace_calls
    def chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @trace_calls
    def get_models(self) -> dict:
        if not self.api_key:
            return {"ok": False, "error": "API key غير موجود", "error_type": "auth"}
        try:
            resp = httpx.get(
                self.models_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data   = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            logger.info("%s models fetched: %d", self.display_name, len(models))
            return {"ok": True, "models": sorted(models)}
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            msg = f"HTTP {status}: {e.response.text[:200]}"
            logger.error("%s get_models error: %s", self.display_name, msg)
            error_type = "auth" if status in (401, 403) else ("rate_limit" if status == 429 else "other")
            return {"ok": False, "error": msg, "error_type": error_type}
        except Exception as e:
            logger.error("%s get_models error: %s", self.display_name, e)
            return {"ok": False, "error": str(e), "error_type": "other"}

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
        error_type: "auth" (401/403 — لا فائدة من إعادة المحاولة)،
        "rate_limit" (429)، "transient" (5xx/timeout/اتصال)، "other".

        timeout_override: مهلة اتصال بديلة لهذا الاستدعاء فقط — راجع
        BaseEngine.send للتفاصيل. max_output_tokens/json_mode: راجع
        توثيق الوحدة.
        """
        if not self.api_key:
            return {"ok": False, "error": "API key غير موجود", "error_type": "auth"}
        if not self.model:
            return {"ok": False, "error": "لم يتم تحديد النموذج", "error_type": "other"}
        if not prompt.strip():
            return {"ok": False, "error": "الـ prompt فارغ", "error_type": "other"}

        effective_timeout = timeout_override or self.timeout

        payload = {
            "model"      : self.model,
            "messages"   : [{"role": "user", "content": prompt}],
            "temperature": max(0.0, min(2.0, temperature)),
            "max_tokens" : int(max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS),
        }
        use_json = bool(json_mode and self.supports_json_mode)
        if use_json:
            payload["response_format"] = {"type": "json_object"}

        try:
            try:
                resp = self._post(payload, effective_timeout)
            except httpx.HTTPStatusError as e:
                # نموذج لا يدعم response_format يرفضه بـ 400 — نُعيد الطلب بدونه مرة واحدة
                if use_json and e.response.status_code == 400:
                    logger.warning("%s rejected json_mode (400) — retrying without it", self.display_name)
                    payload.pop("response_format", None)
                    resp = self._post(payload, effective_timeout)
                else:
                    raise

            data    = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return {"ok": False, "error": "الرد لا يحتوي على choices", "error_type": "other"}
            if choices[0].get("finish_reason") == "length":
                # الرد وصل لسقف التوكنات: يُسجَّل ليسهل تشخيص JSON المقطوع
                logger.warning("%s response hit max_tokens (limit=%s)", self.display_name, payload["max_tokens"])
            text = (choices[0].get("message", {}).get("content") or "").strip()
            if not text:
                return {"ok": False, "error": "الرد فارغ", "error_type": "other"}
            logger.info("%s response: %d chars", self.display_name, len(text))
            return {"ok": True, "text": text}
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            msg = f"HTTP {status}: {e.response.text[:200]}"
            logger.error("%s send error: %s", self.display_name, msg)
            if status in (401, 403):
                return {
                    "ok": False,
                    "error": f"فشل التحقق من مفتاح {self.display_name} API (401/403). تحقق من صلاحية المفتاح.\n({msg})",
                    "error_type": "auth",
                }
            if status == 429:
                return {"ok": False, "error": msg, "error_type": "rate_limit"}
            if status >= 500:
                return {"ok": False, "error": msg, "error_type": "transient"}
            return {"ok": False, "error": msg, "error_type": "other"}
        except httpx.TimeoutException:
            return {"ok": False, "error": f"انتهت مهلة الاتصال ({effective_timeout}s)", "error_type": "transient"}
        except httpx.RequestError as e:
            return {"ok": False, "error": f"خطأ في الاتصال: {e}", "error_type": "transient"}
        except Exception as e:
            logger.error("%s send error: %s", self.display_name, e)
            return {"ok": False, "error": str(e), "error_type": "other"}

    @trace_calls
    def _post(self, payload: dict, timeout: int) -> httpx.Response:
        """إرسال طلب chat/completions واحد (يرفع HTTPStatusError عند أي حالة خطأ)."""
        resp = httpx.post(
            self.chat_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type" : "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp

    @trace_calls
    def status(self) -> dict:
        result = self.get_models()
        if result["ok"]:
            return {"ok": True, "models_count": len(result["models"])}
        return result



# "json_mode": هل يدعم المزوّد response_format={"type": "json_object"} على
# نماذجه عموماً؟ OpenRouter False لأن الدعم يختلف من نموذج لآخر.
OPENAI_COMPATIBLE_ENGINES = {
    "groq": {
        "display_name" : "Groq",
        "base_url"     : "https://api.groq.com/openai/v1",
        "default_model": "openai/gpt-oss-120b",
        "json_mode"    : True,
    },
    "openrouter": {
        "display_name" : "OpenRouter",
        "base_url"     : "https://openrouter.ai/api/v1",
        "default_model": "mistralai/mistral-7b-instruct",
        "json_mode"    : False,
    },

    "openai": {
        "display_name" : "OpenAI",
        "base_url"     : "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "json_mode"    : True,
    },
}


@trace_calls
def get_registry_entry(engine_name: str) -> dict:
    """إرجاع تعريف محرك من السجل، أو {} لو غير موجود."""
    return OPENAI_COMPATIBLE_ENGINES.get(engine_name.lower().strip(), {})
