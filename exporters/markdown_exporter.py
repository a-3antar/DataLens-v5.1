"""
exporters/markdown_exporter.py
================================
تصدير التقارير إلى Markdown (.md).
أبسط وأسرع صيغة للتصدير — مناسبة للمشاركة والأرشفة.

🧹 تنظيف: حُذفت to_string() — غير مستخدمة في أي مكان (ui/reports.py
يستخدم export() فقط لكتابة ملف مباشرة).
"""

import logging
from pathlib import Path

from exporters.report_manager import ReportManager

logger = logging.getLogger(__name__)


class MarkdownExporter:
    """
    تصدير تقرير إلى Markdown.

    الاستخدام:
        exp = MarkdownExporter(report_manager)
        exp.export(report_id, Path("report.md"))
    """

    def __init__(self, report_manager: ReportManager):
        self.rm = report_manager

    def export(self, report_id: str, output_path: Path) -> dict:
        """
        تصدير تقرير إلى .md.
        يرجع: {"ok": True, "path": "..."} أو {"ok": False, "error": "..."}
        """
        reports = self.rm.list_reports()
        report  = next((r for r in reports if r["id"] == report_id), None)
        if not report:
            return {"ok": False, "error": "التقرير غير موجود"}

        blocks = self.rm.get_blocks(report_id)

        try:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            lines = self._build_markdown(report["title"], blocks)

            output_path.write_text(
                "\n".join(lines),
                encoding="utf-8",
            )
            logger.info("Markdown exported: %s", output_path)
            return {"ok": True, "path": str(output_path)}

        except Exception as e:
            logger.error("Markdown export error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  بناء محتوى الـ Markdown
    # ──────────────────────────────────────────────────────────

    def _build_markdown(self, title: str, blocks: list) -> list[str]:
        lines = []

        # عنوان التقرير
        lines.append(f"# {title}")
        lines.append("")

        for block in blocks:
            btype   = block.get("block_type")
            content = block.get("content", {})

            if btype == "paragraph":
                lines.extend(self._render_paragraph(content))
            elif btype == "table":
                lines.extend(self._render_table(content))
            elif btype == "kpi":
                lines.extend(self._render_kpi(content))
            elif btype == "gauge":
                lines.extend(self._render_gauge(content))
            elif btype == "chart":
                lines.extend(self._render_chart(content))
            elif btype == "dashboard":
                lines.extend(self._render_dashboard(content))

            lines.append("")   # سطر فارغ بين البلوكات

        return lines

    def _render_paragraph(self, content: dict) -> list[str]:
        text = content.get("text", "")
        return text.splitlines() if text else []

    def _render_dashboard(self, content: dict) -> list[str]:
        lines = []
        title = content.get("title", "")
        if title:
            lines.append(f"## {title}")
            lines.append("")

        gauges = content.get("gauges", [])
        if gauges:
            lines.append("| " + " | ".join("مؤشر" for _ in gauges) + " |")
            lines.append("| " + " | ".join("---" for _ in gauges) + " |")
            lines.append("| " + " | ".join(self._snapshot_cell_to_md(g) for g in gauges) + " |")
            lines.append("")

        columns = content.get("columns", [])
        if columns:
            n = len(columns)
            lines.append("| " + " | ".join(f"عمود {i + 1}" for i in range(n)) + " |")
            lines.append("| " + " | ".join("---" for _ in range(n)) + " |")
            max_len = max((len(c) for c in columns), default=0)
            for row_i in range(max_len):
                cells = [self._snapshot_cell_to_md(col[row_i]) if row_i < len(col) else "" for col in columns]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")

        return lines

    def _snapshot_cell_to_md(self, cell: dict) -> str:
        ctype = cell.get("type")
        data = cell.get("content", {}) or {}
        title = cell.get("title") or ""
        parts = [f"**{title}**"] if title else []

        if ctype in ("table", "chart"):
            cols = data.get("columns", [])
            if cols:
                parts.append(" / ".join(str(c) for c in cols))
                for row in data.get("rows", [])[:5]:
                    parts.append(" / ".join(str(row.get(c, "")) for c in cols))
        elif ctype == "gauge":
            r = (data.get("rows") or [{}])[0]
            parts.append(f"{r.get('current_value', 0)} ({r.get('min_value', 0)}–{r.get('max_value', 100)})")
        elif ctype == "kpi":
            r = (data.get("rows") or [{}])[0]
            parts.append(f"{r.get('actual_value', 0)} / {r.get('target_value', 0)}")
        elif ctype == "story":
            parts.append((data.get("story", "") or "")[:200])

        return "<br>".join(parts) if parts else ""

    def _render_table(self, content: dict) -> list[str]:
        data    = content.get("data", [])
        columns = content.get("columns", [])

        if not data or not columns:
            return ["*جدول فارغ*"]

        lines = []
        # Header
        lines.append("| " + " | ".join(str(c) for c in columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
        # Rows
        for row in data:
            values = [str(row.get(c, "")) for c in columns]
            lines.append("| " + " | ".join(values) + " |")

        return lines

    def _render_kpi(self, content: dict) -> list[str]:
        label  = content.get("label", "مؤشر")
        actual = content.get("actual_value", 0)
        target = content.get("target_value", 0)
        unit   = content.get("unit", "")

        gap     = actual - target
        gap_sym = "▲" if gap >= 0 else "▼"
        lines   = [
            f"### {label}",
            "",
            f"| القيمة الفعلية | الهدف | الفجوة |",
            f"| --- | --- | --- |",
            f"| **{actual:,.2f} {unit}** | {target:,.2f} {unit} | {gap_sym} {abs(gap):,.2f} |",
        ]
        return lines

    def _render_gauge(self, content: dict) -> list[str]:
        label   = content.get("label", "مقياس")
        current = content.get("current_value", 0)
        mn      = content.get("min_value", 0)
        mx      = content.get("max_value", 100)

        pct   = ((current - mn) / (mx - mn) * 100) if mx != mn else 0
        bar   = self._progress_bar(pct)
        lines = [
            f"### {label}",
            "",
            f"القيمة الحالية: **{current:,.2f}** ({pct:.1f}%)",
            f"المدى: {mn} ← {bar} → {mx}",
        ]
        return lines

    def _render_chart(self, content: dict) -> list[str]:
        title      = content.get("title", "رسم بياني")
        chart_type = content.get("chart_type", "")
        data       = content.get("data", [])
        x_col      = content.get("x_col", "")
        y_cols     = content.get("y_cols", [])

        lines = [f"### {title} ({chart_type})"]
        if data and x_col and y_cols:
            # نعرض البيانات كجدول
            cols = [x_col] + y_cols
            lines.append("")
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("| " + " | ".join("---" for _ in cols) + " |")
            for row in data:
                values = [str(row.get(c, "")) for c in cols]
                lines.append("| " + " | ".join(values) + " |")
        return lines

    def _progress_bar(self, pct: float, width: int = 20) -> str:
        """شريط تقدم نصي."""
        filled = int(pct / 100 * width)
        return "█" * filled + "░" * (width - filled)
