# DataLens — خطة إعادة الهيكلة والتحسين

## الهدف

إعادة تنظيم المشروع لتوحيد الوظائف المكررة، فصل المسؤوليات، تحسين قابلية الصيانة، وتأسيس بنية واضحة لـ Chat وDashboard وReports، مع تنفيذ تدقيق شامل للكود غير المستخدم قبل الحذف.

> **قاعدة مهمة:** لا يتم حذف أي ملف من المستودع قبل تنفيذ Full Dead-Code / Dependency Audit على كامل المشروع.

---

# 1. المرحلة 0 — تثبيت Design System

## الملف: `ui/common.py`

### الدوال التي تبقى ويتم اعتمادها مؤقتًا

```text
apply_rtl()
apply_theme_css()
apply_page_style()

_normalize_custom_colors()
_resolve_theme_colors()
get_theme_colors()

_hex_to_rgb01()
_rgb01_to_hex()
_generate_chart_colorway()
_apply_chart_colors()
get_chart_theme()
apply_plotly_theme()

render_themed_table()

action_bar()
sequential_form()

require_login()
require_project()
get_project_manager()

sidebar_header()
_render_account_quick_menu()

notify()
format_local_dt()

temp_export_dir()
offer_download()
cleanup_stale_temp_dirs()
```

### المطلوب

- عدم إضافة وظائف جديدة إلى `common.py`.
- استخدام الدوال الموجودة بدل إعادة تنفيذها في صفحات أخرى.
- الحفاظ مؤقتًا على `common.py` كـ Compatibility Layer أثناء التقسيم.

---

# 2. المرحلة 0.5 — توحيد الدوال والمنطق المكرر

## 2.1 إنشاء `core/charting.py`

### نقل من `core/dashboard_cells/cells.py`

```python
_build_chart_figure()
_apply_chart_layout_tweaks()
```

### الهدف

منع الاعتماد:

```text
ui/chat.py → core/dashboard_cells/cells.py
```

واستبداله بـ:

```text
ui/chat.py ─────────┐
                    ↓
              core/charting.py
                    ↑
                    │
Dashboard Cells ────┘
```

---

# 3. المرحلة 0.6 — Result Rendering Layer

## إنشاء `ui/result_renderers.py`

### الدوال

```python
render_result()
render_table_result()
render_chart_result()
render_gauge_result()
render_kpi_result()
render_story_result()
```

### دالة اختيارية

```python
_build_gauge_figure()
```

إذا كان بناء الـGauge مشتركًا بين Chat وDashboard.

### المسؤولية

توحيد عرض:

- Table
- Chart
- Gauge
- KPI
- Story

بحيث لا تحتوي `chat.py` أو Dashboard Cells على نسخ مستقلة من منطق العرض.

---

# 4. المرحلة 0.7 — توحيد Reporting Integration

## إنشاء

```text
core/reporting/
├── __init__.py
├── adapter.py
└── service.py
```

## الدالة العامة

```python
add_result_to_report()
```

## الدوال المتخصصة عند الحاجة

```python
add_table_to_report()
add_chart_to_report()
add_gauge_to_report()
add_kpi_to_report()
add_story_to_report()
```

يفضل أن تكون `add_result_to_report()` هي الواجهة العامة، مع إبقاء الدوال المتخصصة داخلية عند عدم الحاجة لاستدعائها مباشرة.

---

# 5. المرحلة 1 — تقسيم `ui/common.py`

الملف الحالي يحتوي مسؤوليات متعددة، لذلك يتم تقسيمه تدريجيًا.

## الهيكل

```text
ui/
├── common.py
├── theme.py
├── components.py
├── result_renderers.py
├── navigation.py
├── guards.py
└── formatting.py
```

---

## 5.1 `ui/theme.py`

### نقل

```python
apply_rtl()
apply_theme_css()
apply_page_style()

_normalize_custom_colors()
_resolve_theme_colors()
get_theme_colors()

_hex_to_rgb01()
_rgb01_to_hex()
_generate_chart_colorway()
_apply_chart_colors()
get_chart_theme()
apply_plotly_theme()
```

---

## 5.2 `ui/components.py`

### نقل

```python
action_bar()
sequential_form()
```

### المطلوب

تدقيق الصفحات لمعرفة أين يمكن استبدال UI المتكرر بهذه المكونات.

