# Auto Mailer v2 — CSV Migration + Email Format Overhaul

## Summary

Migrate the CSV schema from `Company, Email, Tag, Region, Note` to `Company, Name, Email`. Overhaul the email generation pipeline to produce longer, multi-paragraph, professionally-structured emails with dynamic subject lines. Fix the root-cause token truncation bug and rebalance all downstream systems that relied on the removed columns.

---

## 1. Root Cause Fix — Token Truncation

> [!CAUTION]
> **This is the primary bug.** `GEN_MAX_TOKENS` defaults to `420` ([line 109](file:///c:/code/auto%20mailer/mailer.py#L109)), not 800 as the README claims. The `.env` currently sets it to `800` ([.env line 34](file:///c:/code/auto%20mailer/.env#L34)), but even 800 is insufficient. A 4-paragraph email body + subject in JSON easily needs 900–1200 tokens. The LLM truncates mid-generation, `_extract_content_text` ([line 855](file:///c:/code/auto%20mailer/mailer.py#L855)) detects `finish_reason == "length"` and throws — this failure mode is already firing today.

**Fix:**
- Bump `GEN_MAX_TOKENS` default in code ([line 109](file:///c:/code/auto%20mailer/mailer.py#L109)) from `420` → `1500`
- Bump `GEN_MAX_TOKENS` in [.env line 34](file:///c:/code/auto%20mailer/.env#L34) from `800` → `1500`
- Confirm the value flows through: `.env` → `GEN_MAX_TOKENS` global → `args.max_tokens` ([line 1901](file:///c:/code/auto%20mailer/mailer.py#L1901)) → `generate_and_gate_email` → `generate_email_with_retry` → `generate_email` `max_tokens` param → API `request["max_tokens"]` ([line 1343](file:///c:/code/auto%20mailer/mailer.py#L1343)). ✅ Already traced — no hardcoded override in the call chain.

---

## 2. Subject Line — Remove Dual Hardcoding

The current subject is locked in **three** places:

| Location | What it does |
| :--- | :--- |
| [Line 1289](file:///c:/code/auto%20mailer/mailer.py#L1289) | Python variable `subject_line = "Internship Application - Arnav Sagar (DTU) - AI/ML"` |
| [SYSTEM_PROMPT line 819](file:///c:/code/auto%20mailer/mailer.py#L819) | `"Subject line: Use the format already given in the user prompt — do NOT change it"` |
| [User prompt line 1323](file:///c:/code/auto%20mailer/mailer.py#L1323) | `"Subject must be exactly: \"{subject_line}\""` |

All three contradict the goal of a dynamic subject line. **Fix:**
- Delete the `subject_line` variable at [line 1289](file:///c:/code/auto%20mailer/mailer.py#L1289)
- Rewrite [SYSTEM_PROMPT line 819](file:///c:/code/auto%20mailer/mailer.py#L819) to instruct the model to generate a compelling, specific subject (e.g., `"2nd-Year DTU Engineer — [Relevant Tech Detail]"`)
- Rewrite [user prompt line 1323](file:///c:/code/auto%20mailer/mailer.py#L1323) to remove the "must be exactly" constraint and provide the new subject format guidance
- Bump `EMAIL_MAX_SUBJECT_LEN` default from `50` → `100` ([line 111](file:///c:/code/auto%20mailer/mailer.py#L111), [.env line 36](file:///c:/code/auto%20mailer/.env#L36)) since dynamic subjects will be longer
- Confirm `calculate_quality_score` already inspects subject via [line 1537](file:///c:/code/auto%20mailer/mailer.py#L1537) (`if len(subject) <= EMAIL_MAX_SUBJECT_LEN: score += 5`) — ✅ it does, so the new ceiling will gate overly long subjects automatically

---

## 3. LLM Prompt Overhaul — New Email Structure

#### [MODIFY] `SYSTEM_PROMPT` ([lines 800–834](file:///c:/code/auto%20mailer/mailer.py#L800-L834))

Rewrite the mandatory email structure from the current 3-paragraph/135-word/no-salutation format to:

- The LLM will generate only the dynamic Subject and Paragraphs 1-4.
- The fixed sign-off block (including GitHub, Resume mention, phone number, etc.) will be appended programmatically in Python code after generation, ensuring absolute reliability and zero hallucinated contact details.

**Prompt Guidance for structure (paragraphs 1-4):**
```
Hi {Name}, (or Hi Hiring Team, if Name is missing)

P1: Introduction — who you are, why you're reaching out, what role
P2-P3: Technical value prop — cite 2-3 projects with concrete metrics,
        written as narrative (not bullet points), similar to the example
        provided (CLASP, Regavis, 5G Lab)
P4: Ask — what draws you to this company specifically + request for conversation
```

- Remove the "Max 135 words" rule entirely from both `SYSTEM_PROMPT` and user prompt
- Add a soft word ceiling in the prompt: "Aim for 200–350 words in the body paragraphs"
- Remove the "No salutation" rule — replace with `"Start with: Hi {Name},"` (or fallback)
- Remove all BANNED openers that conflict with the new structure (e.g., "I am a student at" is now valid context)
- Keep the BANNED filler phrases list (passionate, excited, etc.) — those are still good
- The `Name` field from the new CSV must be passed into the user prompt so the LLM can use it

#### [MODIFY] `generate_email()` user prompt ([lines 1291–1334](file:///c:/code/auto%20mailer/mailer.py#L1291-L1334))

- Remove the hardcoded `subject_line` variable
- Add `hr_name = company.get("Name", "")` and inject it into the prompt
- Update the `## Output Requirements` section to match the new structure (P1-P4 only, noting that sign-off will be appended by Python)
- Keep `response_format: json_object` — no change needed there

#### [MODIFY] `.env` values
- `EMAIL_MAX_WORDS=400` (soft ceiling for prompt guidance only)
- `EMAIL_MAX_SUBJECT_LEN=100`

---

## 4. CSV Schema Migration

#### [MODIFY] `load_companies()` ([lines 461–472](file:///c:/code/auto%20mailer/mailer.py#L461-L472))

- Remove `region_filter` and `tag_filter` parameters
- Expect columns: `Company`, `Name`, `Email`
- Graceful handling: if `Name` is missing/empty, default to `"Hiring Team"`

#### [DELETE] CLI args ([lines 1885–1888](file:///c:/code/auto%20mailer/mailer.py#L1885-L1888))

- Remove `--filter-region` and `--filter-tag` argument definitions
- Remove `args.filter_region` and `args.filter_tag` references in:
  - `load_companies()` call at [line 2060](file:///c:/code/auto%20mailer/mailer.py#L2060)
  - `generate_run_report` config dict at [lines 1780–1781](file:///c:/code/auto%20mailer/mailer.py#L1780-L1781)
  - Markdown report template at [lines 1814–1815](file:///c:/code/auto%20mailer/mailer.py#L1814-L1815)

#### [MODIFY] `_cache_key()` ([line 408](file:///c:/code/auto%20mailer/mailer.py#L408))

Currently includes `company['Tag']` in the hash. With Tag removed, this will `KeyError`. Fix: replace with `company.get("Tag", "")` or drop Tag from the key entirely (since the key is just for dedup, `Company:Email` is sufficient).

---

## 5. Project Routing — Synthetic Tag from Scraped Context

> [!IMPORTANT]
> Losing the CSV `Tag` column without replacement collapses `build_candidate_context()` ([line 1283](file:///c:/code/auto%20mailer/mailer.py#L1283)) to the `"default"` bucket for every company. This means every email pitches `YTRAG + LLM Safety Shield + AIMS-DTU` regardless of whether the company does fintech, security, or telecom. This is a **personalization regression**.

**Fix — keyword-based matching per category:**

Add a new function `infer_company_tag(company_context: str) -> str` that:
1. Performs case-insensitive matching of a curated list of keywords against the `company_context` (scraped homepage content/title/description)
2. Uses an explicit mapping of keywords to target domains:
   - **Fintech**: `{"bank", "payment", "lending", "fraud", "credit", "finance", "fintech", "transaction", "wealth"}`
   - **Security**: `{"security", "cyber", "threat", "vulnerability", "breach", "firewall", "safety", "defense", "hack", "penetration", "exploit", "leakage"}`
   - **Audio**: `{"audio", "voice", "speech", "call", "deepfake", "acoustic", "sound", "dsp"}`
   - **Vision/Aerospace**: `{"vision", "satellite", "isro", "aerospace", "image", "thermal", "yolo", "opencv", "camera", "deformable"}`
   - **5G/Telecom**: `{"5g", "telecom", "cellular", "telecommunications", "edge", "latency", "mec", "network"}`
   - **NLP/AI/ML**: `{"llm", "rag", "nlp", "chatbot", "generative", "model", "inference", "prompt", "train", "embeddings", "classification"}`
3. Returns the highest-matching domain tag, or `"default"` if no keywords hit.

Call this in `generate_email()` at [line 1277](file:///c:/code/auto%20mailer/mailer.py#L1277):
```python
co_tag = company.get("Tag", "") or infer_company_tag(company_context)
```

This preserves the existing routing machinery with zero structural change — just re-sourcing the tag input from scraped context instead of CSV column.

---

## 6. Refresh DOMAIN_PROJECT_MAP and PROJECT_BRIEFS

Refresh the mapping to reflect your updated engineering portfolio.

#### [MODIFY] `DOMAIN_PROJECT_MAP` ([lines 1157–1170](file:///c:/code/auto%20mailer/mailer.py#L1157-L1170))

| Tag Key | Top Projects |
| :--- | :--- |
| `AI/ML` | CLASP, AIMS-DTU internship, Retrieval Augmentation System |
| `NLP` | YTRAG, Retrieval Augmentation System, AIMS-DTU internship |
| `Security` | Regavis deepfake detection, JAILS, LLM Safety Shield |
| `Fintech` | CLASP, YTRAG, AIMS-DTU internship |
| `Audio` | Regavis deepfake detection, 5G Lab internship |
| `Infrastructure` | CLASP, 5G Lab internship, ZeroFall+ |
| `Vision/Aerospace` | CAF-OTSRNet, 5G Lab internship |
| `Data` | Retrieval Augmentation System, AI vs Human classifier, YTRAG |
| `SaaS` | CLASP, YTRAG, LLM Safety Shield |
| `5G/Telecom` | 5G Lab internship, ZeroFall+, CAF-OTSRNet |
| `default` | CLASP, AIMS-DTU internship, Retrieval Augmentation System |

#### [MODIFY] `PROJECT_BRIEFS` ([lines 1173–1204](file:///c:/code/auto%20mailer/mailer.py#L1173-L1204))

- Add entries for **CLASP** and **Regavis deepfake detection**:
  - `CLASP`: `"Built a rate-limit-aware multi-provider LLM proxy with token-bucket limiting across multi-key pools, circuit breakers, and an async priority queue with SSE keep-alive absorption; two-tier LRU/SQLite/FAISS cache; 934 passing tests."`
  - `Regavis deepfake detection`: `"Designed a two-stage audio deepfake detection cascade — LFCC-LCNN for fast first-pass screening, frozen XLSR-53 + AASIST for high-precision second-stage verification — with a Hindi/Indic data bootstrapping strategy for underrepresented accents."`
- Remove `Vera Bot` and deprioritise legacy tutorial projects.

---

## 7. Quality Gate Rebalancing

#### [MODIFY] `calculate_quality_score()` ([lines 1528–1569](file:///c:/code/auto%20mailer/mailer.py#L1528-L1569))

**Problem 1: Silent -15 from dead Tag/Note branch.**
[Line 1547](file:///c:/code/auto%20mailer/mailer.py#L1547): `co_tag` and `co_note` will always be `""` with the new CSV. This branch is permanently `False`, silently losing 15 points from every email's score.

**Problem 2: Conciseness penalty for longer emails.**
[Line 1535](file:///c:/code/auto%20mailer/mailer.py#L1535): `if len(words) <= EMAIL_MAX_WORDS: score += 10` — this fails for emails that are intentionally 200–350 words.

**Fix — rebalance the 100-point scoring to:**

| Category | Points | Logic |
| :--- | :--- | :--- |
| Company name mentioned | 20 | Keep existing check ([line 1545](file:///c:/code/auto%20mailer/mailer.py#L1545)) |
| Company context referenced | 15 | **New:** Match keywords against scraped `company_context` instead of dead `Tag`/`Note` fields |
| Technical personalization | 25 | Keep existing keyword check ([lines 1552–1555](file:///c:/code/auto%20mailer/mailer.py#L1552-L1555)) |
| Tone (no stiff phrases) | 15 | Keep existing ([lines 1558–1560](file:///c:/code/auto%20mailer/mailer.py#L1558-L1560)) |
| No spam triggers | 15 | Keep existing ([lines 1563–1567](file:///c:/code/auto%20mailer/mailer.py#L1563-L1567)) |
| Subject length ≤ 100 chars | 5 | Keep existing, just uses new `EMAIL_MAX_SUBJECT_LEN` |
| Word count sanity (50–500) | 5 | **New:** Replace old conciseness check. Score 5 if body is between 50 and 500 words (catch empty or absurdly long, don't penalise the intended 200–350 range) |
| **Total** | **100** | |

- Pass `company_context` as a new parameter to `calculate_quality_score()` so the "company context referenced" check can work
- Drop `EMAIL_MAX_WORDS` from the scoring function entirely — it's only used as a soft prompt guideline now

---

## 8. Contact Scoring Simplification

#### [MODIFY] `calculate_contact_score()` ([lines 732–796](file:///c:/code/auto%20mailer/mailer.py#L732-L796))

> [!IMPORTANT]
> **Pass Bar Relative Change:** With `Tag`, `Region`, and `Note` gone, the maximum possible contact score drops from 10 to 5. Since `MIN_CONTACT_SCORE` defaults to 2, the pass bar shifts from 20% (2/10) to 40% (2/5). This is acceptable as corporate emails with valid format will score 4/5, easily clearing the gate.

**Fix:** Simplify to a 5-point scale based on what's still available:

| Points | Criterion |
| :--- | :--- |
| 2 | Valid email format (existing `is_valid_email` check) |
| 2 | Corporate domain (not gmail/yahoo/hotmail — existing logic) |
| 1 | Not previously contacted (existing sent_log check) |
| -1 | Previously contacted penalty |
| **Max: 5** | |

- Remove the `Note`, `Tag`, `Region` scoring branches entirely

---

## 9. Template Fallback Update

#### [MODIFY] `EMAIL_TEMPLATE` ([lines 1123–1151](file:///c:/code/auto%20mailer/mailer.py#L1123-L1151))

The static fallback template should match the new email format. Update it to use `Hi {name},`, include the longer structure, and reference current projects.

---

## Proposed Changes Summary

### [MODIFY] [.env](file:///c:/code/auto%20mailer/.env)
- `GEN_MAX_TOKENS=1500`
- `EMAIL_MAX_WORDS=400`
- `EMAIL_MAX_SUBJECT_LEN=100`

---

### [MODIFY] [mailer.py](file:///c:/code/auto%20mailer/mailer.py)

Changes grouped by function/section:

| Lines | Function/Section | Change |
| :--- | :--- | :--- |
| 109 | `GEN_MAX_TOKENS` default | `420` → `1500` |
| 110 | `EMAIL_MAX_WORDS` default | `135` → `400` |
| 111 | `EMAIL_MAX_SUBJECT_LEN` default | `50` → `100` |
| 408 | `_cache_key()` | Remove `company['Tag']` → use `Company:Email` only |
| 461–472 | `load_companies()` | Remove `region_filter`/`tag_filter` params, expect `Company, Name, Email` |
| 732–796 | `calculate_contact_score()` | Simplify to valid-email + corporate-domain + not-previously-sent |
| 800–834 | `SYSTEM_PROMPT` | Full rewrite for new email structure, instruct model to stop before sign-off |
| 1123–1151 | `EMAIL_TEMPLATE` | Update fallback to match new format |
| 1157–1204 | `DOMAIN_PROJECT_MAP` + `PROJECT_BRIEFS` | Refresh with current portfolio (including CLASP and Regavis) |
| 1263–1393 | `generate_email()` | Remove hardcoded subject, add `Name` param, rewrite user prompt, append fixed sign-off in code |
| 1528–1569 | `calculate_quality_score()` | Rebalance: drop Tag/Note/word-count penalties, add company_context matching |
| 1780–1815 | `generate_run_report()` | Remove `filter_region`/`filter_tag` references |
| 1885–1888 | `main()` argparse | Remove `--filter-region`/`--filter-tag` args |
| 2060 | `main()` load_companies call | Remove filter args |
| New | `infer_company_tag()` | New function: keyword-based matching from scraped context to map keys |

---

## Verification Plan

### Automated Tests

1. **Token truncation fix:**
   - Run `python mailer.py --dry-run --limit 3 --company-research`
   - Verify no `finish_reason == "length"` errors in `mailer.log`
   - Check actual token usage in response metadata against `GEN_MAX_TOKENS=1500`

2. **Email format:**
   - Inspect dry-run output: confirm `Hi {Name},` salutation, 4-paragraph structure, dynamic subject, and that the fixed code-appended sign-off block is perfectly appended
   - Confirm word count is in the 200–350 range (not truncated, not absurdly long)

3. **Quality gate:**
   - Verify quality scores are ≥70 for well-formed emails (no silent -15 from dead branches)
   - Confirm subject length check uses new 100-char ceiling

4. **Project routing:**
   - Run with `--company-research` for companies in different verticals
   - Verify `infer_company_tag()` routes e.g. a fintech company to fintech-relevant projects, not the default bucket

5. **CSV compatibility:**
   - Test with new `Company, Name, Email` CSV format
   - Confirm no `KeyError` on missing `Tag`/`Region`/`Note` columns

### State & Resume Safety

6. **sent_log.json / bounced_log.json:**
   - Confirm still keyed by email address only (verified: [line 399](file:///c:/code/auto%20mailer/mailer.py#L399) keys by `email.strip().lower()`) — ✅ unaffected by CSV schema change

7. **Checkpoint / --resume:**
   - Confirmed safe: `save_checkpoint` ([line 1866](file:///c:/code/auto%20mailer/mailer.py#L1866)) keys by `generated_cache_by_email` (email address only) — ✅ no Tag/Region/Note in checkpoint schema
   - Still worth a manual test: interrupt a dry run mid-way, then `--resume` with the new CSV to be certain

8. **_cache_key integrity:**
   - After removing `Tag` from the hash, existing `generation_cache.json` entries will have different keys. This is fine — worst case is a cache miss and regeneration, not a crash. But worth noting: first run after migration will regenerate all cached emails.
