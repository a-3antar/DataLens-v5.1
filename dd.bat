@echo off
setlocal
set "ROOT=E:\Work\Masar\V1.0"

echo Deleting unused files from %ROOT% ...

for %%F in (
    "migrate_statuses.py"
    "org_chart_int.py"
    "schemas\__init__.py"
    "exports\.gitkeep"
) do (
    if exist "%ROOT%\%%~F" (
        del /f /q "%ROOT%\%%~F"
        echo Deleted %%~F
    )
)

rem حذف المجلدات الفارغة فقط (الأمر يفشل بأمان إن لم تكن فارغة)
rmdir "%ROOT%\schemas" 2>nul
rmdir "%ROOT%\exports" 2>nul

rem حذف كل مجلدات __pycache__
for /d /r "%ROOT%" %%D in (__pycache__) do (
    if exist "%%D" rd /s /q "%%D"
)

echo Done.
pause