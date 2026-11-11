"""
core/project_manager.py
========================
إنشاء / حذف / إدراج / تصدير / استيراد / ضغط المشاريع.
يعمل فوق ProjectDB ولا يتعامل مع الـ db مباشرة، باستثناء عمليات
الصيانة على ملف project.db نفسه (VACUUM) التي لا توجد لها دالة في
ProjectDB — نفس النمط المتّبع في core/dashboard_manager.py::
update_dashboard_template عبر sqlite3 مباشرة دون تعديل project_db.py.

🆕 تقليل حجم ملفات المشاريع:
------------------------------
- compact(): تنفيذ VACUUM لإعادة المساحة الفارغة التي يتركها SQLite
  داخل الملف بعد كل إعادة كتابة لجدول (تنظيف البيانات، تحديث ملف...).
- export(compress=True): تصدير نسخة مكتنزة عبر VACUUM INTO (بدون
  المساحة الفارغة) ثم ضغطها gzip — يصغّر ملف التصدير كثيراً.
- import_project(): يقبل .db أو .db.gz (الناتج عن التصدير المضغوط).
"""

import gzip
import uuid
import shutil
import sqlite3
import logging
import tempfile
from pathlib import Path
from typing  import Optional

from config      import PROJECTS_DIR
from core.project_db import ProjectDB

logger = logging.getLogger(__name__)


