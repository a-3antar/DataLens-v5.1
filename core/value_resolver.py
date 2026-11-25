"""
core/value_resolver.py
========================
Smart Value Resolver — طبقة مستقلة تكتشف "القيمة المقصودة" عندما يُنتج
الذكاء الاصطناعي استعلاماً صحيحاً تقنياً لكنه يرجع 0 صف لأن القيمة
المكتوبة في شرط نصي (مثل 'باريس') لا تطابق القيمة المخزّنة فعلياً
(مثل 'Paris').

ما يفعله هذا الملف (وما لا يفعله)
-----------------------------------
- لا يتصل بـ AI ولا بقاعدة البيانات مباشرة: يستقبل `table_loader`
  (دالة تُرجع DataFrame الجدول كما "يراه" QueryEngine) و`variant_provider`
  اختيارياً (دالة تقترح كتابات بديلة للقيمة، تُستدعى فقط عند الحاجة).
  فيمكن اختباره بالكامل دون DuckDB أو شبكة.
- لا يُنتج أي SQL للبحث عن القيم: البحث يتم على بيانات الجدول المحمَّلة
  أصلاً في الذاكرة (pandas) وفي عمود واحد فقط هو عمود الفلتر، فلا
  استخراج شامل للقيم الفريدة لكل الأعمدة ولا حقن SQL ممكن في البحث.
- لا يُعدّل SQL بالاستبدال النصي (str.replace). يحدد موضع حرف الـ
  literal المقصود بدقة (start/end داخل نص SQL) عبر Tokenizer بسيط، ثم
  يستبدل ذلك الموضع بالذات بعد التحقق أن النص هناك ما زال نفس الـ
  literal المتوقع — فلا يمكن أن يتغير أي شرط آخر في الاستعلام.

نطاق التصحيح (Categorical فقط)
--------------------------------
تُصحَّح فقط شروط النصوص (=، IN، LIKE، ILIKE مع literal نصي) على أعمدة
تصنيفية (categorical). تُستبعد أعمدة الأرقام والتواريخ والمعرّفات/
الأكواد (راجع is_categorical_column)، وكذلك أي literal يبدو رقماً أو
تاريخاً. الشروط الداخلة في دوال (LOWER(col) = ...)، والمنفية (!=، NOT
LIKE...)، وما يقع داخل CASE/SELECT لا يُلمس إطلاقاً.

مستويات المطابقة (من الأقوى للأضعف)
-------------------------------------
exact → normalized (حالة أحرف/مسافات/إملاء عربي) → translation (كتابة
بديلة مقترحة ويُتحقق منها فعلياً في العمود) → fuzzy (تشابه إملائي) →
partial (تطابق جزئي على مستوى الكلمات). كل مرشح هو بالضرورة قيمة موجودة
فعلاً في العمود، فلا قيمة مخترعة.

تحديد الشرط المسبِّب للصفر
----------------------------
لكل شرط نصي مؤهَّل يُفحص وجود أي تطابق له وحده في جدوله. الشرط الذي لا
يطابق أي صف وحده هو المسبِّب. لو كانت كل الشروط تطابق منفردة فالمشكلة
في تركيبها لا في قيمة، فلا اقتراحات (status = not_applicable).

سياسة القرار
--------------
- مرشح وحيد عالي الثقة، أو تطابق تطبيعي (حالة أحرف/مسافات) لا ينافسه
  مرشح قوي آخر → تصحيح تلقائي (auto).
- أكثر من احتمال → لا اختيار تلقائي، يُعرض للمستخدم مرتبين بالثقة.
- لا مرشح لأحد الشروط → not_found دون اختراع قيمة.

مخرجات resolve()
------------------
{"status": "not_applicable" | "auto_fixed" | "needs_choice" | "not_found",
 "items": [...],          # لكل literal مسبِّب: المرشحون والقرار
 "sql": "...",            # عند auto_fixed فقط: SQL بعد التصحيح
 "fixes": [...],          # [{"table","column","from","to"}]
 "message": "..."}        # عند not_found
كل المخرجات قابلة للتسلسل JSON (تُحفظ في session_state بلا مشاكل).
"""


from core.logger_config import trace_calls

import re
import json
import logging
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Optional

import pandas as pd

