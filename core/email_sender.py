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

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


def _smtp_config() -> dict:
    return {
        "host"     : os.environ.get("DATALENS_SMTP_HOST", ""),
        "port"     : int(os.environ.get("DATALENS_SMTP_PORT", "587") or 587),
        "user"     : os.environ.get("DATALENS_SMTP_USER", ""),
        "password" : os.environ.get("DATALENS_SMTP_PASSWORD", ""),
        "from_addr": os.environ.get("DATALENS_SMTP_FROM", "") or os.environ.get("DATALENS_SMTP_USER", ""),
        "use_tls"  : os.environ.get("DATALENS_SMTP_USE_TLS", "1") != "0",
    }


def is_configured() -> bool:
    """هل خادم SMTP مضبوط فعلياً على هذا السيرفر؟ — تُستخدم في الواجهة
    لإخفاء/تعطيل أزرار الإرسال بدل تركها تفشل بصمت عند الضغط."""
    cfg = _smtp_config()
    return bool(cfg["host"] and cfg["user"] and cfg["password"])


def send_email(to_email: str, subject: str, body: str) -> dict:
    """
    إرسال بريد نصي بسيط.
    يرجع: {"ok": True} أو {"ok": False, "error": "..."}

    لا يرمي استثناءً أبداً — أي فشل (إعدادات ناقصة، رفض الخادم، انقطاع
    شبكة) يُرجَع كخطأ عادي حتى تعرضه الواجهة بوضوح بدل كسر الصفحة.
    """
    cfg = _smtp_config()
    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        msg = "إعدادات خادم البريد (SMTP) غير مضبوطة على السيرفر — راجع مدير النظام"
        logger.warning("send_email skipped: %s", msg)
        return {"ok": False, "error": msg}

    try:
        message = MIMEMultipart()
        message["From"] = cfg["from_addr"]
        message["To"] = to_email
        message["Subject"] = subject
        message.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            if cfg["use_tls"]:
                server.starttls()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from_addr"], [to_email], message.as_string())

        logger.info("Email sent to %s: %s", to_email, subject)
        return {"ok": True}
    except Exception as e:
        logger.error("send_email error (to=%s): %s", to_email, e)
        return {"ok": False, "error": f"تعذر إرسال البريد: {e}"}