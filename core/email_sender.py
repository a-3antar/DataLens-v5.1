"""
core/email_sender.py
=====================
إرسال بريد إلكتروني عبر SMTP لأغراض تأكيد الحساب واستعادة كلمة
المرور وتغيير البريد الإلكتروني. تُقرأ إعدادات خادم SMTP من متغيرات
بيئة — لا شيء منها مطلوب لتشغيل بقية التطبيق؛ لو لم تُضبط، تُعاد
رسالة خطأ واضحة بدل انهيار الصفحة أو محاولة اتصال فاشلة صامتة.

متغيرات البيئة:
    DATALENS_SMTP_HOST      عنوان خادم SMTP (مطلوب لتفعيل الإرسال)
    DATALENS_SMTP_PORT      المنفذ (افتراضي 587)
    DATALENS_SMTP_USER      اسم المستخدم للمصادقة
    DATALENS_SMTP_PASSWORD  كلمة المرور / App Password
    DATALENS_SMTP_FROM      عنوان المرسل الظاهر (افتراضي = SMTP_USER)
    DATALENS_SMTP_USE_TLS   "1" أو "0" (افتراضي "1" — STARTTLS)
"""

import logging
import os
import smtplib
from email.message import EmailMessage

## وضعت قيم متغيرات البيئة في ملف mail_param.py
from core.mail_param import *

logger = logging.getLogger(__name__)


def _smtp_config() -> dict:
    port = int(DATALENS_SMTP_PORT or os.environ.get("DATALENS_SMTP_PORT", "587") or 587)
    use_ssl_env = globals().get("DATALENS_SMTP_USE_SSL", None) or os.environ.get("DATALENS_SMTP_USE_SSL")
    use_ssl = (str(use_ssl_env) == "1") if use_ssl_env is not None else (port == 465)
    return {
        "host"     : DATALENS_SMTP_HOST or os.environ.get("DATALENS_SMTP_HOST", ""),
        "port"     : port,
        "user"     : DATALENS_SMTP_USER or os.environ.get("DATALENS_SMTP_USER", ""),
        "password" : DATALENS_SMTP_PASSWORD or os.environ.get("DATALENS_SMTP_PASSWORD", ""),
        "from_addr": DATALENS_SMTP_FROM or os.environ.get("DATALENS_SMTP_FROM", "") or os.environ.get("DATALENS_SMTP_USER", ""),
        "use_tls"  : DATALENS_SMTP_USE_TLS or os.environ.get("DATALENS_SMTP_USE_TLS", "1") != "0",
        "use_ssl"  : use_ssl,
    }
    
def is_configured() -> bool:
    """هل خادم SMTP مضبوط فعلياً على هذا السيرفر؟"""
    cfg = _smtp_config()
    return bool(cfg["host"] and cfg["user"] and cfg["password"])


def send_email(
    to_email: str, subject: str, body: str, is_html: bool = False
) -> dict:
    """إرسال بريد إلكتروني مع الحماية وإرجاع حالة العملية.

    النتيجة: {"ok": True} أو {"ok": False, "error": "..."}
    """
    cfg = _smtp_config()
    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        msg = "إعدادات خادم البريد (SMTP) غير مضبوطة على السيرفر — راجع مدير النظام"
        logger.warning("send_email skipped: %s", msg)
        return {"ok": False, "error": msg}

    try:
        # استخدام EmailMessage لمنع Header Injection ولتنسيق أسهل
        msg = EmailMessage()
        msg["From"] = cfg["from_addr"]
        msg["To"] = to_email
        msg["Subject"] = (
            subject.replace("\r", "").replace("\n", " ").strip()
        )  # تنظيف العنوان

        if is_html:
            msg.set_content(
                "يرجى فتح الرسالة من متصفح يدعم HTML."
            )  # Fallback نصي
            msg.add_alternative(body, subtype="html")
        else:
            msg.set_content(body)

        # التمييز بين SSL المباشر (465) و STARTTLS (587)
        timeout_seconds = 8

        if cfg["use_ssl"]:
            with smtplib.SMTP_SSL(
                cfg["host"], cfg["port"], timeout=timeout_seconds
            ) as server:
                server.login(cfg["user"], cfg["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(
                cfg["host"], cfg["port"], timeout=timeout_seconds
            ) as server:
                if cfg["use_tls"]:
                    server.starttls()
                server.login(cfg["user"], cfg["password"])
                server.send_message(msg)

        logger.info("Email sent to %s: %s", to_email, subject)
        return {"ok": True}

    except Exception as e:
        logger.error("send_email error (to=%s): %s", to_email, e)
        return {"ok": False, "error": f"تعذر إرسال البريد: {e}"}