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

🆕 Views مفلترة حقيقية لكل جدول (لوحات المعلومات فقط):
-----------------------------------------------------------
كل استدعاء لـ run()/run_with_filters() يفتح اتصال DuckDB جديد تماماً
(في الذاكرة، يُغلق فور الانتهاء) — هذا الاتصال منفصل تماماً عن أي
استدعاء آخر (محادثة، تقارير، خلية لوحة أخرى)، ولا علاقة له بـ
project.db على الإطلاق. البيانات الخام تُقرأ في كل مرة طازجة عبر
db.get_clean_data() كما كانت دائماً (بدون أي تعديل عليها هنا أو حفظ
أي نسخة معدَّلة).

لما تُمرَّر filters (من Slicers لوحة معلومات):
  1. يُسجَّل الجدول الخام كما هو تحت اسم داخلي "{alias}__raw".
  2. يُبنى شرط WHERE من الفلاتر النشطة على هذا الجدول تحديداً.
  3. يُنشأ view حقيقي عبر "CREATE OR REPLACE VIEW {alias} AS SELECT *
     FROM {alias}__raw WHERE ..." — هذا الـ view (وليس الجدول الخام)
     هو ما يُستعلَم عنه فعلياً باسم الجدول العادي (alias) في أي SQL
     لاحق، سواء كُتب يدوياً، وُلِّد عبر AI للتو، أو كان base_sql
     محفوظاً مسبقاً — بدون أي حاجة لتعديل نص الـ SQL نفسه أو schema
     المُرسَل إلى AI (نفس اسم الجدول تماماً).
  4. الـ view يُعاد بناؤه من الصفر مع كل استدعاء (كل تحميل لصفحة
     Dashboard، أو كل تغيير في الفلاتر يُشغّل تحديثاً جديداً) لأن
     الاتصال بالكامل مؤقت وجديد في كل مرة — لا حاجة لأي منطق "تحديث"
     إضافي، فالبناء من الصفر مضمون دائماً.

لما لا تُمرَّر filters (استخدام عادي من المحادثة/التقارير/أي مكان
آخر): يُسجَّل الجدول الخام مباشرة تحت اسمه العادي بدون أي view إضافي
— تماماً كالسلوك القديم، بلا أي فرق في الأداء أو النتيجة.
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

