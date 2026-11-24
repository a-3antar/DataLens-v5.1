"""
core/knowledge_manager.py
===========================
الذاكرة المعرفية للمشروع (Project Knowledge): معرفة مُستخرجة من بيانات
المشروع الكاملة وتُخزَّن داخل project.db، وتُستخدم لرفع جودة توليد SQL
وسؤال وجواب (qa) وStory Telling.

الطبقات الثلاث
---------------
- profile  (الطبقة أ، بلا AI): بروفايل إحصائي بـ DuckDB على الجدول كاملاً —
  أرقام الجدول، دور كل عمود (id/category/measure/date/text/boolean/constant)،
  إحصاءات حسب النوع، القيم الأكثر تكراراً، الارتباطات، وبصمة data_signature.
- semantic (الطبقة ب، AI): غرض الجدول وحبيبته ووصف كل عمود ووحدته ومرادفاته
  وطريقة تجميعه وتحذيراته + ملخص المشروع. JSON يُتحقق منه برمجياً: أي عمود
  غير موجود فعلاً يُهمَل.
- insight  (الطبقة ج، AI + SQL منفَّذ): كل insight مرتبط باستعلام نُفِّذ فعلاً،
  وأرقام نصه يجب أن تُوجد في نتيجة ذلك الاستعلام (أو تُشتق منها حسابياً)، وإلا
  يُرفض. يُخزَّن معه الـ SQL ولقطة صغيرة من النتيجة.
الطبقتان ب وج تُرسلان temperature=0.0 صراحةً ولا تعتمدان على إعدادات المشروع.

التخزين (sqlite3 مباشرة على project.db — بلا تعديل في core/project_db.py)
--------------------------------------------------------------------------
- project_knowledge: المفتاح الأساسي (scope, table_alias, column_name, layer,
  item_key). scope ∈ project|table|column|insight.
- knowledge_jobs: حالة آخر مهمة لكل مشروع.
الجداول تُنشأ عند أول استخدام (CREATE IF NOT EXISTS + ترحيل أعمدة ناقصة)،
فتعمل مع ملفات مشاريع قديمة مستوردة، ومع export/import وVACUUM.

التقادم (stale) مشتق من data_signature
-----------------------------------------
بصمة كل جدول تُحسب في الطبقة أ وتُخزَّن مع صفوفه ومع صفوف الطبقتين ب وج
(بصمة المشروع = تجميع بصمات الجداول). عمود stale نسخة مخزَّنة من المقارنة
(بصمة الصف ≠ بصمة البيانات الحالية). mark_stale() بعد عمليات التنظيف يضع
تلميحاً فورياً بلا حساب؛ وعند إعادة حساب الطبقة أ تُعاد stale من المقارنة
الفعلية للبصمات (فإن لم تتغير البيانات فعلاً يزول التلميح).
تعديلات المستخدم (user_edited=1) لا تُستبدل عند إعادة توليد الطبقة ب: تُحفظ
الحقول التي عدّلها (user_fields) وتُحدَّث بقية الحقول.

التنفيذ في الخلفية
--------------------
ThreadPoolExecutor على مستوى الوحدة، مهمة واحدة نشطة لكل مشروع (طلب جديد
أثناءها يُدمج كطلب معلَّق ويُنفَّذ بعدها). لا st.* داخل الـ threads: الحالة
والتقدم تُكتب في knowledge_jobs وتقرؤها الواجهة. سياق contextvars (اسم
المستخدم في اللوج) يُنقل بـ copy_context. فشل طبقة لا يُلغي ما أُنجز (state=
partial)، وfinally يضمن عدم بقاء state=running. مهمة running أقدم من
KNOWLEDGE_JOB_STALE_MINUTES تُعتبر ميتة (cleanup_dead_jobs_all/get_status).

الخصوصية: عند knowledge_send_values_to_ai=False لا تُرسَل للـ AI قيم فعلية
(قيم فئوية، عينات صفوف، لقطات نتائج)، وتُعطَّل الطبقة ج كلياً.
"""

import contextvars
import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Optional

import duckdb
import numpy as np
import pandas as pd

from config import (
    PROJECTS_DIR, KNOWLEDGE_CONTEXT_MAX_CHARS, KNOWLEDGE_TOP_VALUES,
    KNOWLEDGE_CATEGORICAL_MAX_DISTINCT, KNOWLEDGE_MAX_INSIGHTS,
    KNOWLEDGE_INSIGHT_QUERIES, KNOWLEDGE_INSIGHT_SNAPSHOT_ROWS,
    KNOWLEDGE_JOB_STALE_MINUTES, KNOWLEDGE_MAX_CORRELATIONS,
)

logger = logging.getLogger(__name__)

LAYER_PROFILE = "profile"
LAYER_SEMANTIC = "semantic"
LAYER_INSIGHT = "insight"
ALL_LAYERS = (LAYER_PROFILE, LAYER_SEMANTIC, LAYER_INSIGHT)

ROLES = ("id", "category", "measure", "date", "text", "boolean", "constant")
AGGREGATIONS = ("SUM", "AVG", "COUNT", "COUNT DISTINCT", "MIN", "MAX", "NONE")
EDITABLE_FIELDS = ("description", "synonyms", "aggregation")

_DB_TIMEOUT = 30
_SEMANTIC_CHUNK_COLUMNS = 20     # أعمدة الجدول في استدعاء AI واحد (تفادي اقتطاع الرد)
_SAMPLE_ROWS_FOR_AI = 8
_INSIGHT_PROMPT_ROWS = 60
_CORR_MAX_COLUMNS = 15
_CORR_MIN_ABS = 0.3

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS project_knowledge (
    scope          TEXT NOT NULL DEFAULT '',
    table_alias    TEXT NOT NULL DEFAULT '',
    column_name    TEXT NOT NULL DEFAULT '',
    layer          TEXT NOT NULL DEFAULT '',
    item_key       TEXT NOT NULL DEFAULT '',
    content_json   TEXT NOT NULL DEFAULT '{}',
    data_signature TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT 'stats',
    generated_at   TEXT NOT NULL DEFAULT '',
    user_edited    INTEGER NOT NULL DEFAULT 0,
    stale          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, table_alias, column_name, layer, item_key)
);
CREATE TABLE IF NOT EXISTS knowledge_jobs (
    project_id     TEXT PRIMARY KEY,
    state          TEXT NOT NULL DEFAULT 'idle',
    stage          TEXT NOT NULL DEFAULT '',
    progress_done  INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    started_at     TEXT NOT NULL DEFAULT '',
    finished_at    TEXT NOT NULL DEFAULT '',
    error          TEXT NOT NULL DEFAULT '',
    layers         TEXT NOT NULL DEFAULT '[]',
    triggered_by   TEXT NOT NULL DEFAULT '',
    pending_json   TEXT NOT NULL DEFAULT ''
);
"""

_EXPECTED_COLUMNS = {
    "project_knowledge": {
        "content_json": "TEXT NOT NULL DEFAULT '{}'", "data_signature": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT 'stats'", "generated_at": "TEXT NOT NULL DEFAULT ''",
        "user_edited": "INTEGER NOT NULL DEFAULT 0", "stale": "INTEGER NOT NULL DEFAULT 0",
    },
    "knowledge_jobs": {
        "stage": "TEXT NOT NULL DEFAULT ''", "progress_done": "INTEGER NOT NULL DEFAULT 0",
        "progress_total": "INTEGER NOT NULL DEFAULT 0", "started_at": "TEXT NOT NULL DEFAULT ''",
        "finished_at": "TEXT NOT NULL DEFAULT ''", "error": "TEXT NOT NULL DEFAULT ''",
        "layers": "TEXT NOT NULL DEFAULT '[]'", "triggered_by": "TEXT NOT NULL DEFAULT ''",
        "pending_json": "TEXT NOT NULL DEFAULT ''",
    },
}

_SCHEMA_READY: set = set()
_SCHEMA_LOCK = threading.Lock()


# ══════════════════════════════════════════════════════════════
#  أدوات عامة
# ══════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.utcnow().isoformat()


def _q(name) -> str:
    """اقتباس معرّف DuckDB/SQLite."""
    return '"' + str(name).replace('"', '""') + '"'


def _json_safe(value):
    """تعقيم قيمة قبل التخزين كـ JSON (numpy/Timestamp/NaN) — نفس سلوك _sanitize_rows."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _sanitize_rows(rows: list) -> list:
    return [{str(k): _json_safe(v) for k, v in row.items()} for row in rows]


def _num(value, digits: int = 6):
    v = _json_safe(value)
    if v is None or isinstance(v, bool):
        return v
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return None


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=_json_safe)


def _loads(text: str):
    try:
        return json.loads(text) if text else {}
    except Exception:
        return {}


_AR_MAP = str.maketrans({"ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ؤ": "و", "ئ": "ي", "ـ": ""})
_PUNCT_RE = re.compile(r"[^\w\s]|_", re.UNICODE)


def _tokens(text) -> set:
    """كلمات منظَّمة للمقارنة (casefold + إزالة التشكيل + توحيد الإملاء العربي + حذف 'ال')."""
    t = unicodedata.normalize("NFKD", str(text or "")).casefold()
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").translate(_AR_MAP)
    out = set()
    for tok in _PUNCT_RE.sub(" ", t).split():
        if len(tok) >= 4 and tok.startswith("ال"):
            tok = tok[2:]
        if len(tok) >= 2:
            out.add(tok)
    return out


# ══════════════════════════════════════════════════════════════
#  الاتصال والتخزين
# ══════════════════════════════════════════════════════════════

def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _EXPECTED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in cols.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


