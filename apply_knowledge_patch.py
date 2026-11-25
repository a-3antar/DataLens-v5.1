#!/usr/bin/env python3

from core.logger_config import trace_calls

"""
apply_knowledge_patch.py
=========================
يطبّق تعديلات "الذاكرة المعرفية" على ملفات DataLens الحالية عندك بدل استبدالها
بنسخ كاملة (فلا يتأثر أي كود لم نلمسه). الاستخدام من جذر المشروع:

    python apply_knowledge_patch.py [--root .] [--dry-run]

- كل تعديل يُحدَّد بسطر مرجعي فريد (يُقارن بعد توحيد المسافات) وقد يُشترط سطر سابق/لاحق.
- كل-أو-لا-شيء: لو فشل أي مرجع لا يُكتب أي ملف ويُطبع السبب.
- قابل للتكرار: التعديل المطبَّق سابقاً يُتخطّى.
- بعد الكتابة يُجرى py_compile لكل ملف، ويُستعاد الأصل تلقائياً عند أي فشل.
- نسخ احتياطية للأصول في _knowledge_backup/.
"""

import argparse
import py_compile
import re
import shutil
import sys
import tempfile
from pathlib import Path


@trace_calls
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


@trace_calls
def indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


@trace_calls
def _place(text: str, base: str) -> list:
    """أسطر النص الجديد مزاحة بإزاحة السطر المرجعي (الإزاحة النسبية داخل النص محفوظة)."""
    lines = text.strip("\n").split("\n")
    return [(base + l) if l.strip() else "" for l in lines]


@trace_calls
def apply_op(lines: list, op: dict):
    """يرجع (lines, status) حيث status: applied | skipped | error:<msg>."""
    kind, anchor = op["op"], op.get("anchor")
    if kind == "append":
        if norm(op["marker"]) in norm("\n".join(lines)):
            return lines, "skipped"
        return lines + op["text"].strip("\n").split("\n") + [""], "applied"

    nonblank = [i for i, l in enumerate(lines) if l.strip()]

    @trace_calls
    def neighbor(i, step):
        j = i + step
        while 0 <= j < len(lines) and not lines[j].strip():
            j += step
        return lines[j] if 0 <= j < len(lines) else ""

    hits = []
    for i, l in enumerate(lines):
        if norm(l) != norm(anchor):
            continue
        if op.get("prev") and norm(neighbor(i, -1)) != norm(op["prev"]):
            continue
        if op.get("next_startswith") and not norm(neighbor(i, 1)).startswith(norm(op["next_startswith"])):
            continue
        hits.append(i)

    first_new = norm(op["text"].strip("\n").split("\n")[0])
    if len(hits) == 1 or (len(hits) > 1 and False):
        i = hits[0]
    else:
        if not hits:
            # قد يكون التعديل مطبَّقاً سابقاً
            if kind == "replace" and any(norm(l) == first_new for l in lines):
                return lines, "skipped"
            return lines, f"error: المرجع غير موجود: {anchor!r}"
        return lines, f"error: المرجع غير فريد ({len(hits)} مطابقة): {anchor!r}"

    delta = op.get("indent_delta", 0)
    base = indent_of(lines[i])
    base = base[:delta] if delta < 0 else base + " " * delta
    new = _place(op["text"], base)
    is_block = new[0].lstrip().startswith(("def ", "class ", "# قسم"))
    if kind == "after":
        if any(norm(l) == first_new for l in lines[i + 1: i + len(new) + 3]):
            return lines, "skipped"
        return lines[: i + 1] + ([""] if is_block else []) + new + lines[i + 1:], "applied"
    if kind == "before":
        if any(norm(l) == first_new for l in lines[max(0, i - len(new) - 3): i]):
            return lines, "skipped"
        return lines[:i] + new + ([""] if is_block else []) + lines[i:], "applied"
    if kind == "replace":
        return lines[:i] + new + lines[i + 1:], "applied"
    return lines, f"error: عملية غير معروفة {kind}"


A, B, R, AP = "after", "before", "replace", "append"

