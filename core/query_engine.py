"""
core/query_engine.py
====================
تنفيذ SQL على البيانات النظيفة عبر DuckDB.
لا يكتب في قاعدة البيانات — يقرأ فقط، ولا يُعدّل أي بيانات دائمة على
الإطلاق (project.db لا يُلمس هنا إطلاقاً في أي مسار).

تصحيح تلقائي لأسماء الأعمدة (مرحلتان):
------------------------------------------
نماذج AI المحلية (مثل Qwen) تُخطئ أحياناً في اسم عمود مذكور بوضوح في
الـ schema (مثلاً تكتب "سليم" بدل "السليم"، أو "الحالة" بدل "الحاله").

  ١) 🆕 استباقي (_precorrect_columns): قبل التنفيذ الأول نقارن كل معرّف
     مقتبس بعلامتي اقتباس مزدوجتين في الـ SQL بأعمدة الجداول المحمَّلة
     فعلاً (متاحة مجاناً بعد التحميل). الاسم الذي لا وجود له ويطابق
     عموداً واحداً بثقة (تطابق تام بعد توحيد الإملاء العربي ه/ة أ/ا...
     أو تشابه ≥ QUERY_PRECORRECT_MIN_RATIO دون تعادل) يُستبدل قبل أن
     يصل لـ DuckDB — فنوفّر تنفيذاً فاشلاً كاملاً لكل خطأ إملائي.
     تُستثنى أسماء الأعمدة المُعرَّفة داخل الاستعلام نفسه (AS "x"،
     أسماء CTE) وكل ما بداخل النصوص الحرفية '...'. يُعطَّل بالكامل عبر
     config.QUERY_PRECORRECT_COLUMNS.
  ٢) رد فعلي (_try_auto_fix_column): لما يفشل DuckDB فعلاً، يُرجع أقرب
     الأسماء الصحيحة ضمن رسالة الخطأ ("Candidate bindings") فنستغلها
     لتصحيح الاستعلام وإعادة تنفيذه — بدل استهلاك محاولة كاملة من
     محاولات الذكاء الاصطناعي على خطأ يمكن حله فوراً بدون استدعاء AI.

🆕 كاش الجداول المحمَّلة (راجع _TABLE_CACHE أدناه):
------------------------------------------------------
خطة QA/Story تُنفِّذ عدة استعلامات متتالية على نفس البيانات، وكان كل
استعلام يُعيد قراءة كل جداول المشروع من SQLite. الآن يُحفظ DataFrame
كل جدول (لكل لوحة معلومات على حدة) في كاش على مستوى الوحدة، ويُعاد
استخدامه ما دام ملف project.db (وملف WAL المرافق له) لم يتغيّر — البصمة من
core.project_db.file_signature (مشتركة مع كاش الإعدادات) — فأي
حفظ بيانات أو إعدادات أو تعديل فلاتر لوحة (CREATE VIEW) يُبطل الكاش
تلقائياً بتغيّر البصمة. حدّ زمني وحجمي (LRU) يمنعان تضخّم الذاكرة.
كل شيء قابل للإيقاف عبر config.QUERY_TABLE_CACHE_ENABLED.
الكاش داخل QueryEngine فقط (لا يمر عبر DashboardViews.read_table) لأن
الـ DataFrames المخزَّنة تُسجَّل في DuckDB قراءةً فقط ولا يعدّلها أحد؛
المستهلكون الآخرون (مثل knowledge_manager) يقرؤون نسخهم الخاصة.

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
منفصلاً تماماً عن أي استدعاء آخر؛ الكاش يوفّر فقط قراءة SQLite وبناء
DataFrame، لا اتصال DuckDB نفسه.
"""


from core.logger_config import trace_calls

import re
import time
import logging
import threading
from collections import OrderedDict
from difflib import SequenceMatcher
from typing import Optional

import duckdb

from config import (
    SQL_FORBIDDEN,
    QUERY_TABLE_CACHE_ENABLED, QUERY_TABLE_CACHE_MAX_MB, QUERY_TABLE_CACHE_TTL_SECONDS,
    QUERY_PRECORRECT_COLUMNS, QUERY_PRECORRECT_MIN_RATIO,
)
from core.project_db           import ProjectDB, file_signature
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

