---
name: evaluate-wait-time
description: >
  Step-by-step re-evaluation of whether herbicide treatments pass or fail
  the plant-back wait time restriction for a given test case in the Spanish
  herbicide recommendation system. Use this skill whenever the user asks to
  "re-evaluate", "trace", "verify", "check wait time", "explain the logic",
  or "walk through" the wait-time / plant-back analysis for a specific test
  case, weed, crop, or treatment scenario. Also trigger when the user asks
  why a treatment passes or fails, or wants to understand the interval
  calculation for a particular next crop. This skill applies to the
  Spanish_herbicide Lambda project only.
---

# Evaluate Wait Time

This skill traces the complete decision path that the Lambda function
(`get-herbicides-v3-fr94o.py`) follows when checking whether herbicide
treatments meet the plant-back interval requirement for a given next crop.

The project root is `C:\Users\GNNVK\bedrock_lambda\Spanish_herbicide\`.
All CSV data lives under the `s3_data/` subdirectory.

## Procedure

Follow these seven steps **in order**, presenting each result before
moving to the next. Use tables for clarity.

---

### Step 1 — Identify the test case parameters

Extract (from the test case definition, the user's message, or by reading
the test cases JSON file) the following inputs:

| Parameter | How to determine |
|-----------|-----------------|
| **Weed name(s)** | Latin name(s) from the query or test case |
| **Application timing** | `pre-emergence` or `post-emergence` |
| **Location group** | 1 = Galicia, Asturias, Cantabria; 2 = Castilla y Leon (9 provinces: Leon, Zamora, Salamanca, Valladolid, Palencia, Burgos, Soria, Segovia, Avila); 3 = everywhere else |
| **Dose level** | See dose rules below |
| **Next crop** | Standardised Spanish crop name |

**Dose-level rules** (evaluated in order, first match wins):

1. Group 2 + pre-emergence &rarr; **low**
2. Group 2 + post-emergence + stage "less than three leaves" &rarr; **low**
3. Group 3 + weed is NOT Amaranthus palmeri + pre-emergence + sandy soil &rarr; **medium**
4. Everything else &rarr; **high**

Present these parameters in a summary block so the reader can verify them.

---

### Step 2 — List all candidate treatments sorted by Rank

Search the herbicide table CSV:

```
s3_data/Spanish_herbicides_table_for_one_and_two_weeds_three_dose_levels.csv
```

Use Grep to find rows matching:
- `Weed 1` = the weed name (case-insensitive)
- `Weed 2` = `PLACE HOLDER` for single-weed queries, or the second weed
  name for two-weed queries
- `Application Timing` = the timing value
- `dose level` = the dose value

Present a table with columns:

| Rank | Treatment | Products in treatment | Score (lower score) |
|------|-----------|----------------------|---------------------|

The **Products** column should list only the canonical product names
extracted from the treatment string. The canonical product list is:
`Laudis WG`, `Monsoon`, `Adengo`, `Spade Flexx`, `Cubix`, `Lagon`,
`Capreno`, `Fluva`, `Oizysa`, `Dimetenamida 72%`, `Fluoxipir 20%`,
`Diflufenican 50%`.

---

### Step 3 — List all wait times for the next crop

Search the wait-time table:

```
s3_data/next_crop_wait_time_interval_and_other_restrictions_table.csv
```

Find all rows where `Next crop` matches the crop name (case-insensitive)
and `location group` matches the group number.

Present a table:

| Product | Wait (months) | Restriction |
|---------|--------------|-------------|

---

### Step 4 — Compute the real time interval

1. Look up the crop's earliest planting date in:
   ```
   s3_data/earliest_planting_dates_of_crops.csv
   ```

2. The reference application month is **June** (hardcoded in the Lambda
   at line ~497).

3. Compute the interval:
   - If planting date is a plain month name (e.g. "October"):
     `interval = planting_month_number - 6`
   - If planting date contains "next year" (e.g. "March next year"):
     `interval = (12 + planting_month_number) - 6`

   Examples:
   | Planting date | Calculation | Interval |
   |---------------|-------------|----------|
   | October | 10 - 6 | **4 months** |
   | November | 11 - 6 | **5 months** |
   | March next year | (12 + 3) - 6 | **9 months** |
   | April next year | (12 + 4) - 6 | **10 months** |

State the result clearly:
> **Earliest planting:** {date}
> **Available interval:** {N} months (June &rarr; {month})

---

### Step 5 — Judge each rank

For every candidate treatment from Step 2, check whether the computed
interval (Step 4) is **greater than or equal to** every product's wait
time (Step 3).

The Lambda logic (line ~619) is:
```python
all_valid = all(wait is not None and interval >= wait
                for wait in wait_months_list)
```

A treatment **PASSES** only if ALL products in it pass individually.

Present a table:

| Rank | Products | Wait times | Max wait | interval >= max? | Result |
|------|----------|-----------|----------|-----------------|--------|

---

### Step 6 — Determine valid and invalid candidates

Apply the Lambda's filtering logic:

**Valid candidates** — treatments that PASSED in Step 5.
- The Lambda keeps the first valid row unconditionally, then only
  subsequent rows with `lower_score >= 3` (line 643-650).
- The first valid candidate becomes the **primary recommendation**.

**Invalid candidates** — treatments that FAILED in Step 5.
- The Lambda keeps only rows with `lower_score > 3` (line 654).
- These appear in the exclusion / reason message, formatted as
  `"Treatment (X months)"` where X is the max wait.

**If no valid candidates exist:**
The correct Lambda response is:
> "There is no applicable treatments or no treatment meets the residue
> degradation standard of the specified next crop."
> plus the formatted exclusion list.

**If valid candidates exist:**
State the primary recommendation and any alternatives.

---

### Step 7 — Compare against the agent's actual response

If the agent's response is available (from test results or conversation),
compare it to the expected behaviour determined in Step 6.

State clearly:
- Whether the agent's answer was **correct** or **incorrect**
- If incorrect, what the right answer should have been
- If the test case criteria / scoring were appropriate for the scenario

---

## Notes

- This procedure mirrors the code path in `get-herbicides-v3-fr94o.py`
  lines 562-680 (the standard table-lookup wait-time validation loop).
- For **hardcoded paths** (Amaranthus palmeri G3, Cyperus G1, Setaria G1),
  the product list is fixed rather than looked up from the table. The same
  wait-time check (`check_wait_times()` at lines 193-236) still applies,
  so Steps 3-7 work the same way — just replace Step 2 with the hardcoded
  product list.
- The `adengo_applied` and `taboo_list` filters run *before* the wait-time
  check (lines 586-591). If relevant, note which treatments would be
  skipped by those filters before reaching the wait-time stage.
