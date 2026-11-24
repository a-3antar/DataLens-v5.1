"""
ai/ollama.py
============
محرك Ollama المحلي مع إعدادات مناسبة لمهام تحليل البيانات وكتابة SQL.
"""

import logging
from typing import Optional

import httpx

from ai.base_engine import BaseEngine
from config import OLLAMA_DEFAULT_URL, OLLAMA_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class OllamaEngine(BaseEngine):
    """محرك Ollama المحلي."""

    def __init__(
        self,
        base_url: str = OLLAMA_DEFAULT_URL,
        model: str = "",
        timeout: int = OLLAMA_TIMEOUT_SECONDS,
        api_key: str = "",
        num_ctx: int = 8192,
        keep_alive: str = "10m",
    ):
        super().__init__(api_key="", model=model, timeout=timeout)
        self.base_url = base_url.rstrip("/")
        self.num_ctx = max(2048, int(num_ctx))
        self.keep_alive = keep_alive

    @property
    def models_url(self) -> str:
        return f"{self.base_url}/api/tags"

    @property
    def generate_url(self) -> str:
        return f"{self.base_url}/api/generate"

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

    def _num_predict_for_prompt(self, prompt: str) -> int:
        """حد أصغر لمخرجات SQL لتقليل زمن التوليد ومنع الاستطراد."""
        if "SQL QUERY:" in prompt:
            return 512
        return 1536

    def send(
        self,
        prompt: str,
        temperature: float = 0.1,
        timeout_override: Optional[int] = None,
    ) -> dict:
        """إرسال prompt إلى Ollama مع مهلة قراءة مستقلة وإعدادات توليد محددة."""
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
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": max(0.0, min(1.0, float(temperature))),
                "num_ctx": self.num_ctx,
                "num_predict": self._num_predict_for_prompt(prompt),
            },
        }

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
            duration_s = round(duration / 1_000_000_000, 2) if isinstance(duration, (int, float)) else None
            logger.info(
                "Ollama response: model=%s prompt_chars=%d response_chars=%d duration=%s",
                self.model, len(prompt), len(text), duration_s,
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

    def status(self) -> dict:
        """التحقق من أن Ollama يعمل."""
        result = self.get_models()
        if result["ok"]:
            return {
                "ok": True,
                "base_url": self.base_url,
                "models_count": len(result["models"]),
            }
        return result
