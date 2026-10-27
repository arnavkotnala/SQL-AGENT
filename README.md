# LoopSQL AI

LoopSQL AI is an autonomous text-to-SQL agent. You describe what you need in plain language. The agent:
- inspects your data (a database or **your own Excel file**),
- plans and writes SQL that is grounded in the schema,
- validates it,
- runs it read-only,
- checks the result against your goal,
- corrects itself when needed (at most 5 iterations),
- exports verified results to **Excel (.xlsx)** and CSV.

For Excel files, it can also **change the workbook**, for example "add a Status column marking students below 50% as Detained". Every change is shown as a cell-by-cell preview, and nothing is written until you approve it.

Google Gemini does the reasoning. All database access and file writes are done by application code, and only after validation.

---

## 1. Quick start (Windows, local mode)

Requirements: Python 3.11+ and Node.js 18+.

1. Double-click **`start.bat`**.
2. The first run creates `backend\.env` and opens it in Notepad. Set `GEMINI_API_KEY` to a key from https://aistudio.google.com/apikey, then save the file.
3. Run `start.bat` again. It opens http://127.0.0.1:8000.

macOS/Linux: run `./start.sh` instead.

Manual start, for development with hot reload:

```bash
# terminal 1 - API on :8000
cd backend
python -m venv .venv && .venv\Scripts\activate        # source .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt
copy .env.example .env                                  # then set GEMINI_API_KEY
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# terminal 2 - UI on :5173 (proxies /api to :8000)
cd frontend
npm install
npm run dev
```

## 2. Using it

| You want to… | How |
|---|---|
| Query **your Excel / CSV / SQLite file** | *Data source → Excel / file*. Either click **Open file…** or paste a path such as `C:\Users\you\Documents\marks.xlsx`. You can also put the path in the request itself; the agent connects to it. |
| Run a request without picking a file | Just run it. The agent **pauses and asks** which file or database to use, then continues. |
| **Change** your Excel file | Ask for the change, e.g. *"Add a column Status: Detained if attendance below 50%, else Eligible"*. Review the before/after preview, then choose how to save (see the table below). |
| Use a database server | *Data source → Database*: `postgresql+psycopg://user:pass@host:5432/db` or `mysql+pymysql://…`. Install the driver first: `pip install "psycopg[binary]"` or `pymysql`. Connections are read-only. |
| Try it without your own data | *Samples*: an Education database (students, courses, subjects, attendance, marks) or a Hospital database. |
| Review SQL before it runs | Tick **Review SQL before it runs**. You can edit the SQL; your edit is validated again before it executes. |

When you approve a change to an Excel file, you choose how it is saved:

| Save option | When it's available | What happens |
|---|---|---|
| **Save back into the file I opened** | Chrome or Edge, with the file opened via *Open file…* | The browser writes the approved changes into your local file. You get an **Undo** button. This works for deployed servers too. If the file changed after you opened it, it is not overwritten. |
| **Overwrite original** | The file was connected by path, in local mode | A timestamped backup goes to `backend/data/backups/`, then the file is updated in place. |
| **Save as new copy** | Always | Your original is untouched. You download `<name>_edited.xlsx`. |

Safety rules for edits:
- Formula columns are read-only.
- After a delete, row-by-row formulas are re-pointed to the shifted rows. Formulas that reference a range, such as a SUM over the column, are not rewritten, and the preview warns you about this.
- If the file changes on disk after the preview was made, the edit is refused.
- Workbooks containing charts or pivot tables get a warning recommending *new copy*, because openpyxl may not preserve those.
- **Close the file in Excel before you overwrite it.** Windows locks open files, and you'll get a clear error if it's still open.

## 3. How the agent works

```
OBSERVE (find source; ask if missing) → INSPECT SCHEMA → DEFINE GOAL (+ clarifying question if ambiguous)
   → [ PLAN → GENERATE SQL → VALIDATE → (your approval) → EXECUTE → EVALUATE → REFINE ] × ≤ 5
   → EXPORT (xlsx + csv) / APPLY change → VERIFY (re-open the file) → COMPLETE
```

- **The schema is inspected before any SQL is written.** The model gets:
  - tables and views, columns and types, and foreign keys;
  - for spreadsheets, notes such as "Excel percentage stored as 0–1" and "formula column (read-only)";
  - a few sample rows per table.
- **Gemini returns structured JSON** that is validated with Pydantic. There are three contracts: `GoalInterpretation`, `SqlProposal` and `ResultEvaluation`, in `backend/app/llm/schemas.py`.
- **Recoverable failures are fed back with the exact error** to produce a corrected query:
  - unknown table or column,
  - syntax error,
  - database error,
  - a result that fails evaluation.