OPS = {
    "config.py": [
        {"op": A, "anchor": '"story_timeout": STORY_TIMEOUT_SECONDS,', "text": (
            '"use_knowledge": True,\n"knowledge_auto_refresh": True,\n"knowledge_send_values_to_ai": True,')},
        {"op": AP, "marker": "KNOWLEDGE_CONTEXT_MAX_CHARS = ", "text": '''
# ─── الذاكرة المعرفية للمشروع (core/knowledge_manager.py) ───
KNOWLEDGE_CONTEXT_MAX_CHARS = 6000            # أقصى طول لقسم DATA CONTEXT في الـ prompt
KNOWLEDGE_TOP_VALUES = 15                     # عدد القيم الفئوية الأكثر تكراراً المخزَّنة لكل عمود
KNOWLEDGE_CATEGORICAL_MAX_DISTINCT = 50       # أعلى كاردينالية تُعتبر فئوية وتُخزَّن قيمها
KNOWLEDGE_MAX_INSIGHTS = 12                   # أقصى عدد insights محفوظة
KNOWLEDGE_INSIGHT_QUERIES = 6                 # أقصى عدد استعلامات يخطّط لها AI لاكتشاف الأنماط
KNOWLEDGE_INSIGHT_SNAPSHOT_ROWS = 20          # صفوف لقطة نتيجة الدليل المخزَّنة مع كل insight
KNOWLEDGE_JOB_STALE_MINUTES = 30              # مهمة running أقدم من هذا تُعتبر ميتة
KNOWLEDGE_MAX_CORRELATIONS = 8                # أقصى أزواج ارتباط مخزَّنة لكل جدول'''},
    ],
    "ai/prompt_builder.py": [
        {"op": B, "anchor": "class PromptBuilder:", "text": '''
# قسم سياق المعرفة المُضاف للـ prompts (راجع core/knowledge_manager.py::build_context)
KNOWLEDGE_RULES = (
    "هذا القسم ملف معرفة مُستخرج مسبقاً عن البيانات الكاملة للمشروع (غير مفلترة). "
    "استخدمه للفهم والتلميح فقط (معاني الأعمدة، المرادفات، طريقة التجميع، القيم المعروفة). "
    "الأرقام تأتي من DATA أو نتيجة الاستعلام فقط، وعند التعارض تُقدَّم DATA. "
    "استخدم أسماء الأعمدة والقيم كما وردت حرفياً."
)

'''},
        {"op": A, "anchor": "result_type: Optional[str] = None,", "text": "knowledge_context: Optional[str] = None,"},
        {"op": B, "anchor": "# 5. Result Type hint (اختياري)", "text": "parts.extend(self._knowledge_parts(knowledge_context))\n"},
        {"op": R, "anchor": "def build_story_plan(self, question: str) -> str:",
         "text": "def build_story_plan(self, question: str, knowledge_context: Optional[str] = None) -> str:"},
        {"op": B, "anchor": 'parts.append(self._section("QUESTION", question))',
         "prev": 'parts.append(self._section("SAMPLE DATA", sample_text))',
         "text": "parts.extend(self._knowledge_parts(knowledge_context))"},
        {"op": A, "anchor": "summary : Optional[str] = None,", "prev": "history : Optional[list] = None,",
         "text": "knowledge_context: Optional[str] = None,"},
        {"op": B, "anchor": "summary_text = self._format_qa_summary(summary)",
         "prev": 'parts.append(self._section("SAMPLE DATA", sample_text))',
         "text": "parts.extend(self._knowledge_parts(knowledge_context))"},
        {"op": A, "anchor": "summary : Optional[str] = None,", "prev": "failed : Optional[list] = None,",
         "text": "knowledge_context: Optional[str] = None,"},
        {"op": A, "anchor": 'parts.append(self._section("STYLE EXAMPLE", self._qa_style_example(answer_length)))',
         "text": "parts.extend(self._knowledge_parts(knowledge_context))"},
        {"op": R, "anchor": "def build_story_multi(self, question: str, datasets: list, max_rows: int = 30) -> str:",
         "text": ("def build_story_multi(self, question: str, datasets: list, max_rows: int = 30,\n"
                  "                      knowledge_context: Optional[str] = None) -> str:")},
        {"op": B, "anchor": 'self._section("DATA", full_data_text),',
         "text": "*self._knowledge_parts(knowledge_context),"},
        {"op": B, "anchor": "def _section(self, title: str, content: str) -> str:", "text": '''
def _knowledge_parts(self, knowledge_context: Optional[str]) -> list:
    """قسم [DATA CONTEXT] (قائمة فارغة لو لا سياق معرفة)."""
    ctx = (knowledge_context or "").strip()
    if not ctx:
        return []
    return [self._section("DATA CONTEXT", KNOWLEDGE_RULES + "\\n\\n" + ctx)]
'''},
    ],
    "core/data_manager.py": [
        {"op": B, "anchor": "@staticmethod", "text": '''
def _mark_knowledge_stale(self, table_alias: str) -> None:
    """وسم معرفة الجدول stale بعد تعديل بياناته (تلميح فقط بلا حساب؛ لا يكسر أي عملية)."""
    try:
        from core.knowledge_manager import KnowledgeManager
        KnowledgeManager(self.db).mark_stale(table_alias)
    except Exception as e:
        logger.debug("mark_knowledge_stale skipped: %s", e)
'''},
        {"op": A, "anchor": "self.db.save_clean_data(table_alias, df)", "next_startswith": 'logger.info("dtype changed',
         "text": "self._mark_knowledge_stale(table_alias)"},
        {"op": A, "anchor": "self.db.save_clean_data(table_alias, df)", "next_startswith": 'logger.info("filter_rows',
         "text": "self._mark_knowledge_stale(table_alias)"},
        {"op": A, "anchor": "self.db.save_clean_data(table_alias, df)", "next_startswith": "after = len(df)",
         "text": "self._mark_knowledge_stale(table_alias)"},
        {"op": A, "anchor": "self.db.save_clean_data(table_alias, df)", "next_startswith": 'return {"ok": True}',
         "text": "self._mark_knowledge_stale(table_alias)"},
        {"op": A, "anchor": "self.db.save_clean_data(table_alias, df)", "prev": "df[column] = df[column].fillna(fill_val)",
         "text": "self._mark_knowledge_stale(table_alias)"},
    ],
    "ui/files.py": [
        {"op": A, "anchor": "from core.dashboard_views import DashboardViews",
         "text": "from core.knowledge_manager import KnowledgeManager, trigger_after_load"},
        {"op": B, "anchor": "def _render_upload_form(fm: FileManager, dm: DataManager) -> None:", "text": '''
def _start_knowledge_job(db, table_alias: str) -> None:
    """تشغيل تحديث الذاكرة المعرفية في الخلفية بعد رفع/تحديث جدول (لا يكسر الصفحة عند أي فشل)."""
    try:
        r = trigger_after_load(db, table_alias)
        if r.get("ok"):
            notify("بدأ تحديث معرفة المشروع في الخلفية", kind="info")
    except Exception as e:
        logger.warning("knowledge job start failed: %s", e)


'''},
        {"op": A, "anchor": "st.session_state.pop(_UPLOAD_CACHE_KEY, None)", "text": "_start_knowledge_job(dm.db, table_alias)"},
        {"op": B, "anchor": 'notify(f"تم التحديث — {r[\'rows\']} صف", kind="success")',
         "text": '_start_knowledge_job(dm.db, f["table_alias"])'},
        {"op": B, "anchor": 'DashboardViews(db).drop_for_table(f["table_alias"])',
         "text": 'KnowledgeManager(db).drop_for_table(f["table_alias"])'},
    ],
    "ui/settings.py": [
        {"op": B, "anchor": 'st.markdown("**🌍 المنطقة الزمنية**")', "text": '''
st.markdown("**🧠 الذاكرة المعرفية للمشروع**")
use_knowledge = st.toggle(
    "استخدام الذاكرة المعرفية في الـ prompts", value=bool(settings.get("use_knowledge", True)),
    help="يُضاف ملخص معرفة المشروع (أوصاف الأعمدة، المرادفات، طرق التجميع) إلى أسئلة SQL والتحليل.",
)
knowledge_auto_refresh = st.toggle(
    "تحديث الذاكرة (الطبقتين ب وج) تلقائياً بعد رفع/تحديث الملفات",
    value=bool(settings.get("knowledge_auto_refresh", True)),
    help="الطبقة الإحصائية (أ) تعمل دائماً بدون AI. الطبقتان ب وج تستهلكان استدعاءات AI.",
)
knowledge_send_values = st.toggle(
    "السماح بإرسال قيم فعلية من بياناتك للذكاء الاصطناعي",
    value=bool(settings.get("knowledge_send_values_to_ai", True)),
)
st.warning(
    "🔒 خصوصية: عند التفعيل تُرسَل للمحرك الخارجي عيّنات صفوف وقيم فئوية وأكثر القيم تكراراً ولقطات "
    "من نتائج الاستعلامات لبناء الذاكرة. عند الإيقاف تُرسَل أسماء الأعمدة والإحصاءات فقط، وتُعطَّل "
    "طبقة الـ Insights (ج) كلياً."
)
'''},
        {"op": A, "anchor": '"timezone": timezone_choice,', "text": (
            '"use_knowledge": use_knowledge,\n"knowledge_auto_refresh": knowledge_auto_refresh,\n'
            '"knowledge_send_values_to_ai": knowledge_send_values,')},
    ],
    "main.py": [
        {"op": A, "anchor": "from ui.data import show_data", "text": "from ui.knowledge import show_knowledge"},
        {"op": A, "anchor": "from core.logger_config import setup_logging",
         "text": "from core.knowledge_manager import cleanup_dead_jobs_all"},
        {"op": A, "anchor": '"🧹 تنظيف البيانات": show_data,', "text": '"🧠 معرفة المشروع": show_knowledge,'},
        {"op": A, "anchor": "AuthManager().purge_expired_deletions()", "text": "cleanup_dead_jobs_all()"},
    ],
    "ai/ai_manager.py": [
        {"op": B, "anchor": "🆕 Smart Value Resolver (القيم النصية التي لا تطابق البيانات):", "text": '''🆕 الذاكرة المعرفية (core/knowledge_manager.py):
-------------------------------------------------
ask/tell_story/answer_question تضيف سياق المعرفة المخزَّن للمشروع إلى الـ prompt
(قسم DATA CONTEXT) عبر _knowledge_context — صامت: لا معرفة أو أي فشل = سلوك
سابق بلا تغيير. طبقتا المعرفة ب وج تستدعيان _run_planned_query بحرارة 0.0 صريحة
(معامل temperature) بدل self.temperature.
'''},
        {"op": A, "anchor": "from core.value_resolver import ValueResolver, build_variants_prompt, parse_variants_response",
         "text": "from core.knowledge_manager import KnowledgeManager"},
        {"op": A, "anchor": "return (elapsed + self.retry_delay) > budget", "text": '''
def _knowledge_context(self, question: str, schema: Optional[dict], mode: str,
                       dashboard_id: Optional[str] = None) -> Optional[str]:
    """سياق معرفة المشروع للـ prompt (None بصمت عند غيابه أو أي فشل)."""
    try:
        return KnowledgeManager(self.db).build_context(
            question, schema, mode=mode, filtered=bool(dashboard_id)) or None
    except Exception as e:
        logger.debug("knowledge context skipped: %s", e)
        return None
''', "indent_delta": -4},
        {"op": R, "anchor": "prompt = builder.build(question, result_type)", "text": (
            "prompt = builder.build(\n    question, result_type,\n"
            '    knowledge_context=self._knowledge_context(question, schema, "sql", dashboard_id),\n)')},
        {"op": R, "anchor": "prompt = builder.build_story_plan(question)", "text": (
            "prompt = builder.build_story_plan(\n    question,\n"
            '    knowledge_context=self._knowledge_context(question, schema, "sql", dashboard_id),\n)')},
        {"op": A, "anchor": "max_rows=STORY_SAMPLE_ROWS_IN_PROMPT,",
         "text": 'knowledge_context=self._knowledge_context(question, schema, "answer", dashboard_id),'},
        {"op": R, "anchor": "prompt = builder.build_qa_plan(question, history=history_turns, summary=summary)", "text": (
            "prompt = builder.build_qa_plan(\n    question, history=history_turns, summary=summary,\n"
            '    knowledge_context=self._knowledge_context(question, schema, "sql"),\n)')},
        {"op": A, "anchor": "summary=summary,", "text": 'knowledge_context=self._knowledge_context(question, schema, "answer"),'},
        {"op": A, "anchor": "budget       : Optional[int] = None,", "text": "temperature  : Optional[float] = None,"},
        {"op": R, "anchor": "send_result = self.engine.send(retry_prompt, self.temperature)", "text": (
            "send_result = self.engine.send(\n    retry_prompt, temperature if temperature is not None else self.temperature,\n)")},
    ],
}