إذا ثبت أن إحدى الدوال غير مستخدمة فعليًا، تحذف في مرحلة Dead-Code Audit.

---

## 5.3 `ui/guards.py`

### نقل

```python
require_login()
require_project()
get_project_manager()
```

---

## 5.4 `ui/navigation.py`

### نقل

```python
sidebar_header()
_render_account_quick_menu()
```

---

## 5.5 `ui/formatting.py`

### نقل

```python
notify()
format_local_dt()
temp_export_dir()
offer_download()
cleanup_stale_temp_dirs()
```

إذا اتضح أن `notify()` مسؤوليتها مختلفة، يمكن فصلها لاحقًا إلى `notifications.py`.

---

## 5.6 `ui/common.py`

بعد النقل يصبح Compatibility Layer، مثل:

```python
from ui.theme import ...
from ui.components import ...
from ui.guards import ...
from ui.navigation import ...
from ui.formatting import ...
```

بعد تحديث جميع imports يمكن حذف `common.py` إذا لم يعد مستخدمًا.

---

# 6. المرحلة 2 — إعادة هيكلة `ui/chat.py`

## المسؤولية النهائية

```text
Chat Page
Question Input
Conversation State
AI Manager
Result Routing
```

ولا يحتوي على:

```text
Chart Construction
Gauge Construction
KPI Rendering
Table Rendering
Story Rendering
Report Integration
```

---

## 6.1 `show_chat()`

### تعديل

تبسيطها لتدير:

```text
Authentication
Project
Theme
Sidebar
AI Manager
Chat UI
Result Routing
```

وتفويض العرض إلى `ui/result_renderers.py`.

---

## 6.2 `_render_result()`

### الإجراء

تحويلها إلى Wrapper بسيط:

```python
render_result(...)
```

أو حذفها بعد نقل جميع مسؤولياتها إلى:

```text
ui/result_renderers.py
```

---

## 6.3 `_add_block_for_type()`

### الإجراء

**حذف نهائيًا.**

السبب: تكرار مسؤولية Reporting الموجودة أصلًا في Dashboard Cells.

يستبدل بـ:

```python
add_result_to_report()
```

من طبقة Reporting.

---

## 6.4 Imports مؤكدة الحذف حاليًا

من `ui/chat.py`:

```python
import plotly.express as px
```

و:

```python
get_theme_colors
```

مع إعادة فحص باقي imports بعد النقل.

---

# 7. المرحلة 3 — إعادة هيكلة `core/dashboard_cells/cells.py`

## المشكلة

الملف يجمع:

```text
Cell Definitions
Chart Building
Rendering
Report Integration
```

ويجب تفكيك هذه المسؤوليات.

---

## 7.1 `EmptyCell`

### مراجعة

```python
render_result()
```

---

## 7.2 `TableCell`

### مراجعة

```python
render_result()
send_to_report()
```

العرض يعتمد على Result Renderer.

والـReporting يعتمد على Reporting API.

---

## 7.3 `ChartCell`

### مراجعة

```python
render_result()
send_to_report()
```

استخدام:

```text
core/charting.py
ui/result_renderers.py
core/reporting/
```

---

## 7.4 `GaugeCell`

### مراجعة

```python
render_result()
send_to_report()
```

---

## 7.5 `KpiCell`

### مراجعة

```python
render_result()
send_to_report()
```

---

## 7.6 `StoryCell`

### مراجعة

```python
execute()
render_result()
send_to_report()
```

خصوصًا فصل:

```text
AI
SQL
Rendering
Table
Reporting
```

عن بعضها.

---

## 7.7 حذف من `cells.py`

بعد نقلها إلى `core/charting.py`:

```python
_build_chart_figure()
_apply_chart_layout_tweaks()
```

---

## 7.8 تنظيف imports

يوجد تكرار لـ:

```python
import json
```

و:

```python
from core.dashboard_cells.base import DashboardCellBase, _sanitize_rows
```

يجب الإبقاء على import واحد فقط.

كما أن:

```python
get_theme_colors
```

غير مستخدمة حاليًا وتحذف.

---

# 8. المرحلة 4 — توحيد Result Contract

## إنشاء

```text
core/results/
├── __init__.py
├── types.py
└── models.py
```

## `types.py`

تعريف مركزي لـ:

```python
ResultType
```

