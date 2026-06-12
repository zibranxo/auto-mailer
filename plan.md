# Job Application Email Automator - Improvement Plan

## Overview
This plan outlines a phased approach to fix current reliability issues and implement enhancements for a robust, feature-rich email automation pipeline. The focus is on quality over quantity, addressing the core problems identified in the code review and aligning with the vision in `ideas.md`.

## Current Issues to Fix Immediately
1. **Critical Response Parsing Bug**: `'NoneType' object has no attribute 'strip'` when model returns `None` content
2. **Fragile JSON Parsing**: Unterminated strings, empty content, and inconsistent model outputs
3. **Ineffective Fallback**: `NIM_FALLBACK_MODEL` not configured, resulting in no actual fallback
4. **Observability Gaps**: No structured reports, per-company failure reasons, or checkpointing
5. **Data Quality Risks**: Missing email validation, duplicate detection, and contact scoring

---

## Phase 1 — Reliability (Near-Zero Crashes)
*Goal: Make email generation robust and predictable.*

### 1.1 Fix Response Parsing (`mailer.py`)
- **Handle `None` content**: In `_extract_content_text()`, check for `None` and empty content before `.strip()`
- **Add detailed logging**: Log finish reason, token usage, and response structure for debugging
- **Improve empty-content detection**: Distinguish between token-exhaustion (reasoning) and actual failures

### 1.2 Robust JSON Parsing & Repair
- **Enhance `_parse_email_json()`**:
  - Preprocess common LLM artifacts (markdown fences, explanatory text)
  - Implement JSON repair for:
    - Unterminated strings (add closing quotes)
    - Missing commas between fields
    - Trailing commas
    - Single quotes → double quotes
  - Use `json5` library or custom repair for lenient parsing
  - Validate schema strictly: only `subject` and `body` keys, both non-empty strings
  - On repair failure, return structured error with raw output for analysis

### 1.3 Effective Fallback System
- **Configure fallback model**: Set `NIM_FALLBACK_MODEL` in `.env` to a reliably fast model (e.g., `mistralai/mistral-7b-instruct-v0.3`)
- **Smart fallback triggers**:
  - Primary model returns `None`/empty content after retries
  - Primary model exceeds timeout (configurable)
  - Primary model returns invalid JSON after repair attempts
- **Fallback chain**: Allow multiple fallbacks (primary → fallback1 → fallback2) with escalating simplicity

### 1.4 Improved Retry Policy
- **Categorize errors**:
  - **Retryable**: Network timeouts, rate limits, temporary API errors, empty content (if reasoning detected)
  - **Fatal**: Invalid API keys, malformed requests, persistent JSON errors after repair
- **Exponential backoff with jitter**: Prevent thundering herd
- **Max attempts**: Configurable per error type (e.g., 3 for network, 1 for empty content)
- **Separate counters**: Track generation vs. sending retries independently

### 1.5 Status Separation
- **Generation status**: Track whether email was successfully generated (regardless of sending)
- **Send status**: Track SMTP delivery success
- **Decouple retries**: Retry generation failures without resending already-generated emails
- **New log fields**: `generation_status`, `generation_error`, `send_status`, `send_error`

---

## Phase 2 — Deliverability (Reduce Bounce/Spam Risk)
*Goal: Ensure emails reach inboxes and maintain sender reputation.*

### 2.1 Email Hygiene
- **Pre-send validation**:
  - Validate email format with regex (`^[^@\s]+@[^@\s]+\.[^@\s]+$`)
  - Check for disposable/temporary email domains (using built-in list or API)
  - Validate domain DNS MX records (optional, async)
- **Duplicate detection**:
  - Normalize emails (lowercase, trim)
  - Skip duplicates within CSV and across runs (using sent log)
  - Log skipped duplicates with reason
- **Contact scoring** (lightweight):
  - Score based on:
    - Email format validity (2 pts)
    - Known corporate domain vs. generic (gmail/yahoo) (1 pt)
    - Presence in recent communications (from sent log) (-1 pt if recently contacted)
    - Role quality from Note field (e.g., "HR", "Recruiter" = +2, "Founder" = +1)
  - Only send to contacts scoring ≥ threshold (configurable, default 2)

### 2.2 SMTP Robustness
- **Connection pooling**: Reuse SMTP connections for multiple sends (where supported)
- **Specific error handling**:
  - `4xx` errors (temporary): Retry with backoff
  - `5xx` errors (permanent): Log and skip (do not retry)
  - Authentication failures: Fail fast
