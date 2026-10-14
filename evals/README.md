# UrbanOS evals

Triage decides a report's category, priority, department and semantic tag. The
ranking that an MP sanctions from is built entirely out of those fields, so
if triage quality drifts, the product gives confidently wrong advice and
nothing in the system notices. These suites exist so that drift shows up as a
number instead of as a complaint from a constituency office.

## Running

```bash
python -m evals.run                 # suites that cost no model quota
python -m evals.run --suite health  # scores the data already stored
python -m evals.run --all           # includes model-backed suites
python -m evals.run --all --json report.json
```

Exit code is non-zero when a threshold is breached, so it can gate CI.

## Why the suites are split

| Suite | Spends quota | What it answers |
|---|---|---|
| `guardrails` | no | Does the geofence silence real citizens, or let other cities in? |
| `health` | no | How much of the stored data is already degraded? |
| `triage` | yes | Does the model follow its own documented department routing? |
| `tags` | yes | Do different phrasings of one issue cluster together? |

The deterministic suites must keep working while the model is down, because
that is precisely when the guardrails are carrying the system. During the
current quota outage, `guardrails` and `health` still run.

## Where the labels come from

Hand-written labels are an opinion unless they are anchored to something. These
are anchored:

- **Department routing** is asserted from rules the triage prompt already
  states: potholes in a gali go to Nagar Nigam, water and sewage to Jal
  Sansthan, electricity to LESA, buildings to LDA. A model that breaks its own
  documented routing is wrong by the system's definition, not by mine.
- **Geofence** labels are geography. Kanpur is not Lucknow.
- **Tag consistency** asserts only that two wordings of one real issue share a
  tag. It never asserts what the tag should be — the grouping is the product
  requirement, the wording is the model's business.
- **Priority** cases are limited to ones where ordering is not a matter of
  taste: a live electric wire where children walk outranks a request for more
  park benches.

Fields with no defensible ground truth are left `None` and simply not scored.
Inventing a label is worse than scoring nothing, because it manufactures a
failure signal that points the wrong way.

## Two metric choices worth keeping

**Macro-F1, not accuracy.** Accuracy is dominated by whatever category is
common, so a model that never once predicts a rare category can still score
90%. Rare categories are exactly the needs an MP has not already heard about.

**The two guardrail errors are never averaged.** Wrongly rejecting a real
complaint silences a citizen; wrongly accepting a junk one costs an aide a few
seconds. A single blended number lets the expensive mistake hide behind the
cheap one, so `false_reject_rate` and `false_accept_rate` are always reported
apart, and the false-reject threshold is the strictest in the file.

## Growing this into a real benchmark

What is here is a **floor, not a benchmark**: a few dozen cases that must never
break. A real set comes from production, and the cheapest source is work an
aide is already doing:

1. Add a correction UI to the dashboard, so an aide fixing a wrong category is
   recorded rather than just overwritten.
2. Every correction becomes a labelled case — the label is free, and it is
   human, and it is drawn from the real distribution.
3. Track **correction rate** as the live quality metric. Offline scores tell
   you about the cases you thought of; correction rate tells you about the ones
   you did not.
4. Sample 1–2% of production triage for review even when nobody complains,
   so quality is measured rather than inferred from silence.

Target a few hundred cases, stratified by language (including romanised
Hinglish, which is the most common real input and the easiest regression to
miss), category, and whether a photo was attached.

## Before trusting a score

- **Pin the model version.** `gemini-2.5-flash` is a moving alias; Google
  updating it silently rewrites your civic classifications.
- **Version the prompt** alongside the dataset, and record both in the report.
- **Re-run on every prompt or model change**, not on a schedule. The change is
  the risk, not the calendar.
