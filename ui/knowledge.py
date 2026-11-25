"""
ui/knowledge.py
=================
صفحة "🧠 معرفة المشروع": عرض وتحرير الذاكرة المعرفية المخزَّنة في project.db
(راجع core/knowledge_manager.py للطبقات والتخزين).

- شريط حالة المهمة الخلفية يتحدّث تلقائياً عبر st.fragment(run_every=...) فقط
  أثناء التشغيل، وعند انتهائها يُعاد رسم الصفحة كاملة فيتوقف الاستطلاع. إن لم
  يتوفر st.fragment يُستخدم زر "تحديث الحالة" اليدوي.
- الطبقتان ب وج تحملان وسم "مستنتج بالذكاء الاصطناعي"؛ تعديل المستخدم لوصف
  عمود/مرادفاته/تجميعه يضع user_edited=1 ولا تستبدله إعادة التوليد.
- عند إيقاف إرسال القيم للـ AI تُعطَّل الطبقة ج ويُنبَّه المستخدم هنا.
- Streamlit لا يسمح بتداخل expander داخل expander، لذا تفاصيل الأعمدة تُعرض
  بقائمة اختيار.
"""


from core.logger_config import trace_calls

import hashlib

import pandas as pd
import streamlit as st

from ui.common import (
    apply_rtl, apply_theme_css, require_login, require_project, sidebar_header,
    notify, render_themed_table, format_local_dt,
)
from core.knowledge_manager import (
    KnowledgeManager, request_refresh, AGGREGATIONS,
    LAYER_PROFILE, LAYER_SEMANTIC, LAYER_INSIGHT,
)
from ai.ai_manager import build_ai_manager

_AI_BADGE = "🤖 مستنتج بالذكاء الاصطناعي"
_STATE_LABELS = {
    "idle": "⚪ لم تُشغَّل بعد", "running": "⏳ جارية", "done": "✅ اكتملت",
    "partial": "⚠️ اكتملت جزئياً", "error": "❌ فشلت",
}
_LAYER_LABELS = {
    LAYER_PROFILE: "أ — البروفايل الإحصائي",
    LAYER_SEMANTIC: "ب — الوصف الدلالي (AI)",
    LAYER_INSIGHT: "ج — الـ Insights (AI)",
}
_IMPORTANCE = {"high": "🔴 مهم", "medium": "🟡 متوسط", "low": "⚪ عادي"}
_ROLE_LABELS = {
    "id": "معرّف", "category": "فئة", "measure": "قيمة رقمية", "date": "تاريخ",
    "text": "نص", "boolean": "منطقي", "constant": "ثابت/فارغ",
}


@trace_calls
def _h(value) -> str:
    return hashlib.md5(str(value).encode("utf-8")).hexdigest()[:8]


@trace_calls
def show_knowledge():
    apply_rtl()
    require_login()
    db = require_project()
    settings = db.get_settings()
    apply_theme_css(settings)
    sidebar_header()

    st.title("🧠 معرفة المشروع")
    st.caption("ذاكرة تُبنى في الخلفية من بياناتك الكاملة وتُستخدم لرفع جودة SQL والأسئلة والتحليل النصي.")

    if not db.get_files():
        st.info("لا توجد جداول بعد. ارفع ملفاً أولاً من صفحة الملفات.")
        return

    km = KnowledgeManager(db)
    send_values = bool(settings.get("knowledge_send_values_to_ai", True))
    if not send_values:
        st.warning(
            "🔒 إرسال القيم للذكاء الاصطناعي مُوقَف (من الإعدادات): الطبقة ب تعتمد على الأسماء والإحصاءات فقط، "
            "والطبقة ج (Insights) معطّلة."
        )
    if not settings.get("use_knowledge", True):
        st.caption("ℹ️ استخدام الذاكرة في الـ prompts مُوقَف حالياً من الإعدادات.")

    _render_controls(db, send_values)
    _render_status_entry(db, settings)

    st.divider()
    tab_sum, tab_tables, tab_insights = st.tabs(["📌 ملخص المشروع", "📄 الجداول والأعمدة", "💡 Insights"])
    with tab_sum:
        _render_summary(km, settings)
    with tab_tables:
        _render_tables(db, km, settings)
    with tab_insights:
        _render_insights(km, settings, send_values)


