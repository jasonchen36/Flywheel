---
name: spreadsheet
description: "Reads, edits, and creates spreadsheet files (.xlsx, .csv). Use when working with Excel or CSV data as primary input or output."
---

# /spreadsheet
# spreadsheet Skill

**Usage:** `/spreadsheet <task description>`

Create, edit, analyze, or format spreadsheets (`.xlsx`, `.csv`, `.tsv`) using Python (`openpyxl`, `pandas`).

---

## Pre-flight: dependency check

```bash
python3 -c "import openpyxl, pandas" 2>/dev/null || uv pip install openpyxl pandas
```

For charts or PDF rendering:
```bash
python3 -c "import matplotlib" 2>/dev/null || uv pip install matplotlib
```

If `uv` is unavailable, fall back to `pip install`. If install fails, report immediately — do not proceed.

---

## Goal classification (pick one)

```
Task involves creating a new workbook?        → Create   (Step 1A)
Task involves editing an existing file?       → Edit     (Step 1B — read formula state first)
Task involves filtering/aggregating data?     → Analyze  (Step 1C)
Task involves exporting from BQ/DB?          → Export   (Step 1D)
```

---

## Step 1A: Create new workbook

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

wb = Workbook()
ws = wb.active
ws.title = "Summary"
ws["A1"] = "Header"
ws["A1"].font = Font(bold=True)
output_path = "output/spreadsheet/report.xlsx"
wb.save(output_path)
```

After saving → go to Step 2 (verify write).

---

## Step 1B: Edit existing workbook

**Always scan for formula cells before editing:**

```python
from openpyxl import load_workbook

wb = load_workbook("existing.xlsx")
ws = wb.active

# Detect formula cells in the region you plan to edit
formula_cells = [
    (cell.coordinate, cell.value)
    for row in ws.iter_rows()
    for cell in row
    if isinstance(cell.value, str) and cell.value.startswith("=")
]
if formula_cells:
    print("Formula cells found:", formula_cells[:10])
    # NOTE: openpyxl does not evaluate formulas.
    # Overwriting a formula cell with a value destroys the formula.
```

If formula cells are detected in the cells you need to modify: **stop and tell the user** before proceeding. Only overwrite a formula cell if the user explicitly approves.

```python
# Write to value cells only
ws["B2"] = 42
output_path = "existing_updated.xlsx"
wb.save(output_path)
```

After saving → go to Step 2 (verify write). **Never overwrite the original path** — always write to a new path first.

---

## Step 1C: Analyze / read data

```python
import pandas as pd

df = pd.read_csv("data.csv")   # or pd.read_excel("data.xlsx")
print(df.shape)
print(df.dtypes)
print(df.describe())
print(df.groupby("category")["value"].sum())
```

If the file is large (>100k rows): use `chunksize` or `nrows` for sampling before full load.
If encoding errors: retry with `encoding="latin-1"` or `encoding="utf-8-sig"`.

---

## Step 1D: Export from BigQuery

```bash
bq query --format=csv --use_legacy_sql=false \
  "SELECT ... FROM ..." > tmp/spreadsheets/bq_export.csv
```

Then convert:
```python
import pandas as pd
df = pd.read_csv("tmp/spreadsheets/bq_export.csv")
output_path = "output/spreadsheet/bq_export.xlsx"
df.to_excel(output_path, index=False)
```

After saving → go to Step 2 (verify write).

---

## Step 2: Verify the write

After every save, reopen the file and confirm:

```python
from openpyxl import load_workbook
import pandas as pd

# For xlsx
wb_check = load_workbook(output_path)
ws_check = wb_check.active
actual_rows = ws_check.max_row
actual_cols = ws_check.max_column
print(f"Rows: {actual_rows}, Cols: {actual_cols}")

# Or for csv
df_check = pd.read_csv(output_path)
print(f"Shape: {df_check.shape}")
print(f"Columns: {list(df_check.columns)}")
```

**Pass:** row count matches expected; headers match expected columns; no empty file.
**Fail:** if output_path is missing or row count is 0 → retry the save once, then report.

Maximum 2 save attempts. If still failing after 2: report the exact error.

---

## Step 3: Visual review (optional)

If LibreOffice is available for PDF render:
```bash
soffice --headless --convert-to pdf --outdir /tmp/ report.xlsx
pdftoppm -png /tmp/report.pdf /tmp/report_page
```

If rendering tools are unavailable, tell the user to review the output locally.

---

## Error recovery

| Failure | Recovery |
|---|---|
| `ModuleNotFoundError: openpyxl` | Run pre-flight install, retry once |
| `FileNotFoundError` on load | Confirm exact path with user before retrying |
| `UnicodeDecodeError` on CSV read | Retry with `encoding="latin-1"` or `encoding="utf-8-sig"` |
| Formula cell overwrite warning | Stop, report formula cells to user, wait for approval |
| `BadZipFile` on xlsx load | File is corrupt; ask user to provide a fresh copy |
| Output file is 0 bytes | Retry save once; if still 0 bytes report immediately |

---

## File conventions
- Temp files: `tmp/spreadsheets/`
- Final output: `output/spreadsheet/`
- Never overwrite the original file in-place; always write to a new path
- Delete temp files in `tmp/spreadsheets/` when done

## Rules
- Pre-flight check deps before any Python execution
- Scan formula cells before editing any xlsx file
- Verify every write by reopening the file (Step 2)
- Max 2 save attempts before reporting failure
- `openpyxl` does not evaluate formulas — note this explicitly when relevant
- Never overwrite original; always confirm path with user before replacing