# ── التصحيح الاستباقي ────────────────────────────────────────
# توحيد الإملاء العربي للمقارنة فقط (ه/ة، أ/إ/آ/ا، ى/ي...) — نفس
# جدول التحويل المستخدم في AIManager._norm_col حتى تتطابق نتيجة الطريقتين.
_AR_NORMALIZE = str.maketrans({"ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ؤ": "و", "ئ": "ي"})
# النصوص الحرفية '...' (مع '' للهروب) — لا نلمس أي شيء بداخلها
_STRING_LITERAL_SPLIT = re.compile(r"('(?:[^']|'')*')")
_QUOTED_IDENT = re.compile(r'"([^"]+)"')
# اسم مُعرَّف داخل الاستعلام نفسه (AS "x") أو CTE ("x" AS (...)) → ليس عموداً
_DEFINED_ALIAS = re.compile(r'\bAS\s+"([^"]+)"', re.IGNORECASE)
_DEFINED_CTE = re.compile(r'"([^"]+)"\s+AS\s*\(', re.IGNORECASE)


@trace_calls
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


@trace_calls
def _norm_ident(name) -> str:
    """توحيد معرّف للمقارنة: حروف صغيرة + توحيد الإملاء العربي."""
    return str(name).strip().lower().translate(_AR_NORMALIZE)


# ══════════════════════════════════════════════════════════════
#  كاش الجداول المحمَّلة (على مستوى الوحدة — مشترك بين كل المستخدمين
#  لكن المفتاح يحوي مسار project.db فلا اختلاط بين المشاريع)
# ══════════════════════════════════════════════════════════════

# المفتاح: (مسار project.db, dashboard_id أو "", table_alias)
# القيمة : {"sig", "ts", "df", "has_view", "bytes"}
# "sig" بصمة core.project_db.file_signature (مأخوذة قبل القراءة دائماً: لو حدثت
# كتابة أثناء القراءة تُخزَّن البيانات تحت بصمة أقدم فتُرفض في الطلب التالي —
# لا بيانات قديمة تُقدَّم كحديثة).
_TABLE_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_TABLE_CACHE_LOCK = threading.Lock()


@trace_calls
def _cache_get(key: tuple, sig: tuple) -> Optional[dict]:
    """إدخال الكاش لو صالح (نفس البصمة وضمن المهلة)، وإلا None مع حذفه."""
    if not QUERY_TABLE_CACHE_ENABLED:
        return None
    with _TABLE_CACHE_LOCK:
        entry = _TABLE_CACHE.get(key)
        if entry is None:
            return None
        expired = (
            QUERY_TABLE_CACHE_TTL_SECONDS > 0
            and time.monotonic() - entry["ts"] > QUERY_TABLE_CACHE_TTL_SECONDS
        )
        if entry["sig"] != sig or expired:
            del _TABLE_CACHE[key]
            return None
        _TABLE_CACHE.move_to_end(key)   # LRU: الأحدث استخداماً في النهاية
        return entry


@trace_calls
def _cache_put(key: tuple, sig: tuple, df, has_view: bool) -> None:
    """تخزين جدول في الكاش مع إخلاء الأقدم استخداماً عند تجاوز السقف الحجمي."""
    if not QUERY_TABLE_CACHE_ENABLED:
        return
    max_bytes = int(QUERY_TABLE_CACHE_MAX_MB * 1024 * 1024)
    try:
        size = int(df.memory_usage(deep=True).sum())
    except Exception:
        return
    if size > max_bytes:
        return   # جدول وحده أكبر من السقف: لا يُخزَّن (يُقرأ كل مرة كما كان)

    with _TABLE_CACHE_LOCK:
        # أي إدخال لنفس المشروع ببصمة أقدم صار باطلاً — نحذفه الآن بدل انتظار
        # مهلته أو إخلائه، حتى لا يشغل ذاكرة بلا فائدة
        stale = [k for k, e in _TABLE_CACHE.items() if k[0] == key[0] and e["sig"] != sig]
        for k in stale:
            del _TABLE_CACHE[k]

        _TABLE_CACHE[key] = {
            "sig": sig, "ts": time.monotonic(), "df": df,
            "has_view": has_view, "bytes": size,
        }
        _TABLE_CACHE.move_to_end(key)

        total = sum(e["bytes"] for e in _TABLE_CACHE.values())
        while total > max_bytes and len(_TABLE_CACHE) > 1:
            _, evicted = _TABLE_CACHE.popitem(last=False)
            total -= evicted["bytes"]


