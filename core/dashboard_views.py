"""
core/dashboard_views.py
=========================
إدارة "views" الفلترة الخاصة بلوحات المعلومات — طبقة فلترة منفصلة
تماماً عن الاستعلامات (SQL) التي يولّدها الذكاء الاصطناعي.

الفكرة:
--------
لكل لوحة معلومات لها فلاتر (Slicers) نشطة، يُنشأ داخل project.db
(SQLite) view لكل جدول عليه فلتر نشط:

    dview_{dashboard_id}__{table_alias}
        = SELECT * FROM data_{table_alias} WHERE <فلاتر اللوحة>

- الجدول الذي لا فلتر نشط عليه لا يُنشأ له view (يُقرأ الجدول الأصلي).
- الـ views تُعاد بناؤها بالكامل عند أي تعديل على فلاتر اللوحة
  (sync) وتُحذف مع اللوحة (drop)، ومع حذف الجدول المرتبط بها
  (drop_for_table).
- QueryEngine يقرأ البيانات عبر read_table(): لو وُجد view للوحة
  يُحمَّل منه، وإلا من الجدول الأصلي — ثم يُسجَّل في DuckDB بنفس اسم
  الجدول الأصلي. فلا يرى الذكاء الاصطناعي أي فلتر في الـ SQL، ويبقى
  base_sql مستقلاً عن قيم الفلاتر ويصلح لأي فلتر لاحق.
- get_schema() يُرجع نفس schema مع عينة صفوف مأخوذة من الـ view، حتى
  لا يرى الذكاء الاصطناعي بيانات خارج الفلتر إطلاقاً.

لا يعدّل هذا الملف core/project_db.py (قرار معماري): كل العمليات عبر
sqlite3 مباشرة على نفس ملف project.db، بنفس نمط
core/dashboard_manager.py::_update_dashboard_columns.

نوع الفلاتر المدعومة (نفس التخزين الحالي في dashboard_slicers):
- قائمة قيم:  CAST("col" AS TEXT) IN ('a', 'b')
- نطاق تاريخ (العمود مسجَّل في _date_cols_{table} وله قيمتان):
  date("col") BETWEEN date('start') AND date('end')   — شامل لطرفيه.

ملاحظة: SQLite لا يقبل معاملات (?) داخل CREATE VIEW، فتُكتب القيم
كنصوص حرفية مع مضاعفة علامة الاقتباس المفردة ('' ) للحماية من الحقن.
"""

import logging
import sqlite3
from typing import Optional

import pandas as pd

from config import SAMPLE_ROWS

logger = logging.getLogger(__name__)

VIEW_PREFIX = "dview_"
_SEP = "__"


def _q(name: str) -> str:
    """اقتباس معرّف SQLite (اسم جدول/عمود/view)."""
    return '"' + str(name).replace('"', '""') + '"'


def _lit(value) -> str:
    """نص حرفي SQLite آمن (مضاعفة علامة الاقتباس المفردة)."""
    return "'" + str(value).replace("'", "''") + "'"


