"""
tests/test_value_resolver.py
==============================
اختبارات core/value_resolver.py — تعمل بـ unittest (أو pytest) دون
DuckDB ولا شبكة ولا AI حقيقي: الجداول DataFrames صغيرة ومزوّد الكتابات
البديلة دالة وهمية.

التشغيل من جذر المشروع:
    python -m unittest tests.test_value_resolver -v

آخر قسم (TestAskIntegration) يختبر تكامل AIManager.ask مع QueryEngine
الحقيقي (DuckDB)، ويُتخطى تلقائياً إن لم تتوفر DuckDB/httpx في البيئة.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# عزل مسار التخزين قبل أي استيراد لـ config (يُنشئ مجلدات عند الاستيراد)
_TMP = tempfile.mkdtemp(prefix="datalens_test_")
os.environ.setdefault("DATALENS_DATA_DIR", _TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from core.value_resolver import (
    ValueResolver, normalize_text, is_categorical_column,
    build_variants_prompt, parse_variants_response,
)


def make_resolver(frames: dict, provider=None) -> ValueResolver:
    return ValueResolver(lambda name: frames.get(name.lower()), provider)


CUSTOMERS = pd.DataFrame({
    "id": [1, 2, 3, 4],
    "customername": ["Paris", "Paris Trading", "Al Paris", "London"],
    "region": ["North", "North", "South", "North"],
})


class TestNormalization(unittest.TestCase):
    def test_case_and_spaces(self):
        self.assertEqual(normalize_text("  PARIS  "), normalize_text("paris"))

    def test_arabic_variants(self):
        self.assertEqual(normalize_text("مدرسة"), normalize_text("مدرسه"))
        self.assertEqual(normalize_text("أحمد"), normalize_text("احمد"))

    def test_accents(self):
        self.assertEqual(normalize_text("Café"), normalize_text("cafe"))


class TestColumnEligibility(unittest.TestCase):
    def test_text_column_ok(self):
        self.assertTrue(is_categorical_column(CUSTOMERS["customername"]))

    def test_numeric_column_rejected(self):
        self.assertFalse(is_categorical_column(CUSTOMERS["id"]))

    def test_date_column_rejected(self):
        s = pd.Series(pd.to_datetime(["2024-01-01", "2024-02-01"]))
        self.assertFalse(is_categorical_column(s))

    def test_text_dates_rejected(self):
        self.assertFalse(is_categorical_column(pd.Series(["2024-01-01", "2024-02-03", "2024-03-04"])))

    def test_numeric_strings_rejected(self):
        self.assertFalse(is_categorical_column(pd.Series(["10", "20.5", "300"])))

    def test_codes_rejected(self):
        self.assertFalse(is_categorical_column(pd.Series(["AB12", "CD34", "EF56"])))


class TestResolve(unittest.TestCase):
    def test_existing_value_not_applicable(self):
        r = make_resolver({"customers": CUSTOMERS}).resolve(
            "SELECT * FROM customers WHERE customername = 'Paris'")
        self.assertEqual(r["status"], "not_applicable")

    def test_case_difference_auto_fixed(self):
        r = make_resolver({"customers": CUSTOMERS}).resolve(
            "SELECT * FROM customers WHERE customername = 'PARIS'")
        self.assertEqual(r["status"], "auto_fixed")
        self.assertEqual(r["sql"], "SELECT * FROM customers WHERE customername = 'Paris'")
        self.assertEqual(r["fixes"][0]["to"], "Paris")

    def test_arabic_to_english_multiple_candidates(self):
        provider = lambda w, t, c, s: ["Paris"]
        r = make_resolver({"customers": CUSTOMERS}, provider).resolve(
            "SELECT * FROM customers WHERE customername = 'باريس'")
        self.assertEqual(r["status"], "needs_choice")
        values = [c["value"] for c in r["items"][0]["candidates"]]
        self.assertEqual(values[0], "Paris")
        self.assertIn("Paris Trading", values)
        self.assertIn("Al Paris", values)
        scores = [c["score"] for c in r["items"][0]["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertFalse(r["items"][0]["auto"])

    def test_arabic_single_translation_auto(self):
        df = pd.DataFrame({"customername": ["Paris", "London"]})
        provider = lambda w, t, c, s: ["Paris"]
        r = make_resolver({"customers": df}, provider).resolve(
            "SELECT * FROM customers WHERE customername = 'باريس'")
        self.assertEqual(r["status"], "auto_fixed")
        self.assertIn("'Paris'", r["sql"])

    def test_typo_needs_confirmation(self):
        df = pd.DataFrame({"customername": ["Paris", "London"]})
        r = make_resolver({"customers": df}).resolve(
            "SELECT * FROM customers WHERE customername = 'Pariss'")
        self.assertEqual(r["status"], "needs_choice")
        self.assertEqual(r["items"][0]["candidates"][0]["value"], "Paris")
        self.assertEqual(r["items"][0]["candidates"][0]["level"], "fuzzy")

    def test_no_match_not_found(self):
        df = pd.DataFrame({"customername": ["Paris", "London"]})
        r = make_resolver({"customers": df}, lambda *a: []).resolve(
            "SELECT * FROM customers WHERE customername = 'Zzzzqx'")
        self.assertEqual(r["status"], "not_found")
        self.assertTrue(r["message"])

    def test_other_conditions_preserved(self):
        sql = ("SELECT region, COUNT(*) FROM customers "
               "WHERE customername = 'PARIS' AND region = 'North' GROUP BY region ORDER BY 2 DESC")
        r = make_resolver({"customers": CUSTOMERS}).resolve(sql)
        self.assertEqual(r["status"], "auto_fixed")
        self.assertEqual(r["sql"], sql.replace("'PARIS'", "'Paris'"))

    def test_combination_zero_not_a_value_problem(self):
        # كل شرط يطابق وحده؛ الصفر ناتج عن تركيبهما
        sql = "SELECT * FROM customers WHERE customername = 'London' AND region = 'South'"
        r = make_resolver({"customers": CUSTOMERS}).resolve(sql)
        self.assertEqual(r["status"], "not_applicable")

    def test_numeric_and_id_columns_ignored(self):
        df = pd.DataFrame({"code": ["AB12", "CD34"], "qty": [1, 2], "customername": ["Paris", "x"]})
        r = make_resolver({"t": df}).resolve("SELECT * FROM t WHERE code = 'AB13'")
        self.assertEqual(r["status"], "not_applicable")
        r = make_resolver({"t": df}).resolve("SELECT * FROM t WHERE qty = '5'")
        self.assertEqual(r["status"], "not_applicable")

    def test_join_with_aliases(self):
        orders = pd.DataFrame({"cust_id": [1, 2], "status": ["Open", "Closed"]})
        sql = ("SELECT o.status FROM orders o JOIN customers c ON o.cust_id = c.id "
               "WHERE c.customername = 'PARIS'")
        r = make_resolver({"orders": orders, "customers": CUSTOMERS}).resolve(sql)
        self.assertEqual(r["status"], "auto_fixed")
        self.assertEqual(r["items"][0]["table"], "customers")
        self.assertTrue(r["sql"].endswith("c.customername = 'Paris'"))

    def test_in_list_only_missing_literal_fixed(self):
        df = pd.DataFrame({"city": ["Paris", "London", "Berlin"]})
        sql = "SELECT * FROM t WHERE city IN ('PARIS', 'LONDON')"
        r = make_resolver({"t": df}).resolve(sql)
        self.assertEqual(r["status"], "auto_fixed")
        self.assertEqual(r["sql"], "SELECT * FROM t WHERE city IN ('Paris', 'London')")

    def test_ilike_pattern_keeps_wildcards(self):
        provider = lambda w, t, c, s: ["Paris"]
        df = pd.DataFrame({"customername": ["Paris", "London"]})
        r = make_resolver({"t": df}, provider).resolve(
            "SELECT * FROM t WHERE customername ILIKE '%باريس%'")
        self.assertEqual(r["status"], "auto_fixed")
        self.assertIn("ILIKE '%Paris%'", r["sql"])

    def test_case_when_and_select_literals_untouched(self):
        sql = ("SELECT CASE WHEN customername = 'zzz' THEN 1 ELSE 0 END FROM customers "
               "WHERE region = 'North'")
        r = make_resolver({"customers": CUSTOMERS}).resolve(sql)
        self.assertEqual(r["status"], "not_applicable")

    def test_function_wrapped_and_negated_ignored(self):
        for sql in ("SELECT * FROM customers WHERE LOWER(customername) = 'zzz'",
                    "SELECT * FROM customers WHERE customername != 'zzz'",
                    "SELECT * FROM customers WHERE customername NOT LIKE '%zzz%'"):
            self.assertEqual(make_resolver({"customers": CUSTOMERS}).resolve(sql)["status"],
                             "not_applicable")

    def test_quote_escaping(self):
        df = pd.DataFrame({"name": ["O'Brien", "Smith"]})
        r = make_resolver({"t": df}).resolve("SELECT * FROM t WHERE name = 'O''BRIEN'")
        self.assertEqual(r["status"], "auto_fixed")
        self.assertEqual(r["sql"], "SELECT * FROM t WHERE name = 'O''Brien'")

    def test_provider_failure_is_not_fatal(self):
        def boom(*a):
            raise RuntimeError("AI down")
        df = pd.DataFrame({"customername": ["Paris"]})
        r = make_resolver({"t": df}, boom).resolve("SELECT * FROM t WHERE customername = 'باريس'")
        self.assertEqual(r["status"], "not_found")

    def test_unknown_table_and_malformed_sql(self):
        self.assertEqual(make_resolver({}).resolve("SELECT * FROM nope WHERE a = 'x'")["status"],
                         "not_applicable")
        self.assertEqual(make_resolver({}).resolve("SELECT 'abc")["status"], "not_applicable")

    def test_ai_not_called_when_local_match_is_strong(self):
        calls = []
        provider = lambda *a: calls.append(a) or []
        df = pd.DataFrame({"customername": ["Paris"]})
        make_resolver({"t": df}, provider).resolve("SELECT * FROM t WHERE customername = 'PARIS'")
        self.assertEqual(calls, [])

    def test_ai_receives_limited_samples_only(self):
        got = {}
        def provider(w, t, c, s):
            got["samples"] = s
            return []
        df = pd.DataFrame({"customername": [f"Name{i} Co" for i in range(50)]})
        make_resolver({"t": df}, provider).resolve("SELECT * FROM t WHERE customername = 'باريس'")
        self.assertLessEqual(len(got["samples"]), 5)


class TestApplyChoices(unittest.TestCase):
    def _needs_choice(self):
        provider = lambda w, t, c, s: ["Paris"]
        sql = "SELECT * FROM customers WHERE customername = 'باريس' AND region = 'North'"
        return sql, make_resolver({"customers": CUSTOMERS}, provider).resolve(sql)

    def test_user_choice_applied_and_rest_preserved(self):
        sql, r = self._needs_choice()
        out = ValueResolver.apply_choices(sql, r["items"], {"0": "Paris Trading"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["sql"],
                         "SELECT * FROM customers WHERE customername = 'Paris Trading' AND region = 'North'")

    def test_missing_choice_rejected(self):
        sql, r = self._needs_choice()
        self.assertFalse(ValueResolver.apply_choices(sql, r["items"], {})["ok"])

    def test_stale_sql_rejected(self):
        sql, r = self._needs_choice()
        out = ValueResolver.apply_choices(sql.replace("SELECT *", "SELECT  *  "), r["items"], {"0": "Paris"})
        self.assertFalse(out["ok"])

    def test_items_are_json_serializable(self):
        import json
        _, r = self._needs_choice()
        json.dumps(r)


class TestVariantsHelpers(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_variants_response('x {"variants": ["Paris", " Paris ", "Pariss"]} y'),
                         ["Paris", "Pariss"])
        self.assertEqual(parse_variants_response("garbage"), [])

    def test_prompt_has_no_full_data(self):
        p = build_variants_prompt("باريس", "customers", "customername", ["A", "B"])
        self.assertIn("باريس", p)
        self.assertIn("customername", p)


# ══════════════════════════════════════════════════════════════
#  تكامل AIManager.ask (يتطلب DuckDB وhttpx — يُتخطى إن لم تتوفرا)
# ══════════════════════════════════════════════════════════════

try:
    import duckdb  # noqa: F401
    import httpx   # noqa: F401
    _HAS_RUNTIME = True
except Exception:
    _HAS_RUNTIME = False


@unittest.skipUnless(_HAS_RUNTIME, "DuckDB/httpx غير متوفرين في هذه البيئة")
class TestAskIntegration(unittest.TestCase):
    def setUp(self):
        from ai.base_engine import BaseEngine
        from ai.ai_manager import AIManager
        from core.project_db import ProjectDB

        class FakeEngine(BaseEngine):
            def __init__(self, sql):
                super().__init__("k", "m")
                self.sql = sql
                self.variant_calls = 0
            def get_models(self): return {"ok": True, "models": []}
            def status(self): return {"ok": True}
            def send(self, prompt, temperature=0.1, timeout_override=None):
                if "كتابات بديلة" in prompt:
                    self.variant_calls += 1
                    return {"ok": True, "text": '{"variants": ["Paris"]}'}
                return {"ok": True, "text": self.sql}

        self.Fake, self.AIManager = FakeEngine, AIManager
        self.db = ProjectDB("u_test", "p_" + os.urandom(4).hex())
        self.db.save_clean_data("customers", CUSTOMERS)
        self.db.add_file("f1", "customers.xlsx", "customers")

    def _ask(self, sql, **kw):
        ai = self.AIManager(self.db, self.Fake(sql), max_tries=1, retry_delay=0)
        return ai, ai.ask("q", result_type="table", **kw)

    def test_rows_found_resolver_not_involved(self):
        _, r = self._ask("SELECT * FROM customers WHERE customername = 'Paris'", resolve_values=True)
        self.assertEqual(r["rows"], 1)
        self.assertNotIn("value_resolution", r)
        self.assertNotIn("value_fixes", r)

    def test_zero_rows_auto_fix_reruns(self):
        _, r = self._ask("SELECT * FROM customers WHERE customername = 'PARIS' AND region = 'North'",
                         resolve_values=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["rows"], 1)
        self.assertEqual(r["value_fixes"][0]["to"], "Paris")

    def test_zero_rows_needs_choice_then_apply(self):
        ai, r = self._ask("SELECT * FROM customers WHERE customername = 'باريس'", resolve_values=True)
        self.assertEqual(r["rows"], 0)
        res = r["value_resolution"]
        self.assertEqual(res["status"], "needs_choice")
        out = ai.apply_value_choices(res["sql"], res["items"], {"0": "Al Paris"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["rows"], 1)

    def test_resolver_off_by_default(self):
        _, r = self._ask("SELECT * FROM customers WHERE customername = 'PARIS'")
        self.assertEqual(r["rows"], 0)
        self.assertNotIn("value_resolution", r)

    def test_sql_error_is_not_zero_rows(self):
        ai = self.AIManager(self.db, self.Fake("SELECT * FROM customers WHERE nocol = 'x'"),
                            max_tries=1, retry_delay=0)
        r = ai.ask("q", result_type="table", resolve_values=True)
        self.assertFalse(r["ok"])
        self.assertNotIn("value_resolution", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
