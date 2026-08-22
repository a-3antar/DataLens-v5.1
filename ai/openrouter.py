"""
ai/openrouter.py
================
محرك OpenRouter AI — يدعم مئات النماذج عبر واجهة موحدة.
"""

import logging
import httpx
from ai.base_engine import BaseEngine

logger = logging.getLogger(__name__)

OPENROUTER_BASE     = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS   = f"{OPENROUTER_BASE}/models"
OPENROUTER_CHAT     = f"{OPENROUTER_BASE}/chat/completions"


class OpenRouterEngine(BaseEngine):
    """
    محرك OpenRouter.
    يدعم نماذج متعددة: GPT-4, Claude, Mistral, Llama, إلخ.
    """

    def __init__(self, api_key: str, model: str = "mistralai/mistral-7b-instruct", timeout: int = 30):
        super().__init__(api_key, model, timeout)

    def get_models(self) -> dict:
        if not self.api_key:
            return {"ok": False, "error": "API key غير موجود"}
        try:
            resp = httpx.get(
                OPENROUTER_MODELS,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data   = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            logger.info("OpenRouter models fetched: %d", len(models))
            return {"ok": True, "models": sorted(models)}
        except httpx.HTTPStatusError as e:
            msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error("OpenRouter get_models error: %s", msg)
            return {"ok": False, "error": msg}
        except Exception as e:
            logger.error("OpenRouter get_models error: %s", e)
            return {"ok": False, "error": str(e)}

    def send(self, prompt: str, temperature: float = 0.1) -> dict:
        if not self.api_key:
            return {"ok": False, "error": "API key غير موجود"}
        if not self.model:
            return {"ok": False, "error": "لم يتم تحديد النموذج"}
        if not prompt.strip():
            return {"ok": False, "error": "الـ prompt فارغ"}
        try:
            resp = httpx.post(
                OPENROUTER_CHAT,
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
            data    = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return {"ok": False, "error": "الرد لا يحتوي على choices"}
            text = choices[0].get("message", {}).get("content", "").strip()
            if not text:
                return {"ok": False, "error": "الرد فارغ"}
            logger.info("OpenRouter response: %d chars", len(text))
            return {"ok": True, "text": text}
        except httpx.HTTPStatusError as e:
            msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error("OpenRouter send error: %s", msg)
            return {"ok": False, "error": msg}
        except httpx.TimeoutException:
            return {"ok": False, "error": f"انتهت مهلة الاتصال ({self.timeout}s)"}
        except Exception as e:
            logger.error("OpenRouter send error: %s", e)
            return {"ok": False, "error": str(e)}

    def status(self) -> dict:
        result = self.get_models()
        if result["ok"]:
            return {"ok": True, "models_count": len(result["models"])}
        return result
