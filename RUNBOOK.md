# Runbook

What to do when the weekly pipeline surfaces an issue. Each scenario walks you through symptom → diagnosis → action → expected result.

---

## 1. Verifier flagged a HARD error (🔴 on Fleet View)

**Symptom.** The Fleet View card shows 🔴. The dashboard was NOT generated. The card displays the warning(s) inline with claim, issue, and evidence.

**Example.**

```
🔴 <report name>
   Updated 06:08 · FAILED verification

   Hard error w1: The narrative says "loss rate 10.88 bags/h" for
   the latest complete week, but the CSV shows 12.45 bags/h.

   → To override and publish anyway:
     python run_weekly.py --override w1 "<your reason>"
```

**Decide which path you're on:**

| Situation | What to do |
|---|---|
| The narrative *is* wrong — the verifier caught a real bug | Don't override. Re-run with a more constrained Insights agent prompt, or fix the data, or fix the methodology rule. Then `python run_weekly.py` to regenerate from scratch. |
| The verifier is wrong (false positive) — the claim is actually valid | Override. See next step. |
| You're not sure | Read the Insights agent's prompt + the methodology section the warning cites. If the methodology genuinely supports the claim, override. If not, don't. |

**To override:**

```bash
cd ~/Desktop/weekly-automated-multi-agent-reports
python run_weekly.py --override w1 "<short, specific reason — what made this a false positive>"
```

The justification is mandatory; the command will fail without it.

**Expected result.**

- Pipeline re-runs.
- Warning `w1` is marked `overridden: true` with your justification recorded in `outputs/verifier_report.json`.
- Status drops from `fail` to `warn`.
- Dashboard renders. The inline "Reviewer flags" section at the top shows the overridden warning + your justification — visible to anyone who reads the dashboard later.
- Fleet View card flips from 🔴 to 🟡 with the warning count showing 1 overridden.

---

## 2. Verifier flagged SOFT warnings only (🟡 on Fleet View)

**Symptom.** Dashboard was generated. Fleet View card shows 🟡. Inline "Reviewer flags" section is visible at the top of the dashboard listing the warnings.

**Action.** None required to publish — the dashboard is already live. But before sharing externally:

1. Skim each soft warning on the dashboard.
2. If any look like real problems (not cosmetic), treat them like a hard error: fix the root cause and re-run.
3. If they're cosmetic / acceptable, you can leave the dashboard as-is. The warnings stay on the dashboard as part of the audit trail.

**You do not need to "approve" anything.** Soft warnings are informational; they're already part of the published dashboard.

---

## 3. Pipeline failed BEFORE the verifier ran

**Symptom.** Fleet View card shows 🔴 with no verifier warnings. Instead, the card shows a "Data quality" or "Pipeline error" message.

**Common causes & checks:**

| Cause | Where to look | Quick fix |
|---|---|---|
| Source CSVs missing or empty | `inputs/` — is the latest month's data there? | Re-run the extractor, or check the upstream data source. |
| KPI script crashed | Terminal output from the failed run | Read the traceback; usually a column-name or NaN issue. |
| Insights agent API error / timeout | Terminal output | Wait a minute, re-run. Persistent failures = API key / rate limit. |
| The audit script said new dates need excluding | `python audit_exclusions.py` | Edit `data_exclusions.py`, add the dates, re-run. |

**Action.** Address the underlying cause, then re-run:

```bash
cd ~/Desktop/weekly-automated-multi-agent-reports
python run_weekly.py
```

---

## 4. New monthly data has dropped

**Symptom.** Fresh source CSVs from the upstream extraction are sitting outside `inputs/`. Time to ingest.

**Steps.**

1. Move the new files into `inputs/` (use the `_<MMM>` suffix for completed months, `_<MMM>_incomplete` for an in-progress month). Drop video-match CSVs into `inputs/videos/`.
2. Run the audit so the exclusion list catches any new low-volume days:
   ```bash
   python audit_exclusions.py
   ```
   If it reports new dates that match the rule, add them to `data_exclusions.py` and re-run the audit until the list is up to date.
3. Run the weekly pipeline:
   ```bash
   python run_weekly.py
   ```
4. Open the Fleet View, click into the report, verify the dashboard renders with the new week visible (and partial weeks rendered with a lighter bar).

---

## 5. The Fleet View isn't updating

**Symptom.** You re-ran the pipeline but the Fleet View card still shows old timestamps.

**Cause.** The Fleet View is built from `status/*.json`. If those weren't updated (e.g., a run failed before writing the status), the index reflects the last successful run.

**Action.**

```bash
python build_fleet_view.py
```

This re-reads every `status/*.json` and regenerates the index. If a report has no status file at all, it won't appear on the Fleet View — drop a stub status JSON or run that report's pipeline at least once.

---

## 6. Re-running the dashboard for a *past* week

**Symptom.** You need to regenerate an older report — e.g., to audit a previous run, or to re-render after fixing a methodology bug.

**Steps.**

1. Restore (or keep) the source CSVs from that week's data in `inputs/`.
2. Run the pipeline pointing at that week:
   ```bash
   python run_weekly.py --as-of 2026-05-13
   ```
   The `--as-of` flag tells the pipeline to treat that date as "today" for purposes of partial-week handling and which weeks count as complete.
3. The dashboard regenerates as it would have on that Monday. The status JSON is overwritten with the new timestamp; the historical claim is now whatever the latest agents say.

---

## Glossary

- **Hard error** — verifier found a claim in the narrative that contradicts the data, or a direct violation of a methodology MUST rule. Pipeline halts; dashboard not generated.
- **Soft warning** — verifier flagged something worth a human's attention (tone drift, weak framing, methodology SHOULD violation). Dashboard renders; warning is visible inline.
- **Override** — reviewer-driven re-run that publishes the dashboard despite a hard error, with a justification recorded for audit.
- **Partial week** — a week whose end date is in the future relative to "today." Rendered with lighter bars in the charts; never used as a headline number in the widgets.