@trace_calls
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    results, errors = {}, []
    for rel, ops in OPS.items():
        path = root / rel
        if not path.exists():
            errors.append(f"{rel}: الملف غير موجود")
            continue
        original = path.read_text(encoding="utf-8")
        lines = original.split("\n")
        for n, op in enumerate(ops, 1):
            lines, status = apply_op(lines, op)
            if status.startswith("error"):
                errors.append(f"{rel} [عملية {n}]: {status[7:]}")
            elif status == "skipped":
                print(f"  = {rel} [عملية {n}] مطبَّقة سابقاً")
        results[rel] = ("\n".join(lines), original)

    if errors:
        print("❌ لم يُكتب أي ملف. المشاكل:")
        for e in errors:
            print("  -", e)
        return 1

    changed = {rel: new for rel, (new, old) in results.items() if new != old}
    for rel in changed:
        print(f"  ✓ {rel}")
    if args.dry_run or not changed:
        print("dry-run: لا كتابة." if args.dry_run else "لا شيء للتعديل (كله مطبَّق).")
        return 0

    backup = root / "_knowledge_backup"
    backup.mkdir(exist_ok=True)
    for rel in changed:
        dest = backup / rel.replace("/", "__")
        shutil.copy2(root / rel, dest)
        (root / rel).write_text(changed[rel], encoding="utf-8")

    failed = []
    for rel in changed:
        try:
            py_compile.compile(str(root / rel), cfile=str(Path(tempfile.gettempdir()) / "kn_check.pyc"), doraise=True)
        except py_compile.PyCompileError as e:
            failed.append((rel, str(e)))
    if failed:
        for rel, msg in failed:
            print(f"❌ خطأ صياغة في {rel}: {msg}")
        for rel in changed:
            shutil.copy2(backup / rel.replace("/", "__"), root / rel)
        print("تمت استعادة كل الملفات الأصلية.")
        return 1
    print(f"✅ تم. نسخ الأصول في {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
