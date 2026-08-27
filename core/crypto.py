"""
core/crypto.py
================
تشفير/فك تشفير مفاتيح API قبل تخزينها في أي قاعدة بيانات
(users.db أو project.db)، باستخدام Fernet (تشفير متماثل من
مكتبة cryptography — نفس المكتبة المستخدمة أصلاً لتوقيع bcrypt
غير مطلوبة هنا، Fernet مستقلة وتأتي ضمن حزمة cryptography).

المفتاح السري:
----------------
يُقرأ أولاً من متغير بيئة DATALENS_SECRET_KEY. هذا هو الخيار
المطلوب فعلياً على أي استضافة سحابية بنظام ملفات مؤقت (مثل
Streamlit Community Cloud) — يُضبط مرة واحدة عبر "Secrets" في
إعدادات التطبيق، ويجب أن يبقى ثابتاً؛ لو تغيّر، تصبح كل القيم
المشفَّرة سابقاً غير قابلة لفك التشفير.

لو لم يوجد المتغير (تشغيل محلي عادةً)، يُنشأ مفتاح عشوائي مرة
واحدة ويُحفظ في data/secret.key (تحت DATA_DIR — نفس مجلد بيانات
التطبيق القابل للتخصيص عبر DATALENS_DATA_DIR في config.py) ويُعاد
استخدامه في كل تشغيل لاحق.

🛠️ إصلاح: كان _KEY_FILE مضبوطاً سابقاً كنص (str) لمسار Windows ثابت
مكتوب يدوياً (على قرص E مباشرة) — هذا لا يعمل
إطلاقاً خارج ذلك الجهاز تحديداً، وحتى عليه كان يرمي AttributeError
عند أول استخدام فعلي لأن str لا يملك .exists()/.parent/.read_bytes()
(كلها دوال pathlib.Path فقط). أُعيد الآن لاستخدام DATA_DIR / "secret.key"
كـ Path فعلي، ديناميكي ومتوافق مع أي بيئة تشغيل.

توليد المفتاح:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

التوافق مع مفاتيح قديمة غير مُشفَّرة:
------------------------------------------
أي قيمة كانت محفوظة قبل هذا التحديث بنص صريح ستفشل في فك التشفير
كـ Fernet token صالح. decrypt_value لا ترمي استثناءً في هذه الحالة
— تُرجع القيمة كما هي (نص عادي) حتى تستمر المفاتيح القديمة بالعمل
بدون أي إجراء يدوي من المستخدم. أول عملية حفظ جديدة لنفس المفتاح
(مثلاً بعد ضغط "حفظ" في صفحة الإعدادات) ستُشفِّره تلقائياً.
"""

import os
import logging

from cryptography.fernet import Fernet, InvalidToken

from config import DATA_DIR

logger = logging.getLogger(__name__)

# ✅ مسار ديناميكي فعلي (Path) تحت مجلد بيانات التطبيق — يعمل على أي
# جهاز/بيئة (محلي أو سحابي) بدل مسار Windows ثابت مكتوب يدوياً.
_KEY_FILE = DATA_DIR / "secret.key"
_fernet: Fernet = None


def _load_or_create_key() -> bytes:
    """
    مصدر المفتاح بالترتيب:
    1) متغير البيئة DATALENS_SECRET_KEY (الموصى به على السحابة).
    2) ملف data/secret.key محلي (يُنشأ تلقائياً عند أول تشغيل).
    """
    env_key = os.environ.get("DATALENS_SECRET_KEY")
    if env_key:
        return env_key.encode() if isinstance(env_key, str) else env_key

    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()

    key = Fernet.generate_key()
    try:
        _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _KEY_FILE.write_bytes(key)
        logger.warning(
            "DATALENS_SECRET_KEY غير مضبوط كمتغير بيئة — تم توليد مفتاح "
            "تشفير محلي وحفظه في %s. على استضافة سحابية (Streamlit "
            "Community Cloud) اضبط DATALENS_SECRET_KEY كـ Secret ثابت، "
            "وإلا فقد يتغيّر هذا الملف مع كل إعادة نشر وتُفقد القدرة "
            "على فك تشفير المفاتيح المحفوظة سابقاً.",
            _KEY_FILE,
        )
    except Exception as e:
        logger.error("تعذر حفظ مفتاح التشفير المحلي على القرص: %s", e)
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt_value(value: str) -> str:
    """تشفير نص (مفتاح API عادة). قيمة فارغة/None تبقى كما هي."""
    if not value:
        return value
    try:
        return _get_fernet().encrypt(value.encode()).decode()
    except Exception as e:
        logger.error("encrypt_value error: %s", e)
        return value


def decrypt_value(value: str) -> str:
    """
    فك تشفير نص. لو كانت القيمة غير مُشفَّرة أصلاً (مفتاح قديم قبل
    هذا التحديث، أو تالفة لأي سبب)، تُرجع كما هي بدل رمي استثناء
    يوقف الصفحة — مع تسجيل الحالة في اللوج فقط.
    """
    if not value:
        return value
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return value
    except Exception as e:
        logger.error("decrypt_value error: %s", e)
        return value