@contextmanager
def _open(db):
    """اتصال sqlite خاص بالعملية (timeout طويل لتفادي database is locked) + commit/close."""
    path = str(db.db_path)
    conn = sqlite3.connect(path, timeout=_DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout = {_DB_TIMEOUT * 1000}")
        with _SCHEMA_LOCK:
            ready = path in _SCHEMA_READY
        if not ready:
            conn.executescript(_SCHEMA_SQL)
            _migrate(conn)
            conn.commit()
            with _SCHEMA_LOCK:
                _SCHEMA_READY.add(path)
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _upsert(conn, scope, alias, column, layer, item_key, content, signature, source,
            user_edited: int = 0, stale: int = 0) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO project_knowledge (scope, table_alias, column_name, layer, item_key,"
        " content_json, data_signature, source, generated_at, user_edited, stale)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (scope, alias or "", column or "", layer, item_key or "", _dumps(content),
         signature or "", source, _now(), int(user_edited), int(stale)),
    )


def _row_dict(r) -> dict:
    return {
        "scope": r["scope"], "table_alias": r["table_alias"], "column_name": r["column_name"],
        "layer": r["layer"], "item_key": r["item_key"], "content": _loads(r["content_json"]),
        "data_signature": r["data_signature"], "source": r["source"],
        "generated_at": r["generated_at"], "user_edited": bool(r["user_edited"]),
        "stale": bool(r["stale"]),
    }


def _select(conn, where: str = "", params: tuple = ()) -> list:
    sql = "SELECT * FROM project_knowledge" + (f" WHERE {where}" if where else "")
    return [_row_dict(r) for r in conn.execute(sql, params).fetchall()]


def _project_signature(conn) -> str:
    rows = conn.execute(
        "SELECT table_alias, data_signature FROM project_knowledge "
        "WHERE scope='table' AND layer='profile' ORDER BY table_alias"
    ).fetchall()
    return hashlib.sha1(_dumps([[r[0], r[1]] for r in rows]).encode()).hexdigest() if rows else ""


def _sync_table_stale(conn, alias: str, sig: str) -> None:
    """stale = (بصمة صف الطبقة ب ≠ بصمة بيانات الجدول الحالية)."""
    conn.execute(
        "UPDATE project_knowledge SET stale = CASE WHEN data_signature = ? THEN 0 ELSE 1 END "
        "WHERE table_alias = ? AND layer = 'semantic'", (sig, alias))


def _sync_project_stale(conn) -> None:
    sig = _project_signature(conn)
    conn.execute(
        "UPDATE project_knowledge SET stale = CASE WHEN data_signature = ? THEN 0 ELSE 1 END "
        "WHERE scope IN ('project','insight')", (sig,))


# ══════════════════════════════════════════════════════════════
#  الطبقة أ — البروفايل الإحصائي (DuckDB على الجدول كاملاً)
# ══════════════════════════════════════════════════════════════

_ID_NAME_RE = re.compile(r"(^|_)(id|code|no|num|key|uuid)$|^(id|code)(_|$)|كود|معرف|رقم", re.I)
_MEASURE_NAME_RE = re.compile(
    r"qty|quantity|amount|total|price|cost|value|weight|sum|rate|"
    r"كمي|سعر|قيم|اجمالي|إجمالي|مبلغ|وزن|عدد|تكلف|مجموع", re.I)
_CATEGORY_NUM_NAME_RE = re.compile(r"year|month|quarter|day|week|level|grade|rank|سنه|سنة|شهر|ربع|اسبوع|مستوى|درجه|درجة", re.I)


def _kind_of(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s.dtype):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(s.dtype):
        return "date"
    if pd.api.types.is_numeric_dtype(s.dtype):
        return "numeric"
    return "text"


def classify_role(name: str, kind: str, non_null: int, distinct: int,
                  avg_len: Optional[float] = None, integer_like: bool = False) -> str:
    """تصنيف دور العمود بقواعد حتمية (نوع، تفرّد، كاردينالية، اسم العمود)."""
    if non_null == 0 or distinct <= 1:
        return "constant"
    if kind == "boolean":
        return "boolean"
    if kind == "date":
        return "date"
    ratio = distinct / non_null
    hint = bool(_ID_NAME_RE.search(str(name)))

    if kind == "numeric":
        if hint and ratio >= 0.5:
            return "id"
        if _MEASURE_NAME_RE.search(str(name)):
            return "measure"
        if _CATEGORY_NUM_NAME_RE.search(str(name)) and distinct <= KNOWLEDGE_CATEGORICAL_MAX_DISTINCT:
            return "category"
        if integer_like and distinct <= KNOWLEDGE_CATEGORICAL_MAX_DISTINCT and ratio <= 0.05 and non_null >= 20:
            return "category"
        return "measure"

    # نصي
    if hint and ratio >= 0.9:
        return "id"
    if ratio >= 0.9 and (avg_len or 0) <= 24:
        return "id"
    if distinct <= KNOWLEDGE_CATEGORICAL_MAX_DISTINCT or ratio <= 0.5:
        return "category"
    return "text"


