"""
ai/prompt_builder.py
====================
بناء الـ Prompt الكامل للـ AI:
  - AI Rules (system prompt)
  - Schema: الجداول + الأعمدة + الأنواع
  - Relations: علاقات الجداول
  - Sample Data: عينة بيانات
  - User Question: سؤال المستخدم
  - Error Context: سياق الخطأ عند إعادة المحاولة

سؤال وجواب (Q&A / رؤى):
-----------------------------
build_qa_plan / build_qa_answer (في نهاية الكلاس) — مرحلتان مثل
Story Telling (خطة استعلامات ثم كتابة). الإجابة تُكتب بأسلوب محلل
بيانات يرد بشكل طبيعي ومباشر: يبدأ بالجواب مسنوداً بالأرقام الفعلية،
ثم ما يلزم من سياق (إجمالي، نسب، مقارنات). صفوف البيانات المُرسَلة في
مرحلة الإجابة لها سقف عالٍ لكل مجموعة (_QA_DATA_MAX_ROWS_PER_DATASET)،
ويُطلب من AI في مرحلة التخطيط استخدام التجميع (Aggregate) كلما كانت
النتيجة كبيرة. الفلاتر يحددها
المستخدم داخل نص السؤال نفسه (لا Slicers في المحادثة).

🆕 جودة الإجابة (تحديث):
----------------------------
- خطة الاستعلامات (_qa_plan_rules): لا يكتفي AI باستعلام واحد حتى
  للسؤال البسيط — يضيف دائماً استعلام سياق (إجمالي/توزيع/مقارنة)
  حتى تحتوي الإجابة على أرقام كافية وليس رقماً معزولاً.
- قواعد الإجابة (_qa_answer_rules): أقصر وأوضح، تصف الأسلوب المطلوب
  (محلل يتحدث بشكل طبيعي، الأرقام أولاً، بلا مقدمات أو حشو) بدل قائمة
  طويلة من النواهي التي كانت تربك النماذج الأضعف. حتى الإجابة
  "المختصرة" تذكر الأرقام الأساسية المرتبطة بالسؤال.
- الإجابة تمر بقسم STYLE EXAMPLE قصير يوضح الشكل المطلوب بدل الاكتفاء
  بالتعليمات المجردة.

🆕 اختلاف لغة/كتابة القيم النصية (مثل «سانا جاز» مقابل SANA - GAS):
------------------------------------------------------------------------
- خطة الاستعلامات (_qa_plan_rules قاعدة 12): القيمة المخزَّنة قد تكون
  بلغة أو كتابة مختلفة عن السؤال؛ تُستخدم القيمة حرفياً كما في DATA
  CONTEXT/SAMPLE DATA لو ظهرت، وإلا يُضاف استعلام بحث SELECT DISTINCT.
- مرحلة الإجابة تستقبل notes (اختياري) → قسم [NOTES]: ملاحظات من
  النظام عن استعلامات رجعت بصفر صفوف بسبب عدم تطابق كتابة، مع أقرب
  القيم الموجودة فعلاً. قاعدة الدقة رقم 2 في _qa_answer_rules تمنع
  استنتاج "لا توجد بيانات" لكيان ظهر في استعلام آخر.

ذاكرة المحادثة الداخلية (آخر أسئلة config.QA_HISTORY_TURNS + ملخص تراكمي):
------------------------------------------------------------------------------
- build_qa_plan وbuild_qa_answer تستقبلان summary (اختياري) — ملخص
  تراكمي للمحادثة كلها يُوضَع في قسم [CONVERSATION SUMMARY] قبل قسم
  [PREVIOUS Q&A]، فلا ينسى AI ما سبق الأسئلة الأخيرة المرسَلة.
- الأسئلة السابقة تُرسَل مع أجوبتها، لكن بعد حذف جداول Markdown منها
  (_strip_tables) ثم اقتطاع الجواب عند _QA_HISTORY_ANSWER_MAX_CHARS.
  السبب: الجداول تضخّم الـ prompt وتنقل أخطاء الصياغة القديمة للجواب
  الجديد، والأرقام الجوهرية محفوظة أصلاً في الملخص التراكمي، والأرقام
  الجديدة تؤخذ دائماً من DATA وليس من السجل.
- build_qa_summary: prompt مستقل لتحديث الملخص (استدعاء AI منفصل يعمل
  بعد عرض نتيجة السؤال الأخير — راجع ui/chat.py).
- الذاكرة والملخص داخليان بالكامل: لا يُعرضان ولا يُعدَّلان من المستخدم.

🆕 تخفيف حجم الـ prompt (مرحلة الإجابة):
----------------------------------------------
- الأعداد العشرية في DATA تُقرَّب إلى _NUM_DECIMALS خانات (_fmt_cell)؛
  الأرقام الخام الطويلة (مثل 0.027334025286370783) تستهلك توكنات كثيرة
  وتدفع النموذج لنسخ أرقام مشوهة.
- سقف _QA_DATA_MAX_ROWS_PER_DATASET صفاً لكل مجموعة بيانات يقرؤها
  النموذج مع ذكر الإجمالي الفعلي؛ الجداول الكاملة تبقى ظاهرة للمستخدم
  في الواجهة (ui/chat.py) ولا تتأثر بهذا السقف.
- سطر تذكير قبل [QUESTION] يثبّت أن DATA الحالية هي مصدر الأرقام وأن
  السجل لفهم السياق فقط (لأسئلة المتابعة مثل «لآخر يوم فقط»).

🆕 تخفيف مرحلة الخطة (build_qa_plan):
------------------------------------------
- السجل في مرحلة الخطة مضغوط (compact): نص كل سؤال سابق + أول سطر فقط من
  جوابه (_compact_answer). الخطة تحتاج فهم المقصود ووراثة الفلاتر فقط، وهذا
  يغطيه الملخص التراكمي؛ قوائم الأصناف الطويلة في الأجوبة لا تفيد كتابة SQL.
- سياق المعرفة يُقتطع عند _QA_PLAN_KNOWLEDGE_MAX_CHARS (القيم الفعلية
  للأعمدة الفئوية في أوله فتبقى).
- قواعد الخطة (_qa_plan_rules) مدموجة (9 قواعد بدل 12) مع قاعدة صريحة: إن
  ذكر السؤال الجديد كياناً مختلفاً (عميل/منتج آخر) فلا يُورَّث فلتر الكيان
  السابق.

🆕 وضع Ollama الخفيف (lite=True):
-----------------------------------
النماذج المحلية لها نافذة سياق صغيرة (num_ctx ≈ 4096-6144 توكن) وتتجاهل
بداية الـ prompt إذا تجاوزته، لذا PromptBuilder(lite=True) يبني نسخاً أخف
من كل prompts سؤال وجواب (خطة / إجابة / ملخص): قواعد إنجليزية مختصرة
(العربية تستهلك توكنات أكثر بكثير) مع إلزام صريح بأن تكون الإجابة
والملخص وعناوين الاستعلامات بالعربية، بلا STYLE EXAMPLE، وبسياق معرفة
وسجل وصفوف بيانات أقل (_QA_LITE_*). لا يتغير شيء للمحركات السحابية
(lite=False افتراضياً) — يُفعَّل من ai/ai_manager.py عندما يكون المحرك
OllamaEngine.
"""


from core.logger_config import trace_calls

import re
import math
import logging
from typing import Optional

from config import (
    SAMPLE_ROWS, STORY_MAX_QUERIES, QA_MAX_QUERIES, QA_ANSWER_LENGTHS,
    QA_SUMMARY_MAX_CHARS,
)

logger = logging.getLogger(__name__)

# أقصى طول لجواب سابق يُضمَّن كسياق في prompt سؤال وجواب (بعد حذف
# الجداول منه). النص المتبقي جمل قصيرة تكفي لفهم أسئلة المتابعة، بينما
# الملخص التراكمي يغطي ما قبلها وما اقتُطع منها.
_QA_HISTORY_ANSWER_MAX_CHARS = 1000

# عدد الخانات العشرية القصوى للأعداد العشرية المرسَلة للنموذج في DATA
_NUM_DECIMALS = 3

# أقصى عدد صفوف لكل مجموعة بيانات تُرسَل للنموذج في مرحلة الإجابة.
# النتائج الأكبر عادةً لا تُقرأ صفاً صفاً؛ والخطة تُطلب منها التجميع.
_QA_DATA_MAX_ROWS_PER_DATASET = 150

# صف جدول Markdown (| ... |) — يُحذف من جواب سابق قبل إرساله كسياق
_MD_TABLE_ROW_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.MULTILINE)