- **Rate limiting intelligence**:
  - Adaptive pacing: Increase delay after 429 responses
  - Burst protection: Max N emails per M minutes
  - Respect provider limits (Gmail: 100/day for free tiers, 500/day for Workspace)

### 2.3 Better Sending Strategy
- **Batch prioritization**:
  - Sort contacts by score (highest first) for early feedback
  - Send high-priority contacts in smaller batches with monitoring
- **Engagement tracking** (foundation):
  - Add headers for tracking: `X-Entity-Ref-ID` (hash of company+timestamp)
  - Prepare for future open/click tracking via custom domains
- **Bounce handling**:
  - Parse bounce emails (if return path configured)
  - Auto-suppress hard bounces after 1 occurrence
  - Suppress soft bounces after 3 occurrences

---

## Phase 3 — Personalization (Increase Reply Probability)
*Goal: Make emails more relevant and engaging.*

### 3.1 Richer Company Context
- **Automated company research** (optional, opt-in):
  - Fetch company description from website meta tags (if allowed by robots.txt)
  - Extract recent news from RSS feeds or news APIs (configurable)
  - Scrape LinkedIn company page for employee count, industry, specialties
  - Cache results locally to avoid repeated requests
- **Context injection**:
  - Augment user prompt with 1-2 salient facts: 
    *"Noted your recent Series B funding..."*
    *"Saw your launch of [product] last week..."*
  - Keep concise to avoid token bloat

### 3.2 Quality Gate
- **Pre-send scoring** (0-100):
  - **Relevance** (30 pts): Does body mention specific company/project from research?
  - **Personalization** (25 pts): Uses candidate's skills matching company's Tag/Note?
  - **Conciseness** (15 pts): Body ≤ 180 words, subject ≤ 50 chars
  - **Tone** (15 pts): Professional but not stiff (via sentiment/keyword analysis)
  - **Spam likelihood** (15 pts): Checks for spam trigger words, excessive punctuation/all-caps
- **Configurable threshold**: Only send if score ≥ 70 (default)
- **Low-score handling**: 
  - Log to `low_quality_queue.json` for manual review
  - Optionally regenerate with different temperature/prompt

### 3.3 Multi-Variant Generation
- **Generate N variants** (configurable, default 2):
  - Same prompt, different temperature/top_p values
  - Or slightly varied prompts (e.g., "focus on skills" vs "focus on projects")
- **Selection criteria**:
  - Highest quality gate score
  - Or human-in-the-loop review (if `--review-queue` flag used)
- **Efficiency**: Run variants in parallel within worker pool

### 3.4 Personalization Memory
- **Learn from engagement** (foundation for future):
  - Track which email variants get replies (when reply tracking added)
  - Update prompt templates based on successful patterns
  - Store successful phrases per company Tag/Region
  - Example: For "AI/ML" Tag, remember that mentioning "LLM safety" performed well
- **Privacy-first**: All learning local, no external sharing

---

## Phase 4 — Productionization (Enterprise-Ready)
*Goal: Make it reliable, observable, and maintainable for regular use.*

### 4.1 Structured Artifacts
- **Run report** (`run_report_[timestamp].json`):
  ```json
  {
    "run_id": "20260611_153022",
    "start_time": "...",
    "end_time": "...",
    "config": { /* snapshot of args/env */ },
    "statistics": {
      "total_processed": 67,
      "generation_success": 60,
      "generation_failed": 7,
      "send_success": 55,
      "send_failed": 5,
      "skipped_sent": 12,
      "skipped_low_score": 3,
      "skipped_invalid_email": 2
    },
    "failures": [
      {
        "company": "Groww",
        "email": "future@groww.in",
        "stage": "generation",
        "error_type": "empty_content",
        "error_message": "Model returned empty content (reasoning consumed tokens)",
        "timestamp": "...",
        "retry_count": 2
      }
    ],
    "emails": [ /* array of sent/attempted emails with scores */ ]
  }
  ```
- **Human-readable summary**: Generate markdown report for quick review
- **Export options**: CSV of sent emails for CRM integration

### 4.2 Resume-Safe Checkpointing
- **Checkpoint file** (`.mailer_checkpoint.json`):
  ```json
  {
    "last_processed_index": 23,
    "generated_cache": { /* idx: {subject, body} */ },
    "sent_log_snapshot": { /* copy of sent_log at checkpoint */ },
    "timestamp": "..."
  }
  ```