# ──────────────────────────────────────────────────────────────
#  التحكم والحالة
# ──────────────────────────────────────────────────────────────

@trace_calls
def _render_controls(db, send_values: bool) -> None:
    options = [LAYER_PROFILE, LAYER_SEMANTIC] + ([LAYER_INSIGHT] if send_values else [])
    with st.expander("🔄 تحديث الذاكرة المعرفية", expanded=False):
        chosen = st.multiselect(
            "الطبقات المطلوبة", options, default=options,
            format_func=lambda l: _LAYER_LABELS[l], key="kn_layers",
        )
        force = st.checkbox(
            "إعادة توليد كاملة (تتجاهل الكاش، وتُبقي تعديلاتك اليدوية)", value=False, key="kn_force",
        )
        if st.button("🔄 تحديث الذاكرة المعرفية", key="kn_refresh", type="primary", width="stretch"):
            if not chosen:
                notify("اختر طبقة واحدة على الأقل", kind="warning")
            else:
                try:
                    ai, _ = build_ai_manager(db)
                except Exception as e:
                    ai = None
                    notify(f"تعذر تجهيز محرك AI: {e}", kind="warning")
                r = request_refresh(db, ai, chosen, force=force, triggered_by="manual")
                if not r["ok"]:
                    notify(r.get("error", "فشل بدء المهمة"), kind="error")
                else:
                    notify("أُضيف الطلب بعد المهمة الجارية" if r.get("queued") else "بدأ التحديث في الخلفية",
                           kind="info")
                    st.rerun()


@trace_calls
def _render_status_entry(db, settings: dict) -> None:
    running = KnowledgeManager(db).get_status()["state"] == "running"
    if hasattr(st, "fragment"):
        st.fragment(run_every=2 if running else None)(_render_status_panel)(db, settings)
    else:
        _render_status_panel(db, settings)
        st.button("🔃 تحديث الحالة", key="kn_status_refresh")


@trace_calls
def _render_status_panel(db, settings: dict) -> None:
    status = KnowledgeManager(db).get_status()
    running = status["state"] == "running"
    was_running = st.session_state.get("_kn_prev_running", False)
    st.session_state["_kn_prev_running"] = running

    with st.container(border=True):
        label = _STATE_LABELS.get(status["state"], status["state"])
        st.markdown(f"**حالة المهمة:** {label}" + ("  ·  ⏳ بانتظار طلب لاحق" if status["pending"] else ""))
        if running:
            total = max(status["progress_total"], 1)
            st.progress(min(status["progress_done"] / total, 1.0),
                        text=f"{status['stage']} ({status['progress_done']}/{status['progress_total']})")
        elif status["finished_at"]:
            st.caption(f"آخر اكتمال: {format_local_dt(status['finished_at'], settings)}")
        if status["error"] and not running:
            st.caption(f"ملاحظات: {status['error']}")
        if status["stale"] and not running:
            st.caption("⚠️ بعض المعرفة قديمة (تغيّرت البيانات منذ توليدها) — حدّثها من الزر أعلاه.")

    if was_running and not running:
        st.rerun()   # رسم كامل للصفحة: يعرض النتائج الجديدة ويوقف الاستطلاع


# ──────────────────────────────────────────────────────────────
#  الملخص
# ──────────────────────────────────────────────────────────────

@trace_calls
def _render_summary(km: KnowledgeManager, settings: dict) -> None:
    sem = km.get_semantic()
    proj = sem["project"]
    if not proj:
        st.info("لا يوجد ملخص بعد. شغّل الطبقة ب من زر التحديث.")
        return
    st.caption(_AI_BADGE + (" — ⚠️ قديم" if proj.get("stale") else ""))
    st.markdown(proj.get("summary", ""))
    if proj.get("entities"):
        st.markdown("**أهم الكيانات**")
        for e in proj["entities"]:
            st.markdown(f"• **{e['name']}** ({', '.join(e['tables'])}): {e.get('description', '')}")
    if proj.get("relationships"):
        st.markdown("**العلاقات**")
        for r in proj["relationships"]:
            st.markdown(f"• {r}")


# ──────────────────────────────────────────────────────────────
#  الجداول والأعمدة
# ──────────────────────────────────────────────────────────────

@trace_calls
def _fmt(v) -> str:
    return "—" if v is None else str(v)