# ── تخفيف مرحلة الخطة (كل المحركات) ──
# أقصى طول لسياق المعرفة في prompt الخطة (الافتراضي العام 6000 في config)
_QA_PLAN_KNOWLEDGE_MAX_CHARS = 3500
# أقصى طول لما يُؤخذ من جواب سابق في السجل المضغوط (أول سطر فقط)
_QA_COMPACT_ANSWER_CHARS = 150

# ── وضع Ollama الخفيف (lite=True) ──
_QA_LITE_KNOWLEDGE_MAX_CHARS = 1800
_QA_LITE_SUMMARY_MAX_CHARS = 1200
_QA_DATA_MAX_ROWS_LITE = 60

# أقصى طول لجواب يُضمَّن في prompt تحديث الملخص (الجواب الأحدث فقط)
_QA_SUMMARY_ANSWER_MAX_CHARS = 10000

# ── System Prompt الثابت ──────────────────────────────────────
DEFAULT_AI_RULES = """أنت محلل بيانات محترف ومهمتك إنتاج استعلام SQL صحيح قابل للتنفيذ على DuckDB.

القواعد:
1. أخرج استعلاماً واحداً فقط يبدأ بـ SELECT أو WITH وينتهي كاستعلام قراءة.
2. لا تكتب شرحاً أو Markdown أو JSON أو أكثر من استعلام.
3. استخدم أسماء الجداول والأعمدة كما تظهر حرفياً في SCHEMA؛ لا تخترع أسماء أو أعمدة بديلة.
4. استخدم علاقات RELATIONS المرفقة فقط عند الربط، وحدد شرط الربط صراحةً.
5. استخدم دوال DuckDB وصيغ التاريخ المتوافقة معه.
6. نفّذ التجميع المطلوب مع GROUP BY الصحيح، ولا تجمع أعمدة غير متوافقة.
7. لا تضع أي نص قبل SQL أو بعده."""


# قيود مشتركة مستقلة عن القواعد المخصصة التي قد يضيفها المستخدم.
SQL_COMMON_CONSTRAINTS = """- الاستعلام للقراءة فقط: SELECT أو WITH تنتهي بـ SELECT؛ يُمنع أي تعديل أو إنشاء أو حذف للبيانات.
- أنشئ استعلاماً واحداً فقط، ولا تضع عدة أوامر مفصولة بفواصل منقوطة.
- لا تستخدم LIMIT أو TOP أو FETCH FIRST إلا إذا طلب المستخدم صراحةً عدداً محدداً من الصفوف.
- لا تخمّن أسماء الجداول أو الأعمدة أو علاقات الربط. إذا لم تتوفر معلومة لازمة، استخدم أقرب بنية مؤكدة في SCHEMA ولا تختلق حقولاً.
- أسماء الأعمدة العربية أو التي تحتوي مسافات تُكتب كمعرّفات بين علامتي اقتباس مزدوجتين، أما القيم النصية فتُكتب بين علامتي اقتباس مفردتين.
- الجداول المتاحة قد تكون مفلترة مسبقاً بفلاتر خارجية؛ لا تعِد تطبيقها ولا تخمّنها. أضف WHERE فقط للشروط التي ذكرها المستخدم صراحةً."""


# قسم سياق المعرفة المُضاف للـ prompts (راجع core/knowledge_manager.py::build_context)
KNOWLEDGE_RULES = (
    "هذا القسم ملف معرفة مُستخرج مسبقاً عن البيانات الكاملة للمشروع (غير مفلترة). "
    "استخدمه للفهم والتلميح فقط (معاني الأعمدة، المرادفات، طريقة التجميع، القيم المعروفة). "
    "الأرقام تأتي من DATA أو نتيجة الاستعلام فقط، وعند التعارض تُقدَّم DATA. "
    "استخدم أسماء الأعمدة والقيم كما وردت حرفياً."
)

# ── نسخ إنجليزية مختصرة للوضع الخفيف (Ollama) ─────────────────
SQL_CONSTRAINTS_LITE = """- Read-only: one single SELECT (or WITH ... SELECT). No data changes. No multiple statements.
- Use table and column names exactly as written in SCHEMA. Never invent any.
- Quote Arabic or spaced column names with double quotes; text values with single quotes.
- No LIMIT unless the user asks for a specific number of rows.
- Tables may already be filtered externally: do not re-apply that. Add WHERE only for conditions the user stated."""

KNOWLEDGE_RULES_LITE = (
    "Pre-extracted notes about the FULL unfiltered data. Use them only to understand columns, "
    "synonyms, aggregation and known values. Numbers come from DATA only. "
    "Use names and values exactly as written."
)

