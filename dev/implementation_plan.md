# Implementation Plan: Systematically Addressing Gaps & Stabilizing the Auto Mailer

This plan details a phased approach to resolve the remaining gaps, fix critical bugs (such as the duplicate `send_email` override), and implement missing components from [plan.md](file:///c:/code/auto%20mailer/plan.md) to build a production-grade, highly observable, and resilient application.

## User Review Required

> [!WARNING]
> **SMTP Credentials and Sending Environment**: We are shifting the email validation and SMTP settings to support robust connection pooling, rate-limiting, and error-handling. Please verify that your SMTP credentials in `.env` are active and that your SMTP host permits persistent connections.
> 
> **Checkpointing**: Checkpointing will store intermediate states in a `.mailer_checkpoint.json` file in the workspace root. Ensure this file is ignored in `.gitignore`.

---

## Open Questions

> [!IMPORTANT]
> 1. **Disposable Domains Database**: Do you prefer using a built-in static list of common disposable email domains (e.g. mailinator.com, trashmail.com), or should we query a public API? (A local static list is recommended for speed and offline availability).
> 2. **Fallback Chain Selection**: The plan suggests a fallback model chain. In `.env`, we currently have `deepseek-ai/deepseek-v4-flash` and `meta/llama-3.3-70b-instruct`. Do you want to configure a third model in the chain, or is a primary -> fallback model sequence sufficient?

---

## Proposed Changes

### 1. Dependencies and Requirements

#### [MODIFY] [requirements.txt](file:///c:/code/auto%20mailer/requirements.txt)
- Add `json5` package to standard requirements for lenient JSON repair fallback.

---

### 2. Main Logic and CLI

#### [MODIFY] [mailer.py](file:///c:/code/auto%20mailer/mailer.py)

##### A. Immediate Stabilization & Core fixes (Phase 1)
*   **Resolve duplicate functions**: Delete the second `send_email` definition (Lines 886-921). Combine its attachment and MIMEMultipart logic into the first `send_email` definition (Lines 185-226) which uses the SMTP connection pool (`get_smtp_connection()`) and adaptive rate limiting (`update_rate_limit()`).
*   **Harden response parsing logging**: In `_extract_content_text` and `generate_email`, log completion tokens, prompt tokens, and finish reasons if available from response metadata.
*   **Lenient parsing fallback**: Integrate `json5` parsing inside `_parse_email_json` as a fallback when standard `json.loads` and custom repair strategies fail.
*   **Error Categorization**: Define a new helper class `EmailerError` and classify errors into `RETRYABLE` (timeouts, 429 rate limits, empty responses) and `FATAL` (auth failure, invalid schema, 5xx SMTP errors). In `generate_email_with_retry`, abort retries immediately for `FATAL` errors.
*   **Structured error returned on repair failure**: Enhance `_parse_email_json` to include the raw LLM response inside the exception so the caller can log or quarantine the exact malformed response.

##### B. Deliverability & SMTP Robustness (Phase 2)
*   **Email Hygiene**:
    *   Change format validation in `is_valid_email` to use the regular expression `^[^@\s]+@[^@\s]+\.[^@\s]+$`.
    *   Implement list-based disposable domain check.
    *   Strictly lowercase and trim all emails before duplicate checks and comparisons.
*   **Contact Scoring & Selection**:
    *   Read the `sent_log.json` dynamically within `calculate_contact_score` to apply a `-1` point penalty if the contact was recently emailed.
    *   Set the default `--min-contact-score` CLI filter threshold to `2`.
    *   Implement sorting on the loaded list of companies using the calculated `contact_score` descending (highest priority processed first).
*   **SMTP Robustness**:
    *   Handle `4xx` temporary SMTP failures inside `send_email` by scheduling a single retry after a short backoff (e.g. 5 seconds). Fail fast for `5xx` authentication or permanent mailbox failures.
    *   Attach custom `X-Entity-Ref-ID` headers based on a hash of the company name + email + run timestamp.

##### C. Pre-Send Quality Gate (Phase 3)
*   **Quality Scoring**:
    *   Develop a pre-send text evaluator that scores generated drafts from 0 to 100 based on conciseness (body <= 180 words, subject <= 50 chars), relevance (mentions company name/tag), tone, and lack of obvious spam words.
    *   Add CLI parameter `--min-quality-score` (default 70).
    *   If draft score is below threshold, log to `low_quality_queue.json` and attempt regeneration once with adjusted prompt before defaulting to the static fallback template.

##### D. Checkpointing, Artifacts & CLI (Phase 4)
*   **Structured Run Reports**:
    *   On completion, dump a comprehensive `run_report_[timestamp].json` and an accompanying markdown summary report in the directory, tracking metrics, successful deliveries, duplicates skipped, and failures.
*   **Resume-Safe Checkpointing**:
    *   Write a checkpoint helper that reads/writes `.mailer_checkpoint.json` containing the last processed CSV index.
    *   Add a `--resume` command-line argument to restore the run state.
*   **CLI Cleanup**:
    *   Add `--resume`, `--review-queue`, `--min-quality-score`, `--export-report` args.

---

### 3. Verification

#### [NEW] [test_mailer.py](file:///c:/code/auto%20mailer/test_mailer.py)
*   Create a testing suite to verify:
    *   Regex format validation.
    *   Contact scoring calculations.
    *   JSON repair and JSON5 fallback parsing.
    *   SMTP connection pooling state machines.
    *   Quality gate score evaluator.
    *   Checkpoint load/save logic.

---

## Verification Plan

### Automated Tests
*   Run the newly created unit test suite using python's built-in `unittest` tool:
    ```powershell
    python -m unittest test_mailer.py
    ```

### Manual Verification
*   **Dry Run Execution**: Test the CLI pipeline with filter and dry-run flags:
    ```powershell
    python mailer.py --dry-run --limit 3 --min-contact-score 2
    ```
*   **Resume Check**: Interrupt a dry-run execution mid-way, verify `.mailer_checkpoint.json` creation, and run:
    ```powershell
    python mailer.py --dry-run --resume
    ```
*   **Report Generation**: Confirm the generation of `run_report_[timestamp].json` files.
