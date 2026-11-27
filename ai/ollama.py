"""
ai/ollama.py
============
محرك Ollama المحلي مع إعدادات قابلة للضبط لمهام تحليل البيانات وكتابة SQL.

ملاحظات:
- num_ctx يحدد نافذة السياق التي يخصصها Ollama للنموذج.
- num_predict يحدد الحد الأقصى لطول المخرجات.
- لا يمكن معرفة عدد Tokens للـ prompt بدقة قبل الإرسال دون tokenizer مطابق
  للنموذج؛ لذلك نسجل prompt_eval_count الذي يعيده Ollama بعد التنفيذ.
"""

from core.logger_config import trace_calls

import logging
from typing import Optional

import httpx

from ai.base_engine import BaseEngine
from config import OLLAMA_DEFAULT_URL, OLLAMA_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class OllamaEngine(BaseEngine):
    """محرك Ollama المحلي."""

    @trace_calls
    def __init__(
        self,
        base_url: str = OLLAMA_DEFAULT_URL,
        model: str = "",
        timeout: int = OLLAMA_TIMEOUT_SECONDS,
        api_key: str = "",
        num_ctx: Optional[int] = None,
        num_predict_sql: int = 384,
        num_predict_default: int = 768,
        keep_alive: str = "5m",
    ):
        super().__init__(api_key="", model=model, timeout=timeout)
        self.base_url = (base_url or OLLAMA_DEFAULT_URL).rstrip("/")

        # الإعداد الافتراضي متوافق مع السلوك السابق، ويمكن تجاوزه من إعدادات المشروع.
        if num_ctx is None:
            model_lower = (model or "").lower()
            num_ctx = 6144 if "3b" in model_lower else 4096

        self.num_ctx = max(2048, int(num_ctx))
        self.num_predict_sql = max(64, int(num_predict_sql))
        self.num_predict_default = max(128, int(num_predict_default))
        self.keep_alive = keep_alive or "5m"

    @property
    @trace_calls
    def models_url(self) -> str:
        return f"{self.base_url}/api/tags"

    @property
    @trace_calls
    def generate_url(self) -> str:
        return f"{self.base_url}/api/generate"

    @trace_calls
    def get_models(self) -> dict:
        """إرجاع النماذج المثبتة في Ollama."""
        try:
            resp = httpx.get(self.models_url, timeout=10)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            logger.info("Ollama models fetched: %d", len(models))
            return {"ok": True, "models": models}
        except httpx.ConnectError:
            msg = f"تعذر الاتصال بـ Ollama على {self.base_url}"
            logger.error(msg)
            return {"ok": False, "error": msg, "error_type": "transient"}
        except httpx.TimeoutException:
            return {
                "ok": False,
                "error": "انتهت مهلة الاتصال بـ Ollama أثناء جلب النماذج",
                "error_type": "transient",
            }
        except Exception as e:
            logger.error("Ollama get_models error: %s", e)
            return {"ok": False, "error": str(e), "error_type": "other"}

    @trace_calls
    def _num_predict_for_prompt(self, prompt: str) -> int:
        """اختيار حد المخرجات حسب نوع المهمة."""
        if "SQL QUERY:" in prompt or "JSON PLAN:" in prompt:
            return self.num_predict_sql
        return self.num_predict_default

    @trace_calls
    def send(
        self,
        prompt: str,
        temperature: float = 0.1,
        timeout_override: Optional[int] = None,
    ) -> dict:
        """إرسال prompt إلى Ollama مع مهلة قراءة وإعدادات توليد مستقلة."""
        if not self.model:
            return {"ok": False, "error": "لم يتم تحديد النموذج", "error_type": "other"}
        if not prompt or not prompt.strip():
            return {"ok": False, "error": "الـ prompt فارغ", "error_type": "other"}

        effective_timeout = max(5, int(timeout_override or self.timeout))
        timeout = httpx.Timeout(
            connect=min(15, effective_timeout),
            read=effective_timeout,
            write=min(30, effective_timeout),
            pool=min(15, effective_timeout),
        )

        num_predict = self._num_predict_for_prompt(prompt)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": max(0.0, min(1.0, float(temperature))),
                "num_ctx": self.num_ctx,
                "num_predict": num_predict,
            },
        }

        logger.info(
            "Ollama request: model=%s num_ctx=%d num_predict=%d prompt_chars=%d",
            self.model, self.num_ctx, num_predict, len(prompt),
        )

        try:
            resp = httpx.post(self.generate_url, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            text = (data.get("response") or "").strip()
            if not text:
                return {
                    "ok": False,
                    "error": "الرد فارغ من Ollama؛ تحقق من توافق النموذج مع المهمة",
                    "error_type": "other",
                }

            duration = data.get("total_duration")
            duration_s = (
                round(duration / 1_000_000_000, 2)
                if isinstance(duration, (int, float)) else None
            )
            prompt_eval_count = data.get("prompt_eval_count")
            eval_count = data.get("eval_count")

            logger.info(
                "Ollama response: model=%s prompt_tokens=%s output_tokens=%s "
                "prompt_chars=%d response_chars=%d duration=%s",
                self.model,
                prompt_eval_count if prompt_eval_count is not None else "unknown",
                eval_count if eval_count is not None else "unknown",
                len(prompt),
                len(text),
                duration_s,
            )
            return {"ok": True, "text": text}

        except httpx.ConnectError:
            msg = f"تعذر الاتصال بـ Ollama على {self.base_url}"
            logger.error(msg)
            return {"ok": False, "error": msg, "error_type": "transient"}
        except httpx.ConnectTimeout:
            return {
                "ok": False,
                "error": f"انتهت مهلة الاتصال بسيرفر Ollama ({effective_timeout}s)",
                "error_type": "transient",
            }
        except httpx.ReadTimeout:
            return {
                "ok": False,
                "error": (
                    f"تجاوز توليد النموذج مهلة القراءة ({effective_timeout}s). "
                    "قلّل حجم النموذج/السياق أو ارفع مهلة Ollama."
                ),
                "error_type": "transient",
            }
        except httpx.TimeoutException:
            return {
                "ok": False,
                "error": f"انتهت مهلة طلب Ollama ({effective_timeout}s)",
                "error_type": "transient",
            }
        except httpx.HTTPStatusError as e:
            detail = (e.response.text or "")[:500]
            logger.error("Ollama HTTP error %s: %s", e.response.status_code, detail)
            return {
                "ok": False,
                "error": f"خطأ من Ollama HTTP {e.response.status_code}: {detail}",
                "error_type": "other",
            }
        except Exception as e:
            logger.exception("Ollama send error")
            return {"ok": False, "error": str(e), "error_type": "other"}

    @trace_calls
    def status(self) -> dict:
        """التحقق من أن Ollama يعمل وإرجاع إعدادات السياق الفعالة."""
        result = self.get_models()
        if result["ok"]:
            return {
                "ok": True,
                "base_url": self.base_url,
                "models_count": len(result["models"]),
                "num_ctx": self.num_ctx,
                "num_predict_sql": self.num_predict_sql,
                "num_predict_default": self.num_predict_default,
            }
        return result
