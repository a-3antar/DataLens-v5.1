"""
core/query_engine.py
====================
تنفيذ SQL على البيانات النظيفة عبر DuckDB.
لا يكتب في قاعدة البيانات — يقرأ فقط.

تصحيح تلقائي لأسماء الأعمدة:
------------------------------
نماذج AI المحلية (مثل Qwen) تُخطئ أحياناً في اسم عمود مذكور بوضوح في
الـ schema (مثلاً تكتب "سليم" بدل "السليم"). بما أن DuckDB نفسه يُرجع
أقرب الأسماء الصحيحة ضمن رسالة الخطأ ("Candidate bindings")، نستغل هذه
المعلومة لتصحيح الاستعلام تلقائياً وإعادة تنفيذه — بدل استهلاك محاولة
كاملة من محاولات الذكاء الاصطناعي على خطأ يمكن حله فوراً بدون استدعاء AI.
"""

import re
import logging
from difflib import SequenceMatcher
from typing import Optional

import duckdb
import pandas as pd

from config          import SQL_FORBIDDEN
from core.project_db import ProjectDB

logger = logging.getLogger(__name__)

# مثال على رسالة DuckDB التي نحللها:
#   Binder Error: Referenced column "سليم" not found in FROM clause!
#   Candidate bindings: "السليم", "وزن_المنتج"
_MISSING_COL_PATTERN = re.compile(
    r'Referenced column "([^"]+)" not found[^\n]*\n?Candidate bindings:\s*(.+)',
    re.IGNORECASE,
)
_MAX_AUTO_FIX_ATTEMPTS = 3
# حد أدنى للتشابه بين الاسم الخاطئ وأفضل مرشح حتى نثق بالتصحيح التلقائي.
# هذا ضروري لتفادي استبدال عمود بعمود آخر غير ذي صلة إطلاقاً (نتيجة خاطئة
# بصمت أخطر بكثير من فشل واضح يُعاد للـ AI ليصححه).
_MIN_SIMILARITY = 0.5


def _is_confident_match(wrong: str, candidate: str) -> bool:
    """هل نثق بأن candidate هو المقصود الفعلي بدل wrong؟"""
    if not wrong or not candidate:
        return False
    ratio = SequenceMatcher(None, wrong, candidate).ratio()
    if ratio >= _MIN_SIMILARITY:
        return True
    # حالات شائعة: الاسم الخاطئ جزء من الاسم الصحيح أو العكس
    # (مثل "وزن" ضمن "وزن_المنتج")، حتى لو كانت نسبة التشابه الكلية منخفضة
    if len(wrong) >= 3 and (wrong in candidate or candidate in wrong):
        return True
    return False