from config import (
    RESOLVER_MAX_CANDIDATES, RESOLVER_MIN_SCORE, RESOLVER_AUTO_SCORE,
    RESOLVER_FUZZY_MIN, RESOLVER_LOCAL_SUFFICIENT_SCORE,
    RESOLVER_MAX_DISTINCT_SCANNED, RESOLVER_MAX_ITEMS,
    RESOLVER_MAX_AI_ITEMS, RESOLVER_AI_SAMPLE_VALUES, RESOLVER_PARTIAL_MIN_LEN,
)

logger = logging.getLogger(__name__)

# تسميات مستويات المطابقة للعرض في الواجهة
LEVEL_LABELS = {
    "exact"      : "تطابق تام",
    "normalized" : "تطابق مع اختلاف شكلي",
    "translation": "تطابق لغوي",
    "fuzzy"      : "تشابه إملائي",
    "partial"    : "تطابق جزئي",
}

_LEVEL_REASONS = {
    "exact"      : "نفس القيمة تماماً",
    "normalized" : "اختلاف في حالة الأحرف أو المسافات أو الشكل فقط",
    "fuzzy"      : "تشابه إملائي",
    "partial"    : "يحتوي على نفس الكلمات أو جزء منها",
}

# مستويات تُعتبر "قوية" عند تقرير التصحيح التلقائي
_STRONG_LEVELS = ("exact", "normalized", "translation", "fuzzy")


# ══════════════════════════════════════════════════════════════
#  تطبيع النصوص (مصدر واحد لقاعدة المقارنة)
# ══════════════════════════════════════════════════════════════

_AR_MAP = str.maketrans({
    "ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ؤ": "و", "ئ": "ي", "ـ": "",
})
_PUNCT_RE = re.compile(r"[^\w\s]|_", re.UNICODE)


@trace_calls
def normalize_text(value, keep_spaces: bool = False) -> str:
    """
    توحيد نص للمقارنة: casefold، إزالة التشكيل والحركات واللكنات
    (NFKD ثم حذف Mn)، توحيد الإملاء العربي (ة/ه، أ/إ/آ/ا، ى/ي...)،
    تحويل علامات الترقيم إلى مسافات، وإزالة "ال" التعريف من بداية
    الكلمات الطويلة. keep_spaces=True يُبقي الكلمات منفصلة بمسافة
    واحدة (للمقارنة على مستوى الكلمات)، وإلا تُدمج بلا مسافات.
    """
    text = unicodedata.normalize("NFKD", str(value)).casefold()
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.translate(_AR_MAP)
    text = _PUNCT_RE.sub(" ", text)
    tokens = []
    for tok in text.split():
        if len(tok) >= 4 and tok.startswith("ال"):
            tok = tok[2:]
        tokens.append(tok)
    return " ".join(tokens) if keep_spaces else "".join(tokens)


# ══════════════════════════════════════════════════════════════
#  أهلية العمود (categorical فقط)
# ══════════════════════════════════════════════════════════════

_NUMERIC_RE = re.compile(r"^[+-]?[\d\s,.٫٬]+%?$")
_DATE_RE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")
_SAMPLE_FOR_TYPE_CHECK = 500
_TYPE_RATIO = 0.8


@trace_calls
def _looks_numeric(text: str) -> bool:
    return bool(_NUMERIC_RE.match(text.strip())) and any(ch.isdigit() for ch in text)


@trace_calls
def _looks_date(text: str) -> bool:
    return bool(_DATE_RE.match(text.strip()))


@trace_calls
def _looks_code(text: str) -> bool:
    """كود/معرّف: بلا مسافات، قصير، ويحتوي رقماً وليس رقماً صرفاً."""
    t = text.strip()
    return bool(t) and len(t) <= 20 and not any(c.isspace() for c in t) \
        and any(c.isdigit() for c in t) and not t.isdigit()


