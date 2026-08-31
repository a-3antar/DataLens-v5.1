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
            return {"ok": False, "error": "API key غير موجود", "error_type": "auth"}
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
            status = e.response.status_code
            msg = f"HTTP {status}: {e.response.text[:200]}"
            logger.error("Gemini get_models HTTP error: %s", msg)
            error_type = "auth" if status in (401, 403) else ("rate_limit" if status == 429 else "other")
            return {"ok": False, "error": msg, "error_type": error_type}
        except httpx.RequestError as e:
            logger.error("Gemini get_models connection error: %s", e)
            return {"ok": False, "error": f"خطأ في الاتصال: {e}", "error_type": "transient"}
        except Exception as e:
            logger.error("Gemini get_models unexpected error: %s", e)
            return {"ok": False, "error": str(e), "error_type": "other"}

    # ──────────────────────────────────────────────────────────
    #  إرسال Prompt
    # ──────────────────────────────────────────────────────────

    def send(self, prompt: str, temperature: float = 0.1, timeout_override: Optional[int] = None) -> dict:
        """
        إرسال prompt إلى Gemini وإرجاع الرد.
        يرجع: {"ok": True, "text": "..."} أو
              {"ok": False, "error": "...", "error_type": "auth"|"rate_limit"|"transient"|"other"}

        error_type يُستخدم في AIManager للتمييز بين خطأ دائم (مثل رفض
        المصادقة — لا فائدة من إعادة المحاولة بنفس المفتاح) وخطأ مؤقت
        (انقطاع شبكة عابر أو ضغط مؤقت) يستحق إعادة المحاولة فعلاً.

        timeout_override: مهلة اتصال بديلة لهذا الاستدعاء فقط (بدون
        تعديل self.timeout الدائم) — راجع BaseEngine.send للتفاصيل.
        """

        logger.critical("Starting Gemini send method...")
        if not self.api_key:
            return {"ok": False, "error": "API key غير موجود", "error_type": "auth"}
        if not self.model:
            return {"ok": False, "error": "لم يتم تحديد النموذج", "error_type": "other"}
        if not prompt.strip():
            return {"ok": False, "error": "الـ prompt فارغ", "error_type": "other"}

        effective_timeout = timeout_override or self.timeout

        url     = GEMINI_GEN_URL.format(model=self.model)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature"    : max(0.0, min(2.0, temperature)),
                "maxOutputTokens": 2048,
            },
        }
        logging.critical(f"Gemini send method: {url}")
        logging.critical(f"Gemini send method: {payload}")
        try:
            resp = httpx.post(
                url,
                params={"key": self.api_key},
                json=payload,
                timeout=effective_timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            # استخراج النص من الرد
            candidates = data.get("candidates", [])
            if not candidates:
                return {"ok": False, "error": "الرد لا يحتوي على candidates", "error_type": "other"}

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                return {"ok": False, "error": "الرد لا يحتوي على parts", "error_type": "other"}

            text = parts[0].get("text", "").strip()
            if not text:
                return {"ok": False, "error": "الرد فارغ", "error_type": "other"}

            logger.info("Gemini response received: %d chars", len(text))
            return {"ok": True, "text": text}

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            msg = f"HTTP {status}: {e.response.text[:200]}"
            logger.error("Gemini send HTTP error: %s", msg)

            if status in (401, 403):
                # خطأ مصادقة دائم — لا فائدة من إعادة المحاولة بنفس المفتاح.
                # الأسباب الشائعة: قيد IP/Application restriction على المفتاح
                # في Google Cloud Console (خطير خصوصاً على استضافة سحابية
                # بـ IP متغير مثل Streamlit Community Cloud)، أو Generative
                # Language API غير مفعّلة على المشروع المرتبط بالمفتاح،
                # أو المفتاح غير صالح/منتهي.
                friendly = (
                    "فشل التحقق من مفتاح Gemini API (401/403). الأسباب "
                    "المحتملة: (1) قيد IP على المفتاح في Google Cloud "
                    "Console — يجب ضبط Application restrictions على None، "
                    "(2) عدم تفعيل Generative Language API على نفس "
                    "المشروع، (3) مفتاح غير صالح."
                )
                return {"ok": False, "error": f"{friendly}\n({msg})", "error_type": "auth"}

            if status == 429:
                return {
                    "ok": False,
                    "error": f"تم تجاوز الحد المسموح من الطلبات (Quota/Rate Limit). {msg}",
                    "error_type": "rate_limit",
                }

            if status >= 500:
                return {"ok": False, "error": msg, "error_type": "transient"}

            return {"ok": False, "error": msg, "error_type": "other"}

        except httpx.TimeoutException:
            logger.error("Gemini send timeout after %ds", effective_timeout)
            return {"ok": False, "error": f"انتهت مهلة الاتصال ({effective_timeout}s)", "error_type": "transient"}
        except httpx.RequestError as e:
            logger.error("Gemini send connection error: %s", e)
            return {"ok": False, "error": f"خطأ في الاتصال: {e}", "error_type": "transient"}
        except Exception as e:
            logger.error("Gemini send unexpected error: %s", e)
            return {"ok": False, "error": str(e), "error_type": "other"}

    # ──────────────────────────────────────────────────────────
    #  فحص الحالة
    # ──────────────────────────────────────────────────────────

    def status(self) -> dict:
        """التحقق من أن Gemini API يعمل."""
        result = self.get_models()
        if result["ok"]:
            return {"ok": True, "models_count": len(result["models"])}
        return result
