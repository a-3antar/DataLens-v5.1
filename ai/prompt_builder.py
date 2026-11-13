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

🆕 سؤال وجواب (Q&A / رؤى):
-----------------------------
build_qa_plan / build_qa_answer (في نهاية الكلاس) — مرحلتان مثل
Story Telling (خطة استعلامات ثم كتابة)، لكن الإجابة هنا تبدأ بالجواب
المباشر على سؤال المستخدم ثم الرؤى ثم رأي AI وتوصيته، بدل تقرير
سردي ثابت الهيكل. لا يوجد سقف لعدد صفوف البيانات المُرسَلة في
مرحلة الإجابة (تظهر كاملة)، ويُطلب من AI في مرحلة التخطيط استخدام
التجميع (Aggregate) كلما كانت النتيجة كبيرة. الفلاتر يحددها المستخدم
داخل نص السؤال نفسه (لا Slicers في المحادثة).
"""

import logging
from typing import Optional

from config import SAMPLE_ROWS, STORY_MAX_QUERIES, QA_MAX_QUERIES, QA_ANSWER_LENGTHS

logger = logging.getLogger(__name__)

# أقصى طول لجواب سابق يُضمَّن كسياق في prompt سؤال وجواب (الجواب
# الكامل غير ضروري لفهم سؤال المتابعة، والاقتطاع يُبقي الـ prompt صغيراً).
_QA_HISTORY_ANSWER_MAX_CHARS = 700

# ── System Prompt الثابت ──────────────────────────────────────
DEFAULT_AI_RULES = """أنت محلل بيانات محترف. مهمتك الوحيدة هي كتابة SQL query صحيح.

