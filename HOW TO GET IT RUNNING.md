# How to get LoopSQL AI running

LoopSQL AI lets you ask questions about an Excel file or database in plain English. For example: *"Find students whose attendance is below 50%"*. The agent:

- writes SQL;
- checks that the SQL is safe;
- runs it;
- shows the results;
- exports them to Excel.

It can also **change your Excel file**, for example *"Add a Status column…"*, but only after you approve a before/after preview.

There are two versions. **Most people only need the first one.**

| Version | What it is | Needs installing? |
|---|---|---|
| **A. `standalone\index.html`** (recommended) | One web page. Everything runs inside your browser. | **No.** Just double-click it. |
| B. Server version (`backend\` + `frontend\`) | A Python server plus a website, for PostgreSQL/MySQL or a shared team deployment. | Yes: Python and Node.js |

---

## A. Standalone page (no server, no install)

### Step 1 — Open the page

1. Open the folder `C:\Users\Lenovo\loopsql-ai\standalone\`.
2. Double-click **`index.html`**.
   - Use **Chrome or Microsoft Edge**. Firefox works too, but it can't save changes straight back into your file.
3. Keep the **internet on**. The page loads two libraries (the SQL engine and the Excel reader) from `cdnjs.cloudflare.com`.

### Step 2 — Choose the AI engine (section **1. AI engine** on the left)

You have two choices.

#### Option 1: Test mode (no API key)

Leave **"Test mode (no key)"** selected. It works straight away.

- It is **not AI**. It is a built-in rule-based planner.
- It understands common requests:
  - attendance below N%
  - marks below N in any subject
  - average marks per subject, class or course
  - top N students
  - "how many … in each class"
  - list a table
  - add a Status column
  - delete rows
- You can also type SQL directly, e.g. `SELECT * FROM students WHERE class = 10`.
- Everything else works exactly as in AI mode: safety checks, Excel export, approval, and saving changes.

#### Option 2: Gemini AI (needs a free API key)

**Get your API key:**

1. Go to **https://aistudio.google.com/apikey** and sign in with your Google account.
2. Click **Create API key**, then copy the key. It starts with `AIza…`.

**Where to paste the API key:**

1. In section **1. AI engine**, click **Gemini AI**.
2. Paste the key into the **"Gemini API key"** box.
3. Click **Check key**. It should say *"Key works. Model … is available"*.
   - If it says the model is not in your list, type or choose one of the model names it shows (a "flash" model is a good choice) in the **Model** box.
4. Optional: tick **"Remember the key on this computer"**. If you leave it unticked, the key is forgotten when you close the tab.
   - Chrome and Edge share saved data between **all** HTML files opened from your disk, so another downloaded `.html` file could read a remembered key.
   - **Leave it unticked on shared or lab computers**, and only tick it on your own PC.

Rules for keeping your key safe:

- The key goes **only** to Google (`generativelanguage.googleapis.com`).
- **Never** type your key into the `index.html` file itself.
- **Never** share a copy of the file that contains your key.
- **Never** paste the key into chats or documents.

### Step 3 — Open your data (section **2. Your data**)

You can open your data in three ways:

- Click **"Open Excel / CSV / SQLite file…"** and pick your file.
- Drag the file onto the box.
- Click **"Load sample: Education database"** to try it out.

Supported files are `.xlsx`, `.xlsm`, `.csv`, `.db`, `.sqlite` and `.sqlite3`. Old `.xls` files must first be re-saved as `.xlsx` in Excel.

**Pasting a file path or link doesn't work in the standalone page.** A web page is not allowed to read your disk by itself, so a path such as `C:\…\marks.xlsx`, or a OneDrive link, can't be opened from text. If you run a request before opening a file, the agent stops and asks you to open one. Use the **Open…** button or drag and drop, then pick it in the question card. Only the server version (B) can open files from a typed path.

**Ready-made test file:** `standalone\school_student_marks.xlsx` is a synthetic school database (no real people) with three sheets:

| Sheet | Contents |
|---|---|
| Students | 60 students, with attendance % |
| Subjects | 5 subjects |
| Marks | 600 marks, plus a Percentage formula column |

After you open a file, the **Schema** list shows the sheets/tables and their columns.

### Step 4 — Ask

Type a request in section **3. Ask**, or click one of the suggestion chips, then click **▶ Run Agent**. Examples:

- *Find students whose attendance is below 50% and export their names, roll numbers and attendance.*
- *Find students who scored below 40 in any subject.*
- *Calculate average marks for each subject.*
- *Show the top 5 students by average marks.*
- *How many students are in each class?*
- *Add a column 'Status' with 'Detained' for students whose attendance is below 75%, otherwise 'Eligible'.*

While it runs you see a live timeline (Planning → Validating → Executing → Evaluating → Exporting → Completed), the iteration counter, the SQL it wrote, any retries, and the results table (with search and pages).

**Download Excel** gives you a formatted workbook:

- real numbers, percentages and dates;
- a frozen header row and filters;
- a *Query Info* sheet with the SQL that was run.

**Download CSV** is also available.

If you tick **"Review SQL before it runs"**, you can read and edit the SQL before anything executes. Your edits are safety-checked again.

### Step 5 — Changing your Excel file

When you ask for a change, nothing is written straight away. First you see a **preview of every cell that would change** (before → after), then you choose how to save:

| Option | When it's offered | What happens |
|---|---|---|
| **Save back into my file** | Chrome/Edge, and the file was opened with the **Open…** button | The change is written into your original file. An **Undo** button restores the previous version. If the file changed on disk after you opened it, the page refuses to overwrite it. |
| **Download an edited copy** | Always | You get `<name>_edited.xlsx`. Your original is untouched. |

Before you save back, **close the file in Excel**, otherwise Windows blocks the write.

What changes are safe:

- Formula columns are never overwritten.
- When rows are added or deleted, row-by-row formulas keep pointing at the right row.
- Formulas that reference a whole range, such as a `SUM` over a column, are not rewritten. The preview warns you about this.
- Charts and pivot tables may not survive a re-save, so use *Download an edited copy* for workbooks that have them.
- Databases (`.db`) and CSV files are always **read-only**.

### What is sent to Google (Gemini mode only)

Gemini receives:

- the table and column names;
- 3 sample rows per table;
- the first 10 result rows (so it can check its own answer).

Your file itself is never uploaded. If your data is confidential, use **Test mode** or anonymised data. Free AI Studio keys may let Google use your inputs to improve its products, so check the terms that apply to your account.

### Troubleshooting

| Problem | Fix |
|---|---|
| Red banner: *"libraries could not be loaded"* | Connect to the internet and reload the page. |
| *"Gemini model … was not found"* | Click **Check key** and pick a model from the list. |
| *"Rate limit / quota exceeded (429)"* | Free keys have a per-minute limit. The page waits as long as Google asks and retries. If it still fails, wait a minute. |
| *"Gemini rejected the API key"* | Copy the key again from AI Studio. Make sure there are no spaces in it. |
| Test mode says it *"only understands common patterns"* | Rephrase using the examples above, type SQL directly, or switch to Gemini AI. |
| No **Save back into my file** option | Use Chrome or Edge, and open the file with the **Open…** button (not drag and drop from some apps). |
| *"…changed on disk after you opened it"* | Re-open the file in the page and run the request again. |

---

## B. Server version (optional)

Use this only if you need PostgreSQL/MySQL connections, or one shared deployment for a team.

1. Get a Gemini key (see above).
2. Double-click **`start.bat`** in `C:\Users\Lenovo\loopsql-ai\`.
3. The first time, it creates **`backend\.env`** and opens it in Notepad. **This is where the API key goes for the server version:**
   ```
   GEMINI_API_KEY=AIza...your key...
   ```
   Save the file, then double-click `start.bat` again.
4. Your browser opens **http://127.0.0.1:8000**. To stop the server, close the black window.

The full details are in `README.md`.

---

## Should you deploy with AI Studio or Render?

**Short answer: for the standalone page you don't need to deploy anything. Opening `index.html` is enough.**

- **Google AI Studio** (aistudio.google.com) is where you **get the API key**, and where you can try prompts. This project doesn't need anything else from AI Studio. You don't build or host LoopSQL AI there.
- **Render** is a hosting service. Use it only if you want a **web link** other people can open:

| Your goal | Recommendation |
|---|---|
| Use it yourself on your laptop | **No deployment.** Double-click `standalone\index.html`. |
| Share a link with classmates or teachers | Host `standalone/` as a **Render Static Site**, or GitHub Pages or Netlify. Each person pastes **their own** key in the page; your key is never part of the site. |
| A team service with a database server, one shared key, and an access password | Deploy the **server version** to Render as a **Web Service** using the included `render.yaml`. Set `GEMINI_API_KEY` in Render's dashboard, never in code. |

**Publishing the standalone page on Render (Static Site):**

1. Put the project in a GitHub repository. Do **not** commit any `.env` file or API key.
2. On https://render.com, click **New → Static Site** and connect the repository.
3. Settings:
   - **Build command:** leave empty.
   - **Publish directory:** `standalone`.
4. Click **Create Static Site**. Render gives you a link like `https://loopsql-ai.onrender.com`.
5. People who open the link choose Test mode or paste their own Gemini key. On a hosted link, *Save back into my file* still works in Chrome/Edge, because the browser does the writing.

Check Render's current pricing page; static sites have historically been free.

**Deploying the server version on Render (Web Service):**

1. In Render, click **New → Blueprint**, then select the repository. Render reads `render.yaml`.
2. In the dashboard, set **`GEMINI_API_KEY`**. An **`APP_ACCESS_TOKEN`** is generated automatically; give it to your users, because the page asks for it once.
3. A hosted server cannot reach files on users' computers. Users upload their files instead, and Chrome/Edge users can still save approved changes back to their local file.

---

## For developers: re-running the checks

```bash
# Standalone page
cd standalone\dev-tests
npm install
npm test                 # 51 checks against school_student_marks.xlsx (engine + whole page in jsdom)
node browser-check.mjs   # 7 checks in real headless Edge/Chrome, page opened from disk (file://)
node demo.mjs            # runs sample questions and saves the Excel outputs to dev-tests\demo-output\

# Server version: 106 checks
cd backend
.venv\Scripts\python -m pytest
```

**Verified:**

- Every test-mode answer on the school workbook matches ground truth computed independently from the raw cells:
  - attendance below 50%;
  - marks below 40 in any subject;
  - average per subject;
  - top 5 students;
  - count per class;
  - empty results.
- Exports and edits are re-opened and compared cell by cell. Formulas stay correct after rows are added or deleted.
- Unsafe SQL (DROP, stacked statements, a DELETE hidden in a CTE, ATTACH, PRAGMA, injection) is blocked.
- The page works in **real Edge opened from disk**:
  - the libraries and SQLite load;
  - query → results → downloads works;
  - change → preview → approve → edited workbook works;
  - Google's API is reachable from the page.
- The Gemini client was tested against **simulated** Google replies: normal, 429 with a retry delay, bad key, unknown model, bad JSON, and a schema rejected by the API.

**Not yet verified** (try these yourself):

1. A run with a **real Gemini key**: click **Check key**, then run one query and one "Add a column" request.
2. Picking a file with the real **Open…** dialog, *Save back into my file*, and **Undo**. These need a person clicking in Chrome/Edge.
3. Firefox.