class DashboardViews:
    """
    الاستخدام:
        dv = DashboardViews(db)
        dv.sync(dashboard_id)                 # بعد أي تعديل على الفلاتر
        df = dv.read_table("sales", dashboard_id)
        schema = dv.get_schema(dashboard_id)
        dv.drop(dashboard_id)                 # عند حذف اللوحة
    """

    def __init__(self, db):
        self.db = db

    # ──────────────────────────────────────────────────────────
    #  أدوات داخلية
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def view_name(dashboard_id: str, table_alias: str) -> str:
        return f"{VIEW_PREFIX}{dashboard_id}{_SEP}{table_alias}"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _list_views(conn: sqlite3.Connection, dashboard_id: Optional[str] = None) -> list[str]:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall()
        names = [r["name"] for r in rows if r["name"].startswith(VIEW_PREFIX)]
        if dashboard_id is not None:
            prefix = f"{VIEW_PREFIX}{dashboard_id}{_SEP}"
            names = [n for n in names if n.startswith(prefix)]
        return names

    def _active_filters_by_table(self, dashboard_id: str) -> dict:
        """
        فلاتر اللوحة النشطة فقط (لها جدول + عمود + قيم)، مجمَّعة حسب
        الجدول: {table: [{"column", "values"} | {"column", "date_range"}]}.
        """
        settings = self.db.get_settings()
        by_table: dict[str, list] = {}
        for s in self.db.get_dashboard_slicers(dashboard_id):
            table = s.get("table_name")
            column = s.get("column_name")
            values = s.get("selected_values")
            if not table or not column or not values:
                continue

            date_cols = settings.get(f"_date_cols_{table}", [])
            if column in date_cols and len(values) == 2:
                flt = {"column": column,
                       "date_range": {"start": values[0], "end": values[1]}}
            else:
                flt = {"column": column, "values": list(values)}
            by_table.setdefault(table, []).append(flt)
        return by_table

    @staticmethod
    def _build_where(filters: list, columns: set) -> tuple[str, list]:
        """
        بناء شرط WHERE (AND بين الفلاتر) بصياغة SQLite.
        يرجع (النص, أعمدة_متجاهَلة) — العمود غير الموجود في الجدول
        يُتجاهَل بدل أن يكسر إنشاء الـ view.
        """
        clauses, ignored = [], []
        for f in filters:
            column = f["column"]
            if column not in columns:
                ignored.append(column)
                continue

            date_range = f.get("date_range")
            if date_range:
                start, end = date_range.get("start"), date_range.get("end")
                if not start or not end:
                    continue
                clauses.append(
                    f"date({_q(column)}) BETWEEN date({_lit(start)}) AND date({_lit(end)})"
                )
                continue

            values = f.get("values") or []
            if not values:
                continue
            in_list = ", ".join(_lit(v) for v in values)
            clauses.append(f"CAST({_q(column)} AS TEXT) IN ({in_list})")

        return " AND ".join(clauses), ignored

    # ──────────────────────────────────────────────────────────
    #  إنشاء / تعديل / حذف
    # ──────────────────────────────────────────────────────────

    def sync(self, dashboard_id: str) -> dict:
        """
        إعادة بناء كل views هذه اللوحة من فلاترها الحالية (حذف القديمة
        ثم إنشاء الجديدة داخل transaction واحدة). تُستدعى بعد أي حفظ/
        مسح/نسخ للفلاتر وقبل كل تحديث للوحة.

        يرجع: {"ok": True, "created": [aliases], "skipped": [aliases]}
               أو {"ok": False, "error": "..."}
        """
        try:
            by_table = self._active_filters_by_table(dashboard_id)
            conn = self._connect()
            conn.isolation_level = None   # تحكم يدوي بالـ transaction
            created, skipped = [], []
            try:
                conn.execute("BEGIN")
                for name in self._list_views(conn, dashboard_id):
                    conn.execute(f"DROP VIEW IF EXISTS {_q(name)}")

                for table, filters in by_table.items():
                    data_table = f"data_{table}"
                    columns = {
                        r["name"] for r in conn.execute(f"PRAGMA table_info({_q(data_table)})")
                    }
                    if not columns:
                        skipped.append(table)
                        logger.warning("Dashboard view skipped: table '%s' not found", table)
                        continue

                    where, ignored = self._build_where(filters, columns)
                    if ignored:
                        logger.info("Dashboard %s: filter columns ignored for '%s': %s",
                                    dashboard_id, table, ignored)
                    if not where:
                        continue

                    conn.execute(
                        f"CREATE VIEW {_q(self.view_name(dashboard_id, table))} AS "
                        f"SELECT * FROM {_q(data_table)} WHERE {where}"
                    )
                    created.append(table)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

            logger.info("Dashboard views synced: %s — created=%s skipped=%s",
                        dashboard_id, created, skipped)
            return {"ok": True, "created": created, "skipped": skipped}
        except Exception as e:
            logger.error("DashboardViews.sync error: %s", e)
            return {"ok": False, "error": str(e)}

    def drop(self, dashboard_id: str) -> int:
        """حذف كل views لوحة (عند حذف اللوحة). يرجع عدد الـ views المحذوفة."""
        try:
            conn = self._connect()
            try:
                names = self._list_views(conn, dashboard_id)
                for name in names:
                    conn.execute(f"DROP VIEW IF EXISTS {_q(name)}")
                conn.commit()
            finally:
                conn.close()
            if names:
                logger.info("Dashboard views dropped: %s (%d)", dashboard_id, len(names))
            return len(names)
        except Exception as e:
            logger.error("DashboardViews.drop error: %s", e)
            return 0

    def drop_for_table(self, table_alias: str) -> int:
        """
        حذف أي view (لأي لوحة) مبني على جدول معيّن — يُستدعى عند حذف
        الجدول من المشروع. الفلاتر المحفوظة في dashboard_slicers لا
        تُمس؛ ستُتجاهل تلقائياً في sync التالي لأن الجدول لم يعد موجوداً.
        """
        suffix = f"{_SEP}{table_alias}"
        try:
            conn = self._connect()
            try:
                names = [n for n in self._list_views(conn) if n.endswith(suffix)]
                for name in names:
                    conn.execute(f"DROP VIEW IF EXISTS {_q(name)}")
                conn.commit()
            finally:
                conn.close()
            return len(names)
        except Exception as e:
            logger.error("DashboardViews.drop_for_table error: %s", e)
            return 0

    # ──────────────────────────────────────────────────────────
    #  القراءة (تُستخدم من QueryEngine والـ schema)
    # ──────────────────────────────────────────────────────────

    def has_view(self, dashboard_id: str, table_alias: str) -> bool:
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?",
                    (self.view_name(dashboard_id, table_alias),),
                ).fetchone()
            finally:
                conn.close()
            return row is not None
        except sqlite3.Error:
            return False

    def _read_view_once(self, dashboard_id: str, table_alias: str):
        """يرجع (موجود؟, DataFrame|None). يرمي استثناء لو فشلت القراءة."""
        vname = self.view_name(dashboard_id, table_alias)
        conn = self._connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?", (vname,)
            ).fetchone()
            if not exists:
                return False, None
            return True, pd.read_sql(f"SELECT * FROM {_q(vname)}", conn)
        finally:
            conn.close()

    def read_table(self, table_alias: str, dashboard_id: Optional[str] = None) -> Optional[pd.DataFrame]:
        """
        بيانات جدول كما يجب أن "يراها" المستهلك:
        - بدون dashboard_id أو بدون view لهذه اللوحة: الجدول الأصلي
          (db.get_clean_data) كما هو.
        - مع view: البيانات المفلترة، مع استعادة أعمدة التاريخ لنوع
          datetime64 (نفس منطق get_clean_data).

        لو فشلت قراءة الـ view (مثلاً أُعيد إنشاء الجدول بأعمدة مختلفة)
        تُعاد مزامنة views اللوحة مرة واحدة ثم تُعاد المحاولة.
        """
        if not dashboard_id:
            return self.db.get_clean_data(table_alias)

        df = None
        for attempt in (1, 2):
            try:
                exists, df = self._read_view_once(dashboard_id, table_alias)
                if not exists:
                    return self.db.get_clean_data(table_alias)
                break
            except Exception as e:
                if attempt == 1:
                    logger.warning(
                        "View read failed for '%s' (dashboard %s): %s — re-syncing",
                        table_alias, dashboard_id, e,
                    )
                    self.sync(dashboard_id)
                    continue
                raise

        if df.empty:
            # نتيجة الفلتر فارغة: pandas يقرأ أعمدة بلا نوع (object)، فتفشل
            # لاحقاً دوال مثل SUM في DuckDB. نُعيد صفرَ صفوف من الجدول
            # الأصلي نفسه — نفس الأعمدة بأنواعها الصحيحة.
            base = self.db.get_clean_data(table_alias)
            if base is not None:
                return base.iloc[0:0]
            return df

        date_cols = self.db.get_settings().get(f"_date_cols_{table_alias}", [])
        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        return df

    def get_schema(self, dashboard_id: Optional[str] = None) -> dict:
        """
        نفس db.get_schema() (الأعمدة والأنواع)، لكن عينة الصفوف لكل جدول
        له view في هذه اللوحة تُؤخذ من الـ view — حتى لا يرى الذكاء
        الاصطناعي أي صف خارج فلاتر اللوحة. بدون dashboard_id: مطابق
        تماماً لـ db.get_schema().
        """
        schema = self.db.get_schema()
        if not dashboard_id:
            return schema

        try:
            conn = self._connect()
            try:
                for alias, info in schema.items():
                    vname = self.view_name(dashboard_id, alias)
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?", (vname,)
                    ).fetchone()
                    if not exists:
                        continue
                    rows = conn.execute(
                        f"SELECT * FROM {_q(vname)} LIMIT {int(SAMPLE_ROWS)}"
                    ).fetchall()
                    info["sample"] = [dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as e:
            logger.error("DashboardViews.get_schema error: %s", e)
        return schema
