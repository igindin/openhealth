---
name: doctor-note-intake
description: >-
  Files a doctor's note, discharge summary, specialist report or recommendation
  into a structure: markdown with frontmatter (date + source), facts separate
  from hypotheses, raw source immutable. Use it when someone brings a hospital
  discharge summary, a specialist's report, a visit note, a photo/scan of a
  medical document, or says "write down what the doctor said", "file this
  discharge summary", "save this report". It does not interpret and does not
  argue with the doctor - it records facts with provenance. Numeric markers in
  the document are handed off to numeric-lab-normalization. Triggers: "discharge
  summary", "doctor's report", "what the doctor said", "file this visit",
  "doctor note", "clinic note".
---

# doctor-note-intake

A framework for carefully filing a medical document, or something a doctor said, into a local health structure. You are an archivist, not a reviewer: you record what was said, by whom and when. **You do not interpret, you do not dispute the doctor's recommendations, and you do not offer a diagnosis of your own.**

The point is that six months later the person (and the agent) can pull up exactly what the doctor said and on what basis, undistorted.

## Hard rules

1. **Raw sources are immutable.** The original (photo, PDF, scan) is copied into the archive and never edited again (repository core rule: archived sources immutable). You only add, you never rewrite.
2. **Facts separate from hypotheses.** The note physically separates what the doctor said or wrote (fact, source) ↔ what is a hypothesis or your own observation drawn from it. Never mix them (core rule: separate facts / extractions / hypotheses).
3. **Every fact carries a date and a source.** "Who said it" (doctor, specialty, institution if available) and "when". No date - use a range or mark it undated; don't invent one (core rule: do not invent dates).
4. **Do not interpret recommendations.** You record "the doctor prescribed X twice a day" - and that's it. Not "so you have Y", not "you could lower that". This is a framework for recording, not interpreting.
5. **An alarming symptom in the text → to a physician.** Run it through `evidence.scan_text_red_flags`; if a flag comes up, surface it and don't analyze it.
6. **Local.** The document goes nowhere.

## How to work

### 1. Take it as it comes, ask the minimum

Take the text/photo. One question, if it isn't obvious from the document: **when** this happened and **who** said it (which specialist/institution). Don't interrogate beyond that.

### 2. Assemble a structured note

Markdown with frontmatter. Facts and hypotheses go in separate sections:

```markdown
---
title: Endocrinologist visit
date: 2026-05-20
source: endocrinologist, city clinic
note_kind: doctor_note
tags: [doctor-note, endocrinology]
---

## Facts (as stated / from the doctor's document)
- Complaints as described by the person: <...>
- Examination / doctor's conclusion: <verbatim or close to the text>
- Prescriptions: <drug, dose, regimen - exactly as written>
- What the doctor asked to retest / monitor: <...>

## Numeric markers (if present in the document)
- <marker>: <value> <unit>   # these go to numeric-lab-normalization

## Open questions / hypotheses (NOT from the doctor)
- <your observation or a question for the next visit>, tagged with a C1-C5 grade
```

Verbatim prescriptions and the doctor's conclusion go under "Facts". Anything along the lines of "this might be related to..." goes only under "Open questions / hypotheses", with a confidence grade from `openhealth.evidence` (C3 and below is phrased as a question).

### 3. Numeric markers go to normalization

If the summary contains lab values (numbers with units) - don't interpret them here. Hand them to the `numeric-lab-normalization` framework (module `openhealth.lab_normalization`), and the interpretation, if needed, to `lab-interpretation-guardrails`. Russian discharge summaries are often in SI units.

### 4. File it into the system (raw source + record)

Copy the original and the note, and register them through ingest so the document lands in the timeline as an immutable raw source:

```
python3 -m openhealth ingest --source document-tests --path <file-or-folder> \
  --label "Endocrinologist visit 2026-05-20"
```

For a free-form text note, `--source manual-notes` works. Ingest archives the original into the immutable archive and builds a record. The date and source from the frontmatter carry into the record.

### 5. Confirm briefly

One line: what you filed, with what date and source, and where it landed. No retelling of the diagnosis, no assessment.

## Where to write

Into the structure of the person's own health folder / the openhealth repository (sources + ingest). **Never** edit raw material that has already been archived. Never write into someone else's calendar/data sources - only into derived records.

## Disclaimer

I record what the doctor said; I do not interpret it and do not dispute it. Diagnoses and treatment regimens are the treating physician's domain. Alarming symptoms go to a real doctor. This record is a provenance archive, not a medical conclusion.
