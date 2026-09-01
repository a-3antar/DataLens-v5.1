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
"""

import logging
from typing import Optional

from config import SAMPLE_ROWS

logger = logging.getLogger(__name__)

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
        filters    : Optional[list] = None,
    ) -> str:
        """
        بناء Prompt كامل لسؤال المستخدم.

        question    : السؤال بالعربية أو الإنجليزية
        result_type : "chart" | "table" | "gauge" | "kpi" | "story" | None
        filters     : قيود إضافية تُفرض على الاستعلام (من Slicers لوحة
                      المعلومات مثلاً)، بصيغة:
                      [{"table": "sales", "column": "المنطقة", "values": ["الرياض", "جدة"]}]
        """
        parts = []

        # 1. AI Rules
        parts.append(self._section("RULES", self.ai_rules))

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

        # 6. Filters (اختياري — قيود Slicers)
        if filters:
            parts.append(self._section("FILTERS", self._build_filters(filters)))

        # 7. Question
        parts.append(self._section("QUESTION", question))

        # 8. Output reminder
        parts.append("SQL QUERY:")

        prompt = "\n\n".join(parts)
        logger.debug("Prompt built: %d chars, %d tables, %d filters",
                     len(prompt), len(self.schema), len(filters or []))
        return prompt

    def _build_filters(self, filters: list) -> str:
        """صياغة قيود الفلترة (Slicers) كتعليمات واضحة للـ AI."""
        lines = [
            "يجب تقييد نتيجة الاستعلام بكل الشروط التالية معاً (AND فيما بينها):"
        ]
        for f in filters:
            values = f.get("values") or []
            if not values:
                continue
            vals_text = "، ".join(str(v) for v in values)
            lines.append(
                f'- من الجدول "{f["table"]}"، يجب أن يكون العمود "{f["column"]}" '
                f"ضمن إحدى القيم التالية فقط: {vals_text}"
            )
        lines.append(
            "أضف شرط WHERE مناسب يحقق هذه القيود. لو كان الاستعلام يحتاج جدولاً "
            "غير مذكور أصلاً في السؤال لتطبيق أحد هذه الشروط، اربطه عبر "
            "العلاقات المذكورة أعلاه (JOIN) إن وُجدت علاقة مناسبة."
        )
        return "\n".join(lines)

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
        """تلميح للـ AI عن نوع النتيجة المطلوبة."""
        hints = {
            "chart": (
                "النتيجة ستُعرض كـ chart.\n"
                "- أعد عموداً نصياً للمحور السيني (X)\n"
                "- أعد عموداً رقمياً واحداً أو اثنين للمحور الصادي (Y)\n"
                "- لا تعد أكثر من 20 صف"
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
                "- أعد أعمدة بأسماء عربية أو واضحة تساعد على فهم كل رقم\n"
                "- لا تتجاوز 200 صف"
            ),
        }
        return hints.get(result_type, f"نوع النتيجة: {result_type}")

    def _section(self, title: str, content: str) -> str:
        """تنسيق قسم في الـ Prompt."""
        border = "─" * 40
        return f"[{title}]\n{border}\n{content}"

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

        🆕 القواعد أدناه تطلب بنية منظَّمة وليست فقرة متصلة: عناوين
        فرعية '### '، نقاط منفصلة بحرف '• ' (وليس صيغة قوائم Markdown
        الرسمية '- '/'* ' — تفادياً لمشاكل محاذاة القوائم المتداخلة مع
        RTL في بعض المتصفحات)، تخصيص أرقام مهمة بـ '**bold**'، وعند
        وجود بيانات مقارَنة مناسبة: جدول Markdown حقيقي و/أو رسم نصي
        بسيط بالحرف '█'. طبقة العرض في ui/chat.py وcore/dashboard_cells/
        cells.py|base.py تعرض هذا النص عبر st.markdown مباشرة (بدون
        لفّه داخل <div> خام) حتى تُفسَّر كل هذه العناصر فعلياً — راجع
        تلك الملفات وui/common.py (تنسيق CSS للجداول والفقرات) للتفاصيل.

        question : سؤال المستخدم الأصلي
        df       : pandas.DataFrame — نتيجة الاستعلام الفعلية
        max_rows : أقصى عدد صفوف تُضمّن نصياً في الـ prompt
        """
        rules = (
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
            "4. ⚠️ مهم جداً: لا تستخدم إطلاقاً صيغة قوائم Markdown ('- ' أو '* ' أو "
            "أرقام '1. ' في بداية السطر). بدلاً من ذلك، لعرض حقيقة أو رقم منفصل، "
            "اكتب سطراً مستقلاً يبدأ بحرف نقطة '•' متبوعاً بمسافة ثم النص "
            "(مثال: '• بلغ متوسط راتب المدير 25000.0'). اترك سطراً فارغاً تماماً "
            "بين كل نقطة وأختها حتى تظهر كفقرة منفصلة.\n"
            "5. ضع الأرقام والقيم المهمة داخل '**تنسيق عريض**' لتبرز عن باقي النص.\n"
            "6. عند وجود مقارنة بين عدة فئات (مثل وظائف أو أقسام)، أضف جدول "
            "Markdown حقيقي يلخّص المقارنة (رأس الجدول + صف فاصل '---' + الصفوف)، "
            "مثال:\n"
            "   | الوظيفة | متوسط الراتب |\n"
            "   |---|---|\n"
            "   | مدير | 25000 |\n"
            "   | فني | 14000 |\n"
            "7. عند مقارنة قيم عددية قليلة (٢-٦ قيم) وتفاوتها لافت، يمكنك إضافة "
            "رسم بياني نصي بسيط لتوضيح الحجم النسبي باستخدام تكرار الحرف '█' "
            "بعدد متناسب (وليس رسماً فعلياً)، مثال:\n"
            "   مدير المصانع  25000  █████████████\n"
            "   فني الخراطة   14000  ███████\n"
            "   ضعه داخل قسم كود (```) حتى تحافظ الأعمدة على محاذاتها، ولا "
            "تستخدمه إلا إذا أضاف وضوحاً حقيقياً.\n"
            "8. اختم بعنوان '### الخلاصة' يحتوي فقرة سردية قصيرة (2-3 جمل) "
            "أو ملاحظة عملية، وليس نقاطاً أو جدولاً.\n"
            "9. لا تذكر أنك 'نموذج' أو تصف عملية التحليل نفسها — اكتب النتيجة مباشرة.\n"
            "10. لا تضع عنواناً رئيسياً بصيغة '#' أو '##' في بداية الرد — ابدأ "
            "مباشرة بأول '### عنوان فرعي'."
        )

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