@trace_calls
def is_categorical_column(series: pd.Series) -> bool:
    """
    هل العمود تصنيفي (نصوص/فئات) فيصلح للتصحيح؟ يُرفض: الأنواع الرقمية
    والمنطقية والتاريخية، والأعمدة النصية التي تحمل فعلياً أرقاماً أو
    تواريخ أو أكواداً/معرّفات (≥ 80% من عينة القيم).
    """
    dtype = series.dtype
    if (pd.api.types.is_bool_dtype(dtype) or pd.api.types.is_numeric_dtype(dtype)
            or pd.api.types.is_datetime64_any_dtype(dtype)):
        return False
    is_texty = (pd.api.types.is_object_dtype(dtype) or pd.api.types.is_string_dtype(dtype)
                or isinstance(dtype, pd.CategoricalDtype))
    if not is_texty:
        return False

    sample = series.dropna().astype(str).head(_SAMPLE_FOR_TYPE_CHECK)
    if sample.empty:
        return False
    total = len(sample)
    for check in (_looks_numeric, _looks_date, _looks_code):
        if sum(1 for v in sample if check(v)) / total >= _TYPE_RATIO:
            return False
    return True


# ══════════════════════════════════════════════════════════════
#  Tokenizer مبسّط لـ SQL (لتحديد مواضع الـ literals بدقة)
# ══════════════════════════════════════════════════════════════

@dataclass
class _Tok:
    kind  : str    # string | ident | number | op | punct
    raw   : str    # النص كما ورد في SQL (بما فيه علامات الاقتباس)
    start : int
    end   : int
    value : str    # النص بعد فك الاقتباس (للـ string/ident)
    quoted: bool = False

    @property
    @trace_calls
    def lower(self) -> str:
        return self.value.lower()

    @trace_calls
    def is_kw(self, *words: str) -> bool:
        return self.kind == "ident" and not self.quoted and self.lower in words


_WORD_RE = re.compile(r"[^\W\d]\w*", re.UNICODE)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_TWO_CHAR_OPS = {"<=", ">=", "<>", "!=", "||", "::", "->", "=>"}


@trace_calls
def _scan_quoted(sql: str, i: int, quote: str) -> Optional[int]:
    """نهاية نص مقتبس يبدأ عند i (مع دعم تكرار علامة الاقتباس للهروب)."""
    n, j = len(sql), i + 1
    while j < n:
        if sql[j] == quote:
            if j + 1 < n and sql[j + 1] == quote:
                j += 2
                continue
            return j + 1
        j += 1
    return None


@trace_calls
def _tokenize(sql: str) -> Optional[list]:
    """تقسيم SQL إلى tokens مع مواضعها. يرجع None لو تعذّر (نص غير مغلق)."""
    tokens, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in ("'", '"'):
            end = _scan_quoted(sql, i, ch)
            if end is None:
                return None
            raw = sql[i:end]
            tokens.append(_Tok(
                "string" if ch == "'" else "ident", raw, i, end,
                raw[1:-1].replace(ch + ch, ch), quoted=(ch == '"'),
            ))
            i = end
            continue
        m = _WORD_RE.match(sql, i)
        if m:
            tokens.append(_Tok("ident", m.group(), i, m.end(), m.group()))
            i = m.end()
            continue
        m = _NUM_RE.match(sql, i)
        if m:
            tokens.append(_Tok("number", m.group(), i, m.end(), m.group()))
            i = m.end()
            continue
        if sql[i:i + 2] in _TWO_CHAR_OPS:
            tokens.append(_Tok("op", sql[i:i + 2], i, i + 2, sql[i:i + 2]))
            i += 2
            continue
        kind = "punct" if ch in "(),.;" else "op"
        tokens.append(_Tok(kind, ch, i, i + 1, ch))
        i += 1
    return tokens


_ALIAS_STOP_WORDS = {
    "where", "join", "inner", "left", "right", "full", "outer", "cross", "on",
    "group", "order", "having", "limit", "union", "using", "natural", "select",
    "from", "window", "qualify", "offset", "except", "intersect", "as", "set",
}


