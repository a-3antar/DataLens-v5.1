"""
core/dashboard_cells/__init__.py
==================================
نقطة الدخول الموحّدة لحزمة خلايا لوحة المعلومات: الـ factory
(create_cell) وقاموس الأنواع المتاحة (CELL_CLASSES) — مصدر الحقيقة
الوحيد لأسماء الأنواع وتسمياتها المعروضة (label)، بدل تكرار قاموس
"جدول"/"رسم بياني"/... في أكثر من مكان (كان مكرراً سابقاً في
ui/dashboards.py كـ DISPLAY_TYPE_LABELS).

تنظيم الملفات:
    base.py  → DashboardCellBase (الكلاس الأب + كل المنطق المشترك)
    cells.py → EmptyCell + كل الأنواع الفعلية (Table/Chart/Gauge/
               Kpi/Story) — مجمَّعة في ملف واحد لأن كل كلاس منها صغير
               بما لا يبرر ملفاً منفصلاً له.
"""

from core.dashboard_cells.base import DashboardCellBase
from core.dashboard_cells.cells import (
    EmptyCell, TableCell, ChartCell, GaugeCell, KpiCell, StoryCell,
)

# كل الأنواع المعروفة — تُستخدم في الـ factory وفي قائمة اختيار النوع
# عند إضافة خلية جديدة أو تغيير نوع خلية موجودة (DashboardCellBase.render_editor).
CELL_CLASSES = {
    "table": TableCell,
    "chart": ChartCell,
    "gauge": GaugeCell,
    "kpi": KpiCell,
    "story": StoryCell,
}

# تسميات العرض لكل نوع — مبنية من الخاصية الصنفية label لكل كلاس،
# بدل قاموس منفصل كان يتكرر يدوياً في ui/dashboards.py.
DISPLAY_TYPE_LABELS = {key: cls.label for key, cls in CELL_CLASSES.items()}


def create_cell(row: dict):
    """
    بناء كائن الخلية المناسب من صف dashboard_cells الخام (كما يُرجعه
    core.project_db.ProjectDB.get_dashboard_cells()، أو dict مبسّط
    يحتوي "position" فقط لخلية لم تُحفظ بعد).

    - لا يوجد "question" بعد (خلية فارغة تماماً) → EmptyCell.
    - غير ذلك → يُقرأ display_type من الصف، ويُبنى الكلاس المطابق من
      CELL_CLASSES، مع fallback إلى TableCell لو كان النوع غير معروف
      (مثلاً بيانات قديمة/تالفة).
    """
    if not row.get("question"):
        return EmptyCell(position=row["position"])

    display_type = row.get("display_type") or "table"
    cls = CELL_CLASSES.get(display_type, TableCell)
    return cls.from_row(row)