class PromptBuilder:
    """
    بناء الـ Prompt الكامل للـ AI.

    الاستخدام:
        pb = PromptBuilder(schema, relations, ai_rules)
        prompt = pb.build("ما إجمالي المبيعات لكل منطقة؟", result_type="chart")
        error_prompt = pb.build_error_retry(prompt, bad_sql, error_msg)
    """

    @trace_calls
    def __init__(
        self,
        schema   : dict,
        relations: list,
        ai_rules : str = DEFAULT_AI_RULES,
        lite     : bool = False,
    ):
        """
        schema    : من db.get_schema() — {alias: {columns, sample}}
        relations : من db.get_relations() — [{from_table, from_col, to_table, to_col}]
        ai_rules  : system prompt مخصص أو الافتراضي
        lite      : وضع خفيف لمحركات النماذج المحلية (Ollama) — يؤثر فقط على
                    prompts سؤال وجواب (خطة/إجابة/ملخص)، راجع توثيق الوحدة.
        """
        self.schema    = schema
        self.relations = relations
        self.ai_rules  = ai_rules or DEFAULT_AI_RULES
        self.lite      = lite

    # ──────────────────────────────────────────────────────────
    #  بناء الـ Prompt الأساسي
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def build(
        self,
        question   : str,
        result_type: Optional[str] = None,
        knowledge_context: Optional[str] = None,
    ) -> str:
        """
        بناء Prompt كامل لسؤال المستخدم.

        question    : السؤال بالعربية أو الإنجليزية
        result_type : "chart" | "table" | "gauge" | "kpi" | "story" | None

        ⚠️ لا يوجد معامل filters عمداً: فلاتر لوحات المعلومات تُطبَّق في
        طبقة البيانات (core/dashboard_views.py + QueryEngine) قبل وصول
        البيانات إلى الذكاء الاصطناعي، فلا يراها في الـ prompt ولا يكتبها
        في الـ SQL.
        """
        parts = []

        # 1. AI Rules
        parts.append(self._section("RULES", self.ai_rules))

        # 1b. قيود مشتركة (لا LIMIT، لا إعادة تطبيق فلاتر خارجية)
        parts.append(self._section("SQL CONSTRAINTS", SQL_COMMON_CONSTRAINTS))

        # 2. Schema
        parts.append(self._section("SCHEMA", self._build_schema()))

        # 3. Relations (اختياري)
        if self.relations:
            parts.append(self._section("RELATIONS", self._build_relations()))

        # 4. Sample Data
        sample_text = self._build_samples()
        if sample_text:
            parts.append(self._section("SAMPLE DATA", sample_text))

        parts.extend(self._knowledge_parts(knowledge_context))
        # 5. Result Type hint (اختياري)
        if result_type:
            parts.append(self._section("RESULT TYPE", self._build_result_hint(result_type)))

        # 6. Question
        parts.append(self._section("QUESTION", question))

        # 7. Output reminder
        parts.append("SQL QUERY:")

        prompt = "\n\n".join(parts)
        logger.debug("Prompt built: %d chars, %d tables", len(prompt), len(self.schema))
        return prompt

    # ──────────────────────────────────────────────────────────
    #  بناء Prompt إعادة المحاولة عند الخطأ
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def build_error_retry(
        self,
        original_prompt: str,
        failed_sql     : str,
        error_message  : str,
    ) -> str:
        """
        بناء Prompt خاص لتصحيح SQL فشل.
        يحتوي على الـ prompt الأصلي + الـ SQL الخاطئ + رسالة الخطأ.
        """
        error_context = f"""الـ SQL التالي أنتج خطأ:

```sql
{failed_sql}
```

رسالة الخطأ:
{error_message}

المطلوب: اكتب SQL صحيح يحل هذا الخطأ.
تذكر:
- تحقق من أسماء الجداول والأعمدة في الـ Schema
- استخدم نفس أسماء الأعمدة بالضبط (حروف صغيرة)
- أجب بـ SQL فقط بدون أي شرح"""

        # تلميح إضافي لخطأ شائع مع النماذج الصغيرة: اسم عمود عربي مُقسَّم أو
        # مترجَم (مثل ال.slim) فيفسّره DuckDB كجدول غير موجود
        if "Referenced table" in (error_message or ""):
            error_context += (
                "\n- الخطأ يعني أنك كتبت نقطة داخل اسم عمود (مثل ال.slim) ففُسِّر كاسم جدول. "
                "أسماء الأعمدة عربية: انسخها حرفياً من الـ Schema بين علامتي اقتباس مزدوجتين "
                "مثل \"السليم\" دون ترجمة ودون نقطة قبلها."
            )

        # نضع السياق الجديد في نهاية الـ prompt الأصلي
        retry_prompt = original_prompt + "\n\n" + self._section("ERROR CONTEXT", error_context)
        logger.debug("Error retry prompt built for SQL: %s...", failed_sql[:50])
        return retry_prompt

    # ──────────────────────────────────────────────────────────
    #  بناء أجزاء الـ Prompt
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def _build_schema(self) -> str:
        """بناء وصف الجداول والأعمدة."""
        if not self.schema:
            return "لا توجد جداول محملة"

        lines = []
        for alias, info in self.schema.items():
            cols = info.get("columns", {})
            lines.append(f"TABLE: {alias}")
            for col_name, col_type in cols.items():
                lines.append(f"  - {col_name} ({col_type})")
            lines.append("")   # سطر فارغ بين الجداول

        return "\n".join(lines).rstrip()

    @trace_calls
    def _build_relations(self) -> str:
        """بناء وصف العلاقات بين الجداول."""
        lines = []
        for rel in self.relations:
            lines.append(
                f"{rel['from_table']}.{rel['from_col']} = "
                f"{rel['to_table']}.{rel['to_col']}"
            )
        return "\n".join(lines)

    @trace_calls
    def _build_samples(self) -> str:
        """بناء عينة البيانات لكل جدول."""
        if not self.schema:
            return ""

        parts = []
        for alias, info in self.schema.items():
            sample = info.get("sample", [])
            if not sample:
                continue

            cols = list(sample[0].keys()) if sample else []
            if not cols:
                continue

            # header
            header = " | ".join(cols)
            sep    = "-+-".join("-" * len(c) for c in cols)
            rows   = []
            for row in sample[:SAMPLE_ROWS]:
                values = " | ".join(str(row.get(c, "")) for c in cols)
                rows.append(values)

            parts.append(f"-- {alias} --")
            parts.append(header)
            parts.append(sep)
            parts.extend(rows)
            parts.append("")

        return "\n".join(parts).rstrip()


    @trace_calls
    def _build_result_hint(self, result_type: str) -> str:
        """تلميح للـ AI عن نوع النتيجة المطلوبة (بدون أي سقف لعدد الصفوف)."""
        hints = {
            "chart": (
                "النتيجة ستُعرض كـ chart.\n"
                "- أعد عموداً نصياً للمحور السيني (X)\n"
                "- أعد عموداً رقمياً واحداً أو اثنين للمحور الصادي (Y)"
            ),
            "table": (
                "النتيجة ستُعرض كـ جدول.\n"
                "- أعد كل الأعمدة المطلوبة بأسماء واضحة\n"
                "- رتّب البيانات بشكل منطقي"
            ),
            "gauge": (
                "النتيجة ستُعرض كـ gauge.\n"
                "- أعد صفاً واحداً فقط\n"
                "- أعد ثلاثة أعمدة: current_value, min_value, max_value"
            ),
            "kpi": (
                "النتيجة ستُعرض كـ KPI card.\n"
                "- أعد صفاً واحداً فقط\n"
                "- أعد عمودين: actual_value, target_value"
            ),
            "story": (
                "النتيجة ستُستخدم لكتابة تحليل نصي (سرد قصصي) عن البيانات.\n"
                "- أعد بيانات كافية ومفصّلة تسمح بتحليل حقيقي (وليس رقماً واحداً فقط إلا لو كان السؤال يطلب ذلك تحديداً)\n"
                "- أعد أعمدة بأسماء عربية أو واضحة تساعد على فهم كل رقم"
            ),
        }
        return hints.get(result_type, f"نوع النتيجة: {result_type}")

    @trace_calls
    def _knowledge_parts(self, knowledge_context: Optional[str]) -> list:
        """قسم [DATA CONTEXT] (قائمة فارغة لو لا سياق معرفة)."""
        ctx = (knowledge_context or "").strip()
        if not ctx:
            return []
        rules = KNOWLEDGE_RULES_LITE if self.lite else KNOWLEDGE_RULES
        return [self._section("DATA CONTEXT", rules + "\n\n" + ctx)]

    @trace_calls
    def _section(self, title: str, content: str) -> str:
        """تنسيق قسم في الـ Prompt."""
        border = "─" * 40
        return f"[{title}]\n{border}\n{content}"

    # ──────────────────────────────────────────────────────────
    #  قواعد كتابة السرد النصي — مشتركة بين build_story وbuild_story_multi
    # ──────────────────────────────────────────────────────────
    #
    # 🆕 استُبدل الرسم البياني النصي القائم على كتلة كود (```) بجدول
    # Markdown حقيقي (3 أعمدة: العنصر / النسبة / التوزيع النسبي) —
    # كتل الكود تُفرض داخلها اتجاه LTR صراحة في ui/common.py::RTL_CSS
    # (.stMarkdown pre { direction: ltr }), وهو ما كان يكسر محاذاة
    # الأعمدة مع تسميات عربية طويلة أو غير متساوية الطول. الجدول
    # Markdown العادي يمر عبر نفس قواعد تنسيق الجداول في RTL_CSS/
    # apply_theme_css (حدود، رأس بلون الثيم...) ويحافظ على محاذاة
    # الأعمدة تلقائياً بغض النظر عن طول النص العربي المجاور.
    @trace_calls
    def _story_writing_rules(self) -> str:
        return (
            "أنت محلل بيانات محترف تكتب تقارير تحليلية بالعربية الفصحى الواضحة، "
            "بصيغة Markdown منظَّمة (وليس فقرة نصية متصلة).\n\n"
            "القواعد الصارمة:\n"
            "1. اكتب تحليلاً نصياً (سرد قصصي/Storytelling) يشرح ما تدل عليه البيانات — "
            "وليس كوداً أو SQL أو JSON.\n"
            "2. استخدم فقط الأرقام والحقائق الموجودة فعلياً في البيانات أدناه — "
            "لا تخترع أرقاماً أو أسماء غير مذكورة.\n"
            "3. نظّم التحليل تحت عناوين فرعية بصيغة Markdown '### عنوان' "
            "(مثلاً: ### نظرة عامة، ### أبرز الملاحظات، ### الخلاصة) — عنوانان "
            "إلى أربعة عناوين حسب حجم التحليل، وليس عنواناً واحداً فقط.\n"
            "4. ⚠️ مهم جداً: لا تستخدم إطلاقاً صيغة قوائم Markdown القياسية ('- ' أو '* ' أو "
            "أرقام '1. ' في بداية السطر). بدلاً من ذلك، لعرض حقيقة أو رقم منفصل، "
            "اكتب سطراً مستقلاً يبدأ بحرف نقطة '•' متبوعاً بمسافة ثم النص "
            "(مثال: '• بلغ متوسط راتب المدير **25000.0**'). اترك سطراً فارغاً تماماً "
            "بين كل نقطة وأختها حتى تظهر كفقرة منفصلة.\n"
            "5. ضع الأرقام والقيم المهمة داخل '**تنسيق عريض**' لتبرز عن باقي النص.\n"
            "6. عند وجود مقارنة بين عدة فئات (مثل وظائف أو أقسام)، أضف جدول "
            "Markdown حقيقي يلخّص المقارنة (رأس الجدول + صف فاصل '---' + الصفوف)، "
            "مثال:\n"
            "   | الوظيفة | متوسط الراتب |\n"
            "   | :--- | :---: |\n"
            "   | مدير | 25000 |\n"
            "   | فني | 14000 |\n"
            "7. عند مقارنة قيم عددية قليلة (٢-١٠ قيم) وتفاوتها لافت، أضف رسم بياني "
            "نصي داخل جدول Markdown بدلاً من كتل الكود لتجنب مشاكل اتجاه النص "
            "(RTL) في Streamlit.\n"
            "- يتكون الجدول من 3 أعمدة: [العنصر] ، [النسبة] ، [التوزيع النسبي].\n"
            "- يتكون عمود التوزيع النسبي من شريط بطول ثابت (مثلاً 25 رمزاً) "
            "باستعمال '█' للنسبة المحققة و '░' للمتبقي.\n"
            "- ⚠️ اجعل رأس الجدول (الصف الأول) فارغاً تماماً بدون أي عناوين "
            "أعمدة (لا تكتب 'العنصر'/'النسبة'/'التوزيع النسبي' في هذا الصف) — "
            "فقط ثلاث خلايا فارغة مفصولة بـ '|'، حتى لا يظهر رأس جدول مرئي "
            "فوق البيانات، مع الإبقاء على صف الفاصل '---' كما هو (مطلوب "
            "لصحة صيغة Markdown نفسها).\n"
            "مثال لتنسيق الجدول المطلوب:\n\n"
            "|  |  |  |\n"
            "| :--- | :---: | :--- |\n"
            "| محبس بليه | 56.8% | ██████████████░░░░░░░░░░░ |\n"
            "| اكسسوار | 14.3% | ████░░░░░░░░░░░░░░░░░░░░░ |\n"
            "| محبس زاويه تلميع | 12.9% | ███░░░░░░░░░░░░░░░░░░░░░░ |\n\n"
            "لا تستخدم كتل الكود (```) للرسوم البيانية إطلاقاً لضمان سلامة "
            "المحاذاة والعرض.\n"
            "8. اختم بعنوان '### الخلاصة' يحتوي فقرة سردية قصيرة (2-3 جمل) "
            "أو ملاحظة عملية، وليس نقاطاً أو جدولاً.\n"
            "9. لا تذكر أنك 'نموذج' أو تصف عملية التحليل نفسها — اكتب النتيجة مباشرة.\n"
            "10. لا تضع عنواناً رئيسياً بصيغة '#' أو '##' في بداية الرد — ابدأ "
            "مباشرة بأول '### عنوان فرعي'."
        )

    # ──────────────────────────────────────────────────────────
    #  بناء Prompt السرد القصصي (Story Telling)
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def build_story(
        self,
        question: str,
        df,
        max_rows: int = 30,
    ) -> str:
        """
        بناء Prompt لكتابة تحليل نصي (سرد) بالعربية بناءً على بيانات
        فعلية تم الحصول عليها من تنفيذ SQL مسبقاً (وليس Schema فقط).

        🆕 القواعد (_story_writing_rules) تطلب بنية منظَّمة وليست فقرة
        متصلة: عناوين فرعية '### '، نقاط منفصلة بحرف '• ' (وليس صيغة
        قوائم Markdown الرسمية '- '/'* ' — تفادياً لمشاكل محاذاة القوائم
        المتداخلة مع RTL في بعض المتصفحات)، تخصيص أرقام مهمة بـ
        '**bold**'، وعند وجود بيانات مقارَنة مناسبة: جدول Markdown حقيقي
        للمقارنة النصية، وجدول Markdown آخر (3 أعمدة: عنصر/نسبة/توزيع
        نسبي بالحرفين █/░) بدل كتلة كود — لأن كتل الكود تُفرض داخلها LTR
        صراحة في ui/common.py::RTL_CSS، مما يكسر محاذاة الأعمدة مع
        تسميات عربية. طبقة العرض في ui/chat.py وcore/dashboard_cells/
        cells.py|base.py تعرض هذا النص عبر st.markdown مباشرة (بدون لفّه
        داخل <div> خام) حتى تُفسَّر كل هذه العناصر فعلياً — راجع تلك
        الملفات وui/common.py (تنسيق CSS للجداول والفقرات) للتفاصيل.

        question : سؤال المستخدم الأصلي
        df       : pandas.DataFrame — نتيجة الاستعلام الفعلية
        max_rows : أقصى عدد صفوف تُضمّن نصياً في الـ prompt
        """
        rules = self._story_writing_rules()

        rows = df.head(max_rows).to_dict(orient="records")
        cols = list(df.columns)
        lines = [" | ".join(cols)]
        for row in rows:
            lines.append(" | ".join(str(row.get(c, "")) for c in cols))
        data_text = "\n".join(lines)

        note = ""
        if len(df) > max_rows:
            note = f"\n(ملاحظة: تُعرض أول {max_rows} صفاً فقط من إجمالي {len(df)} صفاً)"

        parts = [
            self._section("RULES", rules),
            self._section("DATA", data_text + note),
            self._section("QUESTION", question),
            "التحليل النصي (Markdown):",
        ]
        prompt = "\n\n".join(parts)
        logger.debug("Story prompt built: %d chars, %d rows", len(prompt), len(rows))
        return prompt

    # ──────────────────────────────────────────────────────────
    #  🆕 خطة استعلامات السرد (Story Telling متعدد الاستعلامات)
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def build_story_plan(self, question: str, knowledge_context: Optional[str] = None) -> str:
        """
        بناء Prompt يطلب من AI خطة استعلامات SQL (وليس استعلاماً واحداً)
        تجيب مجتمعة على سؤال المستخدم تمهيداً لكتابة سرد شامل. AI هو من
        يقرر عدد الاستعلامات (بحد أقصى STORY_MAX_QUERIES) ونوع كل واحد.

        لا فلاتر هنا عمداً — فلاتر اللوحة تُطبَّق في طبقة البيانات.
        """
        parts = [
            self._section("RULES", self._story_plan_rules()),
            self._section("SQL CONSTRAINTS", SQL_COMMON_CONSTRAINTS),
            self._section("SCHEMA", self._build_schema()),
        ]
        if self.relations:
            parts.append(self._section("RELATIONS", self._build_relations()))
        sample_text = self._build_samples()
        if sample_text:
            parts.append(self._section("SAMPLE DATA", sample_text))
        parts.extend(self._knowledge_parts(knowledge_context))
        parts.append(self._section("QUESTION", question))
        parts.append("JSON PLAN:")
        prompt = "\n\n".join(parts)
        logger.debug("Story plan prompt built: %d chars", len(prompt))
        return prompt

    @trace_calls
    def _story_plan_rules(self) -> str:
        return f"""أنت محلل بيانات محترف تخطط لتحليل نصي (Story Telling) شامل بناءً على
سؤال المستخدم. مهمتك: تحديد استعلام SQL واحد أو أكثر تجلب كل البيانات
اللازمة لكتابة تحليل غني ومتعدد الجوانب.

القواعد الصارمة:
1. أجب فقط بصيغة JSON صالحة — بدون أي شرح أو نص إضافي أو Markdown code fences.
2. الشكل المطلوب بالضبط (بدون أي مفاتيح إضافية):
{{"queries": [{{"title": "عنوان قصير يصف البيانات", "sql": "SELECT ..."}}]}}
3. عدد الاستعلامات بين 1 و {STORY_MAX_QUERIES} — اختر العدد المناسب فعلياً
لتغطية السؤال (زاوية واحدة قد تكفي، وأحياناً يحتاج السؤال عدة زوايا:
اتجاه زمني، توزيع حسب فئة، مقارنة بين مجموعات...).
4. كل SQL يجب أن يكون SELECT فقط (ممنوع DROP/DELETE/INSERT/UPDATE/ALTER/
CREATE)، متوافقاً مع DuckDB، ويستخدم فقط الجداول والأعمدة المذكورة في
الـ Schema.
5. لا تكرر نفس الاستعلام بصياغات مختلفة — كل استعلام يجب أن يضيف زاوية
أو تفصيلاً مختلفاً فعلياً عن سؤال المستخدم.
6. إذا لم تكن متأكدًا تماماً من وجود عمود معين، لا تستخدمه في الاستعلام.
7. استخدم فقط أسماء الجداول من غير alias المذكورة في الـ Schema.
"""

    # ──────────────────────────────────────────────────────────
    #  🆕 بناء Prompt السرد من عدة مجموعات بيانات (نتائج عدة استعلامات)
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def build_story_multi(self, question: str, datasets: list, max_rows: int = 30,
                          knowledge_context: Optional[str] = None) -> str:
        """
        نفس فلسفة build_story القديمة (وتشترك معها في نفس نص القواعد
        عبر _story_writing_rules، بما فيها قاعدة الرسم البياني كجدول
        Markdown بدل كتلة كود)، لكن تستقبل datasets: قائمة
        {"title": "...", "df": DataFrame} بدل DataFrame واحد — كل
        مجموعة بيانات تُعرض تحت عنوانها الخاص ضمن قسم DATA، ليبني AI
        سرداً واحداً متكاملاً يربط بينها.
        """
        rules = self._story_writing_rules() + (
            "\n11. لا تذكر عناوين مجموعات البيانات كما هي حرفياً (مثل '### بيانات المبيعات') "
            "كعناوين فرعية للتحليل — استخدم عناوين تحليلية (نظرة عامة، أبرز الملاحظات...) "
            "تدمج المعلومات من كل المجموعات معاً."
        )

        data_parts = []
        for ds in datasets:
            title = ds["title"]
            df = ds["df"]
            rows = df.head(max_rows).to_dict(orient="records")
            cols = list(df.columns)
            lines = [" | ".join(cols)]
            for row in rows:
                lines.append(" | ".join(str(row.get(c, "")) for c in cols))
            data_text = "\n".join(lines)
            note = ""
            if len(df) > max_rows:
                note = f"\n(ملاحظة: تُعرض أول {max_rows} صفاً فقط من إجمالي {len(df)} صفاً)"
            data_parts.append(f"### {title}\n{data_text}{note}")

        full_data_text = "\n\n".join(data_parts)

        parts = [
            self._section("RULES", rules),
            *self._knowledge_parts(knowledge_context),
            self._section("DATA", full_data_text),
            self._section("QUESTION", question),
            "التحليل النصي (Markdown):",
        ]
        prompt = "\n\n".join(parts)
        logger.debug("Story-multi prompt built: %d chars, %d dataset(s)", len(prompt), len(datasets))
        return prompt

    # ══════════════════════════════════════════════════════════
    #  🆕 سؤال وجواب (Q&A / رؤى)
    # ══════════════════════════════════════════════════════════

    @staticmethod
    @trace_calls
    def _strip_tables(answer: str) -> str:
        """
        حذف صفوف جداول Markdown من جواب سابق قبل إدراجه كسياق. الجداول
        تتكرر عادةً بين الأسئلة المتتالية وتحمل أخطاء صياغة قديمة قد
        يقلّدها النموذج، وأرقامها الجوهرية موجودة في الملخص التراكمي.
        الجمل النصية في الجواب تبقى كما هي.
        """
        text = _MD_TABLE_ROW_RE.sub("", answer)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    @staticmethod
    @trace_calls
    def _compact_answer(answer: str) -> str:
        """
        أول سطر نصي من جواب سابق (بعد حذف الجداول وعلامات التنسيق **)
        مقتطعاً عند _QA_COMPACT_ANSWER_CHARS — يكفي لمعرفة ما أُجيب عنه
        دون قوائم الأصناف الطويلة التي لا تفيد التخطيط.
        """
        text = PromptBuilder._strip_tables(answer).replace("**", "")
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if len(first) > _QA_COMPACT_ANSWER_CHARS:
            first = first[:_QA_COMPACT_ANSWER_CHARS].rstrip() + " …"
        return first

    @staticmethod
    @trace_calls
    def _limit_text(text: Optional[str], max_chars: int) -> str:
        """اقتطاع نص عند حد أقصى، مع القص عند آخر سطر كامل قدر الإمكان."""
        text = (text or "").strip()
        if len(text) <= max_chars:
            return text
        cut = text[:max_chars]
        nl = cut.rfind("\n")
        return (cut[:nl] if nl > max_chars // 2 else cut).rstrip()

    @staticmethod
    @trace_calls
    def _fmt_cell(value) -> str:
        """
        تنسيق قيمة خلية في DATA: الأعداد العشرية تُقرَّب إلى _NUM_DECIMALS
        خانات بدون أصفار زائدة وبدون صيغة علمية (4930.0 → 4930،
        0.027334 → 0.027). غير ذلك (نص، عدد صحيح، None، NaN) يُحوَّل لنص
        كما هو.
        """
        if isinstance(value, float) and math.isfinite(value):
            text = f"{value:.{_NUM_DECIMALS}f}".rstrip("0").rstrip(".")
            return "0" if text in ("", "-0") else text
        return str(value)

    @staticmethod
    @trace_calls
    def _format_qa_history(history: Optional[list], compact: bool = False) -> str:
        """
        صياغة آخر أسئلة/أجوبة الجلسة كسياق. كل عنصر:
        {"question": "...", "answer": "..."} — تُحذف جداول Markdown من
        الجواب (_strip_tables) ثم يُقتطع (راجع _QA_HISTORY_ANSWER_MAX_CHARS)
        لإبقاء الـ prompt بحجم معقول؛ ما سبقها وما حُذف منها يغطيه الملخص
        التراكمي. تُستخدم هذه الدالة في مرحلتي الخطة والإجابة معاً.

        compact=True: السؤال كاملاً + أول سطر من الجواب فقط (_compact_answer)
        — للخطة (لا تحتاج تفاصيل الأجوبة) ولوضع Ollama الخفيف.
        """
        if not history:
            return ""
        lines = []
        for i, turn in enumerate(history, 1):
            q = str(turn.get("question", "")).strip()
            raw = str(turn.get("answer", "")).strip()
            if compact:
                a = PromptBuilder._compact_answer(raw)
                lines.append(f"[{i}] Q: {q}\n    A: {a}")
                continue
            a = PromptBuilder._strip_tables(raw)
            if len(a) > _QA_HISTORY_ANSWER_MAX_CHARS:
                a = a[:_QA_HISTORY_ANSWER_MAX_CHARS].rstrip() + " …"
            lines.append(f"[{i}] سؤال: {q}\n    جواب: {a}")
        return "\n".join(lines)

    @staticmethod
    @trace_calls
    def _format_qa_summary(summary: Optional[str], lite: bool = False) -> str:
        """
        نص الملخص التراكمي جاهزاً للإدراج في الـ prompt (فارغ لو لا يوجد).
        lite=True: ترويسة إنجليزية قصيرة، والملخص يُقتطع عند
        _QA_LITE_SUMMARY_MAX_CHARS (الأحدث في آخره فيُحتفظ بالنهاية).
        """
        summary = (summary or "").strip()
        if not summary:
            return ""
        if lite:
            if len(summary) > _QA_LITE_SUMMARY_MAX_CHARS:
                summary = summary[-_QA_LITE_SUMMARY_MAX_CHARS:].lstrip()
            return (
                "Cumulative summary of the earlier conversation (context for follow-up "
                "questions only; numbers come from DATA only):\n" + summary
            )
        return (
            "ملخص تراكمي لكل المحادثة السابقة (يشمل ما قبل الأسئلة والأجوبة المذكورة "
            "في PREVIOUS Q&A). هذا سياق داخلي لفهم أسئلة المتابعة فقط — "
            "ليس مصدراً لأرقام جديدة (الأرقام تأتي من DATA فقط):\n" + summary
        )

    @trace_calls
    def build_qa_plan(
        self,
        question: str,
        history : Optional[list] = None,
        summary : Optional[str] = None,
        knowledge_context: Optional[str] = None,
    ) -> str:
        """
        بناء Prompt يطلب من AI خطة استعلامات SQL تجيب على سؤال
        المستخدم (بحث/فلترة/تجميع/مقارنة) — الفلاتر يحددها المستخدم في
        نص السؤال نفسه ويترجمها AI إلى شروط WHERE.

        history (اختياري): آخر أسئلة/أجوبة الجلسة لحل مراجع أسئلة المتابعة.
        summary (اختياري): الملخص التراكمي للمحادثة كلها (داخلي).
        """
        lite = self.lite
        parts = [
            self._section("RULES", self._qa_plan_rules_lite() if lite else self._qa_plan_rules()),
            self._section("SQL CONSTRAINTS", SQL_CONSTRAINTS_LITE if lite else SQL_COMMON_CONSTRAINTS),
            self._section("SCHEMA", self._build_schema()),
        ]
        if self.relations:
            parts.append(self._section("RELATIONS", self._build_relations()))
        sample_text = self._build_samples()
        if sample_text:
            parts.append(self._section("SAMPLE DATA", sample_text))
        knowledge_limit = _QA_LITE_KNOWLEDGE_MAX_CHARS if lite else _QA_PLAN_KNOWLEDGE_MAX_CHARS
        parts.extend(self._knowledge_parts(self._limit_text(knowledge_context, knowledge_limit)))
        summary_text = self._format_qa_summary(summary, lite)
        if summary_text:
            parts.append(self._section("CONVERSATION SUMMARY", summary_text))
        # الخطة تحتاج فهم المقصود ووراثة الفلاتر فقط → سجل مضغوط دائماً
        history_text = self._format_qa_history(history, compact=True)
        if history_text:
            parts.append(self._section("PREVIOUS Q&A", history_text))
        parts.append(self._section("QUESTION", question))
        parts.append("JSON PLAN:")
        prompt = "\n\n".join(parts)
        logger.debug("QA plan prompt built: %d chars", len(prompt))
        return prompt

    @trace_calls
    def _qa_plan_rules(self) -> str:
        """قواعد خطة الاستعلامات (9 قواعد مدموجة؛ كانت 12 بتداخل في قواعد الأعمدة والقيم)."""
        return f"""أنت محلل بيانات محترف. سيسألك المستخدم سؤالاً حراً عن بياناته، ومهمتك الآن
فقط تحديد استعلامات SQL التي تجلب كل ما يلزم للإجابة عليه بدقة وبأرقام كافية.

القواعد الصارمة:
1. أجب فقط بصيغة JSON صالحة — بدون شرح أو Markdown code fences، وبالشكل بالضبط:
{{"queries": [{{"title": "عنوان قصير يصف البيانات", "sql": "SELECT ..."}}]}}
2. عدد الاستعلامات بين 1 و {QA_MAX_QUERIES}. الاستعلام الأول يجيب مباشرة على السؤال، ثم أضف
من 1 إلى 3 استعلامات داعمة تعطي سياقاً رقمياً (لا تكتفِ برقم معزول): الإجمالي أو المتوسط
للمقارنة، التوزيع حسب فئة ذات صلة بترتيب تنازلي، الاتجاه الزمني، أو أعلى وأدنى القيم.
السؤال التحليلي أو الذي يطلب رأياً يستحق عدة زوايا. لا تكرر استعلاماً بصياغة أخرى.
3. كل SQL هو SELECT فقط، متوافق مع DuckDB، ويستخدم الجداول والأعمدة الموجودة في الـ Schema
بأسمائها حرفياً بنفس الإملاء (ه/ة، أ/ا...) وبين علامتي اقتباس مزدوجتين، دون ترجمتها أو تقسيمها
أو وضع نقطة قبلها (خطأ: ال.slim ؛ صواب: "السليم"). لا تخترع عموداً لمفهوم ورد في السؤال؛ إن لم
يوجد عمود مناسب فاحذف هذا الجانب بدل تخمين اسم.
4. أي شرط ذكره المستخدم (منتج، فترة، عميل، حالة...) يُترجم إلى WHERE دقيق. للتواريخ استخدم
عمود التاريخ الفعلي مع BETWEEN أو DATE_TRUNC/EXTRACT. الاستعلامات الداعمة تُبنى على نفس
الفلاتر ما لم تحتج الإجمالي الكلي دون فلتر (اذكر ذلك في عنوانها).
5. القيم النصية المخزَّنة قد تختلف في اللغة أو الكتابة عمّا كتبه المستخدم (مثلاً «سانا جاز»
مقابل SANA - GAS). إن ظهرت القيمة الفعلية في DATA CONTEXT أو SAMPLE DATA فاستخدمها حرفياً
بالتطابق (=). وإلا استخدم ILIKE '%جزء%' مع عدة صيغ محتملة (عربي OR لاتيني)، أو أضف
استعلام بحث (SELECT DISTINCT عمود FROM جدول) على العمود المعني بدل التخمين.
6. لا يوجد حد أقصى للصفوف عند طلب قائمة أو تفاصيل. لكن إن كانت النتيجة كبيرة جداً ولا
تفيد قراءتها صفاً صفاً (مثل «حلل بياناتي»)، استخدم التجميع (GROUP BY مع SUM/AVG/COUNT).
7. أعطِ كل عمود ناتج من تجميع اسماً واضحاً (AS) يصف معناه.
8. إن كان السؤال متابعة لما سبق («وماذا عن...»، «لآخر يوم فقط»، «اعد الجواب كجدول»)، استخدم
CONVERSATION SUMMARY وPREVIOUS Q&A لفهم المقصود وورِّث منهما الفلاتر التي لم يغيّرها
المستخدم صراحةً.
9. إن ذكر السؤال الجديد كياناً مختلفاً (عميل أو منتج أو قسم آخر) فهو المرجع: لا تورِّث فلتر
الكيان السابق من المحادثة."""

    @trace_calls
    def _sql_example(self) -> str:
        """
        مثال صيغة SQL صحيحة مبني من أول جدول في الـ Schema الفعلي (أسماء
        حقيقية بين علامتي اقتباس). النماذج الصغيرة تتعلم من المثال أكثر
        من القاعدة المجردة، وخصوصاً أن تنسخ اسم العمود العربي كاملاً
        بدل ترجمته أو تقسيمه (مثل ال.slim بدل "السليم").
        """
        for alias, info in (self.schema or {}).items():
            cols = list(info.get("columns", {}).items())
            if not cols:
                continue
            group_col = cols[0][0]
            num_col = next(
                (c for c, t in cols if str(t).upper() in ("REAL", "INTEGER", "FLOAT", "DOUBLE", "BIGINT")),
                cols[-1][0],
            )
            return f'SELECT "{group_col}", SUM("{num_col}") AS "total" FROM {alias} GROUP BY "{group_col}"'
        return ""

    @trace_calls
    def _qa_plan_rules_lite(self) -> str:
        """نسخة إنجليزية مختصرة من قواعد الخطة لمحركات Ollama (توكنات أقل). عناوين الاستعلامات بالعربية."""
        return f"""You are a data analyst. The user asks a free question about their data. Your ONLY job now is to write the SQL queries that fetch everything needed to answer it.

Rules:
1. Reply with valid JSON only, no explanation, no code fences, exactly:
{{"queries": [{{"title": "short Arabic title", "sql": "SELECT ..."}}]}}
2. Between 1 and {QA_MAX_QUERIES} queries. Query 1 answers the question directly; add 1-3 supporting queries for context (overall total/average, distribution by a related category, monthly trend, top/bottom values). Never repeat a query.
3. SELECT only, DuckDB syntax. Use table and column names EXACTLY as in SCHEMA (same spelling). Never invent a column; if none fits, drop that part.
   Column names are Arabic: copy each one character by character from SCHEMA inside double quotes. NEVER translate a column name to English, NEVER split it, and NEVER put a dot or prefix before it (wrong: ال.slim ; right: "السليم"). Valid pattern: {self._sql_example()}
4. Translate every user condition (customer, product, period, status...) into a precise WHERE. Dates: use the real date column with BETWEEN or DATE_TRUNC/EXTRACT. Supporting queries keep the same filters unless they need the unfiltered total.
5. Stored text values may be written in another language/spelling than the question (Arabic vs Latin, dashes). If the real value appears in DATA CONTEXT or SAMPLE DATA, use it exactly. Otherwise use ILIKE '%part%' with several likely spellings (OR), or add a lookup query (SELECT DISTINCT column FROM table).
6. No row limit for lists. If a result would be huge and unreadable, aggregate (GROUP BY with SUM/AVG/COUNT).
7. Give every aggregated column a clear alias (AS).
8. Follow-up questions ("and what about...", "only the last day", "show it as a table"): use CONVERSATION SUMMARY and PREVIOUS Q&A to understand the reference and inherit filters the user did not change.
9. If the new question names a DIFFERENT entity (another customer/product/department), the new question wins: do NOT inherit the previous entity filter."""

    @trace_calls
    def _qa_answer_rules(self, answer_length: str) -> str:
        """
        🆕 قواعد كتابة الجواب — تصف الأسلوب المطلوب (محلل بيانات يتحدث
        بشكل طبيعي ومهني، الأرقام أولاً، بلا مقدمات أو حشو) بدل سرد
        طويل من النواهي. الشكل يتناسب مع نوع السؤال، والطول يتبع اختيار
        المستخدم، لكن الإجابة المختصرة نفسها تذكر الأرقام الأساسية.
        """
        if answer_length not in QA_ANSWER_LENGTHS:
            answer_length = "short"

        if answer_length == "short":
            length_rules = (
                "الطول المطلوب: مختصر لكن مدعوم بالأرقام (حوالي 3 إلى 6 جمل أو ما يعادلها).\n"
                "• الجملة الأولى هي الجواب المباشر بالرقم أو الاسم المطلوب.\n"
                "• ثم اذكر ما يعطي الرقم معناه من البيانات: النسبة من الإجمالي، المقارنة بالأعلى "
                "أو بالمتوسط أو بالفترة السابقة، أو أبرز عنصرين مرتبطين بالسؤال — بأرقامها الفعلية.\n"
                "• إن كان السؤال يطلب قائمة أو ترتيباً فاعرضها كاملة بأرقامها (جدول أو نقاط) "
                "دون اقتطاع.\n"
                "• لا تضف رأياً أو توصية إلا إذا كان هناك ما يستحق التنبيه فعلاً."
            )
        else:
            length_rules = (
                "الطول المطلوب: مفصّل ومنظَّم بقدر ما تحتمله البيانات، بلا حشو أو تكرار.\n"
                "• ابدأ بالجواب المباشر بأرقامه، ثم وسِّعه: النسب والحصص، المقارنات بين الفئات، "
                "أعلى وأدنى القيم، الاتجاه عبر الزمن، والقيم الشاذة إن وُجدت.\n"
                "• استخدم جدولاً عند مقارنة عدة عناصر، وعناوين فرعية قليلة وبأسماء مناسبة إن "
                "تعددت الأجزاء.\n"
                "• في نهاية الإجابة أضف فقرة قصيرة بعنوان '### ما يلفت الانتباه' تضم ما تراه مهماً "
                "من تفسير محتمل أو توصية عملية، مع تمييز الرأي عن الحقيقة، وذلك فقط إن كان "
                "السؤال تحليلياً أو يحتمل قراراً."
            )

        return (
            "أنت محلل بيانات خبير يرد على مدير سأل عن بياناته. اكتب بالعربية الفصحى السليمة "
            "الواضحة (راجع إملاءك ونحوك قبل الإرسال)، بأسلوب طبيعي ومهني كأنك تشرح له وجهاً "
            "لوجه، بصيغة Markdown. البيانات في قسم DATA هي نتائج استعلامات نُفِّذت فعلاً "
            "على بياناته ومصدرك الوحيد للحقائق.\n\n"
            "الأسلوب المطلوب:\n"
            "• ادخل في الجواب من أول كلمة: لا مقدمات ولا تكرار للسؤال ولا عبارات مثل "
            "'بناءً على البيانات' أو 'بعد تحليل' أو 'يسعدني'، ولا خاتمة إنشائية.\n"
            "• الأرقام هي أساس الجواب: اذكر القيم الفعلية (مجاميع، متوسطات، نسب مئوية، "
            "أعداد) داخل الجمل بتنسيق عريض مثل **12,500**. كل ادعاء يقترن برقمه من DATA.\n"
            "• اجعل الشكل يناسب السؤال: رقم أو اسم محدد ← جملة مباشرة ثم سياقها الرقمي؛ "
            "قائمة أو ترتيب ← جدول أو نقاط؛ مقارنة ← جدول قصير؛ تحليل أو رأي ← شرح مدعوم بالأرقام.\n"
            "• لعرض نقاط منفصلة اكتب كل نقطة في سطر مستقل يبدأ بالحرف '•' ثم مسافة، واترك سطراً "
            "فارغاً بين النقاط. لا تستخدم '- ' أو '* ' أو '1. ' في بداية السطر.\n"
            "• عند عرض جدول استخدم جدول Markdown حقيقياً (رأس + صف فاصل '---' + الصفوف). لا تستخدم "
            "كتل الكود (```) ولا عنواناً رئيسياً بـ '#' أو '##'. لا تذكر SQL أو أسماء الجداول التقنية.\n"
            "• أجب بنفس لغة سؤال المستخدم، ولا تذكر أنك نموذج ولا تصف خطوات التحليل.\n\n"
            "قواعد الدقة:\n"
            "1. استخدم فقط الأرقام والأسماء الموجودة حرفياً في DATA — لا تخترع ولا تقدّر. الحسابات "
            "البسيطة (فرق، نسبة، مجموع) مسموحة بشرط أن تكون صحيحة.\n"
            "2. إذا كانت نتيجة استعلام فارغة (0 صف) فلا تستنتج منها وحدها أن الكيان المسؤول عنه "
            "غير موجود: ابحث أولاً في بقية مجموعات DATA وفي قسم NOTES (إن وُجد) — فقد يكون السبب "
            "اختلاف كتابة الاسم (لغة أو رموز) لا غياب البيانات، وقد يظهر الكيان نفسه بكتابته "
            "الفعلية في استعلام آخر (مثل جدول مقارنة بين كل العملاء). عندها اعتمد تلك الصف/الصفوف "
            "بأرقامها وأجب بها مباشرة دون أن تبدأ بعبارة 'لا توجد بيانات'. لا تقل إن البيانات غير "
            "متوفرة إلا إذا لم تجد الكيان في أي مجموعة DATA إطلاقاً، وعندها اذكر الشرط الذي طُبِّق "
            "كما فهمته.\n"
            "3. الاستعلامات المذكورة في FAILED QUERIES لم تكتمل: لا تبنِ عليها أي رقم، وأشر في "
            "جملة واحدة إلى أن جزءاً من التحليل لم يكتمل.\n"
            "4. فرِّق بين الحقيقة (ما تقوله البيانات) والرأي. الأسباب المحتملة تُكتب كاحتمالات "
            "('يبدو أن'، 'قد يعود إلى') لأن البيانات لا تثبت السببية. لا تستخدم معلومات من خارج البيانات.\n"
            "5. CONVERSATION SUMMARY وPREVIOUS Q&A سياق للمحادثة فقط: افهم منهما ما يشير إليه "
            "السؤال الجديد ('وماذا عن...') واربط الإجابة بما سبق عند الحاجة دون تكراره، لكن الأرقام "
            "الجديدة تؤخذ من DATA فقط. عند التعارض فـ DATA هي المعتمدة.\n"
            "6. قسم NOTES (إن وُجد) ملاحظات من النظام عن استعلامات رجعت فارغة بسبب عدم تطابق "
            "كتابة قيمة نصية، مع أقرب القيم الموجودة فعلاً. هي للفهم فقط وليست مصدراً لأرقام، "
            "ولا تذكرها للمستخدم بصيغة تقنية.\n\n"
            + length_rules
        )

    @trace_calls
    def _qa_answer_rules_lite(self, answer_length: str) -> str:
        """
        نسخة إنجليزية مختصرة من قواعد الإجابة لمحركات Ollama. الإجابة نفسها
        يجب أن تكون بالعربية (الفصحى الواضحة)؛ الإنجليزية للتعليمات فقط لأنها
        أخف بكثير في التوكنات.
        """
        if answer_length not in QA_ANSWER_LENGTHS:
            answer_length = "short"
        if answer_length == "short":
            length_rules = (
                "Length: short but backed by numbers (about 3-6 sentences). First sentence = the direct "
                "answer. Then add context from DATA (share of total, comparison, top 2 items). "
                "If the user asks for a list or ranking, show it fully. No opinion unless truly needed."
            )
        else:
            length_rules = (
                "Length: detailed but no filler. Start with the direct answer, then shares, comparisons, "
                "highest/lowest, trend, outliers. Use a table for comparisons. For analytical questions "
                "end with a short '### ما يلفت الانتباه' paragraph (possible interpretation/recommendation, "
                "clearly marked as opinion)."
            )
        return (
            "You are an expert data analyst answering a manager. Write the answer in clear Modern Standard "
            "Arabic (check spelling), Markdown. [DATA] holds results of queries already executed on his data: "
            "it is your ONLY source of facts.\n\n"
            "Rules:\n"
            "1. Start with the answer itself: no intro, no repeating the question, no closing remarks.\n"
            "2. Numbers first: put real values in bold, e.g. **12,500**. Every claim needs a number from DATA. "
            "Simple math (difference, %, sum) is allowed if correct. Never invent or estimate numbers or names.\n"
            "3. Tables/rankings/comparisons: a real Markdown table (header row + '---' row + rows). "
            "No code blocks, no '#'/'##' headings. For separate points write each on its own line starting "
            "with '• ' (never '- ', '* ' or '1. ') and leave a blank line between points.\n"
            "4. SUMMARY and PREVIOUS Q&A only help you understand follow-up questions; DATA always wins.\n"
            "5. If a query returned 0 rows, look in the other DATA sets and in NOTES before saying data is "
            "missing: the entity may be spelled differently. Never say 'no data' if it appears elsewhere.\n"
            "6. FAILED QUERIES are incomplete: use no number from them and mention it in one sentence.\n"
            "7. Possible causes are guesses: write them as possibilities ('يبدو أن', 'قد يعود إلى'). "
            "No outside information. Do not mention SQL or table names or that you are a model.\n\n"
            + length_rules
        )

    @staticmethod
    @trace_calls
    def _qa_style_example(answer_length: str) -> str:
        """
        🆕 مثال قصير على الأسلوب المطلوب (يوضح الشكل لا المحتوى). أرقام
        المثال وهمية ويُنبَّه النموذج صراحةً بعدم استخدامها.
        """
        if answer_length == "long":
            body = (
                "بلغ إجمالي مبيعات مارس **1,250,000** بزيادة **12.4%** عن فبراير (**1,112,000**).\n\n"
                "• جاء **58%** من المبيعات من منتج (أ) بقيمة **725,000**، يليه منتج (ب) بـ **310,000**.\n\n"
                "• أضعف منتج هو (د) بـ **42,000** فقط، أي أقل من **4%** من الإجمالي.\n\n"
                "### ما يلفت الانتباه\n"
                "يبدو أن النمو يعتمد على منتج واحد بدرجة كبيرة، وقد يستحق تنويع المبيعات اهتماماً."
            )
        else:
            body = (
                "بلغ إجمالي مبيعات مارس **1,250,000**، بزيادة **12.4%** عن فبراير (**1,112,000**). "
                "وجاء **58%** منها من منتج (أ) بقيمة **725,000**، يليه منتج (ب) بـ **310,000**."
            )
        return (
            "مثال على الأسلوب والتنسيق فقط (الأرقام والأسماء وهمية — لا تستخدمها ولا تنسخ "
            "صياغتها، واعتمد على DATA الفعلية وحدها):\n" + body
        )

    @trace_calls
    def build_qa_answer(
        self,
        question     : str,
        datasets     : list,
        answer_length: str = "short",
        history      : Optional[list] = None,
        failed       : Optional[list] = None,
        summary      : Optional[str] = None,
        knowledge_context: Optional[str] = None,
        notes        : Optional[list] = None,
    ) -> str:
        """
        بناء Prompt كتابة الإجابة النهائية (شكلها يتناسب مع السؤال وطول
        الرد المختار، وتتضمن الأرقام الأساسية دائماً).

        datasets : [{"title": "...", "df": DataFrame}] — تُضمَّن الصفوف حتى
                   _QA_DATA_MAX_ROWS_PER_DATASET لكل مجموعة (مع ذكر العدد
                   الكلي إن اقتُطعت)، والأعداد العشرية تُقرَّب (_fmt_cell).
                   الجداول الكاملة تبقى ظاهرة للمستخدم في الواجهة.
        failed   : [{"title": "...", "error": "..."}] — استعلامات فشلت
                   ولم تدخل التحليل، لتُذكر كقيد في الإجابة.
        summary  : الملخص التراكمي للمحادثة (داخلي) — يُوضَع قبل
                   PREVIOUS Q&A.
        notes    : 🆕 قائمة نصوص ملاحظات من النظام عن استعلامات رجعت
                   بصفر صفوف بسبب عدم تطابق كتابة قيمة نصية (مع أقرب
                   القيم الموجودة فعلاً) — تُوضَع في قسم NOTES بعد DATA.
        """
        if answer_length not in QA_ANSWER_LENGTHS:
            answer_length = "short"

        data_parts = []
        for i, ds in enumerate(datasets, 1):
            df = ds["df"]
            cols = list(df.columns)
            if self.lite:
                header = f"-- DATA {i}: {ds['title']} ({len(df)} rows) --"
            else:
                header = f"-- بيانات {i}: {ds['title']} ({len(df)} صف) --"
            if df.empty:
                data_parts.append(f"{header}\n" + ("(no matching rows)" if self.lite else "(لا توجد صفوف مطابقة)"))
                continue
            max_rows = _QA_DATA_MAX_ROWS_LITE if self.lite else _QA_DATA_MAX_ROWS_PER_DATASET
            shown = df.head(max_rows)
            lines = [" | ".join(str(c) for c in cols)]
            for row in shown.to_dict(orient="records"):
                lines.append(" | ".join(self._fmt_cell(row.get(c, "")) for c in cols))
            if len(df) > len(shown):
                lines.append(
                    f"(showing first {len(shown)} of {len(df)} rows)" if self.lite
                    else f"(معروض أول {len(shown)} صفاً من إجمالي {len(df)} صفاً)"
                )
            data_parts.append(header + "\n" + "\n".join(lines))

        if self.lite:
            # وضع Ollama الخفيف: قواعد إنجليزية مختصرة، بلا STYLE EXAMPLE، وسياق أقل
            parts = [self._section("RULES", self._qa_answer_rules_lite(answer_length))]
            parts.extend(self._knowledge_parts(
                self._limit_text(knowledge_context, _QA_LITE_KNOWLEDGE_MAX_CHARS)))
        else:
            parts = [self._section("RULES", self._qa_answer_rules(answer_length))]
            parts.append(self._section("STYLE EXAMPLE", self._qa_style_example(answer_length)))
            parts.extend(self._knowledge_parts(knowledge_context))
        summary_text = self._format_qa_summary(summary, self.lite)
        if summary_text:
            parts.append(self._section("CONVERSATION SUMMARY", summary_text))
        history_text = self._format_qa_history(history, compact=self.lite)
        if history_text:
            parts.append(self._section("PREVIOUS Q&A", history_text))
        parts.append(self._section("DATA", "\n\n".join(data_parts)))
        if notes:
            parts.append(self._section("NOTES", "\n".join(f"- {n}" for n in notes)))
        if failed:
            failed_text = "\n".join(f"- {f['title']}: {f.get('error', '')}" for f in failed)
            parts.append(self._section("FAILED QUERIES", failed_text))
        reminder = (
            "Use ONLY the current DATA for numbers; history and summary are for context only. "
            "For a follow-up (e.g. 'last day only') answer for everything DATA covers unless the "
            "user names a specific subset. Write the answer in Arabic."
            if self.lite else
            "اعتمد على DATA الحالية وحدها في الأرقام؛ السجل والملخص لفهم السياق فقط. "
            "إن كان السؤال متابعة (مثل «لآخر يوم فقط») فأجب عن كل ما تغطيه DATA ما لم "
            "يطلب المستخدم مجموعة محددة."
        )
        parts.append(self._section("REMINDER", reminder))
        parts.append(self._section("QUESTION", question))
        parts.append("Answer in Arabic (Markdown):" if self.lite else "الإجابة (Markdown):")

        prompt = "\n\n".join(parts)
        logger.debug(
            "QA answer prompt built: %d chars, %d dataset(s), length=%s",
            len(prompt), len(datasets), answer_length,
        )
        return prompt

    # ──────────────────────────────────────────────────────────
    #  🆕 تحديث الملخص التراكمي للمحادثة (استفسار منفصل — داخلي)
    # ──────────────────────────────────────────────────────────

    @trace_calls
    def _qa_summary_rules(self) -> str:
        return f"""أنت تدير ذاكرة محادثة بين مدير ومحلل بيانات. مهمتك: دمج الملخص السابق
(إن وُجد) مع آخر سؤال وجواب في ملخص واحد محدَّث، يُستخدم لاحقاً فقط ليفهم
المحلل أسئلة المتابعة دون أن ينسى ما دار في المحادثة.

القواعد الصارمة:
1. أجب بنص الملخص فقط — بدون مقدمة أو عنوان أو شرح أو Markdown code fences.
2. لا يتجاوز الملخص {QA_SUMMARY_MAX_CHARS} حرفاً إطلاقاً. لو ضاق المكان، اختصر أقدم
التفاصيل أولاً وأبقِ الأحدث والأهم.
3. احتفظ بما ينفع أسئلة المتابعة: موضوع المحادثة وأهداف المستخدم، الفلاتر
والفترات والمنتجات والأقسام التي ذكرها، الأرقام والنتائج الرئيسية (مع
تسمية ما تخصه)، الاستنتاجات والتوصيات المهمة، وأي تفضيل عبّر عنه المستخدم.
4. احذف الصياغة والتفاصيل الثانوية والجداول الكاملة — الأرقام الجوهرية فقط.
5. لا تخترع أي معلومة أو رقم غير موجود في الملخص السابق أو السؤال/الجواب الجديدين.
6. اكتب بصيغة نقاط قصيرة، كل نقطة في سطر مستقل تبدأ بالحرف '•' ثم مسافة.
7. اكتب بنفس لغة المحادثة (العربية افتراضياً)."""

    @trace_calls
    def _qa_summary_rules_lite(self) -> str:
        """نسخة إنجليزية مختصرة لقواعد تحديث الملخص (Ollama) — الناتج بالعربية."""
        return f"""You maintain the memory of a conversation between a manager and a data analyst. Merge the PREVIOUS SUMMARY (if any) with the NEW question and answer into ONE updated summary, used later only to understand follow-up questions.

Rules:
1. Output the summary text only: no intro, no title, no code fences.
2. Write it in Arabic, as short bullet lines, each starting with '• '.
3. Maximum {QA_SUMMARY_MAX_CHARS} characters. If space is short, drop the oldest details first.
4. Keep: topic and goals, filters/periods/customers/products mentioned, key numbers (say what each belongs to), important conclusions, user preferences (e.g. wants tables).
5. Drop wording, secondary details and full tables. Never invent anything."""

    @trace_calls
    def build_qa_summary(self, old_summary: Optional[str], question: str, answer: str) -> str:
        """
        بناء Prompt تحديث الملخص التراكمي: الملخص السابق + آخر سؤال
        وجواب → ملخص جديد بحد أقصى QA_SUMMARY_MAX_CHARS حرف.
        """
        answer = (answer or "").strip()
        if len(answer) > _QA_SUMMARY_ANSWER_MAX_CHARS:
            answer = answer[:_QA_SUMMARY_ANSWER_MAX_CHARS].rstrip() + " …"

        old = (old_summary or "").strip() or "(لا يوجد ملخص سابق — هذه بداية المحادثة)"

        parts = [
            self._section("RULES", self._qa_summary_rules_lite() if self.lite else self._qa_summary_rules()),
            self._section("PREVIOUS SUMMARY", old),
            self._section("NEW QUESTION", str(question).strip()),
            self._section("NEW ANSWER", answer),
            "Updated summary (Arabic):" if self.lite else "الملخص المحدَّث:",
        ]
        prompt = "\n\n".join(parts)
        logger.debug("QA summary prompt built: %d chars", len(prompt))
        return prompt