بدل انتشار strings مثل:

```text
table
chart
gauge
kpi
story
```

---

## `models.py`

تعريف Result موحد يحتوي عند الحاجة على:

```text
type
data
metadata
title
sql
chart_config
```

### الهدف

جعل:

```text
Chat
Dashboard
Reports
```

تتعامل مع نفس Result Contract.

---

# 9. المرحلة 5 — تقسيم Dashboard Cells

بعد استقرار Result Contract:

```text
core/dashboard_cells/
├── __init__.py
├── base.py
├── empty.py
├── table.py
├── chart.py
├── gauge.py
├── kpi.py
└── story.py
```

## التوزيع

```text
empty.py  → EmptyCell
table.py  → TableCell
chart.py  → ChartCell
gauge.py  → GaugeCell
kpi.py    → KpiCell
story.py  → StoryCell
```

---

## `cells.py`

بعد تحديث جميع imports يصبح أحد الخيارين:

1. Compatibility Layer مؤقت.
2. يحذف بعد التأكد من عدم وجود references.

---

# 10. المرحلة 6 — قاعدة موحدة للـTables

يوجد مساران مشروعان:

| الاستخدام | الطريقة |
|---|---|
| بيانات صغيرة للعرض | `render_themed_table()` |
| جدول تفاعلي | `st.dataframe()` |
| Dataset كبير | `st.dataframe()` |
| Report Preview | `render_themed_table()` |
| Chat Result صغير | `render_themed_table()` |

لا يتم إنشاء Table Renderer ثالث.

---

# 11. المرحلة 7 — توحيد UI Components

## تدقيق شامل

البحث عن:

```text
Action Bars
Forms
Buttons
Notifications
Empty States
Headers
Dialogs
```

وأي منطق مكرر ينقل إلى:

```text
ui/components.py
```

### `action_bar()`

يتم اعتمادها إذا كانت مناسبة بدل تنفيذ Action Bar جديد في الصفحات.

### `sequential_form()`

يتم اعتمادها إذا كانت مناسبة.

إذا ثبت أن الدوال غير مستخدمة ولا يوجد احتياج فعلي لها:

```text
تحذف في مرحلة Dead-Code Audit
```

---

# 12. المرحلة 8 — Full Dead-Code / Dependency Audit

هذه مرحلة إلزامية قبل حذف الملفات.

## يتم فحص

```text
Imports
Call Graph
Streamlit Page Registration
Dynamic Imports
Plugin / Connector References
Config References
CLI References
Tests
Assets
Runtime References
```

---

## تصنيف الملفات

### USED

```text
Direct Import
Indirect Import
Runtime Usage
Page Usage
```

### UNUSED

```text
Confirmed Unused
Legacy
Duplicate
```

### UNKNOWN

```text
Dynamic / Runtime Reference
```

### قاعدة الحذف

يحذف فقط:

```text
Confirmed Unused
```

ولا يحذف:

```text
UNKNOWN
```

---

# 13. الملفات التي سيتم تعديلها

## مؤكد من الملفات المفحوصة

```text
ui/chat.py
ui/common.py
core/dashboard_cells/cells.py
```

## مراجعة لازمة

```text
core/dashboard_cells/base.py
```

## ملفات جديدة

```text
core/charting.py

core/results/__init__.py
core/results/types.py
core/results/models.py

core/reporting/__init__.py
core/reporting/adapter.py
core/reporting/service.py

ui/theme.py
ui/components.py
ui/result_renderers.py
ui/navigation.py
ui/guards.py
ui/formatting.py
```

---

# 14. الدوال المطلوب حذفها

## `ui/chat.py`

```python
_add_block_for_type()
```

## `core/dashboard_cells/cells.py`

بعد النقل:

```python
_build_chart_figure()
_apply_chart_layout_tweaks()
```

## Imports غير المستخدمة المؤكدة

### `ui/chat.py`

```python
plotly.express as px
get_theme_colors
```

### `core/dashboard_cells/cells.py`

```python
get_theme_colors
```

---

# 15. الملفات المرشحة للحذف

لا يوجد حاليًا ملف يمكن إعلان حذفه نهائيًا اعتمادًا على الفحص الجزئي فقط.

بعد إعادة الهيكلة تصبح هذه الملفات مرشحة للحذف:

```text
ui/common.py
core/dashboard_cells/cells.py
```

