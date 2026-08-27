"""
core/project_db.py
==================
المسؤول الوحيد عن التعامل مع ملف project.db.
كل module آخر يستخدم هذا الملف فقط ولا يلمس الـ db مباشرة.

🆕 تشفير مفاتيح API المخزَّنة ضمن جدول settings:
------------------------------------------------------
كل إعداد بمفتاح يبدأ بـ "api_key_" (مثل "api_key_groq",
"api_key_openrouter") يُشفَّر عبر core.crypto قبل الكتابة في
settings، ويُفَكّ تشفيره تلقائياً عند القراءة عبر get_settings —
بدون أي تغيير مطلوب في أي كود آخر يستخدم db.get_settings()/
save_settings() (ui/settings.py، ui/dashboards.py، ui/chat.py...).
القيم القديمة غير المُشفَّرة (قبل هذا التحديث) تستمر بالعمل تلقائياً
(راجع core/crypto.decrypt_value)، وتُشفَّر عند أول حفظ جديد لها.
"""

import sqlite3
import json
import shutil
import logging
from pathlib  import Path
from datetime import datetime
from typing   import Optional

import pandas as pd

from config import PROJECTS_DIR, DEFAULT_SETTINGS
from core.crypto import encrypt_value, decrypt_value

logger = logging.getLogger(__name__)

# بادئة مفاتيح الإعدادات التي تحتوي مفاتيح API فعلية ويجب تشفيرها
# قبل الكتابة في settings (مثل "api_key_groq"، "api_key_openrouter"...)
_API_KEY_SETTING_PREFIX = "api_key_"


# ══════════════════════════════════════════════════════════════
#  أدوات مساعدة داخلية
# ══════════════════════════════════════════════════════════════