# لاحقة الجدول الخام الداخلي المستخدَم فقط أثناء بناء الـ view المفلترة
# (لا علاقة له بأي شيء مخزَّن على القرص — موجود فقط داخل اتصال DuckDB
# المؤقت طوال مدة هذا الاستدعاء).
_RAW_SUFFIX = "__raw"


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

    الاستخدام العادي (بدون فلاتر — محادثة/تقارير/أي مكان آخر):
        qe = QueryEngine(db)
        result = qe.run("SELECT SUM(amount) FROM sales")

    الاستخدام مع فلاتر لوحة معلومات (يبني view مفلترة حقيقية لكل جدول
    له فلتر نشط، دون أي تعديل على project.db):
        result = qe.run("SELECT ...", filters=[
            {"table": "sales", "column": "المنطقة", "values": ["الرياض"]},
        ])
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
    #  بناء شرط WHERE من فلاتر جدول واحد
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_where_clause(filters: list, available_columns) -> tuple[str, list]:
        """
        بناء شرط WHERE من فلاتر جدول واحد (AND فيما بينها). يرجع
        (نص_الشرط, قائمة_أعمدة_الفلاتر_المتجاهَلة).

        نوعان من الفلاتر مدعومان لكل عنصر في filters:
        - فلتر قائمة (Slicer عادي): {"column": ..., "values": [...]}
          → IN (...) مع CAST(... AS VARCHAR) حتى يعمل بغض النظر عن نوع
          العمود الفعلي (رقمي/نصي/تاريخ)، بدل الاعتماد على تطابق أنواع
          ضمني قد يفشل صامتاً في DuckDB بين VARCHAR وINTEGER مثلاً.
        - 🆕 فلتر نطاق تاريخ: {"column": ..., "date_range": {"start": "...", "end": "..."}}
          → BETWEEN على العمود مُحوَّلاً صراحة إلى DATE، بغض النظر عن
          كونه مخزَّناً كـ TEXT في SQLite (راجع project_db.get_clean_data
          — يُعاد تحويله إلى datetime64 فعلياً عند التحميل، لكن الـ CAST
          هنا حماية إضافية لا تضر).
        """
        clauses = []
        skipped = []
        for f in filters:
            column = f.get("column")
            if not column:
                continue
            if column not in available_columns:
                skipped.append(column)
                logger.info("Filter skipped: column '%s' not present in table", column)
                continue

            date_range = f.get("date_range")
            if date_range:
                start = date_range.get("start")
                end = date_range.get("end")
                if not start or not end:
                    continue
                start_escaped = str(start).replace("'", "''")
                end_escaped = str(end).replace("'", "''")
                clauses.append(
                    f'CAST("{column}" AS DATE) BETWEEN '
                    f"DATE '{start_escaped}' AND DATE '{end_escaped}'"
                )
                continue

            values = f.get("values") or []
            if not values:
                continue
            escaped_values = ", ".join(
                "'" + str(v).replace("'", "''") + "'" for v in values
            )
            clauses.append(f'CAST("{column}" AS VARCHAR) IN ({escaped_values})')

        return " AND ".join(clauses), skipped
    # ──────────────────────────────────────────────────────────
    #  تحميل الجداول في DuckDB (مع بناء views مفلترة عند وجود فلاتر)
    # ──────────────────────────────────────────────────────────

    def _load_tables(self, conn: duckdb.DuckDBPyConnection, filters: Optional[list] = None) -> list[str]:
        """
        تحميل كل الجداول النظيفة من project.db إلى DuckDB.

        - جدول بلا فلتر نشط: يُسجَّل مباشرة تحت اسمه (alias) كما كان
          دائماً — لا فرق عن السلوك السابق.
        - جدول له فلتر نشط واحد أو أكثر: يُسجَّل الجدول الخام تحت اسم
          داخلي "{alias}__raw"، ثم يُبنى فوقه "CREATE OR REPLACE VIEW
          {alias} AS SELECT * FROM {alias}__raw WHERE ..." — بحيث أي
          استعلام لاحق يستخدم اسم الجدول العادي (alias) يصل فعلياً
          إلى الـ view المفلترة تلقائياً، بدون أي تعديل على نص الـ
          SQL أو على البيانات الأصلية في project.db.

        كل هذا يحدث داخل هذا الاتصال المؤقت فقط (يُغلق فور انتهاء
        الاستعلام) — لا يمس project.db بأي شكل، وبيانات المحادثة/
        التقارير/أي استخدام آخر لا يمر عبر filters فتبقى كما هي تماماً.

        يرجع قائمة بأسماء الجداول/الـ views المحملة.
        """
        loaded = []
        files  = self.db.get_files()

        filters_by_table: dict[str, list] = {}
        for f in (filters or []):
            table = f.get("table")
            if table:
                filters_by_table.setdefault(table, []).append(f)

        for f in files:
            alias = f["table_alias"]
            df    = self.db.get_clean_data(alias)
            if df is None or df.empty:
                logger.warning("Table '%s' is empty or missing — skipped", alias)
                continue

            table_filters = filters_by_table.get(alias)
            if not table_filters:
                # لا فلتر نشط على هذا الجدول — تسجيل مباشر كما كان دائماً
                conn.register(alias, df)
                loaded.append(alias)
                logger.debug("Table loaded into DuckDB: '%s' (%d rows)", alias, len(df))
                continue

            # ── بناء view مفلترة حقيقية فوق الجدول الخام ──
            raw_name = f"{alias}{_RAW_SUFFIX}"
            conn.register(raw_name, df)

            where_clause, skipped_cols = self._build_where_clause(table_filters, set(df.columns))
            if where_clause:
                conn.execute(
                    f'CREATE OR REPLACE VIEW "{alias}" AS '
                    f'SELECT * FROM "{raw_name}" WHERE {where_clause}'
                )
                logger.debug(
                    "Filtered view built for '%s' (%d filter(s) applied%s)",
                    alias, len(table_filters),
                    f", {len(skipped_cols)} skipped" if skipped_cols else "",
                )
            else:
                # كل فلاتر هذا الجدول غير قابلة للتطبيق (أعمدة غير
                # موجودة) — نُبقي الـ view مطابقة تماماً للجدول الخام
                conn.execute(f'CREATE OR REPLACE VIEW "{alias}" AS SELECT * FROM "{raw_name}"')

            loaded.append(alias)

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

    def run(self, sql: str, filters: Optional[list] = None) -> dict:
        """
        تنفيذ SQL وإرجاع النتيجة كـ DataFrame.

        filters (اختياري): [{"table": "sales", "column": "المنطقة",
        "values": ["الرياض", "جدة"]}, ...] — يبني view مفلترة حقيقية
        لكل جدول له فلتر نشط (راجع _load_tables)، بحيث sql يُنفَّذ
        فعلياً على البيانات المفلترة بغض النظر عن أعمدة الإخراج
        النهائية أو عمّا إذا كان sql يحتوي WHERE يدوي لنفس الشرط.
        البيانات الأصلية في project.db لا تُلمس مطلقاً.

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
            result = self._execute_once(current_sql, filters=filters)

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
    #  تطبيق فلاتر (Slicers) على SQL جاهز (base_sql) — بدون AI
    # ──────────────────────────────────────────────────────────

    def run_with_filters(self, base_sql: str, filters: Optional[list] = None) -> dict:
        """
        تنفيذ SQL أساسي (تم توليده سابقاً عبر AI مرة واحدة، ومحفوظ
        كـ base_sql لخلية لوحة معلومات) مع تطبيق فلاتر (Slicers).

        🆕 بما أن الفلاتر أصبحت views حقيقية تُبنى فوق الجداول الخام
        لحظة تحميلها (راجع _load_tables)، لم تعد هناك حاجة لتنفيذ
        الاستعلام مرتين (تجربة أولى بدون فلاتر لمعرفة الأعمدة، ثم لفّ
        النتيجة كـ subquery وإضافة WHERE على الإخراج النهائي) — فقط
        ننفّذ base_sql عبر run() مع تمرير الفلاتر، وهي نفسها الدالة
        التي تضمن أن أي إشارة لاسم الجدول داخل base_sql تصل فعلياً
        إلى الـ view المفلترة (تعمل حتى مع GROUP BY/JOIN معقدة، لأن
        الفلترة تحدث قبل التجميع لا بعده).

        يرجع نفس بنية run(): {"ok": True, "df": ..., "rows": N}
                              أو {"ok": False, "error": "..."}
        """
        base_sql = base_sql.strip().rstrip(";")
        if not base_sql:
            return {"ok": False, "error": "لا يوجد SQL أساسي محفوظ لهذه الخلية"}

        return self.run(base_sql, filters=filters)

    def _execute_once(self, sql: str, filters: Optional[list] = None) -> dict:
        """تنفيذ استعلام واحد فعلياً (بدون منطق التصحيح التلقائي)."""
        conn = None
        try:
            conn   = duckdb.connect()
            loaded = self._load_tables(conn, filters=filters)

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