- **Trigger**: After every N companies (configurable, default 10) or on clean shutdown
- **Recovery**: On startup, if checkpoint exists and `--resume` flag offered:
  - Validate checkpoint freshness (< 1 hour old)
  - Resume from last processed index
  - Preserve generated emails to avoid regeneration cost
  - Rebuild sent log from snapshot + new sends

### 4.3 Unit Tests
- **Test suite** (`test_mailer.py`):
  - Mock OpenAI API responses (success, None content, malformed JSON, timeouts)
  - Test `_extract_content_text()` with various response structures
  - Test `_parse_email_json()` with valid, repairable, and invalid JSON
  - Test email validation and scoring functions
  - Test SMTP connection handling (without actual sending)
  - Test CSV loading and filtering
  - Test sent log operations
- **Coverage goal**: 80%+ of core logic
- **CI integration**: Ready for GitHub Actions (though not implemented yet)

### 4.4 CLI Cleanup & Enhancements
- **New arguments**:
  - `--resume`: Resume from latest checkpoint
  - `--review-queue`: Generate emails but pause for manual approval before sending
  - `--max-score-threshold`: Override quality gate threshold (0-100)
  - `--export-report`: Generate run report after completion
  - `--no-cache-generation`: Disable generation checkpointing (for testing)
  - `--company-research`: Enable/disable automated company lookup (opt-in)
  - `--variant-count`: Number of email variants to generate per company (1-5)
- **Improved help**: Clear examples and explanations
- **Config profiles**: Support for `--config profile_name` (load from `.mailerrc` or env)
- **Dry-run enhancements**: Show quality scores, variant options, and predicted sending order

### 4.5 Observability & Monitoring
- **Structured logging**: JSON lines format option for log aggregation
- **Metrics collection** (optional):
  - Generation latency per company
  - Token usage statistics
  - Fallback activation rate
  - Quality score distribution
- **Health check endpoint**: Simple HTTP server (optional) reporting:
  - Queue depth
  - Recent failure rates
  - Last successful run timestamp
- **Alerting foundations**: Easy to add webhook notifications for:
  - Consecutive generation failures (>3)
  - SMTP authentication failure
  - Checkpoint staleness (>2 hours without progress)

---

## Implementation Sequence
We recommend tackling phases in order, but with overlapping where sensible:

### Immediate Stabilization (Week 1)
1. Fix `NoneType` crash in `_extract_content_text()`
2. Implement basic JSON repair in `_parse_email_json()`
3. Configure `NIM_FALLBACK_MODEL` in `.env`
4. Add empty-content detection and reasoning-aware retry logic
5. Separate generation and send status tracking

### Phase 1 Completion (Week 2)
1. Full robust JSON parsing with repair
2. Intelligent retry policy with error categorization
3. Basic email format validation
4. Duplicate detection and skipping
5. Initial run report generation

### Phase 2 (Week 3)
1. Advanced email hygiene (disposable domains, MX checks optional)
2. SMTP connection pooling and specific error handling
3. Contact scoring system
4. Adaptive rate limiting

### Phase 3 (Week 4)
1. Optional company research module (with caching)
2. Quality gate scoring implementation
3. Multi-variant generation and selection
4. Foundation for personalization memory (tracking successful patterns)

### Phase 4 (Week 5-6)
1. Comprehensive unit test suite
2. Resume-safe checkpointing
3. CLI enhancements and config profiles
4. Structured run reports and export
5. Optional metrics and health check

### Ongoing
- Monitor failure patterns from logs
- Refine quality gate based on engagement data (when available)
- Update company research sources as needed
- Maintain test coverage

---

## Success Metrics
After implementation, we aim for:
- **Generation success rate**: ≥95% (down from current ~50% observed in logs)
- **Send success rate**: ≥98% of generated emails (network/SMTP issues only)
- **Zero crashes**: No unhandled exceptions during normal operation
- **Actionable logs**: Every failure has clear reason and solution path
- **Resumability**: Can interrupt and resume without reprocessing or duplicating
- **Quality improvement**: Subjective increase in email relevance and personalization

This plan provides a roadmap to transform the current fragile prototype into a reliable, production-grade tool that respects user time and maximizes outreach effectiveness.
