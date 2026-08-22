"""
core/auth.py
============
إدارة المستخدمين والجلسات.
يستخدم users.db المنفصل عن project.db.
"""

import sqlite3
import uuid
import logging
from datetime import datetime, timedelta
from pathlib  import Path
from typing   import Optional

import bcrypt

from config import USERS_DB, SESSION_EXPIRE_HOURS, BCRYPT_ROUNDS

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
        """)
        conn.commit()
    logger.info("users.db initialized: %s", USERS_DB)

# ══════════════════════════════════════════════════════════════
#  AuthManager
# ══════════════════════════════════════════════════════════════

class AuthManager:
    """
    واجهة موحدة لعمليات المصادقة.

    Example:
        auth = AuthManager()
        auth.register("admin", "password123")
        token = auth.login("admin", "password123")
        user  = auth.get_user_by_token(token)
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
