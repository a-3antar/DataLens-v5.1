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
"""

import sqlite3
import uuid
import logging
from datetime import datetime, timedelta
from pathlib  import Path
from typing   import Optional

import bcrypt

from config import USERS_DB, SESSION_EXPIRE_HOURS, BCRYPT_ROUNDS
from core.crypto import encrypt_value, decrypt_value

logger = logging.getLogger(__name__)


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

def _init_users_db() -> None:
    """إنشاء جداول users.db عند أول استيراد."""
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
        """)
        conn.commit()
    logger.info("users.db initialized: %s", USERS_DB)


# ══════════════════════════════════════════════════════════════
#  AuthManager
# ══════════════════════════════════════════════════════════════

class AuthManager:
    """
    واجهة موحدة لعمليات المصادقة ومفاتيح API الشخصية للمستخدم.

    Example:
        auth = AuthManager()
        auth.register("admin", "password123")
        token = auth.login("admin", "password123")
        user  = auth.get_user_by_token(token)

        auth.save_api_key(user_id, "gemini", "AIza...", "gemini-2.0-flash")
        saved = auth.get_api_key(user_id, "gemini")   # مفتاح صريح جاهز للاستخدام
    """

    def __init__(self):
        # تهيئة قاعدة البيانات عند كل إنشاء instance
        # آمن: CREATE TABLE IF NOT EXISTS لا يضر لو الجداول موجودة
        _init_users_db()

    # ──────────────────────────────────────────────────────────
    #  التسجيل
    # ──────────────────────────────────────────────────────────

    def register(self, username: str, password: str) -> dict:
        """
        تسجيل مستخدم جديد.
        يرجع: {"ok": True, "user_id": "..."} أو {"ok": False, "error": "..."}
        """
        username = username.strip().lower()

        if not username or not password:
            return {"ok": False, "error": "اسم المستخدم وكلمة المرور مطلوبان"}

        if len(username) < 3:
            return {"ok": False, "error": "اسم المستخدم 3 أحرف على الأقل"}

        if len(password) < 6:
            return {"ok": False, "error": "كلمة المرور 6 أحرف على الأقل"}

        hashed  = bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS))
        user_id = str(uuid.uuid4())

        try:
            with _connect() as conn:
                conn.execute(
                    "INSERT INTO users (id, username, password, created_at) VALUES (?,?,?,?)",
                    (user_id, username, hashed.decode(), _now_str())
                )
                conn.commit()
            logger.info("User registered: '%s'", username)
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
    #  التحقق من الجلسة
    # ──────────────────────────────────────────────────────────

    def get_user_by_token(self, token: str) -> Optional[dict]:
        """
        التحقق من token وإرجاع بيانات المستخدم.
        يرجع None إذا كان الـ token منتهياً أو غير موجود.
        """
        if not token:
            return None
        try:
            with _connect() as conn:
                row = conn.execute(
                    """
                    SELECT u.id, u.username, s.expires_at
                    FROM sessions s
                    JOIN users u ON s.user_id = u.id
                    WHERE s.token = ?
                    """,
                    (token,)
                ).fetchone()

            if not row:
                return None

            # التحقق من انتهاء الجلسة
            expires_at = datetime.fromisoformat(row["expires_at"])
            if _now() > expires_at:
                self._delete_session(token)
                logger.info("Session expired for token: %s...", token[:8])
                return None

            return {"user_id": row["id"], "username": row["username"]}

        except sqlite3.Error as e:
            logger.error("get_user_by_token error: %s", e)
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

    # ──────────────────────────────────────────────────────────
    #  إدارة المستخدمين
    # ──────────────────────────────────────────────────────────

    def get_all_users(self) -> list[dict]:
        """إرجاع كل المستخدمين (بدون كلمات المرور)."""
        try:
            with _connect() as conn:
                rows = conn.execute(
                    "SELECT id, username, created_at FROM users ORDER BY created_at"
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.error("get_all_users error: %s", e)
            return []

    def user_exists(self, username: str) -> bool:
        """التحقق من وجود مستخدم."""
        try:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT id FROM users WHERE username = ?",
                    (username.strip().lower(),)
                ).fetchone()
            return row is not None
        except sqlite3.Error as e:
            logger.error("user_exists error: %s", e)
            return False

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
