"""
core/query_engine.py
====================
تنفيذ SQL على البيانات النظيفة عبر DuckDB.
لا يكتب في قاعدة البيانات — يقرأ فقط، ولا يُعدّل أي بيانات دائمة على
الإطلاق (project.db لا يُلمس هنا إطلاقاً في أي مسار).

تصحيح تلقائي لأسماء الأعمدة:
------------------------------
نماذج AI المحلية (مثل Qwen) تُخطئ أحياناً في اسم عمود مذكور بوضوح في
الـ schema (مثلاً تكتب "سليم" بدل "السليم"). بما أن DuckDB نفسه يُرجع
أقرب الأسماء الصحيحة ضمن رسالة الخطأ ("Candidate bindings")، نستغل هذه
المعلومة لتصحيح الاستعلام تلقائياً وإعادة تنفيذه — بدل استهلاك محاولة
كاملة من محاولات الذكاء الاصطناعي على خطأ يمكن حله فوراً بدون استدعاء AI.

🆕 فلاتر لوحات المعلومات = طبقة بيانات منفصلة (views):
---------------------------------------------------------
لم يعد QueryEngine يبني أي WHERE أو view مؤقتة في DuckDB. فلاتر كل لوحة
تُحفظ كـ views دائمة داخل project.db (راجع core/dashboard_views.py)،
وهذا الملف يكتفي بقراءة "البيانات كما يجب أن تُرى":

  - run(sql) بدون dashboard_id (محادثة/تقارير/أي مكان آخر): تُحمَّل كل
    الجداول الأصلية كما كانت دائماً.
  - run(sql, dashboard_id=...): لكل جدول له view في هذه اللوحة تُحمَّل
    البيانات المفلترة منه، وإلا الجدول الأصلي — وتُسجَّل في DuckDB
    بنفس اسم الجدول الأصلي تماماً. فيبقى الـ SQL (وbase_sql المحفوظ)
    خالياً من أي فلتر ولا يعرف شيئاً عن قيم Slicers.

كل استدعاء يفتح اتصال DuckDB جديداً في الذاكرة (يُغلق فور الانتهاء)،
منفصلاً تماماً عن أي استدعاء آخر.
"""

import re
import logging
from difflib import SequenceMatcher
from typing import Optional

import duckdb

from config                    import SQL_FORBIDDEN
from core.project_db           import ProjectDB
from core.dashboard_views      import DashboardViews

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

    الاستخدام العادي (محادثة/تقارير/أي مكان آخر):
        qe = QueryEngine(db)
        result = qe.run("SELECT SUM(amount) FROM sales")

    الاستخدام ضمن لوحة معلومات (البيانات تُقرأ من views فلاتر اللوحة):
        result = qe.run("SELECT ...", dashboard_id="...")
    """

    def __init__(self, db: ProjectDB):
        self.db = db
        self.views = DashboardViews(db)

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

    def _load_tables(self, conn: duckdb.DuckDBPyConnection, dashboard_id: Optional[str] = None) -> list[str]:
        """
        تحميل كل الجداول النظيفة من project.db إلى DuckDB بأسمائها
        الأصلية (alias). مع dashboard_id: الجدول الذي له view في هذه
        اللوحة يُحمَّل مفلتراً من الـ view (راجع
        DashboardViews.read_table)، وغيره من الجدول الأصلي.

        جدول الأصل الفارغ/المفقود يُتخطّى كما كان دائماً. أما الجدول
        الذي أفرغه فلتر اللوحة فيُسجَّل فارغاً (بأنواع أعمدته الصحيحة)
        حتى يعود الاستعلام بنتيجة فارغة سليمة بدل خطأ "جدول غير موجود".

        يرجع قائمة بأسماء الجداول المحملة.
        """
        loaded = []
        for f in self.db.get_files():
            alias = f["table_alias"]
            df = self.views.read_table(alias, dashboard_id)

            filtered_empty = (
                df is not None and df.empty and bool(dashboard_id)
                and self.views.has_view(dashboard_id, alias)
            )
            if df is None or (df.empty and not filtered_empty):
                logger.warning("Table '%s' is empty or missing — skipped", alias)
                continue

            conn.register(alias, df)
            loaded.append(alias)
            logger.debug(
                "Table loaded into DuckDB: '%s' (%d rows%s)",
                alias, len(df), ", dashboard view" if dashboard_id and self.views.has_view(dashboard_id, alias) else "",
            )

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

    def run(self, sql: str, dashboard_id: Optional[str] = None) -> dict:
        """
        تنفيذ SQL وإرجاع النتيجة كـ DataFrame.

        dashboard_id (اختياري): عند تمريره تُقرأ الجداول عبر views فلاتر
        هذه اللوحة (راجع _load_tables) — الـ SQL نفسه لا يحتوي ولا يحتاج
        أي شرط فلترة للوحة. البيانات الأصلية في project.db لا تُلمس.

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
            result = self._execute_once(current_sql, dashboard_id=dashboard_id)

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

    # ──────────────────────────────────────────────────────────
    #  تنفيذ SQL جاهز (base_sql) على بيانات لوحة — بدون AI
    # ──────────────────────────────────────────────────────────

    def run_with_filters(self, base_sql: str, dashboard_id: Optional[str] = None) -> dict:
        """
        تنفيذ SQL أساسي (تم توليده سابقاً عبر AI مرة واحدة، ومحفوظ
        كـ base_sql لخلية لوحة معلومات) على بيانات اللوحة المفلترة.

        الاسم قديم عمداً (للتوافق مع المستدعين)، لكن لا يوجد "تطبيق
        فلاتر" هنا: الفلترة تحدث في طبقة البيانات (views اللوحة) قبل
        التنفيذ، فيكفي تمرير dashboard_id وتنفيذ base_sql كما هو.

        يرجع نفس بنية run().
        """
        base_sql = base_sql.strip().rstrip(";")
        if not base_sql:
            return {"ok": False, "error": "لا يوجد SQL أساسي محفوظ لهذه الخلية"}

        return self.run(base_sql, dashboard_id=dashboard_id)

    def _execute_once(self, sql: str, dashboard_id: Optional[str] = None) -> dict:
        """تنفيذ استعلام واحد فعلياً (بدون منطق التصحيح التلقائي)."""
        conn = None
        try:
            conn   = duckdb.connect()
            loaded = self._load_tables(conn, dashboard_id=dashboard_id)

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
