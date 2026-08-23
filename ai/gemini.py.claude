"""
ai/gemini.py
============
محرك Google Gemini AI.
يرث من BaseEngine ويطبق get_models / send / status.
"""

import logging
from typing import Optional

import httpx

from ai.base_engine import BaseEngine

logger = logging.getLogger(__name__)

GEMINI_API_BASE   = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_LIST_URL   = f"{GEMINI_API_BASE}/models"
GEMINI_GEN_URL    = f"{GEMINI_API_BASE}/models/{{model}}:generateContent"


class GeminiEngine(BaseEngine):
    """
    محرك Google Gemini.

    الاستخدام:
        engine = GeminiEngine(api_key="AIza...", model="gemini-2.0-flash")
        models = engine.get_models()
        result = engine.send("SELECT * FROM sales")
    """

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash", timeout: int = 30):
        super().__init__(api_key, model, timeout)

    # ──────────────────────────────────────────────────────────
    #  قائمة النماذج
    # ──────────────────────────────────────────────────────────

    def get_models(self) -> dict:
        """إرجاع قائمة نماذج Gemini المتاحة."""
        if not self.api_key:
            return {"ok": False, "error": "API key غير موجود"}
        try:
            resp = httpx.get(
                GEMINI_LIST_URL,
                params={"key": self.api_key},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data   = resp.json()
            models = [
                m["name"].replace("models/", "")
                for m in data.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
            logger.info("Gemini models fetched: %d models", len(models))
            return {"ok": True, "models": models}

        except httpx.HTTPStatusError as e:
            msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error("Gemini get_models HTTP error: %s", msg)
            return {"ok": False, "error": msg}
        except httpx.RequestError as e:
            logger.error("Gemini get_models connection error: %s", e)
            return {"ok": False, "error": f"خطأ في الاتصال: {e}"}
        except Exception as e:
            logger.error("Gemini get_models unexpected error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  إرسال Prompt
    # ──────────────────────────────────────────────────────────

    def send(self, prompt: str, temperature: float = 0.1) -> dict:
        """
        إرسال prompt إلى Gemini وإرجاع الرد.
        يرجع: {"ok": True, "text": "..."} أو {"ok": False, "error": "..."}
        """
        if not self.api_key:
            return {"ok": False, "error": "API key غير موجود"}
        if not self.model:
            return {"ok": False, "error": "لم يتم تحديد النموذج"}
        if not prompt.strip():
            return {"ok": False, "error": "الـ prompt فارغ"}

        url     = GEMINI_GEN_URL.format(model=self.model)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature"    : max(0.0, min(2.0, temperature)),
                "maxOutputTokens": 2048,
            },
        }

        try:
            resp = httpx.post(
                url,
                params={"key": self.api_key},
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            # استخراج النص من الرد
            candidates = data.get("candidates", [])
            if not candidates:
                return {"ok": False, "error": "الرد لا يحتوي على candidates"}

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                return {"ok": False, "error": "الرد لا يحتوي على parts"}

            text = parts[0].get("text", "").strip()
            if not text:
                return {"ok": False, "error": "الرد فارغ"}

            logger.info("Gemini response received: %d chars", len(text))
            return {"ok": True, "text": text}

        except httpx.HTTPStatusError as e:
            msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error("Gemini send HTTP error: %s", msg)
            return {"ok": False, "error": msg}
        except httpx.TimeoutException:
            logger.error("Gemini send timeout after %ds", self.timeout)
            return {"ok": False, "error": f"انتهت مهلة الاتصال ({self.timeout}s)"}
        except httpx.RequestError as e:
            logger.error("Gemini send connection error: %s", e)
            return {"ok": False, "error": f"خطأ في الاتصال: {e}"}
        except Exception as e:
            logger.error("Gemini send unexpected error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  فحص الحالة
    # ──────────────────────────────────────────────────────────

    def status(self) -> dict:
        """التحقق من أن Gemini API يعمل."""
        result = self.get_models()
        if result["ok"]:
            return {"ok": True, "models_count": len(result["models"])}
        return result
