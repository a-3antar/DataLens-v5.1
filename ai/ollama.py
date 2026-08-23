"""
ai/ollama.py
============
محرك Ollama — نماذج AI محلية على نفس السيرفر.
يدعم تغيير الـ IP/URL من الإعدادات.
"""

import logging
import httpx
from ai.base_engine import BaseEngine
from config import OLLAMA_DEFAULT_URL

logger = logging.getLogger(__name__)


class OllamaEngine(BaseEngine):
    """
    محرك Ollama المحلي.

    الاستخدام:
        engine = OllamaEngine(base_url="http://localhost:11434", model="llama3")
        models = engine.get_models()
        result = engine.send(prompt)
    """

    def __init__(
        self,
        base_url: str = OLLAMA_DEFAULT_URL,
        model   : str = "",
        timeout : int = 60,   # أطول لأن النماذج المحلية أبطأ
        api_key : str = "",   # غير مطلوب لـ Ollama
    ):
        super().__init__(api_key="", model=model, timeout=timeout)
        # نضمن عدم وجود slash في النهاية
        self.base_url = base_url.rstrip("/")

    @property
    def models_url(self) -> str:
        return f"{self.base_url}/api/tags"

    @property
    def generate_url(self) -> str:
        return f"{self.base_url}/api/generate"

    # ──────────────────────────────────────────────────────────
    #  قائمة النماذج
    # ──────────────────────────────────────────────────────────

    def get_models(self) -> dict:
        """إرجاع النماذج المثبتة في Ollama."""
        try:
            resp = httpx.get(self.models_url, timeout=10)
            resp.raise_for_status()
            data   = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            logger.info("Ollama models fetched: %d", len(models))
            return {"ok": True, "models": models}
        except httpx.ConnectError:
            msg = f"تعذر الاتصال بـ Ollama على {self.base_url}"
            logger.error(msg)
            return {"ok": False, "error": msg, "error_type": "transient"}
        except httpx.TimeoutException:
            return {"ok": False, "error": "انتهت مهلة الاتصال بـ Ollama", "error_type": "transient"}
        except Exception as e:
            logger.error("Ollama get_models error: %s", e)
            return {"ok": False, "error": str(e), "error_type": "other"}

    # ──────────────────────────────────────────────────────────
    #  إرسال Prompt
    # ──────────────────────────────────────────────────────────

    def send(self, prompt: str, temperature: float = 0.1) -> dict:
        """إرسال prompt إلى Ollama وإرجاع الرد.

        ملاحظة: Ollama محلي ولا يملك مفهوم "مفتاح API"، لذا لا يوجد
        error_type="auth" هنا — أخطاؤه إما اتصال/مهلة (transient) أو
        غير ذلك (other).
        """
        if not self.model:
            return {"ok": False, "error": "لم يتم تحديد النموذج", "error_type": "other"}
        if not prompt.strip():
            return {"ok": False, "error": "الـ prompt فارغ", "error_type": "other"}

        try:
            resp = httpx.post(
                self.generate_url,
                json={
                    "model"  : self.model,
                    "prompt" : prompt,
                    "stream" : False,
                    "options": {
                        "temperature": max(0.0, min(1.0, temperature)),
                        "num_predict": 2048,
                    },
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data.get("response", "").strip()

            if not text:
                return {"ok": False, "error": "الرد فارغ من Ollama", "error_type": "other"}

            logger.info("Ollama response: %d chars", len(text))
            return {"ok": True, "text": text}

        except httpx.ConnectError:
            msg = f"تعذر الاتصال بـ Ollama على {self.base_url}"
            logger.error(msg)
            return {"ok": False, "error": msg, "error_type": "transient"}
        except httpx.TimeoutException:
            return {"ok": False, "error": f"انتهت مهلة الاتصال ({self.timeout}s)", "error_type": "transient"}
        except Exception as e:
            logger.error("Ollama send error: %s", e)
            return {"ok": False, "error": str(e), "error_type": "other"}

    # ──────────────────────────────────────────────────────────
    #  فحص الحالة
    # ──────────────────────────────────────────────────────────

    def status(self) -> dict:
        """التحقق من أن Ollama يعمل."""
        result = self.get_models()
        if result["ok"]:
            return {
                "ok"          : True,
                "base_url"    : self.base_url,
                "models_count": len(result["models"]),
            }
        return result