def _now() -> str:
    """الوقت الحالي بصيغة ISO."""
    return datetime.utcnow().isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    """فتح اتصال بقاعدة البيانات مع تفعيل الـ foreign keys."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # أداء أفضل
    return conn


# ══════════════════════════════════════════════════════════════
#  ProjectDB
# ══════════════════════════════════════════════════════════════

class ProjectDB:
    """
    واجهة كاملة للتعامل مع ملف project.db الخاص بمشروع واحد.

    الاستخدام:
        db = ProjectDB(user_id="u1", project_id="p1")
        db.save_settings({"theme": "ocean_dark"})
        df = db.get_clean_data("sales")
    """

    def __init__(self, user_id: str, project_id: str):
        self.user_id    = user_id
        self.project_id = project_id
        self.db_dir     = PROJECTS_DIR / user_id / project_id
        self.db_path    = self.db_dir / "project.db"
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ──────────────────────────────────────────────────────────
    #  تهيئة الجداول
    # ──────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        """إنشاء الجداول الثابتة إن لم تكن موجودة."""
        sql_statements = [
            # الإعدادات
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            # معلومات الملفات المصدر
            """
            CREATE TABLE IF NOT EXISTS source_files (
                id              TEXT PRIMARY KEY,
                original_name   TEXT NOT NULL,
                table_alias     TEXT NOT NULL UNIQUE,
                selected_sheet  TEXT,
                selected_columns TEXT NOT NULL DEFAULT '[]',
                uploaded_at     TEXT NOT NULL
            )
            """,
            # العلاقات بين الجداول
            """
            CREATE TABLE IF NOT EXISTS relations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                from_table  TEXT NOT NULL,
                from_col    TEXT NOT NULL,
                to_table    TEXT NOT NULL,
                to_col      TEXT NOT NULL,
                UNIQUE(from_table, from_col, to_table, to_col)
            )
            """,
            # سجل المحادثات
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                id          TEXT PRIMARY KEY,
                question    TEXT NOT NULL,
                sql_query   TEXT,
                result_type TEXT,
                result_json TEXT,
                error       TEXT,
                created_at  TEXT NOT NULL
            )
            """,
            # التقارير
            """
            CREATE TABLE IF NOT EXISTS reports (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            # بلوكات التقارير
            """
            CREATE TABLE IF NOT EXISTS report_blocks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id  TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                position   INTEGER NOT NULL,
                block_type TEXT NOT NULL,
                content    TEXT NOT NULL DEFAULT '{}'
            )
            """,
            # لوحات المعلومات
            """
            CREATE TABLE IF NOT EXISTS dashboards (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                template_id TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT
            )
            """,
            # خلايا لوحة المعلومات
            # base_sql: الـ SQL الأساسي الذي وُلِّد عبر AI أول مرة لهذا
            # السؤال. طالما لم يتغيّر نص السؤال، يُعاد استخدامه لتطبيق
            # فلاتر (Slicers) مختلفة بدون إعادة استدعاء AI في كل تحديث.
            """
            CREATE TABLE IF NOT EXISTS dashboard_cells (
                id              TEXT PRIMARY KEY,
                dashboard_id    TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
                position        INTEGER NOT NULL,
                display_type    TEXT,
                title           TEXT,
                question        TEXT,
                chart_type      TEXT,
                base_sql        TEXT,
                last_result     TEXT,
                last_sql        TEXT,
                last_error      TEXT,
                last_updated_at TEXT,
                UNIQUE(dashboard_id, position)
            )
            """,
            # عناصر الفلترة (Slicers) لكل لوحة
            """
            CREATE TABLE IF NOT EXISTS dashboard_slicers (
                id              TEXT PRIMARY KEY,
                dashboard_id    TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
                position        INTEGER NOT NULL,
                table_name      TEXT,
                column_name     TEXT,
                selected_values TEXT,
                UNIQUE(dashboard_id, position)
            )
            """,
        ]
        try:
            with _connect(self.db_path) as conn:
                for sql in sql_statements:
                    conn.execute(sql)
                conn.commit()

                # ── Migration: إضافة عمود base_sql لو الجدول موجود من
                # تشغيل سابق قبل إضافة هذه الميزة (CREATE TABLE IF NOT
                # EXISTS لا يضيف أعمدة جديدة لجدول موجود مسبقاً) ──────
                try:
                    cols = [r["name"] for r in conn.execute(
                        "PRAGMA table_info(dashboard_cells)"
                    ).fetchall()]
                    if "base_sql" not in cols:
                        conn.execute("ALTER TABLE dashboard_cells ADD COLUMN base_sql TEXT")
                        conn.commit()
                        logger.info("Migration: added base_sql column to dashboard_cells")
                except sqlite3.Error as e:
                    logger.warning("base_sql migration check failed: %s", e)

                # إدراج الإعدادات الافتراضية إن لم توجد
                for key, value in DEFAULT_SETTINGS.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                        (key, json.dumps(value))
                    )
                conn.commit()
            logger.info("ProjectDB initialized: %s", self.db_path)
        except sqlite3.Error as e:
            logger.error("ProjectDB init error: %s", e)
            raise

    # ──────────────────────────────────────────────────────────
    #  الإعدادات
    # ──────────────────────────────────────────────────────────

    def get_settings(self) -> dict:
        """
        إرجاع كل الإعدادات كـ dict. أي مفتاح يبدأ بـ "api_key_" يُفَكّ
        تشفيره تلقائياً قبل الإرجاع (القيمة المخزَّنة فعلياً في
        settings مُشفَّرة — راجع save_settings أدناه).
        """
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute("SELECT key, value FROM settings").fetchall()
            result = {}
            for row in rows:
                key = row["key"]
                value = json.loads(row["value"])
                if key.startswith(_API_KEY_SETTING_PREFIX) and isinstance(value, str):
                    value = decrypt_value(value)
                result[key] = value
            return result
        except sqlite3.Error as e:
            logger.error("get_settings error: %s", e)
            return dict(DEFAULT_SETTINGS)

    def save_settings(self, updates: dict) -> None:
        """
        تحديث إعدادات محددة (لا يحذف الباقي). أي مفتاح يبدأ بـ
        "api_key_" يُشفَّر عبر core.crypto قبل الكتابة في قاعدة
        البيانات — القيمة الأصلية (الصريحة) الممرَّرة هنا لا تُلمس،
        فقط النسخة المخزَّنة على القرص هي المُشفَّرة.
        """
        try:
            with _connect(self.db_path) as conn:
                for key, value in updates.items():
                    stored_value = value
                    if key.startswith(_API_KEY_SETTING_PREFIX) and isinstance(value, str):
                        stored_value = encrypt_value(value)
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                        (key, json.dumps(stored_value))
                    )
                conn.commit()
            logger.info("Settings updated: %s", list(updates.keys()))
        except sqlite3.Error as e:
            logger.error("save_settings error: %s", e)
            raise

    # ──────────────────────────────────────────────────────────
    #  الملفات
    # ──────────────────────────────────────────────────────────

    def add_file(
        self,
        file_id       : str,
        original_name : str,
        table_alias   : str,
        selected_sheet: Optional[str] = None,
        selected_columns: list        = None,
    ) -> None:
        """تسجيل ملف مصدر جديد."""
        columns = selected_columns or []
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO source_files
                        (id, original_name, table_alias,
                         selected_sheet, selected_columns, uploaded_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (file_id, original_name, table_alias,
                     selected_sheet, json.dumps(columns), _now())
                )
                conn.commit()
            logger.info("File registered: %s → table '%s'", original_name, table_alias)
        except sqlite3.Error as e:
            logger.error("add_file error: %s", e)
            raise

    def get_files(self) -> list[dict]:
        """إرجاع كل الملفات المسجلة."""
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute("SELECT * FROM source_files").fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["selected_columns"] = json.loads(d["selected_columns"])
                result.append(d)
            return result
        except sqlite3.Error as e:
            logger.error("get_files error: %s", e)
            return []

    def remove_file(self, file_id: str) -> None:
        """حذف ملف مصدر وجدول البيانات النظيفة الخاص به."""
        try:
            with _connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT table_alias FROM source_files WHERE id = ?", (file_id,)
                ).fetchone()
                if row:
                    alias = row["table_alias"]
                    conn.execute(f'DROP TABLE IF EXISTS "data_{alias}"')
                conn.execute("DELETE FROM source_files WHERE id = ?", (file_id,))
                conn.commit()
            logger.info("File removed: %s", file_id)
        except sqlite3.Error as e:
            logger.error("remove_file error: %s", e)
            raise

    # ──────────────────────────────────────────────────────────
    #  البيانات النظيفة
    # ──────────────────────────────────────────────────────────

    def save_clean_data(self, table_alias: str, df: pd.DataFrame) -> None:
        """
        حفظ DataFrame كجدول بيانات نظيفة داخل project.db.

        ملاحظة مهمة: SQLite لا يملك نوع بيانات تاريخ حقيقياً، فتُحفظ
        أعمدة datetime64 كنص. لنتفادى فقدان النوع عند إعادة التحميل
        (وهو ما يكسر دوال مثل DATE_TRUNC لاحقاً في DuckDB)، نُسجّل أسماء
        أعمدة التاريخ في الإعدادات ونعيد تحويلها في get_clean_data.
        """
        table_name = f"data_{table_alias}"
        try:
            date_cols = [
                col for col in df.columns
                if pd.api.types.is_datetime64_any_dtype(df[col])
            ]
            with _connect(self.db_path) as conn:
                df.to_sql(table_name, conn, if_exists="replace", index=False)
            self.save_settings({f"_date_cols_{table_alias}": date_cols})
            logger.info(
                "Clean data saved: table '%s' (%d rows, %d cols, %d date cols)",
                table_name, len(df), len(df.columns), len(date_cols)
            )
        except Exception as e:
            logger.error("save_clean_data error: %s", e)
            raise

    def get_clean_data(self, table_alias: str) -> Optional[pd.DataFrame]:
        """
        تحميل البيانات النظيفة كـ DataFrame، مع استعادة أعمدة التاريخ
        إلى نوعها الأصلي (datetime64) بدل تركها نصاً كما يُخزّنها SQLite.
        """
        table_name = f"data_{table_alias}"
        try:
            with _connect(self.db_path) as conn:
                df = pd.read_sql(f'SELECT * FROM "{table_name}"', conn)

            date_cols = self.get_settings().get(f"_date_cols_{table_alias}", [])
            for col in date_cols:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce")

            logger.info("Clean data loaded: table '%s' (%d rows)", table_name, len(df))
            return df
        except Exception as e:
            logger.error("get_clean_data error ('%s'): %s", table_alias, e)
            return None

    def get_schema(self) -> dict:
        """
        بناء schema كامل لكل الجداول.
        يُستخدم في Prompt Builder.
        مثال:
            {
              "sales": {
                "columns": {"date": "DATE", "amount": "REAL"},
                "sample": [{"date": "2024-01-01", "amount": 5000}]
              }
            }
        """
        from config import SAMPLE_ROWS
        schema = {}
        try:
            files = self.get_files()
            with _connect(self.db_path) as conn:
                for f in files:
                    alias      = f["table_alias"]
                    table_name = f"data_{alias}"
                    # أنواع الأعمدة
                    cursor = conn.execute(f'PRAGMA table_info("{table_name}")')
                    cols   = {row["name"]: row["type"] for row in cursor.fetchall()}
                    if not cols:
                        continue

                    # نُصحّح نوع أعمدة التاريخ المُتتبَّعة: SQLite يُخزّنها
                    # كنص (TEXT) فتظهر كذلك افتراضياً، بينما هي فعلياً
                    # datetime64 بعد إعادة التحميل عبر get_clean_data.
                    # هذا التصحيح يُعلم الذكاء الاصطناعي أنه يمكنه استخدام
                    # دوال التاريخ (مثل DATE_TRUNC) مباشرة بدون CAST يدوي.
                    date_cols = self.get_settings().get(f"_date_cols_{alias}", [])
                    for col in date_cols:
                        if col in cols:
                            cols[col] = "DATE"

                    # عينة بيانات
                    sample_rows = conn.execute(
                        f'SELECT * FROM "{table_name}" LIMIT {SAMPLE_ROWS}'
                    ).fetchall()
                    sample = [dict(r) for r in sample_rows]
                    schema[alias] = {"columns": cols, "sample": sample}
            return schema
        except Exception as e:
            logger.error("get_schema error: %s", e)
            return {}

    # ──────────────────────────────────────────────────────────
    #  العلاقات
    # ──────────────────────────────────────────────────────────

    def add_relation(
        self,
        from_table: str, from_col: str,
        to_table  : str, to_col  : str,
    ) -> None:
        """إضافة علاقة بين جدولين."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO relations
                        (from_table, from_col, to_table, to_col)
                    VALUES (?, ?, ?, ?)
                    """,
                    (from_table, from_col, to_table, to_col)
                )
                conn.commit()
            logger.info("Relation added: %s.%s → %s.%s",
                        from_table, from_col, to_table, to_col)
        except sqlite3.Error as e:
            logger.error("add_relation error: %s", e)
            raise

    def get_relations(self) -> list[dict]:
        """إرجاع كل العلاقات."""
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute("SELECT * FROM relations").fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.error("get_relations error: %s", e)
            return []

    def remove_relation(self, relation_id: int) -> None:
        """حذف علاقة بالـ id."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute("DELETE FROM relations WHERE id = ?", (relation_id,))
                conn.commit()
            logger.info("Relation removed: id=%d", relation_id)
        except sqlite3.Error as e:
            logger.error("remove_relation error: %s", e)
            raise

    # ──────────────────────────────────────────────────────────
    #  المحادثات
    # ──────────────────────────────────────────────────────────

    def save_chat_result(
        self,
        chat_id    : str,
        question   : str,
        sql_query  : Optional[str]  = None,
        result_type: Optional[str]  = None,
        result_data: Optional[dict] = None,
        error      : Optional[str]  = None,
    ) -> None:
        """حفظ نتيجة سؤال في سجل المحادثة."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO chat_history
                        (id, question, sql_query, result_type,
                         result_json, error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id, question, sql_query, result_type,
                        json.dumps(result_data) if result_data else None,
                        error, _now()
                    )
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.error("save_chat_result error: %s", e)
            raise

    def get_chat_history(self, limit: int = 50) -> list[dict]:
        """إرجاع آخر N سجل من المحادثات."""
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM chat_history ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["result_data"] = json.loads(d["result_json"]) if d["result_json"] else None
                del d["result_json"]
                result.append(d)
            return result
        except sqlite3.Error as e:
            logger.error("get_chat_history error: %s", e)
            return []

    # ──────────────────────────────────────────────────────────
    #  التقارير
    # ──────────────────────────────────────────────────────────

    def create_report(self, report_id: str, title: str) -> None:
        """إنشاء تقرير جديد."""
        try:
            now = _now()
            with _connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO reports (id, title, created_at, updated_at) VALUES (?,?,?,?)",
                    (report_id, title, now, now)
                )
                conn.commit()
            logger.info("Report created: '%s'", title)
        except sqlite3.Error as e:
            logger.error("create_report error: %s", e)
            raise

    def get_reports(self) -> list[dict]:
        """إرجاع كل التقارير."""
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM reports ORDER BY updated_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.error("get_reports error: %s", e)
            return []

    def save_report_block(
        self,
        report_id : str,
        position  : int,
        block_type: str,
        content   : dict,
    ) -> None:
        """إضافة بلوك لتقرير (paragraph / chart / table / gauge / kpi)."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO report_blocks
                        (report_id, position, block_type, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (report_id, position, block_type, json.dumps(content))
                )
                # تحديث updated_at للتقرير
                conn.execute(
                    "UPDATE reports SET updated_at = ? WHERE id = ?",
                    (_now(), report_id)
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.error("save_report_block error: %s", e)
            raise

    def get_report_blocks(self, report_id: str) -> list[dict]:
        """إرجاع كل بلوكات تقرير مرتبة حسب الموضع."""
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM report_blocks
                    WHERE report_id = ?
                    ORDER BY position
                    """,
                    (report_id,)
                ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["content"] = json.loads(d["content"])
                result.append(d)
            return result
        except sqlite3.Error as e:
            logger.error("get_report_blocks error: %s", e)
            return []

    def delete_report(self, report_id: str) -> None:
        """حذف تقرير وكل بلوكاته."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
                conn.commit()
            logger.info("Report deleted: %s", report_id)
        except sqlite3.Error as e:
            logger.error("delete_report error: %s", e)
            raise

    # ──────────────────────────────────────────────────────────
    #  لوحات المعلومات (Dashboards)
    # ──────────────────────────────────────────────────────────

    def create_dashboard(self, dashboard_id: str, title: str, template_id: str) -> None:
        """إنشاء لوحة معلومات جديدة بقالب معيّن."""
        try:
            now = _now()
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO dashboards (id, title, template_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (dashboard_id, title, template_id, now, now)
                )
                conn.commit()
            logger.info("Dashboard created: '%s' (template=%s)", title, template_id)
        except sqlite3.Error as e:
            logger.error("create_dashboard error: %s", e)
            raise

    def get_dashboards(self) -> list[dict]:
        """إرجاع كل لوحات المعلومات في المشروع."""
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM dashboards ORDER BY updated_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.error("get_dashboards error: %s", e)
            return []

    def get_dashboard(self, dashboard_id: str) -> Optional[dict]:
        """إرجاع لوحة معلومات واحدة."""
        try:
            with _connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM dashboards WHERE id = ?", (dashboard_id,)
                ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error("get_dashboard error: %s", e)
            return None

    def rename_dashboard(self, dashboard_id: str, new_title: str) -> None:
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE dashboards SET title = ?, updated_at = ? WHERE id = ?",
                    (new_title, _now(), dashboard_id)
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.error("rename_dashboard error: %s", e)
            raise

    def touch_dashboard(self, dashboard_id: str) -> None:
        """تحديث updated_at (يُستدعى بعد كل ضغطة تحديث بيانات ناجحة)."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE dashboards SET updated_at = ? WHERE id = ?",
                    (_now(), dashboard_id)
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.error("touch_dashboard error: %s", e)

    def delete_dashboard(self, dashboard_id: str) -> None:
        """حذف لوحة معلومات وكل خلاياها وفلاترها."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute("DELETE FROM dashboards WHERE id = ?", (dashboard_id,))
                conn.commit()
            logger.info("Dashboard deleted: %s", dashboard_id)
        except sqlite3.Error as e:
            logger.error("delete_dashboard error: %s", e)
            raise

    def duplicate_dashboard(self, dashboard_id: str, new_id: str, new_title: str) -> None:
        """تكرار لوحة معلومات كاملة (خلايا + فلاتر) بمعرّف جديد."""
        import uuid as _uuid
        try:
            src = self.get_dashboard(dashboard_id)
            if not src:
                raise ValueError("اللوحة الأصلية غير موجودة")
            self.create_dashboard(new_id, new_title, src["template_id"])
            for cell in self.get_dashboard_cells(dashboard_id):
                self.save_dashboard_cell(
                    new_id, cell["position"], cell.get("display_type"),
                    cell.get("title"), cell.get("question"), cell.get("chart_type"),
                )
                # ننسخ أيضاً base_sql لو كان موجوداً — بما أن السؤال لم
                # يتغيّر، لا داعي لإعادة توليده عبر AI في اللوحة الجديدة
                if cell.get("base_sql"):
                    self.save_dashboard_cell_base_sql(new_id, cell["position"], cell["base_sql"])
            for slicer in self.get_dashboard_slicers(dashboard_id):
                self.save_dashboard_slicer(
                    new_id, slicer["position"],
                    slicer.get("table_name"), slicer.get("column_name"),
                    slicer.get("selected_values") or [],
                )
        except sqlite3.Error as e:
            logger.error("duplicate_dashboard error: %s", e)
            raise

    # ── خلايا اللوحة ──

    def save_dashboard_cell(
        self,
        dashboard_id: str,
        position    : int,
        display_type: Optional[str],
        title       : Optional[str],
        question    : Optional[str],
        chart_type  : Optional[str] = None,
    ) -> None:
        """
        إنشاء/تعديل إعداد خلية (سؤالها ونوع عرضها) — لا يُنفّذها.

        ملاحظة: أي تعديل على السؤال هنا (سواء خلية جديدة أو سؤال مُغيَّر)
        يُفرغ base_sql تلقائياً — لأن الـ SQL الأساسي المخزَّن أصبح غير
        مطابق للسؤال الجديد ويجب إعادة توليده عبر AI في أول تحديث قادم.
        نفحص هذا بمقارنة السؤال الجديد بالسؤال المخزَّن حالياً (لو وُجد).
        """
        try:
            with _connect(self.db_path) as conn:
                existing = conn.execute(
                    "SELECT question, base_sql FROM dashboard_cells WHERE dashboard_id = ? AND position = ?",
                    (dashboard_id, position)
                ).fetchone()

                question_changed = (
                    existing is None or existing["question"] != question
                )
                new_base_sql = None if question_changed else existing["base_sql"]

                conn.execute(
                    """
                    INSERT INTO dashboard_cells
                        (id, dashboard_id, position, display_type, title, question, chart_type, base_sql)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dashboard_id, position) DO UPDATE SET
                        display_type = excluded.display_type,
                        title        = excluded.title,
                        question     = excluded.question,
                        chart_type   = excluded.chart_type,
                        base_sql     = excluded.base_sql
                    """,
                    (f"{dashboard_id}_{position}", dashboard_id, position,
                     display_type, title, question, chart_type, new_base_sql)
                )
                conn.commit()
            if question_changed:
                logger.info(
                    "Dashboard cell %s[%d]: question changed — base_sql invalidated",
                    dashboard_id, position
                )
        except sqlite3.Error as e:
            logger.error("save_dashboard_cell error: %s", e)
            raise

    def save_dashboard_cell_base_sql(self, dashboard_id: str, position: int, base_sql: str) -> None:
        """
        حفظ الـ SQL الأساسي (الناتج من أول استدعاء AI ناجح لهذا السؤال).
        التحديثات اللاحقة (بتطبيق فلاتر مختلفة) تُعاد بناؤها فوق هذا الـ
        SQL مباشرة في طبقة بايثون/DuckDB بدون أي استدعاء AI إضافي.
        """
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE dashboard_cells SET base_sql = ? WHERE dashboard_id = ? AND position = ?",
                    (base_sql, dashboard_id, position)
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.error("save_dashboard_cell_base_sql error: %s", e)
            raise

    def save_dashboard_cell_result(
        self,
        dashboard_id: str,
        position    : int,
        result      : Optional[dict],
        sql         : Optional[str],
        error       : Optional[str],
    ) -> None:
        """حفظ نتيجة تنفيذ خلية (تُستدعى فقط عند ضغط زر تحديث البيانات)."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE dashboard_cells
                    SET last_result = ?, last_sql = ?, last_error = ?, last_updated_at = ?
                    WHERE dashboard_id = ? AND position = ?
                    """,
                    (json.dumps(result) if result is not None else None,
                     sql, error, _now(), dashboard_id, position)
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.error("save_dashboard_cell_result error: %s", e)
            raise

    def get_dashboard_cells(self, dashboard_id: str) -> list[dict]:
        """إرجاع كل خلايا لوحة، مرتبة حسب الموضع."""
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM dashboard_cells WHERE dashboard_id = ? ORDER BY position",
                    (dashboard_id,)
                ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["last_result"] = json.loads(d["last_result"]) if d.get("last_result") else None
                result.append(d)
            return result
        except sqlite3.Error as e:
            logger.error("get_dashboard_cells error: %s", e)
            return []

    def clear_dashboard_cell(self, dashboard_id: str, position: int) -> None:
        """إفراغ إعداد خلية (تعيدها لحالة «إضافة عنصر» فارغة)."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM dashboard_cells WHERE dashboard_id = ? AND position = ?",
                    (dashboard_id, position)
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.error("clear_dashboard_cell error: %s", e)
            raise

    # ── فلاتر اللوحة (Slicers) ──

    def save_dashboard_slicer(
        self,
        dashboard_id : str,
        position     : int,
        table_name   : Optional[str],
        column_name  : Optional[str],
        selected_values: list,
    ) -> None:
        """حفظ إعداد Slicer (قد تكون قيمه staged ولم تُطبَّق بعد)."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO dashboard_slicers
                        (id, dashboard_id, position, table_name, column_name, selected_values)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dashboard_id, position) DO UPDATE SET
                        table_name      = excluded.table_name,
                        column_name     = excluded.column_name,
                        selected_values = excluded.selected_values
                    """,
                    (f"{dashboard_id}_slicer_{position}", dashboard_id, position,
                     table_name, column_name, json.dumps(selected_values or []))
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.error("save_dashboard_slicer error: %s", e)
            raise

    def get_dashboard_slicers(self, dashboard_id: str) -> list[dict]:
        """إرجاع كل Slicers لوحة، مرتبة حسب الموضع."""
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM dashboard_slicers WHERE dashboard_id = ? ORDER BY position",
                    (dashboard_id,)
                ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["selected_values"] = json.loads(d["selected_values"]) if d.get("selected_values") else []
                result.append(d)
            return result
        except sqlite3.Error as e:
            logger.error("get_dashboard_slicers error: %s", e)
            return []

    def reset_dashboard_slicers(self, dashboard_id: str) -> None:
        """إعادة كل Slicers لوحة إلى الوضع الافتراضي (بدون جدول/عمود/قيم)."""
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM dashboard_slicers WHERE dashboard_id = ?",
                    (dashboard_id,)
                )
                conn.commit()
            logger.info("Slicers reset for dashboard: %s", dashboard_id)
        except sqlite3.Error as e:
            logger.error("reset_dashboard_slicers error: %s", e)
            raise

    # ──────────────────────────────────────────────────────────
    #  النسخ الاحتياطي
    # ──────────────────────────────────────────────────────────

    def backup(self) -> Path:
        """إنشاء نسخة احتياطية من project.db."""
        timestamp   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_path = self.db_dir / f"backup_{timestamp}.db"
        try:
            shutil.copy2(self.db_path, backup_path)
            logger.info("Backup created: %s", backup_path)
            return backup_path
        except Exception as e:
            logger.error("backup error: %s", e)
            raise

    # ──────────────────────────────────────────────────────────
    #  معلومات عامة
    # ──────────────────────────────────────────────────────────

    def get_info(self) -> dict:
        """معلومات سريعة عن المشروع."""
        try:
            size_mb = self.db_path.stat().st_size / (1024 * 1024)
        except FileNotFoundError:
            size_mb = 0.0
        return {
            "user_id"   : self.user_id,
            "project_id": self.project_id,
            "db_path"   : str(self.db_path),
            "size_mb"   : round(size_mb, 3),
            "files"     : len(self.get_files()),
            "relations" : len(self.get_relations()),
            "reports"   : len(self.get_reports()),
        }