@trace_calls
def _collect_tables(tokens: list) -> tuple:
    """
    استخراج الجداول المذكورة بعد FROM/JOIN مع الـ aliases:
    (qualifiers: {alias أو اسم الجدول بحروف صغيرة → اسم الجدول},
     tables_order: [أسماء الجداول بترتيب ظهورها]).
    الأسماء التي ليست جداول مشروع تُهمَل لاحقاً عند التحميل.
    """
    qualifiers, order = {}, []
    n, i = len(tokens), 0
    while i < n:
        if not tokens[i].is_kw("from", "join"):
            i += 1
            continue
        j = i + 1
        while j < n and tokens[j].kind == "ident":
            tname = tokens[j]
            j += 1
            while j + 1 < n and tokens[j].raw == "." and tokens[j + 1].kind == "ident":
                tname = tokens[j + 1]          # schema.table → table
                j += 2
            alias = None
            if j < n and tokens[j].is_kw("as"):
                j += 1
            if j < n and tokens[j].kind == "ident" and (
                    tokens[j].quoted or tokens[j].lower not in _ALIAS_STOP_WORDS):
                alias = tokens[j]
                j += 1
            qualifiers[tname.lower] = tname.value
            if alias is not None:
                qualifiers[alias.lower] = tname.value
            if tname.value not in order:
                order.append(tname.value)
            if j < n and tokens[j].raw == ",":
                j += 1
                continue
            break
        i = max(j, i + 1)
    return qualifiers, order


_CLAUSE_WORDS = {
    "select", "from", "where", "group", "order", "having", "on", "when",
    "then", "else", "join", "limit", "using", "qualify", "window",
}
_FILTER_CLAUSES = {"where", "having", "on"}
_NON_COLUMN_WORDS = {"and", "or", "not", "null", "true", "false", "case", "end", "is"}


@trace_calls
def _extract_predicates(tokens: list) -> list:
    """
    استخراج شروط النصوص الإيجابية داخل WHERE/HAVING/ON فقط:
        [qualifier.]column  =  'literal'
        [qualifier.]column  [I]LIKE  'literal'
        [qualifier.]column  IN ('a', 'b', ...)
    كل عنصر: {"qualifier", "column", "op", "literals": [_Tok, ...]}.
    ما عدا ذلك (دوال حول العمود، شروط منفية، CASE/SELECT) يُتجاهل.
    """
    preds, n = [], len(tokens)
    clause, case_stack = None, []
    for i, t in enumerate(tokens):
        if t.kind == "ident" and not t.quoted:
            if t.lower == "case":
                case_stack.append(clause)
                clause = "case"
                continue
            if t.lower == "end" and case_stack:
                clause = case_stack.pop()
                continue
            if t.lower in _CLAUSE_WORDS:
                clause = t.lower
                continue
        if clause not in _FILTER_CLAUSES:
            continue
        if t.kind != "ident" or (not t.quoted and t.lower in _NON_COLUMN_WORDS):
            continue
        if i > 0 and tokens[i - 1].raw == ".":
            continue
        if i > 0 and tokens[i - 1].is_kw("not"):
            continue

        parts, j = [t], i + 1
        while j + 1 < n and tokens[j].raw == "." and tokens[j + 1].kind == "ident":
            parts.append(tokens[j + 1])
            j += 2
        if j + 1 >= n:
            continue
        nxt, found, op, lits, end_idx = tokens[j], False, None, [], j + 2

        if nxt.kind == "op" and nxt.raw == "=" and tokens[j + 1].kind == "string":
            found, op, lits = True, "=", [tokens[j + 1]]
        elif nxt.is_kw("like", "ilike") and tokens[j + 1].kind == "string":
            found, op, lits = True, nxt.lower.upper(), [tokens[j + 1]]
        elif nxt.is_kw("in") and tokens[j + 1].raw == "(":
            k, ok, lits = j + 2, True, []
            while k < n:
                if tokens[k].kind != "string":
                    ok = False
                    break
                lits.append(tokens[k])
                k += 1
                if k < n and tokens[k].raw == ",":
                    k += 1
                    continue
                if k < n and tokens[k].raw == ")":
                    k += 1
                    break
                ok = False
                break
            if ok and lits:
                found, op, end_idx = True, "IN", k

        if not found:
            continue
        # literal ملحق بعملية أخرى (concat أو cast) ليس مقارنة بسيطة
        if end_idx < n and tokens[end_idx].raw in ("||", "::"):
            continue
        preds.append({
            "qualifier": parts[-2].value if len(parts) >= 2 else None,
            "column"   : parts[-1].value,
            "op"       : op,
            "literals" : lits,
        })
    return preds


# ══════════════════════════════════════════════════════════════
#  مطابقة القيم وتقييمها
# ══════════════════════════════════════════════════════════════

@trace_calls
def _like_regex(pattern: str, ignore_case: bool):
    """تحويل نمط LIKE (% و _) إلى regex مرتكز على كامل النص."""
    out = []
    for ch in pattern:
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
    return re.compile("".join(out), re.DOTALL | (re.IGNORECASE if ignore_case else 0))