class QueryEngine:
    """
    تنفيذ SQL على جداول المشروع عبر DuckDB.

    الاستخدام العادي (محادثة/تقارير/أي مكان آخر):
        qe = QueryEngine(db)
        result = qe.run("SELECT SUM(amount) FROM sales")

    الاستخدام ضمن لوحة معلومات (البيانات تُقرأ من views فلاتر اللوحة):
        result = qe.run("SELECT ...", dashboard_id="...")
    """

    @trace_calls
    def __init__(self, db: ProjectDB):
        self.db = db
        self.views = DashboardViews(db)

    # ──────────────────────────────────────────────────────────
    #  فحص الأمان
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def validate(self, sql: str) -> dict:
        """
        فحص SQL قبل التنفيذ.
        يرفض أي كلمة من SQL_FORBIDDEN.
        يرجع: {"ok": True} أو {"ok": False, "error": "..."}
        """
        # ارفض أي رد لا يبدأ باستعلام قراءة. إزالة التعليقات الافتتاحية
        # تمنع تمرير نص تفسيري/أوامر مخفية قبل SELECT أو WITH.
        without_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        without_comments = re.sub(r"--[^\r\n]*", " ", without_comments)
        if not re.match(r"^\s*(SELECT|WITH)\b", without_comments, re.IGNORECASE):
            return {"ok": False, "error": "يُسمح باستعلامات SELECT أو WITH فقط؛ لم يبدأ الرد باستعلام قراءة صالح"}

        # نسمح بفاصلة منقوطة نهائية واحدة فقط؛ تعدد الأوامر مرفوض.
        if ";" in sql.rstrip().rstrip(";"):
            return {"ok": False, "error": "يُسمح باستعلام SQL واحد فقط"}

        sql_upper = sql.upper()
        for word in SQL_FORBIDDEN:
            # نستخدم word boundary لتجنب false positives
            if re.search(rf"\b{word}\b", sql_upper):
                logger.warning("SQL forbidden word detected: %s", word)
                return {"ok": False, "error": f"كلمة محظورة في الاستعلام: {word}"}
        return {"ok": True}

    # ──────────────────────────────────────────────────────────
    #  تحميل الجداول في DuckDB (عبر الكاش)
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def _load_tables(
        self,
        conn: duckdb.DuckDBPyConnection,
        dashboard_id: Optional[str] = None,
        columns_out: Optional[dict] = None,
    ) -> list[str]:
        """
        تسجيل كل الجداول النظيفة من project.db في DuckDB بأسمائها
        الأصلية (alias). مع dashboard_id: الجدول الذي له view في هذه
        اللوحة يُحمَّل مفلتراً من الـ view (راجع
        DashboardViews.read_table)، وغيره من الجدول الأصلي.

        القراءة تمر عبر _TABLE_CACHE: الاستدعاء الأول يقرأ من SQLite
        ويخزّن، وما بعده (حتى تتغير البصمة) يستعمل الـ DataFrame نفسه.
        إعدادات المشروع تُقرأ مرة واحدة فقط وعند أول فشل كاش فقط، وتُمرَّر
        لكل قراءة جدول (بدل get_settings لكل جدول).

        جدول الأصل الفارغ/المفقود يُتخطّى كما كان دائماً. أما الجدول
        الذي أفرغه فلتر اللوحة فيُسجَّل فارغاً (بأنواع أعمدته الصحيحة)
        حتى يعود الاستعلام بنتيجة فارغة سليمة بدل خطأ "جدول غير موجود".

        columns_out (اختياري): dict يُملأ بـ {alias: [أسماء الأعمدة]} للجداول
        المحمَّلة — يستهلكه التصحيح الاستباقي لأسماء الأعمدة دون أي قراءة إضافية.

        يرجع قائمة بأسماء الجداول المحملة.
        """
        loaded = []
        summary = []
        settings = None   # كسول: لا يُقرأ لو كل الجداول جاءت من الكاش
        db_key = str(self.db.db_path)
        sig = file_signature(self.db.db_path)

        for f in self.db.get_files():
            alias = f["table_alias"]
            key = (db_key, dashboard_id or "", alias)

            entry = _cache_get(key, sig)
            if entry is not None:
                df, has_view, hit = entry["df"], entry["has_view"], True
            else:
                has_view = bool(dashboard_id) and self.views.has_view(dashboard_id, alias)
                if settings is None:
                    settings = self.db.get_settings()
                df = self.views.read_table(alias, dashboard_id, settings=settings)
                hit = False

            filtered_empty = df is not None and df.empty and has_view
            if df is None or (df.empty and not filtered_empty):
                logger.warning("Table '%s' is empty or missing — skipped", alias)
                continue

            if not hit:
                _cache_put(key, sig, df, has_view)

            conn.register(alias, df)
            loaded.append(alias)
            if columns_out is not None:
                columns_out[alias] = [str(c) for c in df.columns]
            summary.append(
                f"{alias}({len(df)} rows{', view' if has_view else ''}{', cache' if hit else ''})"
            )

        # سطر واحد لكل استعلام بدل سطر لكل جدول (كان يغرق اللوج في خطط QA/Story)
        if summary:
            logger.debug("Tables ready in DuckDB: %s", ", ".join(summary))
        return loaded

    # ──────────────────────────────────────────────────────────
    #  🆕 تصحيح استباقي لأسماء الأعمدة (قبل التنفيذ الأول)
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def _precorrect_columns(self, sql: str, columns_by_table: dict) -> tuple[str, list]:
        """
        يصحّح المعرّفات المقتبسة ("...") التي لا تطابق أي عمود فعلي
        لكنها تطابق عموداً واحداً بثقة (راجع توثيق الوحدة). يرجع
        (sql_مصحَّح, [{"from", "to"}]) — نفس بنية auto_fixes فتظهر
        في واجهة المحادثة دون أي تغيير هناك.

        الثقة: تطابق تام بعد توحيد الإملاء العربي لعمود وحيد، أو أعلى
        تشابه ≥ QUERY_PRECORRECT_MIN_RATIO بشرط عدم تعادله مع مرشح ثانٍ.
        أي غموض = لا تصحيح (نتيجة خاطئة بصمت أخطر من فشل واضح يُصلحه
        المسار الفعلي أو الـ AI).
        """
        all_columns = {c for cols in columns_by_table.values() for c in cols}
        if not all_columns:
            return sql, []

        # أجزاء الـ SQL: الفردية (فهرس فردي) نصوص حرفية لا تُمس
        parts = _STRING_LITERAL_SPLIT.split(sql)
        code = "".join(parts[0::2])

        known_cf = {c.casefold() for c in all_columns} | {t.casefold() for t in columns_by_table}
        defined_cf = {
            n.casefold()
            for n in _DEFINED_ALIAS.findall(code) + _DEFINED_CTE.findall(code)
        }

        by_norm: dict[str, set] = {}
        for c in all_columns:
            by_norm.setdefault(_norm_ident(c), set()).add(c)

        fixes: dict[str, str] = {}
        for wrong in dict.fromkeys(_QUOTED_IDENT.findall(code)):
            wrong_cf = wrong.casefold()
            if wrong_cf in known_cf or wrong_cf in defined_cf:
                continue

            exact = by_norm.get(_norm_ident(wrong), set())
            if len(exact) == 1:
                best = next(iter(exact))
            else:
                wrong_norm = _norm_ident(wrong)
                scored = sorted(
                    ((SequenceMatcher(None, wrong_norm, _norm_ident(c)).ratio(), c)
                     for c in all_columns),
                    reverse=True,
                )
                if not scored or scored[0][0] < QUERY_PRECORRECT_MIN_RATIO:
                    continue
                if len(scored) > 1 and scored[1][0] == scored[0][0]:
                    continue   # غامض: مرشحان متساويان
                best = scored[0][1]

            fixes[wrong] = best

        if not fixes:
            return sql, []

        def _replace_in_code(segment: str) -> str:
            for wrong, best in fixes.items():
                safe_best = best.replace('"', '""')
                segment = segment.replace(f'"{wrong}"', f'"{safe_best}"')
            return segment

        for i in range(0, len(parts), 2):
            parts[i] = _replace_in_code(parts[i])

        applied = [{"from": w, "to": b} for w, b in fixes.items()]
        logger.info("Pre-execution column fix: %s", applied)
        return "".join(parts), applied

    # ──────────────────────────────────────────────────────────
    #  تصحيح تلقائي لاسم عمود خاطئ بناءً على رسالة DuckDB
    # ──────────────────────────────────────────────────────────

    @trace_calls
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

    @trace_calls
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
        أعمدة قبل نجاح التنفيذ (استباقية أو بعد خطأ DuckDB)، مثل
        [{"from": "سليم", "to": "السليم"}]. "sql" يحمل الاستعلام الذي
        نُفِّذ فعلاً بعد هذه التصحيحات.
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

            # التصحيح الاستباقي (إن حدث) يُسجَّل ويصبح أساس أي محاولة لاحقة،
            # سواء نجح هذا التنفيذ أم فشل لسبب آخر
            pre_fixes = result.pop("pre_fixes", None)
            executed_sql = result.pop("sql_executed", None)
            if pre_fixes:
                auto_fixes.extend(pre_fixes)
            if executed_sql:
                current_sql = executed_sql

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

    @trace_calls
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

    @trace_calls
    def _execute_once(self, sql: str, dashboard_id: Optional[str] = None) -> dict:
        """
        تنفيذ استعلام واحد فعلياً (بدون منطق التصحيح الفعلي المتكرر في run).
        يطبّق التصحيح الاستباقي للأعمدة بعد التحميل مباشرة؛ وعند حدوثه
        يضيف للنتيجة (نجاحاً أو فشلاً) "pre_fixes" و"sql_executed"
        ليبني عليهما run() — ويُزيلهما قبل إرجاع النتيجة للمستدعي.
        """
        conn = None
        exec_sql = sql
        pre_fixes: list = []
        msg = ""
        try:
            conn    = duckdb.connect()
            columns = {}
            loaded  = self._load_tables(conn, dashboard_id=dashboard_id, columns_out=columns)

            if not loaded:
                return {"ok": False, "error": "لا توجد جداول محملة في المشروع"}

            if QUERY_PRECORRECT_COLUMNS:
                exec_sql, pre_fixes = self._precorrect_columns(sql, columns)

            df   = conn.execute(exec_sql).df()
            rows = len(df)
            logger.info("SQL executed successfully: %d rows returned", rows)
            out = {"ok": True, "df": df, "rows": rows}
            if pre_fixes:
                out["pre_fixes"] = pre_fixes
                out["sql_executed"] = exec_sql
            return out

        except duckdb.CatalogException as e:
            msg = f"جدول أو عمود غير موجود: {e}"
            logger.error("DuckDB CatalogException: %s", e)

        except duckdb.BinderException as e:
            # هذا هو النوع الفعلي الذي يرفعه DuckDB لعمود غير موجود ضمن
            # تعبير (مثل CASE WHEN) — يحتوي عادة على "Candidate bindings".
            msg = f"عمود غير موجود: {e}"
            logger.error("DuckDB BinderException: %s", e)

        except duckdb.ParserException as e:
            msg = f"خطأ في صياغة SQL: {e}"
            logger.error("DuckDB ParserException: %s", e)

        except duckdb.Error as e:
            msg = str(e)
            logger.error("DuckDB error: %s", e)

        except Exception as e:
            msg = str(e)
            logger.error("QueryEngine unexpected error: %s", e)

        finally:
            if conn:
                conn.close()

        failure = {"ok": False, "error": msg}
        if pre_fixes:
            failure["pre_fixes"] = pre_fixes
            failure["sql_executed"] = exec_sql
        return failure