@trace_calls
def _render_tables(db, km: KnowledgeManager, settings: dict) -> None:
    prof, sem = km.get_profile(), km.get_semantic()
    aliases = [f["table_alias"] for f in db.get_files()]
    if not prof:
        st.info("لا يوجد بروفايل بعد. شغّل التحديث من الزر أعلاه.")
        return
    for alias in aliases:
        if alias not in prof:
            continue
        t = prof[alias]["table"]
        cols = prof[alias]["columns"]
        st_sem = sem["tables"].get(alias, {"table": None, "columns": {}})
        with st.expander(f"📄 {alias} — {t['rows']} صف، {t['cols']} عمود", expanded=len(aliases) == 1):
            _render_table_header(t, st_sem["table"])
            _render_columns_overview(alias, t, cols, st_sem["columns"], settings)
            _render_column_detail(km, alias, cols, st_sem["columns"], settings)


@trace_calls
def _render_table_header(t: dict, sem_table) -> None:
    if sem_table:
        st.caption(_AI_BADGE + (" — ⚠️ قديم" if sem_table.get("stale") else ""))
        if sem_table.get("purpose"):
            st.markdown(f"**الغرض:** {sem_table['purpose']}")
        if sem_table.get("granularity"):
            st.markdown(f"**الحبيبة:** {sem_table['granularity']}")
        for note in sem_table.get("quality_notes", []):
            st.markdown(f"• ⚠️ {note}")
    else:
        st.caption("لا يوجد وصف دلالي لهذا الجدول بعد (الطبقة ب).")
    bits = [f"صفوف مكررة: {_fmt(t.get('duplicate_rows'))}"]
    if t.get("relations"):
        bits.append("علاقات: " + " ; ".join(t["relations"]))
    st.caption("📊 إحصائي — " + " · ".join(bits))
    if t.get("correlations"):
        st.caption("ارتباطات بارزة: " + "، ".join(f"{c['a']}~{c['b']} ({c['r']})" for c in t["correlations"][:4]))


@trace_calls
def _render_columns_overview(alias, t, cols, sem_cols, settings) -> None:
    rows = []
    for c in t.get("column_order", list(cols)):
        cp = cols.get(c)
        if not cp:
            continue
        s = sem_cols.get(c, {})
        rows.append({
            "العمود": c + (" ✏️" if s.get("user_edited") else ""),
            "الدور": _ROLE_LABELS.get(s.get("role") or cp.get("role"), cp.get("role")),
            "النوع": cp.get("dtype"), "فارغ %": cp.get("null_pct"), "فريد": cp.get("distinct"),
            "الوصف": s.get("description", ""), "الوحدة": s.get("unit", ""), "التجميع": s.get("aggregation", ""),
        })
    if len(rows) <= 200:
        render_themed_table(pd.DataFrame(rows), settings, key=f"kn_cols_{_h(alias)}")
    else:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


@trace_calls
def _stat_lines(cp: dict) -> list:
    lines = [f"النوع: {cp.get('dtype')} · الدور (إحصائي): {_ROLE_LABELS.get(cp.get('role'), cp.get('role'))} · "
             f"فارغ: {cp.get('null_pct')}% · فريد: {cp.get('distinct')}"]
    kind = cp.get("kind")
    if kind == "numeric" and cp.get("min") is not None:
        lines.append(f"المدى: {cp['min']} ← {cp['max']} · المتوسط: {cp.get('mean')} · الوسيط: {cp.get('median')} · "
                     f"الانحراف: {cp.get('std')}")
        lines.append(f"الشرائح: p25={cp.get('p25')} · p75={cp.get('p75')} · p95={cp.get('p95')} · "
                     f"أصفار: {cp.get('zero_pct')}% · سالب: {cp.get('negative_pct')}%")
    elif kind == "date" and cp.get("min"):
        lines.append(f"من {str(cp['min'])[:10]} إلى {str(cp['max'])[:10]} · الدقة: {cp.get('resolution')}")
        for g in cp.get("gaps", []):
            lines.append(f"فجوة: {g['days']} يوماً ({g['from']} → {g['to']})")
    elif kind == "text" and cp.get("avg_length") is not None:
        lines.append(f"متوسط الطول: {cp['avg_length']} · الأقصى: {cp.get('max_length')}")
    elif kind == "boolean":
        lines.append(f"True: {cp.get('true_count')} · False: {cp.get('false_count')}")
    if cp.get("sample_values"):
        lines.append("عينة: " + "، ".join(cp["sample_values"]))
    return lines