class _TableProfiler:
    """بروفايل جدول واحد عبر DuckDB (يملك df ويعدّل أعمدته النصية المختلطة في مكانها)."""

    def __init__(self, df: pd.DataFrame):
        self.df = self._prepare(df)
        self.cols = [str(c) for c in self.df.columns]
        self.n = len(self.df)
        self.kinds = {c: _kind_of(self.df[c]) for c in self.cols}
        self.con = duckdb.connect()
        self.con.register("t", self.df)
        self._basic = None

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass

    @staticmethod
    def _prepare(df: pd.DataFrame) -> pd.DataFrame:
        df.columns = [str(c) for c in df.columns]
        for c in df.columns:
            s = df[c]
            if isinstance(s.dtype, pd.CategoricalDtype):
                df[c] = s.astype(object)
                s = df[c]
            if pd.api.types.is_object_dtype(s.dtype) and pd.api.types.infer_dtype(s, skipna=True) not in ("string", "empty"):
                df[c] = s.where(s.isna(), s.astype(str))
        return df

    def _rows(self, sql: str) -> list:
        return self.con.execute(sql).fetchall()

    # ── الأرقام الأساسية + البصمة ──

    def basic(self) -> dict:
        if self._basic is not None:
            return self._basic
        self._basic = {}
        if not self.cols:
            return self._basic
        exprs = []
        for c in self.cols:
            q = _q(c)
            exprs += [f"COUNT({q})", f"COUNT(DISTINCT {q})",
                      f"SUM(CAST({q} AS DOUBLE))" if self.kinds[c] == "numeric" else "CAST(NULL AS DOUBLE)"]
        row = self.con.execute("SELECT " + ", ".join(exprs) + " FROM t").fetchone()
        for i, c in enumerate(self.cols):
            self._basic[c] = {"non_null": int(row[3 * i] or 0), "distinct": int(row[3 * i + 1] or 0),
                              "sum": _num(row[3 * i + 2], 4)}
        return self._basic

    def signature(self) -> str:
        b = self.basic()
        payload = [self.n, [[c, str(self.df[c].dtype), b[c]["non_null"], b[c]["distinct"], b[c]["sum"]]
                            for c in self.cols]]
        return hashlib.sha1(_dumps(payload).encode()).hexdigest()

    # ── إحصاءات حسب النوع ──

    def _numeric(self, c: str) -> dict:
        x = f"CAST({_q(c)} AS DOUBLE)"
        r = self._rows(
            f"SELECT MIN({x}), MAX({x}), AVG({x}), MEDIAN({x}), STDDEV_SAMP({x}), "
            f"QUANTILE_CONT({x},0.25), QUANTILE_CONT({x},0.75), QUANTILE_CONT({x},0.95), "
            f"COUNT(*) FILTER (WHERE {x}=0), COUNT(*) FILTER (WHERE {x}<0), "
            f"COUNT(*) FILTER (WHERE {x}<>FLOOR({x})) FROM t")[0]
        nn = max(self.basic()[c]["non_null"], 1)
        return {"min": _num(r[0]), "max": _num(r[1]), "mean": _num(r[2]), "median": _num(r[3]),
                "std": _num(r[4]), "p25": _num(r[5]), "p75": _num(r[6]), "p95": _num(r[7]),
                "zero_pct": round(100 * (r[8] or 0) / nn, 2), "negative_pct": round(100 * (r[9] or 0) / nn, 2),
                "integer_like": (r[10] or 0) == 0}

    def _date(self, c: str) -> dict:
        q = _q(c)
        mn, mx = self._rows(f"SELECT MIN({q}), MAX({q}) FROM t")[0]
        out = {"min": _json_safe(mn), "max": _json_safe(mx), "resolution": "غير معروف",
               "median_gap_days": None, "gaps": []}
        if mn is None:
            return out
        rows = self._rows(
            f"WITH g AS (SELECT d, date_diff('day', LAG(d) OVER (ORDER BY d), d) AS gap FROM "
            f"(SELECT DISTINCT CAST({q} AS DATE) AS d FROM t WHERE {q} IS NOT NULL)) "
            f"SELECT (SELECT MEDIAN(gap) FROM g), d, gap FROM g WHERE gap IS NOT NULL ORDER BY gap DESC LIMIT 3")
        if not rows:
            out["resolution"] = "يوم واحد"
            return out
        med = _num(rows[0][0])
        out["median_gap_days"] = med
        if med is not None:
            out["resolution"] = (
                "يومي" if med <= 1.5 else "أسبوعي" if 5 <= med <= 9 else "شهري" if 25 <= med <= 35
                else "ربع سنوي" if 80 <= med <= 100 else "سنوي" if 330 <= med <= 400 else "غير منتظم")
            for d, gap in ((r[1], r[2]) for r in rows):
                if gap and gap > max(7, 3 * med):
                    end = pd.Timestamp(d)
                    out["gaps"].append({"from": (end - pd.Timedelta(days=int(gap))).date().isoformat(),
                                        "to": end.date().isoformat(), "days": int(gap)})
        return out

    def _text_len(self, c: str) -> dict:
        q = _q(c)
        r = self._rows(f"SELECT AVG(LENGTH(CAST({q} AS VARCHAR))), MAX(LENGTH(CAST({q} AS VARCHAR))) "
                       f"FROM t WHERE {q} IS NOT NULL")[0]
        return {"avg_length": _num(r[0], 2), "max_length": _num(r[1], 0)}

    def _sample(self, c: str, n: int) -> list:
        q = _q(c)
        return [str(r[0])[:60] for r in self._rows(
            f"SELECT DISTINCT CAST({q} AS VARCHAR) FROM t WHERE {q} IS NOT NULL LIMIT {int(n)}")]

    def _top_values(self, c: str) -> list:
        q = _q(c)
        return [{"value": str(r[0])[:80], "count": int(r[1])} for r in self._rows(
            f"SELECT CAST({q} AS VARCHAR) AS v, COUNT(*) AS n FROM t WHERE {q} IS NOT NULL "
            f"GROUP BY 1 ORDER BY n DESC, v LIMIT {int(KNOWLEDGE_TOP_VALUES)}")]

    def column(self, c: str) -> dict:
        kind, b = self.kinds[c], self.basic()[c]
        nn, distinct = b["non_null"], b["distinct"]
        cp = {"name": c, "dtype": str(self.df[c].dtype), "kind": kind, "non_null": nn, "distinct": distinct,
              "null_pct": round(100 * (self.n - nn) / self.n, 2) if self.n else 100.0}
        try:
            extra, avg_len, integer_like = {}, None, False
            if nn > 0:
                if kind == "numeric":
                    extra = self._numeric(c)
                    integer_like = extra.pop("integer_like")
                    extra["integer_like"] = integer_like
                elif kind == "date":
                    extra = self._date(c)
                elif kind == "text":
                    extra = self._text_len(c)
                    avg_len = extra["avg_length"]
                elif kind == "boolean":
                    q = _q(c)
                    t, f = self._rows(f"SELECT COUNT(*) FILTER (WHERE {q}), COUNT(*) FILTER (WHERE NOT {q}) FROM t")[0]
                    extra = {"true_count": int(t or 0), "false_count": int(f or 0)}
            role = classify_role(c, kind, nn, distinct, avg_len, integer_like)
            cp["role"] = role
            cp.update(extra)
            if nn > 0:
                if role == "category":
                    if distinct <= KNOWLEDGE_CATEGORICAL_MAX_DISTINCT:
                        cp["top_values"] = self._top_values(c)
                        cp["all_values"] = distinct <= KNOWLEDGE_TOP_VALUES
                    else:
                        cp["sample_values"] = self._sample(c, 8)
                elif role in ("id", "text", "constant"):
                    cp["sample_values"] = self._sample(c, 3 if role != "text" else 4)
        except Exception as e:
            logger.warning("column profile failed (%s): %s", c, e)
            cp.setdefault("role", classify_role(c, kind, nn, distinct))
            cp["error"] = str(e)[:200]
        return cp

    def duplicates(self) -> Optional[int]:
        if not self.cols or self.n == 0:
            return 0
        try:
            return self.n - int(self._rows("SELECT COUNT(*) FROM (SELECT DISTINCT * FROM t)")[0][0])
        except Exception as e:
            logger.debug("duplicate count skipped: %s", e)
            return None

    def correlations(self, cps: dict) -> list:
        measures = [c for c in self.cols if cps[c].get("role") == "measure" and cps[c].get("non_null", 0) > 2][:_CORR_MAX_COLUMNS]
        pairs = [(a, b) for i, a in enumerate(measures) for b in measures[i + 1:]]
        if not pairs:
            return []
        try:
            exprs = [f"corr(CAST({_q(a)} AS DOUBLE), CAST({_q(b)} AS DOUBLE))" for a, b in pairs]
            row = self.con.execute("SELECT " + ", ".join(exprs) + " FROM t").fetchone()
        except Exception as e:
            logger.debug("correlations skipped: %s", e)
            return []
        found = [{"a": a, "b": b, "r": _num(r, 3)} for (a, b), r in zip(pairs, row)
                 if _num(r) is not None and abs(_num(r)) >= _CORR_MIN_ABS]
        found.sort(key=lambda x: -abs(x["r"]))
        return found[:KNOWLEDGE_MAX_CORRELATIONS]

    def profile(self, alias: str, relations: list) -> tuple:
        cps = {c: self.column(c) for c in self.cols} if self.n else {
            c: {"name": c, "dtype": str(self.df[c].dtype), "kind": self.kinds[c], "role": "constant",
                "non_null": 0, "distinct": 0, "null_pct": 100.0} for c in self.cols}
        rels = [f"{r['from_table']}.{r['from_col']} = {r['to_table']}.{r['to_col']}"
                for r in relations if alias in (r["from_table"], r["to_table"])]
        table = {"table": alias, "rows": self.n, "cols": len(self.cols), "duplicate_rows": self.duplicates(),
                 "relations": rels, "correlations": self.correlations(cps) if self.n else [],
                 "column_order": self.cols}
        return table, cps


def _layer_a(db, alias: str, force: bool, relations: list) -> dict:
    """حساب بروفايل جدول وتخزينه — يتخطى الكتابة لو البصمة لم تتغير ولا يوجد تلميح تقادم."""
    df = db.get_clean_data(alias)
    if df is None:
        return {"ok": False, "error": f"تعذر قراءة الجدول '{alias}'"}
    prof = _TableProfiler(df)
    try:
        sig = prof.signature()
        with _open(db) as conn:
            old = conn.execute(
                "SELECT data_signature, stale FROM project_knowledge WHERE scope='table' AND table_alias=? "
                "AND layer='profile' AND item_key=''", (alias,)).fetchone()
            if old and old["data_signature"] == sig and not old["stale"] and not force:
                _sync_table_stale(conn, alias, sig)
                return {"ok": True, "changed": False}
        table, cps = prof.profile(alias, relations)
        changed = not old or old["data_signature"] != sig
        with _open(db) as conn:
            _upsert(conn, "table", alias, "", LAYER_PROFILE, "", table, sig, "stats")
            for c, cp in cps.items():
                _upsert(conn, "column", alias, c, LAYER_PROFILE, "", cp, sig, "stats")
            marks = ",".join("?" * len(cps)) or "''"
            conn.execute(f"DELETE FROM project_knowledge WHERE scope='column' AND table_alias=? "
                         f"AND column_name NOT IN ({marks})", (alias, *cps.keys()))
            _sync_table_stale(conn, alias, sig)
        return {"ok": True, "changed": changed}
    finally:
        prof.close()


# ══════════════════════════════════════════════════════════════
#  أدوات AI المشتركة للطبقتين ب وج (حرارة 0.0 صريحة)
# ══════════════════════════════════════════════════════════════

_AI_TEMPERATURE = 0.0


def _send_ai(ai, prompt: str, start: float) -> dict:
    """إرسال prompt بحرارة 0.0 مع احترام max_tries/retry_delay/story_timeout والميزانية الكلية."""
    last = ""
    for attempt in range(1, ai.max_tries + 1):
        r = ai.engine.send(prompt, _AI_TEMPERATURE, timeout_override=ai.story_timeout)
        if r["ok"]:
            return r
        last = r.get("error", "")
        if r.get("error_type") == "auth":
            break
        if attempt < ai.max_tries:
            if ai._budget_exceeded(start, ai.story_max_total_wait_seconds):
                break
            if ai.retry_delay > 0:
                time.sleep(ai.retry_delay)
    return {"ok": False, "error": last or "فشل الاتصال بمحرك AI"}


def _ask_json(ai, prompt: str, start: float) -> dict:
    """إرسال + تحليل JSON (يُعاد الإرسال عند رد غير صالح حتى max_tries)."""
    last = ""
    for _ in range(ai.max_tries):
        r = _send_ai(ai, prompt, start)
        if not r["ok"]:
            return r
        text = ai.engine.clean_response(r["text"])
        try:
            obj = json.loads(text[text.index("{"):text.rindex("}") + 1])
            if isinstance(obj, dict):
                return {"ok": True, "data": obj}
            last = "الرد ليس كائن JSON"
        except Exception as e:
            last = f"تعذر تحليل الرد كـ JSON: {e}"
    return {"ok": False, "error": last}


