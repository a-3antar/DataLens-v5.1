"""
core/project_manager.py
========================
إنشاء / حذف / إدراج / تصدير / استيراد المشاريع.
يعمل فوق ProjectDB ولا يتعامل مع الـ db مباشرة.
"""

import uuid
import shutil
import logging
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
        import sqlite3, gc
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
    #  تصدير واستيراد
    # ──────────────────────────────────────────────────────────

    def export(self, project_id: str, export_path: Path) -> dict:
        """
        تصدير project.db إلى مسار خارجي.
        يرجع: {"ok": True, "path": "..."} أو {"ok": False, "error": "..."}
        """
        db = self.open(project_id)
        if not db:
            return {"ok": False, "error": "المشروع غير موجود"}
        try:
            shutil.copy2(db.db_path, export_path)
            logger.info("Project exported: %s → %s", project_id, export_path)
            return {"ok": True, "path": str(export_path)}
        except Exception as e:
            logger.error("export error: %s", e)
            return {"ok": False, "error": str(e)}

    def import_project(self, db_file: Path) -> dict:
        """
        استيراد project.db من ملف خارجي.
        يرجع: {"ok": True, "project_id": "..."} أو {"ok": False, "error": "..."}
        """
        if not db_file.exists():
            return {"ok": False, "error": "الملف غير موجود"}
        if db_file.suffix != ".db":
            return {"ok": False, "error": "الملف يجب أن يكون بامتداد .db"}
        try:
            project_id  = str(uuid.uuid4())
            project_dir = self.user_dir / project_id
            project_dir.mkdir(parents=True)
            dest = project_dir / "project.db"
            shutil.copy2(db_file, dest)
            # التحقق من سلامة الملف
            db = ProjectDB(self.user_id, project_id)
            _ = db.get_settings()
            logger.info("Project imported: %s", project_id)
            return {"ok": True, "project_id": project_id}
        except Exception as e:
            logger.error("import error: %s", e)
            project_dir = self.user_dir / project_id
            if project_dir.exists():
                shutil.rmtree(project_dir, ignore_errors=True)
            return {"ok": False, "error": str(e)}