ولكن فقط إذا أصبحا Compatibility Layers غير مستخدمة.

أي ملف آخر يثبت في المرحلة 8 أنه:

```text
0 imports
0 runtime references
0 page references
0 test references
0 dynamic references
```

يمكن حذفه.

---

# 16. الهيكل النهائي المقترح

```text
DataLens/
│
├── app/
│
├── ai/
│   ├── ai_manager.py
│   └── prompt_builder.py
│
├── core/
│   ├── query_engine.py
│   ├── charting.py
│   │
│   ├── results/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   └── models.py
│   │
│   ├── reporting/
│   │   ├── __init__.py
│   │   ├── adapter.py
│   │   └── service.py
│   │
│   └── dashboard_cells/
│       ├── __init__.py
│       ├── base.py
│       ├── empty.py
│       ├── table.py
│       ├── chart.py
│       ├── gauge.py
│       ├── kpi.py
│       └── story.py
│
├── ui/
│   ├── chat.py
│   ├── theme.py
│   ├── components.py
│   ├── result_renderers.py
│   ├── navigation.py
│   ├── guards.py
│   ├── formatting.py
│   └── common.py              # مؤقتًا
│
├── pages/
│
├── data/
│
├── config.py
│
└── tests/
    ├── test_charting.py
    ├── test_results.py
    ├── test_renderers.py
    ├── test_cells.py
    └── test_reporting.py
```

---

# 17. معمارية الاعتماد النهائية

```text
                    ┌──────────────┐
                    │  AI Manager  │
                    └──────┬───────┘
                           ↓
                    ┌─────────────┐
                    │    Result   │
                    │   Contract  │
                    └──────┬──────┘
                           │
             ┌─────────────┼─────────────┐
             ↓             ↓             ↓
           Chat        Dashboard       Reports
             │             │             │
             ↓             ↓             ↓
       Result Renderer   Cells      Reporting API
             │             │             │
             └─────────────┼─────────────┘
                           ↓
                    Shared Core Logic
```

---

# 18. ترتيب التنفيذ النهائي

```text
0   Design System
        ↓
0.5 Consolidation
        ↓
0.6 Result Contract
        ↓
0.7 Reporting API
        ↓
1   Split common.py
        ↓
2   Split Dashboard Cells
        ↓
3   Refactor Chat
        ↓
4   Dashboard UX
        ↓
5   Reports
        ↓
6   Settings
        ↓
7   Navigation
        ↓
8   Full Dead-Code Audit
        ↓
9   Delete Legacy Files
        ↓
10  Regression Tests
```

---

# 19. قواعد إلزامية أثناء التنفيذ

1. لا توجد نسخة ثانية من Chart Builder.
2. لا توجد نسخة ثانية من Result Renderer.
3. لا توجد نسخة ثانية من Reporting Integration.
4. `chat.py` لا يعتمد على `dashboard_cells/cells.py`.
5. `common.py` لا يستمر في النمو.
6. جميع Result Types تعرف في مكان مركزي.
7. لا يتم حذف ملف قبل Dependency Audit.
8. لا يتم تغيير سلوك Business Logic أثناء Refactor إلا إذا كان ذلك مطلوبًا صراحة.
9. كل مرحلة يجب أن تبقى قابلة للتشغيل والاختبار.
10. بعد كل مرحلة يتم فحص imports وruntime references.
11. التغييرات الكبيرة على الملفات تعاد كملف كامل.
12. أي ملف غير مؤكد الاستخدام يصنف `UNKNOWN` ولا يحذف.

---

# 20. النتيجة المستهدفة

الهدف النهائي هو الانتقال من:

```text
Chat
 ├── Rendering
 ├── Charts
 ├── Reports
 └── UI

Dashboard Cells
 ├── Rendering
 ├── Charts
 └── Reports

Common
 └── وظائف كثيرة غير مترابطة
```

إلى:

```text
                  Shared Contracts
                        │
              ┌─────────┴─────────┐
              │                   │
         Shared Core          Shared UI
              │                   │
       ┌──────┼──────┐      ┌─────┼─────┐
       │      │      │      │     │     │
    Charts Results Reporting Chat Dashboard
```

بحيث تكون كل وظيفة في مكان واحد، وكل واجهة تستخدم نفس الـCore بدل إعادة تنفيذ المنطق.