@trace_calls
def _render_column_detail(km, alias, cols, sem_cols, settings) -> None:
    names = list(cols)
    if not names:
        return
    h = _h(alias)
    col = st.selectbox("تفاصيل عمود", names, key=f"kn_col_{h}")
    cp, s = cols[col], sem_cols.get(col, {})
    ch = f"{h}_{_h(col)}"

    for line in _stat_lines(cp):
        st.caption(line)
    if cp.get("top_values"):
        st.markdown("**القيم الأكثر تكراراً**")
        render_themed_table(
            pd.DataFrame([{"القيمة": v["value"], "العدد": v["count"]} for v in cp["top_values"]]),
            settings, key=f"kn_top_{ch}")
    if s:
        st.caption(_AI_BADGE if s.get("from_ai") else "👤 معرَّف يدوياً")
        for w in s.get("warnings", []):
            st.caption(f"⚠️ {w}")

    with st.form(f"kn_edit_{ch}"):
        desc = st.text_area("الوصف", value=s.get("description", ""), key=f"kn_desc_{ch}", height=80)
        syn = st.text_input("المرادفات (مفصولة بفاصلة)", value="، ".join(s.get("synonyms", [])), key=f"kn_syn_{ch}")
        cur_agg = s.get("aggregation") if s.get("aggregation") in AGGREGATIONS else "NONE"
        agg = st.selectbox("طريقة التجميع", list(AGGREGATIONS), index=list(AGGREGATIONS).index(cur_agg),
                           key=f"kn_agg_{ch}")
        submitted = st.form_submit_button("💾 حفظ التعديل", type="primary", width="stretch")
    if submitted:
        fields = {}
        if desc.strip() != (s.get("description") or ""):
            fields["description"] = desc
        if [x.strip() for x in syn.replace("،", ",").split(",") if x.strip()] != s.get("synonyms", []):
            fields["synonyms"] = syn
        if agg != cur_agg:
            fields["aggregation"] = agg
        if not fields:
            notify("لا تغييرات للحفظ", kind="info")
        else:
            r = km.save_user_edit(alias, col, fields)
            if r["ok"]:
                notify("تم حفظ التعديل (لن تستبدله إعادة التوليد)", kind="success")
                st.rerun()
            else:
                st.error(r["error"])

    if s.get("user_edited"):
        if st.button("↺ استعادة توليد AI", key=f"kn_restore_{ch}", width="stretch"):
            r = km.restore_ai(alias, col)
            if r["ok"]:
                notify("تمت الاستعادة", kind="success")
                st.rerun()
            else:
                st.error(r["error"])


# ──────────────────────────────────────────────────────────────
#  Insights
# ──────────────────────────────────────────────────────────────

@trace_calls
def _render_insights(km: KnowledgeManager, settings: dict, send_values: bool) -> None:
    if not send_values:
        st.info("الـ Insights معطّلة لأن إرسال القيم للذكاء الاصطناعي مُوقَف.")
        return
    items = km.get_insights()
    if not items:
        st.info("لا توجد Insights بعد. شغّل الطبقة ج من زر التحديث.")
        return
    st.caption(_AI_BADGE + " — كل استنتاج مدعوم باستعلام نُفِّذ فعلاً على بياناتك.")
    for i, it in enumerate(items):
        with st.container(border=True):
            st.markdown(f"**{_IMPORTANCE.get(it.get('importance'), '')} {it['title']}**"
                        + ("  ·  ⚠️ قديم" if it.get("stale") else ""))
            st.markdown(it["text"])
            with st.expander("🔎 الدليل (SQL ونتيجته)"):
                st.caption(it.get("evidence_title", ""))
                st.code(it.get("evidence_sql", ""), language="sql")
                snap = it.get("snapshot") or {}
                if snap.get("rows"):
                    render_themed_table(pd.DataFrame(snap["rows"]), settings, key=f"kn_ins_{i}")
                    st.caption(f"لقطة {len(snap['rows'])} من {snap.get('total_rows')} صف")
