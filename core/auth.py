"""
core/auth.py
============
إدارة المستخدمين والجلسات، ومفاتيح API الخاصة بكل مستخدم لكل محرك AI.
يستخدم users.db المنفصل عن project.db.

مفاتيح API:
------------
تُخزَّن هنا (وليس في project.db) لكل مستخدم لكل محرك على حدة.

🆕 تشفير: القيمة المخزَّنة في عمود api_key مُشفَّرة عبر core.crypto
(Fernet) بدل نص صريح — راجع core/crypto.py لتفاصيل مصدر مفتاح
التشفير والتوافق مع مفاتيح قديمة غير مُشفَّرة محفوظة قبل هذا
التحديث. هذا يمنع تسرّب المفاتيح كنص صريح عند تصفح/نسخ ملف
users.db مباشرة، بينما يبقى المفتاح المُستخدَم فعلياً في الطلبات
(get_api_key) نصاً صريحاً كما هو متوقَّع من أي محرك AI.

🆕 البريد الإلكتروني ورموز التحقق (verification_codes):
------------------------------------------------------------
عمودان جديدان على users: email (اختياري) وemail_verified (0/1).
جدول واحد مشترك verification_codes يخدم ثلاثة أغراض (purpose):
  - "verify_email"   : تأكيد أول بريد يُسجَّله المستخدم.
  - "change_email"   : تأكيد بريد جديد قبل استبدال البريد الحالي
                        (target_email يحمل البريد الجديد المطلوب).
  - "reset_password" : استعادة كلمة مرور منسية عبر رمز يُرسَل للبريد
                        المسجَّل (بدل رابط — أبسط في تطبيق Streamlit
                        بدون routing حقيقي عبر URL).
الرمز نفسه (6 أرقام) لا يُخزَّن أبداً كنص صريح — فقط بصمة SHA-256 منه
(code_hash)، بنفس فلسفة عدم تخزين أسرار كنص صريح المتّبعة في باقي
هذا الملف. كل رمز صالح لمدة CODE_EXPIRE_MINUTES دقيقة فقط، ويُحذف
فور استهلاكه بنجاح (استخدام لمرة واحدة).

إرسال الرمز الفعلي عبر core.email_sender (SMTP) — لو خادم البريد غير
مضبوط على السيرفر، تُعاد رسالة خطأ واضحة للواجهة بدل فشل صامت.

🆕 حذف الحساب (Soft Delete + أرشفة مؤقتة):
--------------------------------------------
delete_account() لا يحذف بيانات المستخدم نهائياً من القرص فوراً —
بدلاً من ذلك:
  1. يُنقَل مجلد المشروعات الخاص بالمستخدم (data/projects/{user_id})
     بالكامل إلى data/deleted_users/{user_id}_{timestamp}/ عبر
     shutil.move (نقل فعلي، وليس نسخ، لتفادي مضاعفة المساحة).
  2. تُحذف كل صفوف المستخدم من users.db (users, sessions,
     user_api_keys, verification_codes) فوراً — الحساب يصبح غير قابل
     لتسجيل الدخول أو الاستخدام من هذه اللحظة.
  3. يُسجَّل صف في جدول pending_deletions يحمل مسار الأرشيف وتاريخ
     الحذف الفعلي (purge_at = تاريخ الحذف + 30 يوماً).

purge_expired_deletions() — تُستدعى دورياً (من main.py عند كل إقلاع،
بنفس نمط clean_expired_sessions) — تفحص pending_deletions وتحذف
نهائياً (shutil.rmtree) أي أرشيف تجاوز purge_at، ثم تحذف صفه من
الجدول. هذا يوفّر نافذة أمان بمدة شهر قبل الحذف النهائي غير القابل
للتراجع، مع عدم ترك أي بيانات معلّقة إلى الأبد.

ملاحظة: لا توجد حالياً واجهة "استرجاع حساب محذوف" — الأرشفة هنا
للحماية من الحذف الخاطئ فقط (يمكن استعادتها يدوياً من القرص خلال
الشهر لو لزم الأمر)، وليست ميزة استرجاع ذاتية للمستخدم.

🧹 تنظيف: حُذفت get_user_by_token()، get_all_users()، user_exists()
— الثلاثة كانت غير مستخدمة في أي مكان بالمشروع (التحقق من الجلسة في
الواجهة يعتمد على وجود "token" في st.session_state مباشرة بدون إعادة
التحقق من users.db في كل صفحة، وتسجيل الدخول لا يتحقق من وجود اسم
المستخدم مسبقاً إلا عبر IntegrityError عند register).
"""