القواعد الصارمة:
1. أجب فقط بـ SQL query — لا شرح، لا مقدمة، لا تعليقات
2. استخدم فقط الجداول والأعمدة المذكورة في الـ Schema
3. الاستعلام يجب أن يكون SELECT فقط (لا DROP, DELETE, INSERT, UPDATE)
4. اكتب SQL متوافق مع DuckDB
5. أسماء الجداول والأعمدة تكون بحروف صغيرة كما في الـ Schema
6. إذا كان السؤال يتطلب تجميع بيانات استخدم GROUP BY
7. لا تضع أي نص قبل أو بعد الـ SQL"""


# قيود مشتركة تُضاف دائماً كقسم مستقل في كل prompt يولّد SQL — مستقلة عن
# ai_rules المخصصة (التي تستبدل DEFAULT_AI_RULES بالكامل لو ضبطها المستخدم).
SQL_COMMON_CONSTRAINTS = """- لا تستخدم LIMIT (ولا TOP ولا FETCH FIRST) إطلاقاً، إلا إذا طلب المستخدم صراحةً في سؤاله عدداً محدداً من الصفوف (مثل: أعلى 10، أول 5).
- الجداول المتاحة لك قد تكون مفلترة مسبقاً بفلاتر خارجية؛ لا تحاول إعادة تطبيقها ولا تخمينها. أضف شروط WHERE فقط للشروط التي ذكرها المستخدم صراحةً في سؤاله."""


class PromptBuilder:
    """
    بناء الـ Prompt الكامل للـ AI.

    الاستخدام:
        pb = PromptBuilder(schema, relations, ai_rules)
        prompt = pb.build("ما إجمالي المبيعات لكل منطقة؟", result_type="chart")
        error_prompt = pb.build_error_retry(prompt, bad_sql, error_msg)
    """

    def __init__(
        self,
        schema   : dict,
        relations: list,
        ai_rules : str = DEFAULT_AI_RULES,
    ):
        """
        schema    : من db.get_schema() — {alias: {columns, sample}}
        relations : من db.get_relations() — [{from_table, from_col, to_table, to_col}]
        ai_rules  : system prompt مخصص أو الافتراضي
        """
        self.schema    = schema
        self.relations = relations
        self.ai_rules  = ai_rules or DEFAULT_AI_RULES

    # ──────────────────────────────────────────────────────────
    #  بناء الـ Prompt الأساسي
    # ──────────────────────────────────────────────────────────

    def build(
        self,
        question   : str,
        result_type: Optional[str] = None,
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

        # نضع السياق الجديد في نهاية الـ prompt الأصلي
        retry_prompt = original_prompt + "\n\n" + self._section("ERROR CONTEXT", error_context)
        logger.debug("Error retry prompt built for SQL: %s...", failed_sql[:50])
        return retry_prompt

    # ──────────────────────────────────────────────────────────
    #  بناء أجزاء الـ Prompt
    # ──────────────────────────────────────────────────────────

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

    def _build_relations(self) -> str:
        """بناء وصف العلاقات بين الجداول."""
        lines = []
        for rel in self.relations:
            lines.append(
                f"{rel['from_table']}.{rel['from_col']} = "
                f"{rel['to_table']}.{rel['to_col']}"
            )
        return "\n".join(lines)

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
        نسبي بالحرفين █/░) بدل كتلة كود للرسم البياني — لأن كتل الكود
        تُفرض داخلها LTR صراحة في ui/common.py::RTL_CSS، مما يكسر محاذاة
        الأعمدة مع تسميات عربية. طبقة العرض في ui/chat.py وcore/
        dashboard_cells/cells.py|base.py تعرض هذا النص عبر st.markdown
        مباشرة (بدون لفّه داخل <div> خام) حتى تُفسَّر كل هذه العناصر
        فعلياً — راجع تلك الملفات وui/common.py (تنسيق CSS للجداول
        والفقرات) للتفاصيل.

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

    def build_story_plan(self, question: str) -> str:
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
        parts.append(self._section("QUESTION", question))
        parts.append("JSON PLAN:")
        prompt = "\n\n".join(parts)
        logger.debug("Story plan prompt built: %d chars", len(prompt))
        return prompt

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

    def build_story_multi(self, question: str, datasets: list, max_rows: int = 30) -> str:
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
    def _format_qa_history(history: Optional[list]) -> str:
        """
        صياغة آخر أسئلة/أجوبة الجلسة كسياق مختصر. كل عنصر:
        {"question": "...", "answer": "..."} — الجواب يُقتطع (راجع
        _QA_HISTORY_ANSWER_MAX_CHARS) لأن المطلوب فقط فهم مراجع أسئلة
        المتابعة، وليس إعادة إرسال الإجابات الكاملة.
        """
        if not history:
            return ""
        lines = []
        for i, turn in enumerate(history, 1):
            q = str(turn.get("question", "")).strip()
            a = str(turn.get("answer", "")).strip()
            if len(a) > _QA_HISTORY_ANSWER_MAX_CHARS:
                a = a[:_QA_HISTORY_ANSWER_MAX_CHARS].rstrip() + " …"
            lines.append(f"[{i}] سؤال: {q}\n    جواب: {a}")
        return "\n".join(lines)

    def build_qa_plan(self, question: str, history: Optional[list] = None) -> str:
        """
        بناء Prompt يطلب من AI خطة استعلامات SQL تجيب على سؤال
        المستخدم (بحث/فلترة/تجميع/مقارنة) — الفلاتر يحددها المستخدم في
        نص السؤال نفسه ويترجمها AI إلى شروط WHERE. history (اختياري):
        آخر أسئلة/أجوبة الجلسة لحل مراجع أسئلة المتابعة.
        """
        parts = [
            self._section("RULES", self._qa_plan_rules()),
            self._section("SQL CONSTRAINTS", SQL_COMMON_CONSTRAINTS),
            self._section("SCHEMA", self._build_schema()),
        ]
        if self.relations:
            parts.append(self._section("RELATIONS", self._build_relations()))
        sample_text = self._build_samples()
        if sample_text:
            parts.append(self._section("SAMPLE DATA", sample_text))
        history_text = self._format_qa_history(history)
        if history_text:
            parts.append(self._section("PREVIOUS Q&A", history_text))
        parts.append(self._section("QUESTION", question))
        parts.append("JSON PLAN:")
        prompt = "\n\n".join(parts)
        logger.debug("QA plan prompt built: %d chars", len(prompt))
        return prompt

    def _qa_plan_rules(self) -> str:
        return f"""أنت محلل بيانات محترف. سيسألك المستخدم سؤالاً حراً عن بياناته، ومهمتك الآن
فقط تحديد استعلامات SQL التي تجلب كل ما يلزم للإجابة عليه بدقة، ثم استخراج
رؤى (Insights) منه لاحقاً.

