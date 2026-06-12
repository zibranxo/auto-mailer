# Agentic Cold Mailer (NVIDIA NIM + Web Scraping) 🚀

A production-grade, highly intelligent automated cold-emailing pipeline. Built for AI/ML engineering roles (or easily adaptable for any role), this system autonomously researches companies, generates highly personalized and optimized cold outreach emails using **NVIDIA NIM APIs**, filters them through a stringent internal quality gating system, and safely delivers them via SMTP.

It is heavily optimized for deliverability, preventing spam penalties through advanced rate-limiting, DNS validation, IMAP bounce tracking, and exponential backoff mechanisms.

## ✨ Core Features

### 🧠 Agentic Personalization
- **Web Context Scraping:** Automatically visits the target company's domain, scraping `<title>` and `<meta name="description">` to inject real-world context into the LLM prompt.
- **Dynamic Prompting:** Fuses your markdown `about_me.md` profile with the target company's dataset (Domain, Region, specific Notes) for hyper-personalized output.
- **Intelligent Fallbacks:** Uses a primary NIM model (e.g., Llama 3.3 70B Instruct) and falls back to a faster/simpler model (like Mixtral) on reasoning timeouts or JSON malformations. Finally, defaults to a clean static template if all LLM attempts fail.

### 🧪 Multi-Variant Generation & Quality Gating
- **A/B Testing on the Fly:** Configure `--variant-count N` to generate `N` different email variants in parallel.
- **Strict Quality Gating:** Every generated draft is internally scored (0-100) based on conciseness, relevance, personalization (matching custom keywords), tone, and spam-trigger absence. Only the highest-scoring variant that passes the threshold is sent.

### 🛡️ Unmatched Deliverability Protections
- **Pre-Send DNS Check:** Queries `MX` records via `dnspython` to skip domains with no valid mail exchanges.
- **IMAP Bounce Syncing:** Authenticates via IMAP to sync previously bounced emails (from Mailer-Daemon) to a local `bounced_log.json`, actively suppressing historical hard bounces.
- **Jittered Rate Limiting:** Enforces strict delays between actual SMTP sends, applying exponential backoff with random jitter on API failures.
- **Duplicate & Disposable Prevention:** Uses regex and disposable domain blocklists to quarantine junk emails before processing.

### 📈 Production-Ready Orchestration
- **Resilient Checkpointing:** Saves execution state after every generation and send (`.mailer_checkpoint.json`), ensuring crash recovery without double-sending or wasting LLM tokens.
- **Concurrency Support:** `ThreadPoolExecutor` enables blazing fast generation while keeping SMTP sending strictly sequential and compliant.
- **Health Check Endpoint:** Spin up a background HTTP server (`--health-port 8080`) to monitor real-time execution telemetry (successes, skips, API failures).
- **Run Reports:** Automatically spits out detailed Markdown and JSON execution logs post-run.

---

## ⚙️ Installation & Setup

1. **Clone & Install Dependencies**
```bash
git clone https://github.com/zibranxo/auto-mailer.git
cd auto-mailer
pip install -r requirements.txt
```

2. **Configure Environment**
Create a `.env` file in the root directory (use `.env.example` as a template):
```env
NIM_API_KEY=nvapi-your-key-here
NIM_MODEL=meta/llama-3.3-70b-instruct
NIM_FALLBACK_MODEL=mistralai/mixtral-8x22b-instruct-v0.1

SENDER_NAME="Your Name"
SENDER_EMAIL=your.email@gmail.com
SENDER_APP_PASSWORD=your_gmail_app_password
```
> **Note:** If using Gmail, you must use an [App Password](https://myaccount.google.com/apppasswords) with 2FA enabled, not your raw account password.

3. **Populate Core Assets**
- `hr_emails_directory.csv`: A CSV containing columns `Company`, `Email`, `Tag`, `Region`, `Note`.
- `about_me.md`: Your detailed candidate profile in markdown.
- `resume.pdf`: Your resume file.

---

## 🚀 Usage

The CLI interface exposes powerful flags to tailor your campaigns.

### Safe Previews & Dry Runs
Preview emails locally without dispatching them to SMTP servers:
```bash
# Preview all global contacts with 3 variants per company and company web scraping enabled
python mailer.py --dry-run --company-research --variant-count 3
```

### Targeted Campaigns
Filter targets by specific tags or regions to personalize your strategy:
```bash
# Send to "Fintech" companies in "India", limiting the batch to 10 sends.
python mailer.py --filter-region India --filter-tag Fintech --limit 10
```

### Full Production Run
A fully optimized production run, complete with deliverability checks, multi-variants, web research, and live telemetry:
```bash
python mailer.py --company-research --variant-count 2 --check-bounces --workers 4 --health-port 8080
```

### Resuming From Crashes
If the script is interrupted, safely resume exactly where it left off:
```bash
python mailer.py --resume
```

---

## 🎛️ Command-Line Arguments Reference

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--dry-run` | Generate and print emails to console without sending them. | `False` |
| `--company-research` | Enable active `BeautifulSoup4` web scraping for context injection. | `False` |
| `--variant-count` | Generate `N` variants concurrently and auto-select the best one. | `1` |
| `--check-bounces` | Parse IMAP inbox for hard bounces to automatically suppress future attempts. | `False` |
| `--check-mx` | Actively query DNS for MX records before sending to prevent hard-bounces. | `False` |
| `--workers` | Number of concurrent threads for LLM API calls. | `1` |
| `--health-port` | Exposes a lightweight background HTTP metrics server on this port. | `0` (Disabled) |
| `--limit` | Maximum number of successful contacts to process. | `None` (All) |
| `--filter-region` | Only process companies in a specific region (e.g., `India`). | `None` |
| `--filter-tag` | Only process companies with a specific domain tag (e.g., `AI/ML`). | `None` |
| `--min-contact-score`| Minimum internal contact relevance score (0-10) to process. | `2` |
| `--min-quality-score`| Minimum internal AI draft quality score (0-100) required to send. | `70` |
| `--resume` | Resume execution securely using `.mailer_checkpoint.json`. | `False` |

---

## 📁 Repository Structure & Artifacts

- **`mailer.py`**: The core orchestration engine.
- **`test_mailer.py`**: A robust unit-test suite with comprehensive `unittest.mock` patching for isolated CI/CD testing.
- **`about_me.md`**: Your personal grounding context.
- **`sent_log.json`**: Persistent deduplication log preventing duplicate outreaches.
- **`bounced_log.json`**: Persistent suppression list of hard-bounced targets.
- **`generation_cache.json`**: Backs up expensive LLM outputs to save tokens between test runs.
- **`run_report_*.md` / `.json`**: Auto-generated comprehensive execution metrics created upon run completion.

---

## 💡 Best Practices
1. **Always Dry-Run First**: Make it a habit to use `--dry-run --company-research --variant-count 2` when testing a new `about_me.md` iteration.
2. **Tune Temperature**: Set `NIM_TEMPERATURE` in your `.env` to around `0.6` for creative cold outreach, but keep it below `0.8` to prevent hallucinations.
3. **Monitor the Queue**: Failed quality-gate emails get stored in `low_quality_queue.json`. Periodically inspect this file to tune your internal `calculate_quality_score()` heuristic weights inside `mailer.py`.

*Disclaimer: Ensure compliance with CAN-SPAM, GDPR, or applicable local outreach regulations when sending unsolicited emails.*