import sqlite3
import shutil
import uuid
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from pathlib  import Path
from typing   import Optional

import bcrypt

from config import USERS_DB, SESSION_EXPIRE_HOURS, BCRYPT_ROUNDS, PROJECTS_DIR, DATA_DIR, APP_NAME
from core.crypto import encrypt_value, decrypt_value
from core.email_sender import send_email, is_configured as email_is_configured

logger = logging.getLogger(__name__)

# مجلد أرشفة الحسابات المحذوفة (مؤقتاً) قبل الحذف النهائي
DELETED_USERS_DIR = DATA_DIR / "deleted_users"
ACCOUNT_PURGE_DAYS = 30

# مدة صلاحية رمز التحقق (بريد/استعادة كلمة مرور) بالدقائق
CODE_EXPIRE_MINUTES = 15

_VALID_PURPOSES = {"verify_email", "change_email", "reset_password"}


# ══════════════════════════════════════════════════════════════
#  أدوات مساعدة داخلية
# ══════════════════════════════════════════════════════════════

def _now() -> datetime:
    return datetime.utcnow()

def _now_str() -> str:
    return _now().isoformat()

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(USERS_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def _hash_code(code: str) -> str:
    """بصمة SHA-256 لرمز التحقق — لا يُخزَّن الرمز نفسه كنص صريح أبداً."""
    return hashlib.sha256(code.encode()).hexdigest()

def _generate_code() -> str:
    """رمز عشوائي من 6 أرقام (000000–999999) عبر secrets (آمن تشفيرياً)."""
    return f"{secrets.randbelow(1_000_000):06d}"

def _init_users_db() -> None:
    """إنشاء جداول users.db عند أول استيراد، وتطبيق أي migrations لاحقة."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id         TEXT PRIMARY KEY,
                username   TEXT UNIQUE NOT NULL,
                password   TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );

            -- مفاتيح API لكل مستخدم، مفصولة حسب محرك AI. تُخزَّن هنا
            -- (وليس في project.db) حتى لا تظهر المفاتيح داخل ملفات
            -- المشاريع القابلة للتصدير/الاستيراد أو المشاركة.
            -- العمود api_key يحتوي القيمة مُشفَّرة (راجع core/crypto.py).
            CREATE TABLE IF NOT EXISTS user_api_keys (
                user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                engine_name TEXT NOT NULL,
                api_key     TEXT NOT NULL DEFAULT '',
                model       TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (user_id, engine_name)
            );

            -- 🆕 حسابات محذوفة بانتظار الحذف النهائي (أرشفة مؤقتة).
            -- لا مفتاح خارجي على users(id) عمداً — صف المستخدم نفسه
            -- يُحذف فوراً من جدول users عند delete_account(), بينما
            -- سجل الأرشفة هذا يبقى مستقلاً حتى purge_at.
            CREATE TABLE IF NOT EXISTS pending_deletions (
                id           TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL,
                username     TEXT NOT NULL,
                archive_path TEXT NOT NULL,
                deleted_at   TEXT NOT NULL,
                purge_at     TEXT NOT NULL
            );

            -- 🆕 رموز تحقق (تأكيد بريد / تغيير بريد / استعادة كلمة
            -- مرور) — راجع توثيق الوحدة أعلاه. الرمز نفسه غير مخزَّن،
            -- فقط بصمته (code_hash). استخدام لمرة واحدة (يُحذف الصف
            -- فور نجاح الاستهلاك).
            CREATE TABLE IF NOT EXISTS verification_codes (
                id           TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                purpose      TEXT NOT NULL,
                code_hash    TEXT NOT NULL,
                target_email TEXT,
                expires_at   TEXT NOT NULL,
                created_at   TEXT NOT NULL
            );
        """)
        conn.commit()

        # ── Migration: إضافة عمودي email/email_verified لو الجدول
        # موجود من تشغيل سابق قبل إضافة هذه الميزة (نفس نمط الترحيل
        # المستخدَم في core/project_db.py) ──────────────────────
        try:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "email" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
                logger.info("Migration: added 'email' column to users")
            if "email_verified" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
                logger.info("Migration: added 'email_verified' column to users")
            conn.commit()
        except sqlite3.Error as e:
            logger.warning("users email columns migration check failed: %s", e)

    logger.info("users.db initialized: %s", USERS_DB)