القواعد الصارمة:
1. أجب فقط بصيغة JSON صالحة — بدون أي شرح أو نص إضافي أو Markdown code fences.
2. الشكل المطلوب بالضبط (بدون أي مفاتيح إضافية):
{{"queries": [{{"title": "عنوان قصير يصف البيانات", "sql": "SELECT ..."}}]}}
3. عدد الاستعلامات بين 1 و {QA_MAX_QUERIES}. سؤال بسيط (رقم محدد/قائمة) يكفيه
استعلام واحد. سؤال عن "رؤى/تحليل/رأيك" يحتاج عدة زوايا: الإجمالي كأساس
للمقارنة، التوزيع حسب فئة، الاتجاه الزمني إن وُجد عمود تاريخ، أعلى وأدنى القيم.
4. كل SQL يجب أن يكون SELECT فقط (ممنوع DROP/DELETE/INSERT/UPDATE/ALTER/CREATE)،
متوافقاً مع DuckDB، ويستخدم فقط الجداول والأعمدة المذكورة في الـ Schema
بأسمائها كما هي بالضبط (من غير alias للجداول في الـ Schema).
5. أي شرط أو فلتر ذكره المستخدم في سؤاله (منتج، فترة، قسم، حالة...) يجب ترجمته
إلى WHERE دقيق. للقيم النصية استخدم ILIKE '%قيمة%' بدل التطابق الحرفي
= حتى لا تفشل المطابقة بسبب اختلاف بسيط في الكتابة. للتواريخ استخدم
عمود التاريخ الفعلي مع BETWEEN أو دوال DuckDB مثل DATE_TRUNC/EXTRACT.
6. إذا كان السؤال يشير لقيمة قد لا تعرف كتابتها الدقيقة في البيانات، أضف
استعلام بحث إضافي (SELECT DISTINCT ... WHERE ... ILIKE ... ) بدل التخمين.
7. لا يوجد حد أقصى لعدد الصفوف — أعد كل الصفوف المطلوبة كاملة عندما يطلب
السؤال قائمة أو تفاصيل. لكن لو كانت النتيجة المتوقعة كبيرة جداً ولا تفيد
قراءتها صفاً صفاً (مثل "حلل بياناتي" على آلاف الصفوف)، استخدم التجميع
(GROUP BY مع SUM/AVG/COUNT/MIN/MAX) بدل إرجاع الصفوف الخام.
8. أعطِ كل عمود ناتج من تجميع اسماً واضحاً (AS) يصف معناه.
9. إذا كان السؤال متابعة لسؤال سابق (يستخدم "وماذا عن..."، "نفس الشيء لـ...")،
استخدم قسم PREVIOUS Q&A لفهم المقصود.
10. لا تكرر استعلاماً بصياغة مختلفة — كل استعلام يضيف زاوية مختلفة فعلاً.
11. إذا لم تكن متأكداً من وجود عمود، لا تستخدمه."""

    def _qa_answer_rules(self, answer_length: str) -> str:
        if answer_length not in QA_ANSWER_LENGTHS:
            answer_length = "short"

        if answer_length == "short":
            length_rules = (
                "طول الإجابة: **مختصرة**.\n"
                "• ### الإجابة: جملة أو جملتان تجيبان مباشرة على السؤال بالرقم أو الاسم المطلوب.\n"
                "• ### أبرز الرؤى: من 2 إلى 3 نقاط فقط، كل نقطة جملة واحدة مدعومة برقم.\n"
                "• ### رأيي وتوصيتي: جملة إلى جملتين.\n"
                "• لا تضف جداول إلا إذا طلب السؤال قائمة أو مقارنة صراحةً."
            )
        else:
            length_rules = (
                "طول الإجابة: **مفصّلة**.\n"
                "• ### الإجابة: فقرة قصيرة تجيب مباشرة على السؤال مع الأرقام الأساسية.\n"
                "• ### أبرز الرؤى: من 4 إلى 7 نقاط مدعومة بالأرقام (مقارنات، نسب، اتجاهات، "
                "أعلى/أدنى القيم، قيم شاذة).\n"
                "• أضف جدول Markdown واحداً أو أكثر عند وجود مقارنة بين فئات (رأس جدول حقيقي "
                "بأسماء أعمدة + صف فاصل '---' + الصفوف).\n"
                "• ### رأيي وتوصيتي: من 3 إلى 5 نقاط عملية: تفسير محتمل لما تظهره البيانات، "
                "مخاطر أو فرص، وخطوات مقترحة."
            )

        return (
            "أنت محلل بيانات خبير تجيب على سؤال مدير عن بياناته بالعربية الواضحة، بصيغة "
            "Markdown منظَّمة. البيانات أدناه هي نتائج استعلامات SQL نُفِّذت فعلاً على "
            "بياناته، وهي مصدرك الوحيد للحقائق.\n\n"
            "قواعد الدقة (الأهم):\n"
            "1. استخدم فقط الأرقام والأسماء الموجودة حرفياً في البيانات — لا تخترع ولا "
            "تقدّر ولا تُكمل أرقاماً ناقصة. الحسابات البسيطة (فرق، نسبة، مجموع صفوف ظاهرة) "
            "مسموحة بشرط أن تكون صحيحة.\n"
            "2. إذا كانت نتيجة استعلام فارغة (0 صف)، قل صراحةً إنه لا توجد بيانات مطابقة "
            "للسؤال، واذكر الشرط/الفلتر الذي طُبِّق (كما فهمته من السؤال) بدل تخمين إجابة.\n"
            "3. إذا فشل بعض الاستعلامات (مذكورة في قسم FAILED QUERIES)، لا تبنِ عليها أي "
            "رقم، واذكر في ### ملاحظات أن جزءاً من التحليل لم يكتمل.\n"
            "4. افصل بوضوح بين الحقائق (ما تقوله البيانات) والرأي (تفسيرك وتوصيتك). "
            "الأسباب المحتملة لظاهرة ما تُكتب كاحتمالات ('يبدو أن'، 'قد يعود إلى') وليس كحقائق "
            "مؤكدة، لأن البيانات لا تثبت السببية.\n"
            "5. لا تستخدم أي معلومة خارج البيانات (أسعار سوق، منافسون...) كأنها حقيقة عن بياناته.\n\n"
            "قواعد الشكل:\n"
            "6. ابدأ مباشرة بأول '### عنوان' — لا مقدمة ولا عنواناً رئيسياً بـ '#' أو '##'.\n"
            "7. العناوين بالترتيب: '### الإجابة' ثم '### أبرز الرؤى' ثم '### رأيي وتوصيتي'، "
            "وأضف '### ملاحظات' في النهاية فقط عند وجود قيد حقيقي (بيانات ناقصة، قيم فارغة، "
            "فترة زمنية محدودة، استعلام فشل...) — لا تضفه لمجرد الإضافة.\n"
            "8. ⚠️ لا تستخدم إطلاقاً قوائم Markdown القياسية ('- ' أو '* ' أو '1. ' في بداية "
            "السطر). لعرض نقطة منفصلة اكتب سطراً مستقلاً يبدأ بالحرف '•' ثم مسافة ثم النص، "
            "واترك سطراً فارغاً بين كل نقطة وأختها.\n"
            "9. ضع الأرقام والقيم المهمة داخل '**تنسيق عريض**'.\n"
            "10. لا تستخدم كتل الكود (```) إطلاقاً، ولا تذكر SQL أو أسماء الجداول التقنية.\n"
            "11. لا تذكر أنك 'نموذج' ولا تصف عملية التحليل نفسها — اكتب النتيجة مباشرة.\n"
            "12. أجب بنفس لغة سؤال المستخدم (العربية افتراضياً).\n\n"
            + length_rules
        )

    def build_qa_answer(
        self,
        question     : str,
        datasets     : list,
        answer_length: str = "short",
        history      : Optional[list] = None,
        failed       : Optional[list] = None,
    ) -> str:
        """
        بناء Prompt كتابة الإجابة النهائية (إجابة مباشرة + رؤى + رأي).

        datasets : [{"title": "...", "df": DataFrame}] — كل البيانات تُضمَّن
                   كاملة بلا اقتطاع (قرار صريح: لا سقف للصفوف هنا).
        failed   : [{"title": "...", "error": "..."}] — استعلامات فشلت
                   ولم تدخل التحليل، لتُذكر كقيد في الإجابة.
        """
        data_parts = []
        for i, ds in enumerate(datasets, 1):
            df = ds["df"]
            cols = list(df.columns)
            header = f"-- بيانات {i}: {ds['title']} ({len(df)} صف) --"
            if df.empty:
                data_parts.append(f"{header}\n(لا توجد صفوف مطابقة)")
                continue
            lines = [" | ".join(str(c) for c in cols)]
            for row in df.to_dict(orient="records"):
                lines.append(" | ".join(str(row.get(c, "")) for c in cols))
            data_parts.append(header + "\n" + "\n".join(lines))

        parts = [self._section("RULES", self._qa_answer_rules(answer_length))]
        history_text = self._format_qa_history(history)
        if history_text:
            parts.append(self._section("PREVIOUS Q&A", history_text))
        parts.append(self._section("DATA", "\n\n".join(data_parts)))
        if failed:
            failed_text = "\n".join(f"- {f['title']}: {f.get('error', '')}" for f in failed)
            parts.append(self._section("FAILED QUERIES", failed_text))
        parts.append(self._section("QUESTION", question))
        parts.append("الإجابة (Markdown):")

        prompt = "\n\n".join(parts)
        logger.debug(
            "QA answer prompt built: %d chars, %d dataset(s), length=%s",
            len(prompt), len(datasets), answer_length,
        )
        return prompt