def _clip(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _str_list(value, max_items: int, limit: int) -> list:
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        s = _clip(v, limit)
        if s and s not in out:
            out.append(s)
    return out[:max_items]


# ══════════════════════════════════════════════════════════════
#  الطبقة ب — الطبقة الدلالية
# ══════════════════════════════════════════════════════════════

_SEMANTIC_RULES = """أنت محلل بيانات خبير تصف جدولاً في قاعدة بيانات لتساعد نظاماً آخر على كتابة استعلامات صحيحة لاحقاً.
القواعد الصارمة:
1. أجب فقط بصيغة JSON صالحة بدون أي شرح أو Markdown.
2. الشكل المطلوب بالضبط:
{"purpose": "جملة واحدة عن غرض الجدول", "granularity": "ماذا يمثل كل صف", "quality_notes": ["ملاحظة جودة بيانات"],
 "columns": {"<اسم العمود حرفياً>": {"description": "معنى العمود التجاري", "unit": "الوحدة أو العملة أو فارغ",
 "synonyms": ["مرادف عربي أو إنجليزي"], "role": "id|category|measure|date|text|boolean|constant",
 "aggregation": "SUM|AVG|COUNT|COUNT DISTINCT|MIN|MAX|NONE", "warnings": ["تحذير استخدام"]}}}
3. استخدم أسماء الأعمدة حرفياً كما وردت وغطِّ كل عمود في القائمة. لا تخترع أعمدة.
4. aggregation هي الطريقة الصحيحة لتجميع العمود: SUM للكميات والمبالغ القابلة للجمع، AVG للأسعار والمتوسطات،
   COUNT DISTINCT للمعرفات التي تُعدّ، NONE لما لا يُجمّع (معرّفات، نسب مئوية، تواريخ، نصوص).
5. أضف تحذيراً عند وجود فخ (مثل: عمود نسبة لا يُجمع، قيمة فارغة كثيرة، سعر وحدة وليس إجمالياً).
6. اعتمد على الإحصاءات والعينات المعطاة فقط ولا تفترض معلومات خارجها. مرادفات حتى 5، بالعربية والإنجليزية معاً عند الإمكان.
7. role وaggregation المقترحان يجب أن يتسقا مع الإحصاءات (مثلاً عمود نصي فريد بالكامل غالباً id)."""


def _fmt_col_for_ai(cp: dict, send_values: bool) -> str:
    line = f"- {cp['name']} [{cp.get('dtype')}] role={cp.get('role')} null={cp.get('null_pct')}% distinct={cp.get('distinct')}"
    if cp.get("kind") == "numeric" and cp.get("min") is not None:
        line += f" min={cp['min']} max={cp['max']} mean={cp.get('mean')} median={cp.get('median')} zero%={cp.get('zero_pct')} neg%={cp.get('negative_pct')}"
    elif cp.get("kind") == "date" and cp.get("min"):
        line += f" من {str(cp['min'])[:10]} إلى {str(cp['max'])[:10]} ({cp.get('resolution')})"
    elif cp.get("kind") == "text" and cp.get("avg_length") is not None:
        line += f" avg_len={cp['avg_length']}"
    if send_values:
        if cp.get("top_values"):
            line += " قيم: " + ", ".join(f"{v['value'][:25]}({v['count']})" for v in cp["top_values"][:10])
        elif cp.get("sample_values"):
            line += " عينة: " + ", ".join(v[:25] for v in cp["sample_values"][:5])
    return line


def _sample_rows(db, alias: str, cols: list, n: int = _SAMPLE_ROWS_FOR_AI) -> list:
    try:
        with _open(db) as conn:
            sel = ", ".join(_q(c) for c in cols)
            rows = conn.execute(f'SELECT {sel} FROM {_q("data_" + alias)} LIMIT {int(n)}').fetchall()
        return [[_json_safe(v) for v in r] for r in rows]
    except Exception as e:
        logger.debug("sample rows skipped for %s: %s", alias, e)
        return []


def _semantic_prompt(db, alias, table, cps, chunk, send_values, known_purpose="") -> str:
    parts = [f"[RULES]\n{_SEMANTIC_RULES}",
             f"[TABLE]\nالجدول: {alias} — {table.get('rows')} صف، {table.get('cols')} عمود، صفوف مكررة: {table.get('duplicate_rows')}"]
    if table.get("relations"):
        parts[-1] += "\nعلاقات: " + " ; ".join(table["relations"])
    if known_purpose:
        parts[-1] += f"\nغرض الجدول المعروف مسبقاً (لا تعدّله): {known_purpose}"
    parts.append("[COLUMNS — صِف هذه الأعمدة فقط]\n" + "\n".join(_fmt_col_for_ai(cps[c], send_values) for c in chunk))
    if send_values:
        rows = _sample_rows(db, alias, chunk)
        if rows:
            lines = [" | ".join(chunk)] + [" | ".join(_clip(v, 40) for v in r) for r in rows]
            parts.append("[SAMPLE ROWS]\n" + "\n".join(lines))
    else:
        parts.append("[NOTE]\nلا تتوفر قيم فعلية لأسباب الخصوصية؛ اعتمد على أسماء الأعمدة والإحصاءات فقط.")
    if table.get("correlations"):
        parts.append("[CORRELATIONS]\n" + "\n".join(f"{c['a']} ~ {c['b']}: {c['r']}" for c in table["correlations"][:5]))
    parts.append("JSON:")
    return "\n\n".join(parts)


def _validate_semantic(obj: dict, cps: dict, chunk: list) -> dict:
    """تحقق برمجي: أي عمود غير موجود في الجدول يُهمَل، والقيم تُقيَّد بالمسموح."""
    lookup = {c.casefold(): c for c in chunk}
    cols = {}
    raw = obj.get("columns")
    for name, item in (raw.items() if isinstance(raw, dict) else []):
        real = lookup.get(str(name).strip().casefold())
        if real is None or not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        agg = str(item.get("aggregation", "")).strip().upper()
        agg = {"": "NONE", "NO": "NONE", "NULL": "NONE", "DISTINCT COUNT": "COUNT DISTINCT"}.get(agg, agg)
        cols[real] = {
            "description": _clip(item.get("description"), 300),
            "unit": _clip(item.get("unit"), 40),
            "synonyms": _str_list(item.get("synonyms"), 8, 40),
            "role": role if role in ROLES else cps[real].get("role", "text"),
            "aggregation": agg if agg in AGGREGATIONS else "NONE",
            "warnings": _str_list(item.get("warnings"), 4, 160),
            "from_ai": True,
        }
    return {"purpose": _clip(obj.get("purpose"), 300), "granularity": _clip(obj.get("granularity"), 200),
            "quality_notes": _str_list(obj.get("quality_notes"), 6, 200), "columns": cols}


def _layer_b_table(db, ai, alias, table, cps, send_values, start) -> dict:
    """استدعاء/استدعاءات AI لوصف جدول (تُقسَّم الأعمدة الكثيرة على دفعات). لا تكتب في DB."""
    order = [c for c in table.get("column_order", list(cps)) if c in cps]
    result = {"purpose": "", "granularity": "", "quality_notes": [], "columns": {}}
    errors = []
    for i in range(0, max(len(order), 1), _SEMANTIC_CHUNK_COLUMNS):
        chunk = order[i:i + _SEMANTIC_CHUNK_COLUMNS]
        if not chunk:
            break
        prompt = _semantic_prompt(db, alias, table, cps, chunk, send_values, result["purpose"] if i else "")
        r = _ask_json(ai, prompt, start)
        if not r["ok"]:
            errors.append(r["error"])
            continue
        v = _validate_semantic(r["data"], cps, chunk)
        if i == 0:
            result.update({k: v[k] for k in ("purpose", "granularity", "quality_notes")})
        result["columns"].update(v["columns"])
    if not result["columns"] and not result["purpose"]:
        return {"ok": False, "alias": alias, "error": f"الجدول '{alias}': " + (errors[0] if errors else "رد AI غير صالح")}
    return {"ok": True, "alias": alias, "result": result, "partial_error": errors[0] if errors else ""}


def _store_semantic(db, alias: str, res: dict, sig: str) -> None:
    with _open(db) as conn:
        _upsert(conn, "table", alias, "", LAYER_SEMANTIC, "",
                {k: res[k] for k in ("purpose", "granularity", "quality_notes")}, sig, "ai")
        existing = {r["column_name"]: r for r in _select(
            conn, "scope='column' AND layer='semantic' AND table_alias=?", (alias,))}
        for col, sem in res["columns"].items():
            content, source, edited = dict(sem), "ai", 0
            old = existing.get(col)
            if old and old["user_edited"]:
                oc = old["content"]
                fields = [f for f in (oc.get("user_fields") or []) if f in EDITABLE_FIELDS]
                content["ai_original"] = {f: sem.get(f) for f in fields}
                for f in fields:
                    content[f] = oc.get(f, content.get(f))
                content["user_fields"] = fields
                source, edited = "user", 1
            _upsert(conn, "column", alias, col, LAYER_SEMANTIC, "", content, sig, source, edited)


_PROJECT_RULES = """أنت محلل بيانات تلخّص مشروع بيانات متعدد الجداول.
أجب فقط بصيغة JSON صالحة بدون شرح أو Markdown، بالشكل:
{"summary": "فقرة عربية قصيرة (3-5 جمل) عن موضوع المشروع وما يمكن تحليله", "entities": [{"name": "كيان", "tables": ["اسم جدول"], "description": "وصف قصير"}],
 "relationships": ["وصف علاقة بين كيانين أو جدولين"]}
استخدم أسماء الجداول حرفياً من القائمة فقط ولا تخترع جداول."""


def _layer_b_project(ai, tables: dict, relations: list, start: float) -> dict:
    lines = []
    for alias, t in tables.items():
        lines.append(f"- {alias}: {t.get('purpose', '')} | الحبيبة: {t.get('granularity', '')} | {t.get('rows')} صف")
    parts = [f"[RULES]\n{_PROJECT_RULES}", "[TABLES]\n" + "\n".join(lines)]
    if relations:
        parts.append("[RELATIONS]\n" + "\n".join(
            f"{r['from_table']}.{r['from_col']} = {r['to_table']}.{r['to_col']}" for r in relations))
    parts.append("JSON:")
    r = _ask_json(ai, "\n\n".join(parts), start)
    if not r["ok"]:
        return r
    d = r["data"]
    known = {a.casefold(): a for a in tables}
    entities = []
    for e in d.get("entities") if isinstance(d.get("entities"), list) else []:
        if not isinstance(e, dict):
            continue
        tabs = [known[str(t).strip().casefold()] for t in (e.get("tables") or []) if str(t).strip().casefold() in known]
        if e.get("name") and tabs:
            entities.append({"name": _clip(e["name"], 60), "tables": tabs, "description": _clip(e.get("description"), 200)})
    summary = _clip(d.get("summary"), 1200)
    if not summary:
        return {"ok": False, "error": "ملخص المشروع فارغ في رد AI"}
    return {"ok": True, "content": {"summary": summary, "entities": entities[:10],
                                    "relationships": _str_list(d.get("relationships"), 8, 200)}}


# ══════════════════════════════════════════════════════════════
#  الطبقة ج — Insights (AI + SQL منفَّذ فعلياً)
# ══════════════════════════════════════════════════════════════

_INSIGHT_PLAN_RULES = """أنت محلل بيانات محترف. مهمتك: اقتراح استعلامات SQL تكشف أنماطاً مفيدة في بيانات المشروع كاملة.
القواعد الصارمة:
1. أجب فقط بصيغة JSON صالحة بدون شرح أو Markdown، بالشكل:
{"queries": [{"title": "عنوان قصير", "sql": "SELECT ..."}]}
2. عدد الاستعلامات من 1 إلى __K__، وكل واحد يكشف نمطاً مختلفاً من: تركّز (حصة أكبر الفئات من الإجمالي)، اتجاه زمني
   (حسب الشهر أو السنة)، قيم شاذة (تجميع/عدّ لما يتجاوز المتوسط بعدة انحرافات)، مقارنة بين فئات، فجوات (فترات أو قيم مفقودة).
3. كل SQL هو SELECT فقط، متوافق مع DuckDB، ويستخدم الجداول والأعمدة الموجودة في SCHEMA بأسمائها حرفياً.
4. استخدم التجميع (GROUP BY مع SUM/AVG/COUNT) بدل إرجاع الصفوف الخام، وأعطِ كل عمود ناتج اسماً واضحاً (AS).
5. استعن بقسم DATA CONTEXT لمعرفة أسماء القيم الفئوية وطريقة التجميع الصحيحة وتحذيرات كل عمود.
6. لا تكرر استعلاماً بصياغة مختلفة."""

_INSIGHT_WRITE_RULES = """أنت محلل بيانات تكتب استنتاجات (Insights) عربية قصيرة من نتائج استعلامات نُفِّذت فعلاً.
أجب فقط بصيغة JSON صالحة بدون شرح أو Markdown، بالشكل:
{"insights": [{"title": "عنوان قصير", "text": "جملة إلى ثلاث جمل بأرقام فعلية", "importance": "high|medium|low", "evidence_query_index": 0}]}
القواعد الصارمة:
1. كل insight يستند إلى استعلام واحد من القسم DATA ويذكر رقمه في evidence_query_index.
2. الأرقام في النص يجب أن تكون من نتيجة ذلك الاستعلام حرفياً، أو مشتقة منها حسابياً بشكل مباشر (نسبة، مجموع، فرق). لا تذكر أي رقم غير موجود.
3. لا تستخدم معلومات من خارج النتائج، ولا تجزم بسبب ما (استخدم "يبدو أن" عند الاحتمال).
4. الحد الأقصى __N__ insight، والأهم أولاً. إن لم تجد شيئاً جديراً بالذكر فأعد قائمة فارغة."""

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


def _numbers_in(text) -> list:
    out = []
    for tok in _NUM_RE.findall(str(text).translate(_AR_DIGITS)):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            pass
    return out


def _evidence_numbers(df: pd.DataFrame) -> list:
    """كل الأرقام المسموحة لنص insight: خلايا النتيجة + مجموع/متوسط/أدنى/أعلى كل عمود رقمي + حصص % + عدد الصفوف."""
    cands = [float(len(df))]
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_bool_dtype(s.dtype):
            continue
        if pd.api.types.is_numeric_dtype(s.dtype):
            vals = pd.to_numeric(s, errors="coerce").dropna().astype(float).tolist()[:2000]
            cands += vals
            if vals:
                tot = sum(vals)
                cands += [tot, tot / len(vals), min(vals), max(vals)]
                if tot:
                    cands += [100 * v / tot for v in vals]
                cands += [vals[0] - vals[-1], abs(vals[0] - vals[-1])]
        else:
            for v in s.dropna().astype(str).head(500):
                cands += _numbers_in(v)
    return [v for v in cands if not math.isnan(v) and not math.isinf(v)]


def _number_supported(x: float, cands: list) -> bool:
    if abs(x) < 10 and float(x).is_integer():
        return True   # ترتيب/عدد صغير لا يُعدّ ادعاءً رقمياً
    tol = max(0.051, 0.006 * abs(x))
    return any(abs(x - c * k) <= tol for c in cands for k in (1.0, 1e-3, 1e-6))


def _validate_insight(item, results: dict) -> Optional[dict]:
    """رفض أي insight بلا دليل صالح أو بأرقام غير مدعومة بنتيجة الاستعلام."""
    if not isinstance(item, dict):
        return None
    title, text = _clip(item.get("title"), 90), _clip(item.get("text"), 600)
    try:
        idx = int(item.get("evidence_query_index"))
    except (TypeError, ValueError):
        return None
    ev = results.get(idx)
    if not title or not text or ev is None:
        return None
    cands = _evidence_numbers(ev["df"])
    bad = [x for x in _numbers_in(text) if not _number_supported(x, cands)]
    if bad:
        logger.info("insight rejected (unsupported numbers %s): %s", bad[:3], title)
        return None
    imp = str(item.get("importance", "medium")).lower()
    df = ev["df"].head(KNOWLEDGE_INSIGHT_SNAPSHOT_ROWS)
    return {"title": title, "text": text, "importance": imp if imp in ("high", "medium", "low") else "medium",
            "evidence_index": idx, "evidence_title": ev["title"], "evidence_sql": ev["sql"],
            "snapshot": {"columns": [str(c) for c in df.columns],
                         "rows": _sanitize_rows(df.to_dict(orient="records")), "total_rows": int(len(ev["df"]))}}


def _layer_c(db, km, ai, start: float) -> dict:
    from ai.prompt_builder import PromptBuilder, SQL_COMMON_CONSTRAINTS
    schema = db.get_schema()
    if not schema:
        return {"ok": False, "error": "لا توجد جداول لتوليد الـ insights"}
    relations = db.get_relations()
    builder = PromptBuilder(schema=schema, relations=relations, ai_rules=None)
    ctx = km.build_context("", schema, KNOWLEDGE_CONTEXT_MAX_CHARS, mode="plan", ignore_setting=True)

    parts = [builder._section("RULES", _INSIGHT_PLAN_RULES.replace("__K__", str(KNOWLEDGE_INSIGHT_QUERIES))),
             builder._section("SQL CONSTRAINTS", SQL_COMMON_CONSTRAINTS),
             builder._section("SCHEMA", builder._build_schema())]
    if relations:
        parts.append(builder._section("RELATIONS", builder._build_relations()))
    parts.extend(builder._knowledge_parts(ctx))
    parts.append("JSON PLAN:")
    r = _send_ai(ai, "\n\n".join(parts), start)
    if not r["ok"]:
        return {"ok": False, "error": f"تخطيط الـ insights: {r['error']}"}
    parsed = ai._parse_story_queries(r["text"], max_queries=KNOWLEDGE_INSIGHT_QUERIES)
    if not parsed["ok"]:
        return {"ok": False, "error": f"تخطيط الـ insights: {parsed['error']}"}

    results, failed = {}, []
    for i, q in enumerate(parsed["queries"]):
        run = ai._run_planned_query(
            q["sql"], q["title"], "اكتشاف أنماط ورؤى مفيدة في البيانات", builder, schema,
            dashboard_id=None, allow_ai_fix=True, start_time=start,
            budget=ai.story_max_total_wait_seconds, temperature=_AI_TEMPERATURE)
        if run["ok"]:
            results[i] = {"title": q["title"], "sql": run.get("sql", q["sql"]), "df": run["df"]}
        else:
            failed.append(f"{q['title']}: {run.get('error')}")
    if not results:
        return {"ok": False, "error": "فشلت كل استعلامات الـ insights: " + "؛ ".join(failed)[:400]}

    data = []
    for i, ev in results.items():
        df = ev["df"]
        lines = [" | ".join(str(c) for c in df.columns)] + [
            " | ".join(str(v) for v in row) for row in df.head(_INSIGHT_PROMPT_ROWS).itertuples(index=False)]
        note = f"\n(تُعرض أول {_INSIGHT_PROMPT_ROWS} صفاً من {len(df)})" if len(df) > _INSIGHT_PROMPT_ROWS else ""
        data.append(f"-- استعلام {i}: {ev['title']} ({len(df)} صف) --\n" + "\n".join(lines) + note)
    prompt = "\n\n".join([
        builder._section("RULES", _INSIGHT_WRITE_RULES.replace("__N__", str(KNOWLEDGE_MAX_INSIGHTS))),
        builder._section("DATA", "\n\n".join(data)), "JSON:"])
    w = _ask_json(ai, prompt, start)
    if not w["ok"]:
        return {"ok": False, "error": f"كتابة الـ insights: {w['error']}"}

    raw = w["data"].get("insights")
    valid = [v for v in (_validate_insight(it, results) for it in (raw if isinstance(raw, list) else [])) if v]
    order = {"high": 0, "medium": 1, "low": 2}
    valid.sort(key=lambda v: order[v["importance"]])
    valid = valid[:KNOWLEDGE_MAX_INSIGHTS]
    if not valid:
        return {"ok": False, "error": "لم يُقبل أي insight (لا دليل كافٍ أو أرقام غير مدعومة)"}
    with _open(db) as conn:
        sig = _project_signature(conn)
        conn.execute("DELETE FROM project_knowledge WHERE scope='insight'")
        for i, v in enumerate(valid):
            _upsert(conn, "insight", "", "", LAYER_INSIGHT, f"{i:03d}", v, sig, "ai")
    return {"ok": True, "count": len(valid), "failed_queries": failed}


# ══════════════════════════════════════════════════════════════
#  KnowledgeManager — القراءة والتحرير وبناء السياق
# ══════════════════════════════════════════════════════════════

class KnowledgeManager:
    """
    الاستخدام:
        km = KnowledgeManager(db)
        km.get_profile("sales"); km.get_semantic(); km.get_insights(); km.get_status()
        km.is_stale("sales"); km.mark_stale("sales"); km.drop_for_table("sales"); km.clear_all()
        ctx = km.build_context(question, schema, max_chars, mode="sql"|"answer")
    كل دوال القراءة لا ترمي استثناءً أبداً (قيم فارغة عند أي خلل).
    """

    def __init__(self, db):
        self.db = db

    # ── قراءة ──

    def get_profile(self, alias: Optional[str] = None) -> dict:
        """{alias: {"table": {...محتوى + signature/generated_at/stale}, "columns": {col: {...}}}}"""
        out = {}
        try:
            with _open(self.db) as conn:
                rows = _select(conn, "layer='profile'" + (" AND table_alias=?" if alias else ""),
                               (alias,) if alias else ())
        except Exception as e:
            logger.warning("get_profile failed: %s", e)
            return out
        for r in rows:
            entry = out.setdefault(r["table_alias"], {"table": None, "columns": {}})
            meta = dict(r["content"], signature=r["data_signature"], generated_at=r["generated_at"], stale=r["stale"])
            if r["scope"] == "table":
                entry["table"] = meta
            else:
                entry["columns"][r["column_name"]] = meta
        return {a: e for a, e in out.items() if e["table"] is not None}

    def get_semantic(self, alias: Optional[str] = None) -> dict:
        """{"project": {...}|None, "tables": {alias: {"table": {...}, "columns": {col: {...}}}}}"""
        out = {"project": None, "tables": {}}
        try:
            with _open(self.db) as conn:
                rows = _select(conn, "layer='semantic'")
        except Exception as e:
            logger.warning("get_semantic failed: %s", e)
            return out
        for r in rows:
            meta = dict(r["content"], source=r["source"], user_edited=r["user_edited"],
                        generated_at=r["generated_at"], stale=r["stale"], signature=r["data_signature"])
            if r["scope"] == "project":
                out["project"] = meta
            elif alias is None or r["table_alias"] == alias:
                entry = out["tables"].setdefault(r["table_alias"], {"table": None, "columns": {}})
                if r["scope"] == "table":
                    entry["table"] = meta
                else:
                    entry["columns"][r["column_name"]] = meta
        return out

    def get_insights(self) -> list:
        try:
            with _open(self.db) as conn:
                rows = _select(conn, "scope='insight' ORDER BY item_key")
        except Exception as e:
            logger.warning("get_insights failed: %s", e)
            return []
        return [dict(r["content"], item_key=r["item_key"], generated_at=r["generated_at"], stale=r["stale"])
                for r in sorted(rows, key=lambda x: x["item_key"])]

    def get_status(self) -> dict:
        """حالة آخر مهمة + ملخص الحالة (مع إنهاء المهمة الميتة)."""
        status = {"state": "idle", "stage": "", "progress_done": 0, "progress_total": 0, "started_at": "",
                  "finished_at": "", "error": "", "layers": [], "triggered_by": "", "pending": False,
                  "stale": False, "has_profile": False, "has_semantic": False, "n_insights": 0}
        try:
            self._expire_dead_job()
            with _open(self.db) as conn:
                row = conn.execute("SELECT * FROM knowledge_jobs WHERE project_id=?", (self.db.project_id,)).fetchone()
                if row:
                    status.update({k: row[k] for k in ("state", "stage", "progress_done", "progress_total",
                                                       "started_at", "finished_at", "error", "triggered_by")})
                    status["layers"] = _loads(row["layers"]) or []
                    status["pending"] = bool(row["pending_json"])
                status["has_profile"] = bool(conn.execute(
                    "SELECT 1 FROM project_knowledge WHERE layer='profile' LIMIT 1").fetchone())
                status["has_semantic"] = bool(conn.execute(
                    "SELECT 1 FROM project_knowledge WHERE layer='semantic' LIMIT 1").fetchone())
                status["n_insights"] = conn.execute(
                    "SELECT COUNT(*) FROM project_knowledge WHERE scope='insight'").fetchone()[0]
            status["stale"] = self.is_stale()
        except Exception as e:
            logger.warning("get_status failed: %s", e)
        return status

    def _expire_dead_job(self) -> None:
        cutoff = (datetime.utcnow() - timedelta(minutes=KNOWLEDGE_JOB_STALE_MINUTES)).isoformat()
        with _open(self.db) as conn:
            conn.execute(
                "UPDATE knowledge_jobs SET state='error', finished_at=?, error='انتهت مهلة المهمة دون اكتمال' "
                "WHERE project_id=? AND state='running' AND started_at < ?", (_now(), self.db.project_id, cutoff))

    def is_stale(self, alias: Optional[str] = None) -> bool:
        """alias: هل بيانات الجدول تغيّرت منذ توليد معرفته؟ بدون alias: على مستوى المشروع كله."""
        try:
            with _open(self.db) as conn:
                if alias:
                    return bool(conn.execute(
                        "SELECT 1 FROM project_knowledge WHERE table_alias=? AND stale=1 LIMIT 1", (alias,)).fetchone())
                return bool(conn.execute("SELECT 1 FROM project_knowledge WHERE stale=1 LIMIT 1").fetchone())
        except Exception:
            return False

    def table_state(self) -> dict:
        """{alias: {"profile": bool, "profile_stale": bool, "semantic": bool, "semantic_stale": bool}}"""
        out = {}
        with _open(self.db) as conn:
            for r in conn.execute("SELECT table_alias, layer, stale FROM project_knowledge "
                                  "WHERE scope='table' AND layer IN ('profile','semantic')").fetchall():
                e = out.setdefault(r["table_alias"], {"profile": False, "profile_stale": False,
                                                      "semantic": False, "semantic_stale": False})
                e[r["layer"]] = True
                e[r["layer"] + "_stale"] = bool(r["stale"])
        return out

    # ── علامات التقادم والحذف ──

    def mark_stale(self, alias: str) -> None:
        """تلميح فوري (بلا حساب) بأن بيانات الجدول تغيّرت — لا يرمي استثناءً."""
        try:
            with _open(self.db) as conn:
                conn.execute("UPDATE project_knowledge SET stale=1 WHERE table_alias=? OR scope IN ('project','insight')",
                             (alias,))
        except Exception as e:
            logger.warning("mark_stale failed (%s): %s", alias, e)

    def drop_for_table(self, alias: str) -> None:
        try:
            with _open(self.db) as conn:
                conn.execute("DELETE FROM project_knowledge WHERE table_alias=?", (alias,))
                conn.execute("UPDATE project_knowledge SET stale=1 WHERE scope IN ('project','insight')")
        except Exception as e:
            logger.warning("drop_for_table failed (%s): %s", alias, e)

    def clear_all(self) -> None:
        try:
            with _open(self.db) as conn:
                conn.execute("DELETE FROM project_knowledge")
                conn.execute("DELETE FROM knowledge_jobs WHERE project_id=? AND state<>'running'", (self.db.project_id,))
        except Exception as e:
            logger.warning("clear_all failed: %s", e)

    # ── تحرير المستخدم ──

    def save_user_edit(self, alias: str, column: str, fields: dict) -> dict:
        """حفظ تعديل يدوي لوصف/مرادفات/تجميع عمود (user_edited=1)."""
        try:
            clean = {}
            if "description" in fields:
                clean["description"] = _clip(fields["description"], 400)
            if "synonyms" in fields:
                syn = fields["synonyms"]
                if isinstance(syn, str):
                    syn = re.split(r"[,،;\n]", syn)
                clean["synonyms"] = _str_list(syn, 12, 40)
            if "aggregation" in fields:
                agg = str(fields["aggregation"]).upper()
                clean["aggregation"] = agg if agg in AGGREGATIONS else "NONE"
            if not clean:
                return {"ok": False, "error": "لا توجد حقول للحفظ"}
            with _open(self.db) as conn:
                row = conn.execute(
                    "SELECT content_json, data_signature FROM project_knowledge WHERE scope='column' AND table_alias=? "
                    "AND column_name=? AND layer='semantic' AND item_key=''", (alias, column)).fetchone()
                content = _loads(row["content_json"]) if row else {"from_ai": False}
                sig = row["data_signature"] if row else (conn.execute(
                    "SELECT data_signature FROM project_knowledge WHERE scope='table' AND table_alias=? AND layer='profile'",
                    (alias,)).fetchone() or [""])[0]
                user_fields = [f for f in (content.get("user_fields") or []) if f in EDITABLE_FIELDS]
                original = dict(content.get("ai_original") or {})
                for f, v in clean.items():
                    if f not in user_fields:
                        original[f] = content.get(f)
                        user_fields.append(f)
                    content[f] = v
                content.update({"user_fields": user_fields, "ai_original": original})
                _upsert(conn, "column", alias, column, LAYER_SEMANTIC, "", content, sig, "user", 1)
            return {"ok": True}
        except Exception as e:
            logger.error("save_user_edit failed: %s", e)
            return {"ok": False, "error": str(e)}

    def restore_ai(self, alias: str, column: str) -> dict:
        """استعادة قيم AI لعمود (أو حذف صفّه لو أُنشئ يدوياً بلا توليد AI)."""
        try:
            with _open(self.db) as conn:
                row = conn.execute(
                    "SELECT content_json, data_signature FROM project_knowledge WHERE scope='column' AND table_alias=? "
                    "AND column_name=? AND layer='semantic' AND item_key=''", (alias, column)).fetchone()
                if not row:
                    return {"ok": True}
                content = _loads(row["content_json"])
                if not content.get("from_ai"):
                    conn.execute("DELETE FROM project_knowledge WHERE scope='column' AND table_alias=? "
                                 "AND column_name=? AND layer='semantic'", (alias, column))
                    return {"ok": True}
                for f, v in (content.get("ai_original") or {}).items():
                    content[f] = v
                content.pop("ai_original", None)
                content["user_fields"] = []
                _upsert(conn, "column", alias, column, LAYER_SEMANTIC, "", content, row["data_signature"], "ai", 0)
            return {"ok": True}
        except Exception as e:
            logger.error("restore_ai failed: %s", e)
            return {"ok": False, "error": str(e)}

    # ── بناء السياق للـ prompts ──

    def build_context(self, question: str, schema: Optional[dict] = None,
                      max_chars: int = KNOWLEDGE_CONTEXT_MAX_CHARS, mode: str = "sql",
                      filtered: bool = False, ignore_setting: bool = False) -> str:
        """
        نص معرفة مضغوط ضمن max_chars للـ prompt، أو "" (بصمت) إن لم توجد معرفة أو
        عُطّل use_knowledge أو حدث أي خلل.
        mode: sql (أوصاف أعمدة + قيم فئوية + تعليمات تجميع) | answer (ملخص عام +
        أغراض الجداول + insights ذات الصلة) | plan (مثل sql بلا insights، لتخطيط الطبقة ج).
        """
        try:
            settings = self.db.get_settings()
            if not ignore_setting and not settings.get("use_knowledge", True):
                return ""
            send_values = bool(settings.get("knowledge_send_values_to_ai", True))
            prof, sem = self.get_profile(), self.get_semantic()
            if not prof and not sem["tables"] and not sem["project"]:
                return ""
            aliases = [a for a in (schema.keys() if schema else prof.keys()) if a in prof or a in sem["tables"]]
            if not aliases:
                return ""
            qtok = _tokens(question)

            def score(*texts) -> int:
                toks = set()
                for t in texts:
                    toks |= _tokens(t)
                return sum(1 for q in qtok if q in toks or (len(q) >= 4 and any(q in t or t in q for t in toks if len(t) >= 4)))

            scored = []
            for pos, a in enumerate(aliases):
                st = sem["tables"].get(a, {"table": {}, "columns": {}})
                cols = {}
                for c, cp in prof.get(a, {"columns": {}})["columns"].items():
                    s = st["columns"].get(c, {})
                    cols[c] = score(c, " ".join(s.get("synonyms", [])), s.get("description", ""))
                tscore = score(a, (st["table"] or {}).get("purpose", "")) + (max(cols.values()) if cols else 0)
                scored.append((-tscore, pos, a, cols))
            scored.sort()

            head = ["ملف معرفة المشروع — مستخرج مسبقاً من البيانات الكاملة غير المفلترة، وليس مصدراً لأرقام الإجابة."]
            if filtered:
                head.append("تنبيه: بيانات هذا الاستعلام مفلترة؛ الإحصاءات والقيم أدناه تخص البيانات الكاملة وقد تختلف.")
            blocks = ["\n".join(head)]
            if sem["project"] and sem["project"].get("summary"):
                blocks.append("ملخص المشروع: " + sem["project"]["summary"][:400 if mode == "sql" else 900])

            for _, _, a, colscore in scored:
                blocks.append(self._table_block(a, prof.get(a), sem["tables"].get(a), colscore, mode, send_values))

            if mode == "answer" and send_values:
                ins = [(score(i.get("title"), i.get("text")), i) for i in self.get_insights()]
                ins = [i for s, i in sorted(ins, key=lambda x: -x[0]) if s > 0][:5]
                if ins:
                    blocks.append("ملاحظات سابقة (استنتاجات آلية قد لا تكون محدّثة):\n" +
                                  "\n".join(f"• {i['title']}: {i['text']}" for i in ins))

            out, used = [], 0
            for b in blocks:
                if used + len(b) + 2 > max_chars:
                    room = max_chars - used - 2
                    if room > 200:
                        out.append(b[:room].rsplit("\n", 1)[0])
                    break
                out.append(b)
                used += len(b) + 2
            return "\n\n".join(out)
        except Exception as e:
            logger.warning("build_context failed (ignored): %s", e)
            return ""

    @staticmethod
    def _table_block(alias, prof, sem, colscore: dict, mode: str, send_values: bool) -> str:
        st = (sem or {}).get("table") or {}
        t = (prof or {}).get("table") or {}
        stale = " [قد تكون قديمة]" if (st.get("stale") or t.get("stale")) else ""
        line = f"جدول {alias}" + (f": {st['purpose']}" if st.get("purpose") else "")
        if st.get("granularity"):
            line += f" | الحبيبة: {st['granularity']}"
        if t.get("rows") is not None:
            line += f" | {t['rows']} صف"
        lines = [line + stale]
        if t.get("relations"):
            lines.append("علاقات: " + " ; ".join(t["relations"][:6]))
        if mode == "answer":
            matched = [c for c, s in colscore.items() if s > 0]
            cols = matched[:8]
        else:
            order = list(colscore)
            matched = [c for c in order if colscore[c] > 0]
            cols = order if len(order) <= 14 else (matched + [c for c in order if c not in matched
                                                              and (prof["columns"][c].get("role") in ("measure", "date"))])[:24]
        for c in cols:
            cp = (prof or {}).get("columns", {}).get(c, {})
            s = ((sem or {}).get("columns") or {}).get(c, {})
            role = s.get("role") or cp.get("role", "")
            part = f"- {c} ({role}{', ' + s['unit'] if s.get('unit') else ''})"
            if s.get("description"):
                part += f": {s['description']}"
            if s.get("synonyms"):
                part += " | مرادفات: " + "، ".join(s["synonyms"][:5])
            if s.get("aggregation") and s["aggregation"] != "NONE" and mode != "answer":
                part += f" | تجميع: {s['aggregation']}"
            elif s.get("aggregation") == "NONE" and role == "measure" and mode != "answer":
                part += " | لا يُجمَّع"
            if mode != "answer":
                if send_values and cp.get("top_values"):
                    part += " | قيم: " + "، ".join(f"{v['value'][:25]}({v['count']})" for v in cp["top_values"][:10])
                elif cp.get("kind") == "numeric" and cp.get("min") is not None:
                    part += f" | المدى {cp['min']}..{cp['max']}"
                elif cp.get("kind") == "date" and cp.get("min"):
                    part += f" | من {str(cp['min'])[:10]} إلى {str(cp['max'])[:10]}"
            if s.get("warnings"):
                part += " | تحذير: " + "؛ ".join(s["warnings"][:2])
            lines.append(part)
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  المهام الخلفية
# ══════════════════════════════════════════════════════════════

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="knowledge")
_GUARD = threading.Lock()
_ACTIVE: set = set()
_PENDING: dict = {}