- **The loop is bounded.** It stops at 5 iterations (`AGENT_MAX_ITERATIONS`). An identical repeat query, detected after sqlglot normalisation, is never re-executed, and two repeats in a row stop the run.
- **The run stops** when:
  - the goal is met;
  - user input is needed (no source, an ambiguity, or data that can't answer the request);
  - a security rule is triggered;
  - the iteration budget runs out.
- **Success is only reported once the workbook has been written *and* re-opened and verified.** For a query, the check covers the header and row count. For a change, every written cell is checked.
- The UI polls the backend's real run state: the event timeline, iteration counter, SQL, attempts and errors, and any pending question or approval. Nothing is simulated.

## 4. Security model

Model-generated SQL is treated as untrusted. Several independent layers protect the data:

1. **AST validation with sqlglot** (`app/security/sql_validator.py`):
   - exactly one statement, of type SELECT/CTE/UNION;
   - no DDL/DML, PRAGMA, ATTACH, `SELECT INTO`, or `FOR UPDATE`;
   - dangerous functions are denied (`load_extension`, `pg_read_file`, `sleep`, …);
   - every table must be known, and columns are verified with sqlglot's qualifier;
   - named `:parameters` only;
   - comments are stripped, and the **regenerated** SQL is what gets executed.
2. **Database layer:**
   - SQLite is opened with `mode=ro` and an authorizer callback that allows only reads, plus a progress-handler timeout.
   - PostgreSQL/MySQL sessions are set `READ ONLY` with a statement timeout.
3. **Parameterised values.** User values are bound as parameters, never concatenated into the SQL string.
4. **Limits:**
   - query timeout `QUERY_TIMEOUT_SECONDS`;
   - result cap `MAX_RESULT_ROWS` (truncation is disclosed in the UI and on the workbook's *Query Info* sheet);
   - edit cap `MAX_MODIFIED_ROWS`;
   - upload cap `MAX_UPLOAD_MB`.
5. **Spreadsheet edits** use a separate, narrower validator:
   - one UPDATE/INSERT/DELETE on one sheet table;
   - no rowid tampering;
   - formula columns locked.
   The statement is dry-run on a private mirror inside a rolled-back transaction, then applied only after approval.
6. **Files:**
   - exports are served only by random ID through `/api/exports/{id}`;
   - upload names are sanitised;
   - Excel/CSV formula injection is neutralised;
   - the model never chooses file paths and never runs code.
7. **Secrets:**
   - the Gemini key and DB passwords stay server-side;
   - connection URLs are masked;
   - logs redact secrets;
   - `/api/health` only reports *whether* a key is configured.

**Data sent to Gemini:** the schema, up to 3 sample rows per table, and the first 10 result rows (used for evaluation). Set `SCHEMA_SAMPLE_ROWS=0` and `LLM_RESULT_PREVIEW_ROWS=0` if your data must not leave the machine. Do not use personal or confidential data on an API tier whose terms allow training on inputs.

## 5. Deployment

The production build is a single process: FastAPI serves the API **and** the built React app on one port.

- **Docker:** `docker build -t loopsql-ai . && docker run -p 8000:8000 -e GEMINI_API_KEY=… -e APP_ACCESS_TOKEN=… loopsql-ai`
- **Render:** push the repo and create a *Blueprint* from `render.yaml`. Set `GEMINI_API_KEY` in the dashboard. An `APP_ACCESS_TOKEN` is generated for you; give it to your users, and the UI asks for it once.

On a deployed server:
- `ALLOW_LOCAL_FILE_PATHS=false`, because the server cannot see users' disks.
- Users upload files. **Chrome/Edge users can still save approved edits back into their local file**, because the browser does the write.
- Run **one worker**. Run state and approvals are kept in memory, so restarting clears session history.
- The free Render plan has an ephemeral disk.

## 6. Tests

```bash
cd backend
.venv\Scripts\python -m pytest            # 106 deterministic tests, Gemini mocked
.venv\Scripts\python -m pytest -m live -s # real Gemini; runs only when GEMINI_API_KEY is configured
```

The deterministic suite covers:
- NL→SQL success;
- attendance below 50%, checked against an independent SQL ground truth;
- joins and aggregations;
- **Excel exports re-opened and compared cell by cell** with the database;
- empty results;
- invalid tables and columns;
- syntax errors and DB errors with correction;
- repeated failures and the iteration cap;
- duplicate-query detection;
- destructive SQL and SQL injection (through the agent and at the DB layer);
- invalid connections and password masking;
- Gemini 429 (honouring the server's `retryDelay`)/5xx/403/404 and invalid JSON, with retry and backoff;
- the download endpoint and path traversal;
- result, upload and edit size limits;
- Excel edits: new column, in-place with backup, copy, reject, formula protection, stale file;
- the agent asking for a file or a clarification;
- SQL approval with edits;
- the access-key gate;
- SPA serving;
- startup cleanup.

The live suite runs real Gemini against the education sample and against an Excel workbook. One live test asks the agent in plain English to add and fill a Status column, approves the change, and checks the saved `.xlsx` cell by cell.

## 7. Project layout

```
backend/app/
  config.py                 settings (.env); model name lives only here
  agent/                    loop.py (state machine), run.py (state/events/pauses), prompts.py, manager.py
  llm/                      provider.py (Gemini, retries, error mapping), schemas.py (structured outputs)
  datasources/              base.py (adapter interface), sql_adapter.py (SQLite/Postgres/MySQL),
                            spreadsheet.py (Excel/CSV mirror, diff, write-back), registry.py
  security/sql_validator.py AST validation
  export/exporter.py        xlsx/csv writers, verification, download registry
  api/routes.py, main.py    HTTP API, auth gate, static frontend
  sample_data/generate.py   synthetic education + hospital databases
frontend/src/               React + TypeScript UI (App.tsx, components/, fileAccess.ts)
```

## 8. Known limitations

- PostgreSQL/MySQL adapters share the tested SQLAlchemy path but have **not been run against live servers** in this build. Drivers are optional installs.
- Excel write-back uses openpyxl:
  - charts, images, pivot tables and slicers may not survive a re-save (you are warned);
  - after row deletions, range formulas are not rewritten;
  - legacy `.xls` files must be re-saved as `.xlsx`.
- Formula columns are typed from Excel's cached values. A file produced by a script and never opened in Excel has no cached values.
- Session state is in memory (single worker). Use an external store before scaling horizontally.
- Answer quality depends on the Gemini model. Evaluation is LLM judgement plus deterministic checks, so verify important results.