class ProjectManager:
    """
    إدارة دورة حياة المشاريع لمستخدم واحد.

    الاستخدام:
        pm = ProjectManager(user_id="u1")
        project_id = pm.create("مشروع المبيعات")
        pm.list_projects()
        pm.compact(project_id)
        pm.delete(project_id)
    """

    def __init__(self, user_id: str):
        self.user_id  = user_id
        self.user_dir = PROJECTS_DIR / user_id
        self.user_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────────────────────
    #  إنشاء مشروع
    # ──────────────────────────────────────────────────────────

    def create(self, name: str) -> dict:
        """
        إنشاء مشروع جديد.
        يرجع: {"ok": True, "project_id": "...", "db": ProjectDB}
               أو {"ok": False, "error": "..."}
        """
        name = name.strip()
        if not name:
            return {"ok": False, "error": "اسم المشروع مطلوب"}

        project_id = str(uuid.uuid4())
        try:
            db = ProjectDB(self.user_id, project_id)
            db.save_settings({"project_name": name})
            logger.info("Project created: '%s' (%s)", name, project_id)
            return {"ok": True, "project_id": project_id, "db": db}
        except Exception as e:
            logger.error("create project error: %s", e)
            # تنظيف المجلد لو فشل الإنشاء
            project_dir = self.user_dir / project_id
            if project_dir.exists():
                shutil.rmtree(project_dir, ignore_errors=True)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  قائمة المشاريع
    # ──────────────────────────────────────────────────────────

    def list_projects(self) -> list[dict]:
        """
        إرجاع كل مشاريع المستخدم مع معلومات موجزة.
        """
        projects = []
        for project_dir in sorted(self.user_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            project_id = project_dir.name
            try:
                db       = ProjectDB(self.user_id, project_id)
                settings = db.get_settings()
                info     = db.get_info()
                projects.append({
                    "project_id"  : project_id,
                    "name"        : settings.get("project_name", "بدون اسم"),
                    "files_count" : info["files"],
                    "reports_count": info["reports"],
                    "size_mb"     : info["size_mb"],
                })
            except Exception as e:
                logger.warning("Skipping corrupt project '%s': %s", project_id, e)
        return projects

    # ──────────────────────────────────────────────────────────
    #  فتح مشروع
    # ──────────────────────────────────────────────────────────

    def open(self, project_id: str) -> Optional[ProjectDB]:
        """
        فتح مشروع موجود. يرجع ProjectDB أو None لو لم يوجد.
        """
        project_dir = self.user_dir / project_id
        if not project_dir.exists():
            logger.error("Project not found: %s", project_id)
            return None
        return ProjectDB(self.user_id, project_id)

    # ──────────────────────────────────────────────────────────
    #  إعادة التسمية
    # ──────────────────────────────────────────────────────────

    def rename(self, project_id: str, new_name: str) -> dict:
        """إعادة تسمية مشروع."""
        new_name = new_name.strip()
        if not new_name:
            return {"ok": False, "error": "الاسم الجديد مطلوب"}
        db = self.open(project_id)
        if not db:
            return {"ok": False, "error": "المشروع غير موجود"}
        try:
            db.save_settings({"project_name": new_name})
            logger.info("Project renamed: %s → '%s'", project_id, new_name)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  حذف مشروع
    # ──────────────────────────────────────────────────────────

    def delete(self, project_id: str) -> dict:
        """حذف مشروع وكل ملفاته نهائياً."""
        project_dir = self.user_dir / project_id
        if not project_dir.exists():
            return {"ok": False, "error": "المشروع غير موجود"}
        try:
            # إغلاق أي اتصال مفتوح قبل الحذف (ضروري على Windows)
            self._close_db_connections(project_dir)
            shutil.rmtree(project_dir)
            logger.info("Project deleted: %s", project_id)
            return {"ok": True}
        except Exception as e:
            logger.error("delete project error: %s", e)
            return {"ok": False, "error": str(e)}

    def _close_db_connections(self, project_dir: Path) -> None:
        """إغلاق اتصالات SQLite المفتوحة في مجلد المشروع."""
        import gc
        # تشغيل garbage collector لإغلاق أي connection غير مُغلق
        gc.collect()
        # إغلاق صريح لكل ملفات .db في المجلد
        for db_file in project_dir.glob("*.db"):
            try:
                conn = sqlite3.connect(str(db_file))
                conn.close()
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────
    #  🆕 ضغط (تنظيف) ملف project.db
    # ──────────────────────────────────────────────────────────

    def _db_path(self, project_id: str) -> Path:
        """مسار ملف project.db الفعلي للمشروع."""
        return self.user_dir / project_id / "project.db"

    def compact(self, project_id: str) -> dict:
        """
        تنفيذ VACUUM على project.db لإرجاع المساحة الفارغة التي يتركها
        SQLite داخل الملف بعد كل إعادة كتابة لجدول (تنظيف البيانات،
        تحديث ملف، حذف جدول/لوحة/محادثات...). لا يغيّر أي بيانات.

        يرجع: {"ok": True, "before_mb": X, "after_mb": Y}
               أو {"ok": False, "error": "..."}
        """
        db_path = self._db_path(project_id)
        if not db_path.exists():
            return {"ok": False, "error": "المشروع غير موجود"}
        try:
            before = db_path.stat().st_size
            self._close_db_connections(db_path.parent)
            # VACUUM لا يعمل داخل transaction — autocommit مطلوب
            conn = sqlite3.connect(str(db_path), isolation_level=None)
            try:
                conn.execute("VACUUM")
            finally:
                conn.close()
            after = db_path.stat().st_size
            logger.info("Project compacted: %s — %.2f MB -> %.2f MB",
                        project_id, before / 1048576, after / 1048576)
            return {
                "ok": True,
                "before_mb": round(before / 1048576, 2),
                "after_mb" : round(after / 1048576, 2),
            }
        except Exception as e:
            logger.error("compact error: %s", e)
            return {"ok": False, "error": str(e)}

    # ──────────────────────────────────────────────────────────
    #  تصدير واستيراد
    # ──────────────────────────────────────────────────────────

    def export(self, project_id: str, export_path: Path, compress: bool = False) -> dict:
        """
        تصدير project.db إلى مسار خارجي كنسخة مكتنزة (VACUUM INTO —
        بدون المساحة الفارغة داخل الملف الأصلي).

        compress=True: تُضغط النسخة بـ gzip (يُفضَّل أن ينتهي export_path
        بـ .db.gz ليتعرّف عليه import_project تلقائياً).

        يرجع: {"ok": True, "path": "..."} أو {"ok": False, "error": "..."}
        """
        db = self.open(project_id)
        if not db:
            return {"ok": False, "error": "المشروع غير موجود"}
        try:
            with tempfile.TemporaryDirectory(prefix="datalens_vacuum_") as tmp_dir:
                compact_copy = Path(tmp_dir) / "project.db"
                src = sqlite3.connect(str(db.db_path))
                try:
                    # VACUUM INTO يتطلب SQLite 3.27+ والملف الهدف غير موجود
                    src.execute("VACUUM INTO ?", (str(compact_copy),))
                finally:
                    src.close()

                if compress:
                    with open(compact_copy, "rb") as f_in, gzip.open(export_path, "wb", compresslevel=9) as f_out:
                        shutil.copyfileobj(f_in, f_out)
                else:
                    shutil.copy2(compact_copy, export_path)

            logger.info("Project exported: %s → %s (compress=%s)", project_id, export_path, compress)
            return {"ok": True, "path": str(export_path)}
        except Exception as e:
            logger.error("export error: %s", e)
            return {"ok": False, "error": str(e)}

    def import_project(self, db_file: Path) -> dict:
        """
        استيراد project.db من ملف خارجي (.db أو .db.gz الناتج عن
        التصدير المضغوط).
        يرجع: {"ok": True, "project_id": "..."} أو {"ok": False, "error": "..."}
        """
        if not db_file.exists():
            return {"ok": False, "error": "الملف غير موجود"}

        is_gzip = db_file.suffix == ".gz"
        if not is_gzip and db_file.suffix != ".db":
            return {"ok": False, "error": "الملف يجب أن يكون بامتداد .db أو .db.gz"}

        project_id  = str(uuid.uuid4())
        project_dir = self.user_dir / project_id
        try:
            project_dir.mkdir(parents=True)
            dest = project_dir / "project.db"
            if is_gzip:
                with gzip.open(db_file, "rb") as f_in, open(dest, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            else:
                shutil.copy2(db_file, dest)
            # التحقق من سلامة الملف
            db = ProjectDB(self.user_id, project_id)
            _ = db.get_settings()
            logger.info("Project imported: %s", project_id)
            return {"ok": True, "project_id": project_id}
        except Exception as e:
            logger.error("import error: %s", e)
            if project_dir.exists():
                shutil.rmtree(project_dir, ignore_errors=True)
            return {"ok": False, "error": str(e)}