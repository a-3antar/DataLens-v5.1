"""
core/dashboard_templates.py
=============================
تعريف قوالب لوحات المعلومات الستة.

كل قالب = عدد الخلايا السفلية + اسم دالة تخطيط (layout function) تُنفَّذ
لاحقاً في ui/dashboards.py (لأن رسم الشبكة يحتاج st.columns من Streamlit،
وهو ما ينتمي لطبقة الواجهة لا طبقة البيانات).

لإضافة قالب سابع لاحقاً:
1. أضف إدخالاً جديداً هنا بنفس الصيغة (name, description, cell_count, layout_fn)
2. أضف دالة تخطيط صغيرة مطابقة للاسم في ui/dashboards.py (LAYOUT_REGISTRY)
لا حاجة لتعديل أي شيء آخر — لا قاعدة البيانات ولا بقية المنطق يتأثران.
"""

DASHBOARD_TEMPLATES = {
    "A": {
        "name": "شبكة متوازنة",
        "description": "٤ Gauges أعلى + عمودان وصفان (٤ خلايا متساوية)",
        "cell_count": 4,
        "layout_fn": "layout_2x2",
    },
    "B": {
        "name": "عمود رئيسي وعمود مقسوم",
        "description": "٤ Gauges أعلى + عمود كبير وعمود جانبي مقسوم لصفين",
        "cell_count": 3,
        "layout_fn": "layout_main_and_split",
    },
    "C": {
        "name": "ثلاثة أعمدة",
        "description": "٤ Gauges أعلى + ٣ أعمدة متساوية",
        "cell_count": 3,
        "layout_fn": "layout_3col",
    },
    "D": {
        "name": "خلية كبيرة واحدة",
        "description": "٤ Gauges أعلى + خلية واحدة بعرض الصفحة كاملاً",
        "cell_count": 1,
        "layout_fn": "layout_full",
    },
    "E": {
        "name": "شبكة كثيفة ٢×٣",
        "description": "٤ Gauges أعلى + ٦ خلايا (٣ أعمدة × صفان)",
        "cell_count": 6,
        "layout_fn": "layout_2x3",
    },
    "F": {
        "name": "عمودان كبيران",
        "description": "٤ Gauges أعلى + عمودان كبيران متساويان للمقارنة المباشرة",
        "cell_count": 2,
        "layout_fn": "layout_2col_big",
    },
}


def get_template(template_id: str) -> dict:
    return DASHBOARD_TEMPLATES.get(template_id, DASHBOARD_TEMPLATES["A"])