# ══════════════════════════════════════════════════════════════
#  AuthManager
# ══════════════════════════════════════════════════════════════

class AuthManager:
    """
    واجهة موحدة لعمليات المصادقة، البريد الإلكتروني، ومفاتيح API
    الشخصية للمستخدم.

    Example:
        auth = AuthManager()
        auth.register("admin", "password123", email="admin@example.com")
        token = auth.login("admin", "password123")

        auth.save_api_key(user_id, "gemini", "AIza...", "gemini-2.0-flash")
        saved = auth.get_api_key(user_id, "gemini")   # مفتاح صريح جاهز للاستخدام

        auth.change_password(user_id, "old_pw", "new_pw")
        auth.delete_account(user_id, "current_password")

        # البريد الإلكتروني:
        auth.send_verification_code(user_id)
        auth.verify_email(user_id, "123456")
        auth.request_email_change(user_id, "new@example.com")
        auth.confirm_email_change(user_id, "123456")

        # استعادة كلمة مرور منسية:
        auth.request_password_reset("admin")
        auth.reset_password_with_code("admin", "123456", "new_password")
    """

    def __init__(self):
        # تهيئة قاعدة البيانات عند كل إنشاء instance
        # آمن: CREATE TABLE IF NOT EXISTS لا يضر لو الجداول موجودة
        _init_users_db()

    # ──────────────────────────────────────────────────────────
    #  التسجيل
    # ──────────────────────────────────────────────────────────

    def register(self, username: str, password: str, email: str = "") -> dict:
        """
        تسجيل مستخدم جديد. البريد الإلكتروني اختياري عند التسجيل —
        لو تم إدخاله، يُرسَل رمز تحقق تلقائياً (فشل الإرسال لا يوقف
        التسجيل نفسه، فقط يُسجَّل تحذيراً في اللوج والمستخدم يستطيع
        طلب الرمز لاحقاً من صفحة الإعدادات).

        يرجع: {"ok": True, "user_id": "..."} أو {"ok": False, "error": "..."}
        """
        username = username.strip().lower()
        email = (email or "").strip().lower()

        if not username or not password:
            return {"ok": False, "error": "اسم المستخدم وكلمة المرور مطلوبان"}

        if len(username) < 3:
            return {"ok": False, "error": "اسم المستخدم 3 أحرف على الأقل"}

        if len(password) < 6:
            return {"ok": False, "error": "كلمة المرور 6 أحرف على الأقل"}

        if email and "@" not in email:
            return {"ok": False, "error": "صيغة البريد الإلكتروني غير صحيحة"}

        hashed  = bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS))
        user_id = str(uuid.uuid4())

        try:
            with _connect() as conn:
                conn.execute(
                    "INSERT INTO users (id, username, password, created_at, email, email_verified) "
                    "VALUES (?,?,?,?,?,0)",
                    (user_id, username, hashed.decode(), _now_str(), email)
                )
                conn.commit()
            logger.info("User registered: '%s'", username)

            if email:
                r = self.send_verification_code(user_id)
                if not r["ok"]:
                    logger.warning("Verification email not sent at registration: %s", r.get("error"))

            return {"ok": True, "user_id": user_id}

        except sqlite3.IntegrityError:
            return {"ok": False, "error": "اسم المستخدم مستخدم بالفعل"}
        except sqlite3.Error as e:
            logger.error("register error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

    # ──────────────────────────────────────────────────────────
    #  تسجيل الدخول
    # ──────────────────────────────────────────────────────────

    def login(self, username: str, password: str) -> dict:
        """
        تسجيل الدخول.
        يرجع: {"ok": True, "token": "...", "user_id": "...", "username": "..."}
               أو {"ok": False, "error": "..."}
        """
        username = username.strip().lower()

        try:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT id, password FROM users WHERE username = ?",
                    (username,)
                ).fetchone()

            if not row:
                return {"ok": False, "error": "اسم المستخدم أو كلمة المرور غير صحيحة"}

            if not bcrypt.checkpw(password.encode(), row["password"].encode()):
                return {"ok": False, "error": "اسم المستخدم أو كلمة المرور غير صحيحة"}

            # إنشاء session token
            token      = str(uuid.uuid4())
            expires_at = (_now() + timedelta(hours=SESSION_EXPIRE_HOURS)).isoformat()

            with _connect() as conn:
                conn.execute(
                    "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
                    (token, row["id"], expires_at)
                )
                conn.commit()

            logger.info("User logged in: '%s'", username)
            return {
                "ok"      : True,
                "token"   : token,
                "user_id" : row["id"],
                "username": username,
            }

        except sqlite3.Error as e:
            logger.error("login error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

    # ──────────────────────────────────────────────────────────
    #  معلومات المستخدم
    # ──────────────────────────────────────────────────────────

    def get_user_info(self, user_id: str) -> Optional[dict]:
        """
        معلومات ملف المستخدم الأساسية (اسم، بريد، حالة توثيق البريد) —
        تُستخدم في قائمة الحساب السريعة بالشريط الجانبي وصفحة الإعدادات.
        يرجع None لو المستخدم غير موجود.
        """
        try:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT username, email, email_verified FROM users WHERE id = ?",
                    (user_id,)
                ).fetchone()
            if not row:
                return None
            return {
                "username"      : row["username"],
                "email"         : row["email"] or "",
                "email_verified": bool(row["email_verified"]),
            }
        except sqlite3.Error as e:
            logger.error("get_user_info error: %s", e)
            return None

    # ──────────────────────────────────────────────────────────
    #  تسجيل الخروج
    # ──────────────────────────────────────────────────────────

    def logout(self, token: str) -> None:
        """حذف الجلسة الحالية."""
        self._delete_session(token)
        logger.info("Session deleted: %s...", token[:8] if token else "None")

    def _delete_session(self, token: str) -> None:
        try:
            with _connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
        except sqlite3.Error as e:
            logger.error("_delete_session error: %s", e)

    def _delete_all_sessions_for_user(self, user_id: str) -> None:
        """حذف كل جلسات مستخدم معيّن (تُستخدم عند حذف الحساب)."""
        try:
            with _connect() as conn:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                conn.commit()
        except sqlite3.Error as e:
            logger.error("_delete_all_sessions_for_user error: %s", e)

    # ──────────────────────────────────────────────────────────
    #  إدارة الجلسات
    # ──────────────────────────────────────────────────────────

    def clean_expired_sessions(self) -> int:
        """حذف الجلسات المنتهية. يرجع عدد السجلات المحذوفة."""
        try:
            with _connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM sessions WHERE expires_at < ?",
                    (_now_str(),)
                )
                conn.commit()
            count = cursor.rowcount
            if count:
                logger.info("Cleaned %d expired sessions", count)
            return count
        except sqlite3.Error as e:
            logger.error("clean_expired_sessions error: %s", e)
            return 0

    # ──────────────────────────────────────────────────────────
    #  🆕 تعديل بيانات المستخدم (اسم المستخدم)
    # ──────────────────────────────────────────────────────────

    def update_username(self, user_id: str, new_username: str) -> dict:
        """
        تعديل اسم المستخدم — يتطلب اسماً غير مستخدم من قبل حساب آخر.
        لا يمس الجلسة الحالية (session token يبقى صالحاً)، لكن الواجهة
        يجب أن تُحدِّث st.session_state["username"] فوراً بعد النجاح.

        يرجع: {"ok": True, "username": "..."} أو {"ok": False, "error": "..."}
        """
        new_username = new_username.strip().lower()
        if len(new_username) < 3:
            return {"ok": False, "error": "اسم المستخدم 3 أحرف على الأقل"}

        try:
            with _connect() as conn:
                conn.execute(
                    "UPDATE users SET username = ? WHERE id = ?",
                    (new_username, user_id)
                )
                conn.commit()
            logger.info("Username updated for user %s -> '%s'", user_id, new_username)
            return {"ok": True, "username": new_username}
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "اسم المستخدم مستخدم بالفعل"}
        except sqlite3.Error as e:
            logger.error("update_username error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

    # ──────────────────────────────────────────────────────────
    #  🆕 تغيير كلمة المرور
    # ──────────────────────────────────────────────────────────

    def change_password(self, user_id: str, old_password: str, new_password: str) -> dict:
        """
        تغيير كلمة مرور المستخدم — يتطلب كلمة المرور الحالية الصحيحة.
        كل الجلسات الأخرى (على أجهزة/متصفحات أخرى) تبقى سارية؛ لو
        رغبت لاحقاً بإبطالها جميعاً عند تغيير كلمة المرور، يمكن استدعاء
        _delete_all_sessions_for_user هنا بسهولة.

        يرجع: {"ok": True} أو {"ok": False, "error": "..."}
        """
        if not new_password or len(new_password) < 6:
            return {"ok": False, "error": "كلمة المرور الجديدة يجب أن تكون 6 أحرف على الأقل"}

        try:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT password FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                if not row:
                    return {"ok": False, "error": "المستخدم غير موجود"}

                if not bcrypt.checkpw(old_password.encode(), row["password"].encode()):
                    return {"ok": False, "error": "كلمة المرور الحالية غير صحيحة"}

                new_hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS))
                conn.execute(
                    "UPDATE users SET password = ? WHERE id = ?",
                    (new_hashed.decode(), user_id)
                )
                conn.commit()
            logger.info("Password changed for user: %s", user_id)
            return {"ok": True}
        except sqlite3.Error as e:
            logger.error("change_password error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

    # ──────────────────────────────────────────────────────────
    #  🆕 البريد الإلكتروني — رموز تحقق مشتركة
    # ──────────────────────────────────────────────────────────

    def _create_code(self, user_id: str, purpose: str, target_email: str = None) -> str:
        """
        إنشاء رمز تحقق جديد (6 أرقام) وتخزين بصمته فقط. أي رمز سابق
        بنفس (user_id, purpose) يُحذف أولاً حتى لا يبقى أكثر من رمز
        صالح واحد لكل غرض في نفس الوقت. يرجع الرمز الصريح (للإرسال
        بالبريد فقط — لا يُخزَّن).
        """
        code = _generate_code()
        expires_at = (_now() + timedelta(minutes=CODE_EXPIRE_MINUTES)).isoformat()
        with _connect() as conn:
            conn.execute(
                "DELETE FROM verification_codes WHERE user_id = ? AND purpose = ?",
                (user_id, purpose)
            )
            conn.execute(
                """
                INSERT INTO verification_codes
                    (id, user_id, purpose, code_hash, target_email, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), user_id, purpose, _hash_code(code),
                 target_email, expires_at, _now_str())
            )
            conn.commit()
        return code

    def _consume_code(self, user_id: str, purpose: str, code: str) -> dict:
        """
        التحقق من رمز وحذفه فور نجاح المطابقة (استخدام لمرة واحدة).
        يرجع: {"ok": True, "target_email": "..."} أو {"ok": False, "error": "..."}
        """
        if purpose not in _VALID_PURPOSES:
            return {"ok": False, "error": "نوع تحقق غير معروف"}
        code = (code or "").strip()
        if not code:
            return {"ok": False, "error": "الرجاء إدخال الرمز"}

        try:
            with _connect() as conn:
                row = conn.execute(
                    """
                    SELECT id, code_hash, target_email, expires_at
                    FROM verification_codes
                    WHERE user_id = ? AND purpose = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (user_id, purpose)
                ).fetchone()

                if not row:
                    return {"ok": False, "error": "لا يوجد رمز صالح — اطلب رمزاً جديداً"}

                if datetime.fromisoformat(row["expires_at"]) < _now():
                    conn.execute("DELETE FROM verification_codes WHERE id = ?", (row["id"],))
                    conn.commit()
                    return {"ok": False, "error": "انتهت صلاحية الرمز — اطلب رمزاً جديداً"}

                if _hash_code(code) != row["code_hash"]:
                    return {"ok": False, "error": "الرمز غير صحيح"}

                conn.execute("DELETE FROM verification_codes WHERE id = ?", (row["id"],))
                conn.commit()
            return {"ok": True, "target_email": row["target_email"]}
        except sqlite3.Error as e:
            logger.error("_consume_code error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

    def is_email_configured(self) -> bool:
        """هل خادم SMTP مضبوط على هذا السيرفر؟ — تستخدمها الواجهة لإظهار/إخفاء أزرار الإرسال."""
        return email_is_configured()

    # ── تأكيد البريد الأول ──

    def send_verification_code(self, user_id: str) -> dict:
        """إرسال رمز تأكيد للبريد الحالي المسجَّل للمستخدم."""
        info = self.get_user_info(user_id)
        if not info or not info["email"]:
            return {"ok": False, "error": "لا يوجد بريد إلكتروني مسجَّل لهذا الحساب"}
        if info["email_verified"]:
            return {"ok": False, "error": "البريد الإلكتروني موثّق بالفعل"}

        code = self._create_code(user_id, "verify_email")
        body = (
            f"مرحباً {info['username']},\n\n"
            f"رمز تأكيد بريدك الإلكتروني في {APP_NAME if False else 'DataLens'} هو: {code}\n"
            f"الرمز صالح لمدة {CODE_EXPIRE_MINUTES} دقيقة.\n\n"
            "لو لم تطلب هذا الرمز، تجاهل هذه الرسالة."
        )
        return send_email(info["email"], "رمز تأكيد البريد الإلكتروني — DataLens", body)

    def verify_email(self, user_id: str, code: str) -> dict:
        """استهلاك رمز تأكيد البريد الأول وتعليم البريد كموثّق."""
        r = self._consume_code(user_id, "verify_email", code)
        if not r["ok"]:
            return r
        try:
            with _connect() as conn:
                conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
                conn.commit()
            logger.info("Email verified for user: %s", user_id)
            return {"ok": True}
        except sqlite3.Error as e:
            logger.error("verify_email error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

    # ── تغيير البريد الإلكتروني ──

    def request_email_change(self, user_id: str, new_email: str) -> dict:
        """
        طلب تغيير البريد الإلكتروني: يُرسَل رمز تأكيد إلى البريد
        الجديد (وليس القديم) — البريد لا يتغيّر فعلياً في users إلا
        بعد نجاح confirm_email_change.
        """
        new_email = (new_email or "").strip().lower()
        if not new_email or "@" not in new_email:
            return {"ok": False, "error": "صيغة البريد الإلكتروني غير صحيحة"}

        try:
            with _connect() as conn:
                taken = conn.execute(
                    "SELECT id FROM users WHERE email = ? AND id != ?",
                    (new_email, user_id)
                ).fetchone()
            if taken:
                return {"ok": False, "error": "هذا البريد الإلكتروني مستخدَم بالفعل من حساب آخر"}
        except sqlite3.Error as e:
            logger.error("request_email_change lookup error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

        code = self._create_code(user_id, "change_email", target_email=new_email)
        body = (
            f"رمز تأكيد تغيير البريد الإلكتروني في DataLens هو: {code}\n"
            f"الرمز صالح لمدة {CODE_EXPIRE_MINUTES} دقيقة.\n\n"
            "لو لم تطلب هذا التغيير، تجاهل هذه الرسالة."
        )
        return send_email(new_email, "رمز تأكيد تغيير البريد الإلكتروني — DataLens", body)

    def confirm_email_change(self, user_id: str, code: str) -> dict:
        """استهلاك رمز تغيير البريد وتطبيق البريد الجديد فعلياً."""
        r = self._consume_code(user_id, "change_email", code)
        if not r["ok"]:
            return r
        new_email = r.get("target_email")
        if not new_email:
            return {"ok": False, "error": "تعذر إيجاد البريد الجديد المرتبط بالرمز"}
        try:
            with _connect() as conn:
                conn.execute(
                    "UPDATE users SET email = ?, email_verified = 1 WHERE id = ?",
                    (new_email, user_id)
                )
                conn.commit()
            logger.info("Email changed for user %s -> %s", user_id, new_email)
            return {"ok": True, "email": new_email}
        except sqlite3.Error as e:
            logger.error("confirm_email_change error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

    # ── استعادة كلمة مرور منسية ──

    def request_password_reset(self, username: str) -> dict:
        """
        إرسال رمز استعادة كلمة المرور إلى البريد المسجَّل لاسم
        المستخدم المُعطى. يُستدعى من صفحة تسجيل الدخول (قبل أي جلسة).
        """
        username = username.strip().lower()
        try:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT id, email FROM users WHERE username = ?", (username,)
                ).fetchone()
        except sqlite3.Error as e:
            logger.error("request_password_reset lookup error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

        if not row:
            return {"ok": False, "error": "اسم المستخدم غير موجود"}
        if not row["email"]:
            return {"ok": False, "error": "لا يوجد بريد إلكتروني مسجَّل لهذا الحساب — تواصل مع مدير النظام"}

        code = self._create_code(row["id"], "reset_password")
        body = (
            f"رمز استعادة كلمة المرور في DataLens هو: {code}\n"
            f"الرمز صالح لمدة {CODE_EXPIRE_MINUTES} دقيقة.\n\n"
            "لو لم تطلب استعادة كلمة المرور، تجاهل هذه الرسالة."
        )
        return send_email(row["email"], "رمز استعادة كلمة المرور — DataLens", body)

    def reset_password_with_code(self, username: str, code: str, new_password: str) -> dict:
        """تعيين كلمة مرور جديدة عبر رمز الاستعادة — لا يتطلب تسجيل دخول."""
        if not new_password or len(new_password) < 6:
            return {"ok": False, "error": "كلمة المرور الجديدة يجب أن تكون 6 أحرف على الأقل"}

        username = username.strip().lower()
        try:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()
        except sqlite3.Error as e:
            logger.error("reset_password_with_code lookup error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

        if not row:
            return {"ok": False, "error": "اسم المستخدم غير موجود"}

        user_id = row["id"]
        r = self._consume_code(user_id, "reset_password", code)
        if not r["ok"]:
            return r

        try:
            new_hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS))
            with _connect() as conn:
                conn.execute(
                    "UPDATE users SET password = ? WHERE id = ?",
                    (new_hashed.decode(), user_id)
                )
                conn.commit()
            # إبطال كل الجلسات القديمة احتياطاً — كلمة المرور تغيّرت
            # عبر مسار لا يتطلب معرفة كلمة المرور السابقة
            self._delete_all_sessions_for_user(user_id)
            logger.info("Password reset via code for user: %s", user_id)
            return {"ok": True}
        except sqlite3.Error as e:
            logger.error("reset_password_with_code update error: %s", e)
            return {"ok": False, "error": "خطأ في قاعدة البيانات"}

    # ──────────────────────────────────────────────────────────
    #  🆕 حذف الحساب (Soft Delete + أرشفة 30 يوماً)
    # ──────────────────────────────────────────────────────────

    def delete_account(self, user_id: str, password: str) -> dict:
        """
        حذف حساب المستخدم:
        1. التحقق من كلمة المرور الحالية (تأكيد إضافي قبل عملية لا رجعة
           فيها من واجهة المستخدم، حتى لو كانت البيانات فعلياً مؤرشفة).
        2. نقل data/projects/{user_id} (إن وُجد) إلى
           data/deleted_users/{user_id}_{timestamp}/.
        3. حذف صفوف المستخدم من users/sessions/user_api_keys/
           verification_codes.
        4. تسجيل صف في pending_deletions لحذف الأرشيف نهائياً بعد
           ACCOUNT_PURGE_DAYS يوماً (عبر purge_expired_deletions).

        يرجع: {"ok": True} أو {"ok": False, "error": "..."}
        """
        try:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT username, password FROM users WHERE id = ?", (user_id,)
                ).fetchone()
            if not row:
                return {"ok": False, "error": "المستخدم غير موجود"}

            if not bcrypt.checkpw(password.encode(), row["password"].encode()):
                return {"ok": False, "error": "كلمة المرور غير صحيحة"}

            username = row["username"]

            # ── نقل مجلد بيانات المستخدم إلى الأرشيف (لو كان موجوداً) ──
            source_dir = PROJECTS_DIR / user_id
            timestamp = _now().strftime("%Y%m%d_%H%M%S")
            archive_dir = DELETED_USERS_DIR / f"{user_id}_{timestamp}"

            if source_dir.exists():
                DELETED_USERS_DIR.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source_dir), str(archive_dir))
                logger.info("User data archived: %s -> %s", source_dir, archive_dir)
            else:
                # لا مشاريع لهذا المستخدم أصلاً — لا حاجة لأرشيف فعلي،
                # لكن نُبقي سجل pending_deletions لضبط سلوك موحّد (لا
                # يُنشئ مجلداً فارغاً، فقط يُسجَّل بدون archive_path فعلي)
                archive_dir = None

            # ── حذف صفوف المستخدم من users.db ──
            with _connect() as conn:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM user_api_keys WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM verification_codes WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

                # تسجيل الأرشفة المعلّقة فقط لو كان هناك فعلياً مجلد أُرشِف
                if archive_dir is not None:
                    deleted_at = _now()
                    purge_at = deleted_at + timedelta(days=ACCOUNT_PURGE_DAYS)
                    conn.execute(
                        """
                        INSERT INTO pending_deletions
                            (id, user_id, username, archive_path, deleted_at, purge_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (str(uuid.uuid4()), user_id, username, str(archive_dir),
                         deleted_at.isoformat(), purge_at.isoformat())
                    )
                conn.commit()

            logger.info("Account deleted (archived): '%s' (%s)", username, user_id)
            return {"ok": True}

        except Exception as e:
            logger.error("delete_account error: %s", e)
            return {"ok": False, "error": str(e)}

    def purge_expired_deletions(self) -> int:
        """
        حذف نهائي لكل أرشيفات الحسابات المحذوفة التي تجاوزت مهلة
        ACCOUNT_PURGE_DAYS — تُستدعى دورياً (main.py عند كل إقلاع، بنفس
        نمط clean_expired_sessions). يرجع عدد الأرشيفات المحذوفة نهائياً.
        """
        removed = 0
        try:
            with _connect() as conn:
                rows = conn.execute(
                    "SELECT id, archive_path FROM pending_deletions WHERE purge_at < ?",
                    (_now_str(),)
                ).fetchall()

            for row in rows:
                archive_path = Path(row["archive_path"])
                try:
                    if archive_path.exists():
                        shutil.rmtree(archive_path, ignore_errors=True)
                    with _connect() as conn:
                        conn.execute("DELETE FROM pending_deletions WHERE id = ?", (row["id"],))
                        conn.commit()
                    removed += 1
                except Exception as e:
                    logger.warning("purge_expired_deletions: failed for %s (%s)", archive_path, e)

            if removed:
                logger.info("Purged %d expired deleted-account archive(s)", removed)
            return removed
        except sqlite3.Error as e:
            logger.error("purge_expired_deletions error: %s", e)
            return 0

    # ──────────────────────────────────────────────────────────
    #  مفاتيح API لكل مستخدم — مُشفَّرة في التخزين (core.crypto)
    # ──────────────────────────────────────────────────────────

    def save_api_key(self, user_id: str, engine_name: str, api_key: str, model: str = "") -> None:
        """
        حفظ/تحديث مفتاح API لمحرك معيّن لهذا المستخدم.
        يُشفَّر api_key قبل الكتابة في العمود (core.crypto.encrypt_value)
        — لا يُستخدم لمحرك "ollama" (لا مفتاح له).
        """
        encrypted_key = encrypt_value(api_key)
        try:
            with _connect() as conn:
                conn.execute(
                    """
                    INSERT INTO user_api_keys (user_id, engine_name, api_key, model, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, engine_name) DO UPDATE SET
                        api_key    = excluded.api_key,
                        model      = excluded.model,
                        updated_at = excluded.updated_at
                    """,
                    (user_id, engine_name, encrypted_key, model, _now_str())
                )
                conn.commit()
            logger.info("API key saved (encrypted) for user %s, engine %s", user_id, engine_name)
        except sqlite3.Error as e:
            logger.error("save_api_key error: %s", e)
            raise

    def get_api_key(self, user_id: str, engine_name: str) -> dict:
        """
        إرجاع {"api_key": "...", "model": "..."} لمحرك معيّن — api_key
        مُعاد كنص صريح جاهز للاستخدام مباشرة (بعد فك التشفير)، أو
        {"api_key": "", "model": ""} لو لم يُحفظ شيء بعد لهذا المستخدم
        على هذا المحرك تحديداً.
        """
        try:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT api_key, model FROM user_api_keys WHERE user_id = ? AND engine_name = ?",
                    (user_id, engine_name)
                ).fetchone()
            if row:
                return {"api_key": decrypt_value(row["api_key"]), "model": row["model"]}
            return {"api_key": "", "model": ""}
        except sqlite3.Error as e:
            logger.error("get_api_key error: %s", e)
            return {"api_key": "", "model": ""}

    def get_last_used_engine(self, user_id: str) -> Optional[dict]:
        """
        آخر محرك+مفتاح استخدمه المستخدم (بناءً على أحدث updated_at) —
        يُستخدم لتعبئة إعدادات AI تلقائياً عند إنشاء مشروع جديد بدل
        ترك المستخدم يُعيد إدخال نفس البيانات من جديد لكل مشروع.
        api_key المُعاد هنا نص صريح (بعد فك التشفير).

        يرجع {"engine_name": ..., "api_key": ..., "model": ...} أو None
        لو لم يستخدم المستخدم أي محرك بعد.
        """
        try:
            with _connect() as conn:
                row = conn.execute(
                    """
                    SELECT engine_name, api_key, model FROM user_api_keys
                    WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1
                    """,
                    (user_id,)
                ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["api_key"] = decrypt_value(result["api_key"])
            return result
        except sqlite3.Error as e:
            logger.error("get_last_used_engine error: %s", e)
            return None