class QueryEngine:
    """
    تنفيذ SQL على جداول المشروع عبر DuckDB.

    الاستخدام:
        qe = QueryEngine(db)
        result = qe.run("SELECT SUM(amount) FROM sales")
        if result["ok"]:
            df = result["df"]
    """

    def __init__(self, db: ProjectDB):
        self.db = db

    # ──────────────────────────────────────────────────────────
    #  فحص الأمان
    # ──────────────────────────────────────────────────────────

    def validate(self, sql: str) -> dict:
        """
        فحص SQL قبل التنفيذ.
        يرفض أي كلمة من SQL_FORBIDDEN.
        يرجع: {"ok": True} أو {"ok": False, "error": "..."}
        """
        sql_upper = sql.upper()
        for word in SQL_FORBIDDEN:
            # نستخدم word boundary لتجنب false positives
            if re.search(rf"\b{word}\b", sql_upper):
                logger.warning("SQL forbidden word detected: %s", word)
                return {"ok": False, "error": f"كلمة محظورة في الاستعلام: {word}"}
        return {"ok": True}

    # ──────────────────────────────────────────────────────────
    #  تحميل الجداول في DuckDB
    # ──────────────────────────────────────────────────────────

    def _load_tables(self, conn: duckdb.DuckDBPyConnection) -> list[str]:
        """
        تحميل كل الجداول النظيفة من project.db إلى DuckDB كـ views.
        يرجع قائمة بأسماء الجداول المحملة.
        """
        loaded = []
        files  = self.db.get_files()

        for f in files:
            alias = f["table_alias"]
            df    = self.db.get_clean_data(alias)
            if df is not None and not df.empty:
                conn.register(alias, df)
                loaded.append(alias)
                logger.debug("Table loaded into DuckDB: '%s' (%d rows)", alias, len(df))
            else:
                logger.warning("Table '%s' is empty or missing — skipped", alias)

        return loaded

    # ──────────────────────────────────────────────────────────
    #  تصحيح تلقائي لاسم عمود خاطئ بناءً على رسالة DuckDB
    # ──────────────────────────────────────────────────────────

    def _try_auto_fix_column(self, sql: str, error_msg: str) -> Optional[tuple[str, str, str]]:
        """
        يحاول استخراج اسم العمود الخاطئ وأفضل بديل من رسالة الخطأ،
        ويرجع (sql_مُصحح, الاسم_الخاطئ, الاسم_الصحيح) أو None لو تعذر.
        """
        match = _MISSING_COL_PATTERN.search(error_msg)
        if not match:
            return None

        wrong_name = match.group(1).strip()
        candidates_raw = match.group(2)
        candidate_names = re.findall(r'"([^"]+)"', candidates_raw)
        if not candidate_names:
            return None

        # نختار أول مرشح نثق فيه فعلياً (وليس أول مرشح ذكرته DuckDB
        # بلا قيد) لتفادي استبدال خاطئ يُنتج نتيجة صحيحة الشكل لكنها
        # خاطئة المعنى بصمت.
        best = next((c for c in candidate_names if _is_confident_match(wrong_name, c)), None)
        if best is None or best == wrong_name:
            return None

        # الاسم الخاطئ قد يرد في الـ SQL بين علامتي اقتباس ("تاريخ")
        # أو بدونهما إطلاقاً (تاريخ) — النماذج المحلية كثيراً ما تكتب
        # أسماء الأعمدة العربية بدون تقويسها. نتعامل مع الحالتين معاً،
        # ونستبدل كل الحالات الفعلية الموجودة في هذا الاستعلام تحديداً.
        quoted_pattern   = re.compile(r'"' + re.escape(wrong_name) + r'"')
        unquoted_pattern = re.compile(r'(?<!["\w])' + re.escape(wrong_name) + r'(?!["\w])')

        fixed_sql = sql
        replaced = False
        if quoted_pattern.search(fixed_sql):
            fixed_sql = quoted_pattern.sub(f'"{best}"', fixed_sql)
            replaced = True
        if unquoted_pattern.search(fixed_sql):
            fixed_sql = unquoted_pattern.sub(f'"{best}"', fixed_sql)
            replaced = True

        if not replaced:
            return None

        return fixed_sql, wrong_name, best

    # ──────────────────────────────────────────────────────────
    #  تنفيذ الاستعلام
    # ──────────────────────────────────────────────────────────

    def run(self, sql: str) -> dict:
        """
        تنفيذ SQL وإرجاع النتيجة كـ DataFrame.

        يرجع:
            {"ok": True,  "df": DataFrame, "rows": N, "auto_fixes": [...]}
            {"ok": False, "error": "..."}

        "auto_fixes" (اختياري): قائمة تصحيحات تلقائية طُبّقت على أسماء
        أعمدة قبل نجاح التنفيذ، مثل [{"from": "سليم", "to": "السليم"}].
        """
        sql = sql.strip()
        if not sql:
            return {"ok": False, "error": "الاستعلام فارغ"}

        # فحص الأمان أولاً
        check = self.validate(sql)
        if not check["ok"]:
            return check

        auto_fixes = []
        current_sql = sql

        for attempt in range(_MAX_AUTO_FIX_ATTEMPTS + 1):
            result = self._execute_once(current_sql)

            if result["ok"]:
                if auto_fixes:
                    result["auto_fixes"] = auto_fixes
                    result["sql"] = current_sql
                    logger.info("Query succeeded after auto-fixing columns: %s", auto_fixes)
                return result

            # نحاول تصحيح اسم عمود تلقائياً فقط لو كان الخطأ من هذا النوع
            fix = self._try_auto_fix_column(current_sql, result["error"])
            if not fix or attempt >= _MAX_AUTO_FIX_ATTEMPTS:
                if auto_fixes:
                    result["auto_fixes"] = auto_fixes
                    result["sql"] = current_sql
                return result

            fixed_sql, wrong_name, correct_name = fix
            logger.info("Auto-fixing column name: '%s' -> '%s'", wrong_name, correct_name)
            auto_fixes.append({"from": wrong_name, "to": correct_name})
            current_sql = fixed_sql

        return result

    def _execute_once(self, sql: str) -> dict:
        """تنفيذ استعلام واحد فعلياً (بدون منطق التصحيح التلقائي)."""
        conn = None
        try:
            conn   = duckdb.connect()
            loaded = self._load_tables(conn)

            if not loaded:
                return {"ok": False, "error": "لا توجد جداول محملة في المشروع"}

            df   = conn.execute(sql).df()
            rows = len(df)
            logger.info("SQL executed successfully: %d rows returned", rows)
            return {"ok": True, "df": df, "rows": rows}

        except duckdb.CatalogException as e:
            msg = str(e)
            logger.error("DuckDB CatalogException: %s", msg)
            return {"ok": False, "error": f"جدول أو عمود غير موجود: {msg}"}

        except duckdb.BinderException as e:
            # هذا هو النوع الفعلي الذي يرفعه DuckDB لعمود غير موجود ضمن
            # تعبير (مثل CASE WHEN) — يحتوي عادة على "Candidate bindings".
            msg = str(e)
            logger.error("DuckDB BinderException: %s", msg)
            return {"ok": False, "error": f"عمود غير موجود: {msg}"}

        except duckdb.ParserException as e:
            msg = str(e)
            logger.error("DuckDB ParserException: %s", msg)
            return {"ok": False, "error": f"خطأ في صياغة SQL: {msg}"}

        except duckdb.Error as e:
            msg = str(e)
            logger.error("DuckDB error: %s", msg)
            return {"ok": False, "error": msg}

        except Exception as e:
            logger.error("QueryEngine unexpected error: %s", e)
            return {"ok": False, "error": str(e)}

        finally:
            if conn:
                conn.close()

    # ──────────────────────────────────────────────────────────
    #  معلومات مساعدة
    # ──────────────────────────────────────────────────────────

    def get_table_names(self) -> list[str]:
        """أسماء الجداول المتاحة في المشروع."""
        return [f["table_alias"] for f in self.db.get_files()]

    def preview_table(self, table_alias: str, limit: int = 5) -> dict:
        """معاينة سريعة لجدول بدون كتابة SQL يدوي."""
        return self.run(f'SELECT * FROM "{table_alias}" LIMIT {limit}')