@trace_calls
def _split_like_pattern(pattern: str) -> Optional[tuple]:
    """
    فصل نمط LIKE إلى (بادئة % ، النص الفعلي ، لاحقة %). يرجع None لو
    كان فيه % داخلي (نمط معقّد لا نصحّحه).
    """
    m = re.match(r"^(%*)(.*?)(%*)$", pattern, re.DOTALL)
    if not m or "%" in m.group(2):
        return None
    return m.group(1), m.group(2), m.group(3)


@trace_calls
def _score_value(value: str, wanted: str, variants: list):
    """
    تقييم قيمة موجودة في العمود مقابل النص المطلوب (وكتاباته البديلة).
    يرجع (score, level, reason) لأفضل تطابق، أو None لو أقل من الحد.
    """
    if value == wanted:
        return 1.0, "exact", _LEVEL_REASONS["exact"]

    v_norm = normalize_text(value)
    v_tokens = set(normalize_text(value, keep_spaces=True).split())
    if not v_norm:
        return None
    w_norm = normalize_text(wanted)
    best = None

    @trace_calls
    def offer(score, level, reason):
        nonlocal best
        if best is None or score > best[0]:
            best = (round(score, 3), level, reason)

    if w_norm and v_norm == w_norm:
        offer(0.98, "normalized", _LEVEL_REASONS["normalized"])

    probes = [(wanted, w_norm, False)] + [
        (v, normalize_text(v), True) for v in variants if normalize_text(v)
    ]
    for raw, p_norm, is_variant in probes:
        if not p_norm:
            continue
        if is_variant and p_norm == v_norm:
            offer(0.95, "translation",
                  f"مقابل «{raw}» (كتابة بديلة مقترحة لـ «{wanted}»)")
            continue
        if abs(len(p_norm) - len(v_norm)) <= max(3, len(p_norm)):
            ratio = SequenceMatcher(None, p_norm, v_norm).ratio()
            if ratio >= RESOLVER_FUZZY_MIN:
                if is_variant:
                    offer(ratio * 0.88, "translation",
                          f"قريب من «{raw}» (كتابة بديلة مقترحة لـ «{wanted}»)")
                else:
                    offer(ratio * 0.9, "fuzzy", _LEVEL_REASONS["fuzzy"])
        # تطابق جزئي على مستوى الكلمات
        p_tokens = set(normalize_text(raw, keep_spaces=True).split())
        if p_tokens and v_tokens and len(p_norm) >= RESOLVER_PARTIAL_MIN_LEN \
                and (p_tokens <= v_tokens or v_tokens <= p_tokens):
            shorter, longer = sorted((len(p_norm), len(v_norm)))
            share = shorter / longer if longer else 0
            reason = _LEVEL_REASONS["partial"]
            if is_variant:
                reason += f" (مع «{raw}»)"
            offer(0.55 + 0.3 * share, "partial", reason)

    if best is None or best[0] < RESOLVER_MIN_SCORE:
        return None
    return best


@trace_calls
def _decide_auto(cands: list) -> Optional[dict]:
    """
    متى يصح التصحيح التلقائي؟ (1) مرشح وحيد بثقة عالية، أو (2) تطابق
    تطبيعي/تام لا ينافسه أي مرشح آخر قوي (الجزئي وحده لا ينافس).
    غير ذلك: لا اختيار تلقائي.
    """
    if not cands or cands[0]["score"] < RESOLVER_AUTO_SCORE:
        return None
    top = cands[0]
    if len(cands) == 1:
        return top
    if top["level"] in ("exact", "normalized"):
        if not [c for c in cands[1:] if c["level"] in _STRONG_LEVELS]:
            return top
    return None


# ══════════════════════════════════════════════════════════════
#  اقتراح كتابات بديلة عبر AI (prompt + تحليل الرد)
# ══════════════════════════════════════════════════════════════

