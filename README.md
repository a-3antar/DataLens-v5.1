Purpose & context

Ahmed is building DataLens V5.0, a full-stack Arabic-language data analytics web application using Python and Streamlit. The app enables users to upload Excel/CSV files, query data in natural language (Arabic or English), receive AI-generated SQL executed against DuckDB, and visualize results as tables, charts, gauges, KPIs, or AI-generated Arabic narrative text — all exportable to PDF, Excel, or Markdown. The interface is RTL Arabic throughout.

Deployment target: Streamlit Community Cloud (Docker container approach was canceled). Ollama is reserved for offline/local use only; cloud engines (Gemini, Grok, OpenRouter) are the primary AI backends for the deployed app.

Core architectural decisions:

DuckDB for query execution; SQLite (project.db per project, users.db for auth) for persistence
AI keys stored per-user per-engine in users.db (no encryption); last-used engine auto-populated on new project creation
UTC storage throughout; timezone conversion at display layer only (using zoneinfo)
Single SQLite file per project stores data, settings, relations, and reports
No manual file management — automatic cleanup with zero user intervention

Current state

The application is well-advanced, with the backend and export layers validated:

Phases 2, 3, and 4 tested and producing good results (FileManager/DataManager/QueryEngine, AI layer, ReportManager/exporters)
The most recent development sprint (Aug 24) delivered a large batch of features across 9 files, including:
Per-user API key management via users.db (core/auth.py)
Parallel AI execution via ThreadPoolExecutor with a max_total_wait_seconds global timeout budget (separate budgets for SQL generation and Story Telling)
Story Telling cells always run in parallel; other cells run in parallel for non-Ollama engines; Ollama remains sequential
Animated emoji progress indicator (⏳/⌛ via st.empty()) replacing a complex progress bar
Lazy Slicer loading with explicit load button; Slicer state reset button
AI-powered automatic dashboard builder (optional, same save path as manual creation)
Local time display using format_local_dt() helper in dashboards and chat history
Fail-fast logic for permanent auth errors ("auth" error type) vs. retried transient errors
Configurable AI retry delay (AI_RETRY_DELAY_SECONDS) before retrying failed connections
Dashboard cell refresh reuses cached base_sql (no AI re-call), except Story Telling cells which always call AI

Thread safety approach: DuckDB connections created fresh per call; pandas DataFrames not mutated across threads; SQLite writes deferred to main thread after all futures complete via as_completed.

On the horizon

Remaining items not yet implemented (tracked in CHANGES_AND_ROADMAP.md):

ui/result_renderer.py shared rendering module (eliminate duplication between chat.py and dashboards.py)
Dashboard-to-report one-click export
Slicer presets
Cross-dashboard cell copying
Gallery name search
st.cache_data caching layer
Delete-confirmation timeout
Comprehensive UI redesign study

Key learnings & principles

Arabic text rendering: Pillow's raqm engine auto-applies bidi/shaping — pre-processing with arabic_reshaper+bidi double-reverses text. ReportLab requires manual pre-processing. These behave differently and must not be treated uniformly.
Date handling in SQLite: SQLite has no native datetime type; date columns silently become text strings on save/reload, breaking DATE_TRUNC and date-based queries. Explicit dtype conversion is required at the DB layer.
SQL column correction: Auto-correction must handle both quoted ("col") and unquoted (col) identifiers, and similarity-threshold guarding is essential to prevent silent wrong-column substitutions.
Gemini 401 errors: Google's "Expected OAuth 2 access token..." message signals key-level restrictions (IP restrictions or API not enabled in Cloud Console), not quota exhaustion (which returns 429/RESOURCE_EXHAUSTED). Story Telling is more vulnerable because it makes two sequential AI calls.
Ollama parallelism: Ollama does not benefit from parallel execution in practice due to local resource contention — sequential execution is the correct approach for it.
Plan before implement: Ahmed's strong preference is to discuss and agree on architecture/approach before any implementation begins.
Roadmap as continuity artifact: CHANGES_AND_ROADMAP.md is used to maintain continuity across sessions.

Approach & patterns

All conversations and planning conducted in Arabic
Structured workflow: audit → prioritized plan → review/agreement → implementation grouped by file affinity
Implementation validated with python3 -m py_compile on all changed files
Manual steps required before deployment (e.g., database backups, specific test scenarios) are documented explicitly
Phase-based development with test suites; phases 2, 3, and 4 confirmed working well

Tools & resources

Framework: Streamlit (Community Cloud deployment)
Query engine: DuckDB
Databases: SQLite (project.db, users.db)
AI engines: Gemini, Grok, OpenRouter (cloud/primary); Ollama (offline only)
HTTP client: httpx
Key libraries: pandas, ReportLab, Pillow (raqm), arabic_reshaper, python-bidi, zoneinfo (stdlib), bcrypt
Parallelism: concurrent.futures.ThreadPoolExecutor
Export formats: PDF, Excel, Markdown