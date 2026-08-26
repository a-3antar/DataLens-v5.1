"""
ai/grok.py
==========
محرك xAI Grok — واجهة متوافقة مع OpenAI API.
"""

import logging
import httpx
from ai.base_engine import BaseEngine

logger = logging.getLogger(__name__)

GROK_BASE   = "https://api.groq.com/openai/v1"# "https://api.x.ai/v1"
GROK_MODELS = f"{GROK_BASE}/models"
GROK_CHAT   = f"{GROK_BASE}/chat/completions"


class GrokEngine(BaseEngine):
    """محرك xAI Grok."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile", timeout: int = 30):
        super().__init__(api_key, model, timeout)

    def get_models(self) -> dict:
        if not self.api_key:
            return {"ok": False, "error": "API key غير موجود", "error_type": "auth"}
        try:
            resp = httpx.get(
                GROK_MODELS,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data   = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            logger.info("Grok models fetched: %d", len(models))
            return {"ok": True, "models": models}
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            msg = f"HTTP {status}: {e.response.text[:200]}"
            logger.error("Grok get_models error: %s", msg)
            error_type = "auth" if status in (401, 403) else ("rate_limit" if status == 429 else "other")
            return {"ok": False, "error": msg, "error_type": error_type}
        except Exception as e:
            logger.error("Grok get_models error: %s", e)
            return {"ok": False, "error": str(e), "error_type": "other"}

    def send(self, prompt: str, temperature: float = 0.1) -> dict:
        """
        error_type: "auth" (401/403 — لا فائدة من إعادة المحاولة)،
        "rate_limit" (429)، "transient" (5xx/timeout/اتصال)، "other".
        """
        if not self.api_key:
            return {"ok": False, "error": "API key غير موجود", "error_type": "auth"}
        if not self.model:
            return {"ok": False, "error": "لم يتم تحديد النموذج", "error_type": "other"}
        if not prompt.strip():
            return {"ok": False, "error": "الـ prompt فارغ", "error_type": "other"}
        try:
            resp = httpx.post(
                GROK_CHAT,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type" : "application/json",
                },
                json={
                    "model"      : self.model,
                    "messages"   : [{"role": "user", "content": prompt}],
                    "temperature": max(0.0, min(2.0, temperature)),
                    "max_tokens" : 2048,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            choices = resp.json().get("choices", [])
            if not choices:
                return {"ok": False, "error": "الرد لا يحتوي على choices", "error_type": "other"}
            text = choices[0].get("message", {}).get("content", "").strip()
            if not text:
                return {"ok": False, "error": "الرد فارغ", "error_type": "other"}
            logger.info("Grok response: %d chars", len(text))
            return {"ok": True, "text": text}
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            msg = f"HTTP {status}: {e.response.text[:200]}"
            logger.error("Grok send error: %s", msg)
            if status in (401, 403):
                return {
                    "ok": False,
                    "error": f"فشل التحقق من مفتاح Grok API (401/403). تحقق من صلاحية المفتاح.\n({msg})",
                    "error_type": "auth",
                }
            if status == 429:
                return {"ok": False, "error": msg, "error_type": "rate_limit"}
            if status >= 500:
                return {"ok": False, "error": msg, "error_type": "transient"}
            return {"ok": False, "error": msg, "error_type": "other"}
        except httpx.TimeoutException:
            return {"ok": False, "error": f"انتهت مهلة الاتصال ({self.timeout}s)", "error_type": "transient"}
        except Exception as e:
            logger.error("Grok send error: %s", e)
            return {"ok": False, "error": str(e), "error_type": "other"}

    def status(self) -> dict:
        result = self.get_models()
        if result["ok"]:
            return {"ok": True, "models_count": len(result["models"])}
        return result