@trace_calls
def build_variants_prompt(wanted: str, table: str, column: str, samples: list) -> str:
    """
    prompt قصير يطلب كتابات بديلة محتملة للقيمة المطلوبة (ترجمة أو
    تحويل حروف أو اسم بديل). يُرسَل اسم الجدول والعمود وبضع قيم نموذجية
    فقط (لتحديد لغة/نمط الكتابة) — لا تُرسَل قيم العمود كلها.
    AI هنا يقترح فقط؛ وجود أي اقتراح لا يُعتمد إلا بعد التحقق منه في
    بيانات العمود فعلياً.
    """
    sample_text = "، ".join(str(s) for s in samples) if samples else "(غير متاحة)"
    return (
        "أنت مساعد يطابق أسماء القيم في قاعدة بيانات.\n"
        f"المستخدم كتب القيمة: «{wanted}»\n"
        f"الجدول: {table} — العمود: {column}\n"
        f"أمثلة لقيم موجودة في هذا العمود (لتحديد لغة ونمط الكتابة): {sample_text}\n\n"
        "اقترح حتى 5 كتابات بديلة محتملة لنفس الاسم كما قد تكون مخزّنة في العمود "
        "(ترجمة، تحويل حروف عربي/لاتيني، اسم بديل شائع، اختلاف إملائي شائع).\n"
        "أجب فقط بصيغة JSON صالحة بالشكل: {\"variants\": [\"...\", \"...\"]} "
        "بدون أي شرح أو Markdown. لا تخترع أسماء بعيدة عن المقصود."
    )


@trace_calls
def parse_variants_response(text: str) -> list:
    """استخراج قائمة الكتابات البديلة من رد AI. أي رد غير صالح → قائمة فارغة."""
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
        raw = data.get("variants", [])
    except Exception:
        return []
    cleaned = []
    for v in raw if isinstance(raw, list) else []:
        s = str(v).strip()
        if s and len(s) <= 100 and s not in cleaned:
            cleaned.append(s)
    return cleaned[:5]


# ══════════════════════════════════════════════════════════════
#  ValueResolver
# ══════════════════════════════════════════════════════════════