def _job_key(db) -> str:
    return f"{db.user_id}/{db.project_id}"


def _job_write(db, sql: str, params: tuple) -> None:
    try:
        with _open(db) as conn:
            conn.execute(sql, params)
    except Exception as e:
        logger.warning("knowledge job state write failed: %s", e)


def _merge_requests(old: Optional[dict], new: dict) -> dict:
    if old is None:
        return new
    return {**new, "layers": old["layers"] | new["layers"], "force": old["force"] or new["force"],
            "tables": None if (old["tables"] is None or new["tables"] is None) else old["tables"] | new["tables"],
            "notes": old["notes"] + [n for n in new["notes"] if n not in old["notes"]]}


def request_refresh(db, ai, layers=ALL_LAYERS, force: bool = False, tables=None,
                    triggered_by: str = "manual") -> dict:
    """
    طلب تحديث المعرفة في الخلفية. يرجع {"ok", "queued", "error"?}.
    ai: AIManager مبني في الـ main thread (build_ai_manager(db)) أو None (الطبقة أ فقط).
    tables: None = كل الجداول، وإلا مجموعة aliases.
    طلب أثناء مهمة جارية لنفس المشروع يُدمج كطلب معلَّق ويُنفَّذ بعدها.
    """
    layers = {l for l in layers if l in ALL_LAYERS}
    if not layers:
        return {"ok": False, "error": "لم يُحدَّد أي طبقة"}
    if not db.get_files():
        return {"ok": False, "error": "لا توجد جداول في المشروع"}

    notes = []
    settings = db.get_settings()
    wants_ai = bool(layers & {LAYER_SEMANTIC, LAYER_INSIGHT})
    if wants_ai:
        engine = getattr(ai, "engine", None) if ai is not None else None
        is_ollama = settings.get("ai_engine") == "ollama"
        if engine is None:
            notes.append("تم تخطي الطبقتين ب وج: محرك AI غير متاح")
            ai = None
        elif not is_ollama and not getattr(engine, "api_key", ""):
            notes.append("تم تخطي الطبقتين ب وج: مفتاح API غير مضبوط")
            ai = None
    req = {"db": db, "ai": ai, "layers": layers, "force": bool(force),
           "tables": None if tables is None else set(tables), "triggered_by": triggered_by, "notes": notes}
    key = _job_key(db)
    with _GUARD:
        if key in _ACTIVE:
            _PENDING[key] = _merge_requests(_PENDING.get(key), req)
            _job_write(db, "UPDATE knowledge_jobs SET pending_json=? WHERE project_id=?",
                       (_dumps({"layers": sorted(_PENDING[key]["layers"]), "force": _PENDING[key]["force"]}), db.project_id))
            return {"ok": True, "queued": True}
        _ACTIVE.add(key)
    try:
        _write_job_start(db, req)
        _submit(req)
    except Exception as e:
        with _GUARD:
            _ACTIVE.discard(key)
        logger.error("request_refresh failed: %s", e)
        return {"ok": False, "error": str(e)}
    return {"ok": True, "queued": False}