class ValueResolver:
    """
    الاستخدام:
        resolver = ValueResolver(table_loader, variant_provider)
        res = resolver.resolve(sql)            # بعد نتيجة صفرية فقط
        ...
        out = ValueResolver.apply_choices(sql, res["items"], {"0": "Paris"})

    table_loader(name)  → DataFrame أو None (اسم غير معروف كجدول مشروع).
    variant_provider(wanted, table, column, samples) → list[str] (اختياري).
    """

    @trace_calls
    def __init__(self, table_loader: Callable, variant_provider: Optional[Callable] = None):
        self._loader = table_loader
        self._variant_provider = variant_provider
        self._frames = {}
        self._uniques = {}

    # ── تحميل الجداول والقيم (مخزَّنة لمدة هذا الاستدعاء فقط) ──

    @trace_calls
    def _frame(self, name: str):
        if name not in self._frames:
            try:
                self._frames[name] = self._loader(name)
            except Exception as e:
                logger.warning("value_resolver: table loader failed for '%s': %s", name, e)
                self._frames[name] = None
        return self._frames[name]

    @staticmethod
    @trace_calls
    def _match_column(df: pd.DataFrame, column: str) -> Optional[str]:
        if column in df.columns:
            return column
        hits = [c for c in df.columns if str(c).lower() == column.lower()]
        return hits[0] if len(hits) == 1 else None

    @trace_calls
    def _locate(self, pred: dict, qualifiers: dict, order: list):
        """(table, column_name, series) للشرط، أو None لو لم يتحدد بشكل قاطع."""
        if pred["qualifier"]:
            tname = qualifiers.get(pred["qualifier"].lower())
            candidates = [tname] if tname else []
        else:
            candidates = order
        hits = []
        for tname in candidates:
            df = self._frame(tname)
            if df is None:
                continue
            col = self._match_column(df, pred["column"])
            if col is not None:
                hits.append((tname, col, df[col]))
        return hits[0] if len(hits) == 1 else None

    @trace_calls
    def _distinct(self, table: str, col: str, series: pd.Series) -> list:
        key = (table, col)
        if key not in self._uniques:
            self._uniques[key] = [str(v) for v in series.dropna().astype(str).unique()]
        return self._uniques[key]

    # ── هل للشرط وحده أي تطابق؟ ──

    @trace_calls
    def _predicate_matches(self, distinct: list, op: str, literal_values: list) -> bool:
        if op == "=":
            return literal_values[0] in set(distinct)
        if op == "IN":
            return bool(set(literal_values) & set(distinct))
        rx = _like_regex(literal_values[0], ignore_case=(op == "ILIKE"))
        return any(rx.fullmatch(v) for v in distinct)

    # ── البحث عن المرشحين لقيمة واحدة ──

    @trace_calls
    def _shortlist(self, distinct: list, probes: list) -> tuple:
        """
        تقليص القيم الفريدة للأعمدة الكبيرة فقط (> RESOLVER_MAX_DISTINCT_SCANNED):
        فلترة vectorized بأول 3 أحرف من كلمات النص المطلوب وبدائله، ثم
        الاكتفاء بالحد الأقصى. يرجع (القيم, هل_اقتُطعت).
        """
        if len(distinct) <= RESOLVER_MAX_DISTINCT_SCANNED:
            return distinct, False
        prefixes = {
            tok[:3] for p in probes
            for tok in normalize_text(p, keep_spaces=True).split() if len(tok) >= 2
        }
        if not prefixes:
            return distinct[:RESOLVER_MAX_DISTINCT_SCANNED], True
        ser = pd.Series(distinct)
        mask = ser.str.casefold().str.contains(
            "|".join(re.escape(p) for p in prefixes), regex=True,
        )
        return list(ser[mask].head(RESOLVER_MAX_DISTINCT_SCANNED)), True

    @trace_calls
    def _find_candidates(self, distinct: list, wanted: str, variants: list) -> tuple:
        pool, truncated = self._shortlist(distinct, [wanted] + variants)
        best = {}
        for value in pool:
            scored = _score_value(value, wanted, variants)
            if scored is None:
                continue
            score, level, reason = scored
            if value not in best or score > best[value]["score"]:
                best[value] = {"value": value, "score": score, "level": level, "reason": reason}
        ordered = sorted(best.values(), key=lambda c: (-c["score"], len(c["value"]), c["value"]))
        return ordered[:RESOLVER_MAX_CANDIDATES], truncated

    # ── النقطة الرئيسية ──

    @trace_calls
    def resolve(self, sql: str) -> dict:
        """
        تحليل SQL (نتيجته صفر صفوف) واقتراح تصحيح لقيم شروط النصوص
        المسبِّبة للصفر. لا تُستدعى إلا بعد نجاح التنفيذ ببيانات فارغة.
        """
        empty = {"status": "not_applicable", "items": [], "fixes": []}
        tokens = _tokenize(sql)
        if not tokens:
            return empty
        qualifiers, order = _collect_tables(tokens)
        preds = _extract_predicates(tokens)
        if not preds:
            return empty

        items = []
        for pred in preds:
            if len(items) >= RESOLVER_MAX_ITEMS:
                break
            located = self._locate(pred, qualifiers, order)
            if located is None:
                continue
            table, col, series = located
            if not is_categorical_column(series):
                continue

            distinct = self._distinct(table, col, series)
            values = [tok.value for tok in pred["literals"]]
            if self._predicate_matches(distinct, pred["op"], values):
                continue                      # هذا الشرط ليس سبب الصفر

            for tok in pred["literals"]:
                item = self._build_item(len(items), pred, tok, table, col, distinct)
                if item is not None:
                    items.append(item)
                if len(items) >= RESOLVER_MAX_ITEMS:
                    break

        if not items:
            return empty

        self._enrich_with_variants(items)

        missing = [it for it in items if not it["candidates"]]
        if missing:
            names = "، ".join(f"«{it['wanted']}»" for it in missing)
            return {
                "status": "not_found", "items": items, "fixes": [],
                "message": f"لم أجد في بيانات العمود قيمة مطابقة أو قريبة من {names}.",
            }

        for it in items:
            chosen = _decide_auto(it["candidates"])
            it["auto"] = chosen is not None
            it["chosen"] = chosen["value"] if chosen else None

        if all(it["auto"] for it in items):
            applied = self.apply_choices(sql, items, {})
            if applied["ok"]:
                return {"status": "auto_fixed", "items": items, "sql": applied["sql"],
                        "fixes": applied["fixes"]}
            logger.warning("value_resolver: auto apply failed: %s", applied["error"])
        return {"status": "needs_choice", "items": items, "fixes": []}

    @trace_calls
    def _build_item(self, idx: int, pred: dict, tok: _Tok, table: str, col: str, distinct: list):
        """عنصر تصحيح لـ literal واحد، أو None لو غير مؤهل (رقم/تاريخ/نمط معقد/قصير)."""
        prefix = suffix = ""
        wanted = tok.value
        if pred["op"] in ("LIKE", "ILIKE"):
            parts = _split_like_pattern(tok.value)
            if parts is None:
                return None
            prefix, wanted, suffix = parts
        wanted = wanted.strip()
        if len(wanted) < 2 or _looks_numeric(wanted) or _looks_date(wanted):
            return None
        if pred["op"] in ("=", "IN") and wanted in set(distinct):
            return None                       # هذا الـ literal موجود فعلاً

        cands, truncated = self._find_candidates(distinct, wanted, [])
        return {
            "id": str(idx), "table": table, "column": col, "op": pred["op"],
            "wanted": wanted, "token": tok.raw, "start": tok.start, "end": tok.end,
            "prefix": prefix, "suffix": suffix, "candidates": cands,
            "truncated": truncated, "ai_used": False, "auto": False, "chosen": None,
        }

    @trace_calls
    def _enrich_with_variants(self, items: list) -> None:
        """
        للعناصر التي لا يكفي مرشحوها المحليون (أفضل درجة أقل من
        RESOLVER_LOCAL_SUFFICIENT_SCORE) يُطلب من variant_provider كتابات
        بديلة (حد أقصى RESOLVER_MAX_AI_ITEMS عناصر لكل استدعاء). فشل
        المزود لا يوقف شيئاً: تبقى النتائج المحلية.
        """
        if self._variant_provider is None:
            return
        used = 0
        for it in items:
            if used >= RESOLVER_MAX_AI_ITEMS:
                break
            top = it["candidates"][0]["score"] if it["candidates"] else 0.0
            if top >= RESOLVER_LOCAL_SUFFICIENT_SCORE:
                continue
            distinct = self._uniques.get((it["table"], it["column"]), [])
            try:
                variants = self._variant_provider(
                    it["wanted"], it["table"], it["column"],
                    distinct[:RESOLVER_AI_SAMPLE_VALUES],
                )
            except Exception as e:
                logger.warning("value_resolver: variant provider failed: %s", e)
                continue
            used += 1
            it["ai_used"] = True
            variants = [v for v in (variants or []) if isinstance(v, str) and v.strip()]
            if variants:
                it["candidates"], it["truncated"] = self._find_candidates(
                    distinct, it["wanted"], variants,
                )

    # ── تطبيق الاختيارات على SQL الأصلي ──

    @staticmethod
    @trace_calls
    def apply_choices(sql: str, items: list, choices: dict) -> dict:
        """
        بناء SQL جديد باستبدال مواضع الـ literals المقصودة فقط.
        choices: {item_id: القيمة المختارة}؛ العنصر الذي لم يُختر له شيء
        يُستعمل chosen التلقائي له إن وُجد، وإلا يُرجع خطأ (لا تخمين).
        قبل أي استبدال يُتحقق أن النص عند (start:end) ما زال نفس الـ
        literal الأصلي، وإلا يُرفض التطبيق بدل إفساد استعلام مختلف.

        يرجع: {"ok": True, "sql": ..., "fixes": [...]} أو {"ok": False, "error": ...}
        """
        edits, fixes = [], []
        for it in items:
            value = choices.get(it["id"]) or it.get("chosen")
            if not value:
                return {"ok": False, "error": f"لم يُختَر بديل للقيمة «{it['wanted']}»"}
            if sql[it["start"]:it["end"]] != it["token"]:
                return {"ok": False, "error": "تغيّر نص الاستعلام ولم يعد التصحيح ممكناً"}
            text = f"{it.get('prefix', '')}{value}{it.get('suffix', '')}"
            literal = "'" + text.replace("'", "''") + "'"
            edits.append((it["start"], it["end"], literal))
            fixes.append({"table": it["table"], "column": it["column"],
                          "from": it["wanted"], "to": value})

        new_sql = sql
        for start, end, literal in sorted(edits, reverse=True):
            new_sql = new_sql[:start] + literal + new_sql[end:]
        return {"ok": True, "sql": new_sql, "fixes": fixes}