def trigger_after_load(db, table_alias: Optional[str] = None, triggered_by: str = "auto") -> dict:
    """
    تشغيل تلقائي بعد رفع/تحديث ملف: الطبقة أ دائماً، و(ب+ج) لو knowledge_auto_refresh مفعّل.
    تُبنى AIManager هنا (الـ main thread) قبل الإرسال للخلفية.
    """
    try:
        settings = db.get_settings()
        if table_alias:
            KnowledgeManager(db).mark_stale(table_alias)
        layers, ai = [LAYER_PROFILE], None
        if settings.get("knowledge_auto_refresh", True):
            from ai.ai_manager import build_ai_manager
            ai, _ = build_ai_manager(db)
            layers += [LAYER_SEMANTIC, LAYER_INSIGHT]
        return request_refresh(db, ai, layers, force=False,
                               tables=[table_alias] if table_alias else None, triggered_by=triggered_by)
    except Exception as e:
        logger.error("trigger_after_load failed: %s", e)
        return {"ok": False, "error": str(e)}


def _submit(req: dict) -> None:
    ctx = contextvars.copy_context()
    _EXECUTOR.submit(ctx.run, _run_job, req)


def _write_job_start(db, req: dict) -> None:
    with _open(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO knowledge_jobs (project_id, state, stage, progress_done, progress_total,"
            " started_at, finished_at, error, layers, triggered_by, pending_json)"
            " VALUES (?, 'running', 'في الانتظار', 0, 0, ?, '', '', ?, ?, '')",
            (db.project_id, _now(), _dumps(sorted(req["layers"])), req["triggered_by"]))


def _run_job(req: dict) -> None:
    db, key = req["db"], _job_key(req["db"])
    outcome = {"state": "error", "stage": "فشل غير متوقع", "error": ""}
    try:
        outcome = _execute(req)
    except Exception as e:
        logger.exception("knowledge job crashed")
        outcome = {"state": "error", "stage": "فشل غير متوقع", "error": str(e)[:500]}
    finally:
        _job_write(db, "UPDATE knowledge_jobs SET state=?, stage=?, error=?, finished_at=? WHERE project_id=?",
                   (outcome["state"], outcome["stage"], outcome["error"][:1500], _now(), db.project_id))
        _release_and_chain(key)


def _release_and_chain(key: str) -> None:
    with _GUARD:
        nxt = _PENDING.pop(key, None)
        if nxt is None:
            _ACTIVE.discard(key)
            return
    try:
        _write_job_start(nxt["db"], nxt)
        _submit(nxt)
    except Exception as e:
        logger.error("pending knowledge job failed to start: %s", e)
        with _GUARD:
            _ACTIVE.discard(key)


class _Progress:
    def __init__(self, db, total: int):
        self.db, self.done, self.total = db, 0, total

    def step(self, stage: str, advance: bool = True, total: Optional[int] = None) -> None:
        if advance:
            self.done += 1
        if total is not None:
            self.total = total
        _job_write(self.db, "UPDATE knowledge_jobs SET stage=?, progress_done=?, progress_total=? WHERE project_id=?",
                   (stage, self.done, max(self.total, self.done), self.db.project_id))


def _execute(req: dict) -> dict:
    db, ai, layers, force, only = req["db"], req["ai"], req["layers"], req["force"], req["tables"]
    km = KnowledgeManager(db)
    settings = db.get_settings()
    send_values = bool(settings.get("knowledge_send_values_to_ai", True))
    aliases = [f["table_alias"] for f in db.get_files()]
    if not aliases:
        return {"state": "done", "stage": "لا توجد جداول", "error": ""}
    relations = db.get_relations()
    target = [a for a in aliases if only is None or a in only]
    errors, produced = list(req["notes"]), 0
    want_a, want_b = LAYER_PROFILE in layers, LAYER_SEMANTIC in layers and ai is not None
    want_c = LAYER_INSIGHT in layers and ai is not None and send_values
    if LAYER_INSIGHT in layers and ai is not None and not send_values:
        errors.append("الطبقة ج معطّلة: إعداد إرسال القيم للـ AI مُوقَف")
    start = time.monotonic()

    state = km.table_state()
    a_tables = [a for a in aliases if (want_a and a in target) or
                ((want_a or want_b or want_c) and not state.get(a, {}).get("profile"))]
    prog = _Progress(db, len(a_tables) + (len(target) + 1 if want_b else 0) + (1 if want_c else 0))
    prog.step("الطبقة أ: البروفايل الإحصائي", advance=False)

    for a in a_tables:
        prog.step(f"الطبقة أ — {a}", advance=False)
        try:
            r = _layer_a(db, a, force, relations)
            if r["ok"]:
                produced += 1
            else:
                errors.append(r["error"])
        except Exception as e:
            logger.exception("layer A failed for %s", a)
            errors.append(f"الطبقة أ ({a}): {e}"[:300])
        prog.step(f"الطبقة أ — {a}")
    try:
        with _open(db) as conn:
            _sync_project_stale(conn)
    except Exception as e:
        logger.warning("project stale sync failed: %s", e)

    if want_b:
        state = km.table_state()
        b_tables = [a for a in target if state.get(a, {}).get("profile") and
                    (force or not state[a]["semantic"] or state[a]["semantic_stale"])]
        prog.step("الطبقة ب: الوصف الدلالي", advance=False, total=prog.done + len(b_tables) + 1 + (1 if want_c else 0))
        prof = km.get_profile()
        workers = 1 if settings.get("ai_engine") == "ollama" else 2
        written = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="knowledge-b") as pool:
            futs = {}
            for a in b_tables:
                ctx = contextvars.copy_context()
                futs[pool.submit(ctx.run, _layer_b_table, db, ai, a, prof[a]["table"], prof[a]["columns"],
                                 send_values, start)] = a
            for f in as_completed(futs):
                a = futs[f]
                try:
                    r = f.result()
                except Exception as e:
                    r = {"ok": False, "error": f"الجدول '{a}': {e}"}
                if r["ok"]:
                    _store_semantic(db, a, r["result"], prof[a]["table"]["signature"])
                    produced += 1
                    written += 1
                    if r.get("partial_error"):
                        errors.append(f"{a}: بعض الأعمدة لم تُوصَف ({r['partial_error'][:120]})")
                else:
                    errors.append(r["error"][:300])
                prog.step(f"الطبقة ب — {a}")

        sem = km.get_semantic()
        tables_sem = {a: dict(sem["tables"][a]["table"], rows=prof[a]["table"].get("rows"))
                      for a in aliases if a in sem["tables"] and sem["tables"][a]["table"] and a in prof}
        need_summary = tables_sem and (force or written or not sem["project"] or sem["project"]["stale"])
        prog.step("الطبقة ب — ملخص المشروع", advance=False)
        if need_summary:
            r = _layer_b_project(ai, tables_sem, relations, start)
            if r["ok"]:
                with _open(db) as conn:
                    _upsert(conn, "project", "", "", LAYER_SEMANTIC, "", r["content"], _project_signature(conn), "ai")
                produced += 1
            else:
                errors.append(f"ملخص المشروع: {r['error']}"[:300])
        prog.step("الطبقة ب — ملخص المشروع")

    if want_c:
        need = force or not km.get_insights() or km.is_stale()
        prog.step("الطبقة ج: الـ Insights", advance=False)
        if need:
            try:
                r = _layer_c(db, km, ai, start)
                if r["ok"]:
                    produced += 1
                else:
                    errors.append(r["error"][:300])
            except Exception as e:
                logger.exception("layer C failed")
                errors.append(f"الطبقة ج: {e}"[:300])
        prog.step("الطبقة ج: الـ Insights")

    state_name = "done" if not errors else ("partial" if produced else "error")
    return {"state": state_name, "stage": "اكتملت" if not errors else "اكتملت مع ملاحظات", "error": " | ".join(errors)}


_STARTUP_DONE = False


def cleanup_dead_jobs_all() -> int:
    """مرة واحدة لكل عملية تشغيل: أي مهمة running أقدم من الحد في أي مشروع تُعاد إلى error."""
    global _STARTUP_DONE
    if _STARTUP_DONE:
        return 0
    _STARTUP_DONE = True
    cutoff = (datetime.utcnow() - timedelta(minutes=KNOWLEDGE_JOB_STALE_MINUTES)).isoformat()
    fixed = 0
    for path in PROJECTS_DIR.glob("*/*/project.db"):
        try:
            conn = sqlite3.connect(str(path), timeout=5)
            try:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_jobs'").fetchone():
                    cur = conn.execute(
                        "UPDATE knowledge_jobs SET state='error', finished_at=?, error='انتهت مهلة المهمة دون اكتمال' "
                        "WHERE state='running' AND started_at < ?", (_now(), cutoff))
                    fixed += cur.rowcount
                    conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug("cleanup_dead_jobs skipped %s: %s", path, e)
    if fixed:
        logger.info("Dead knowledge jobs reset: %d", fixed)
    return fixed
