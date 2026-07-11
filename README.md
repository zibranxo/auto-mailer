<div align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue.svg?style=for-the-badge&logo=python&logoColor=white" alt="Python Version"/>
  <img src="https://img.shields.io/badge/LLM-Agnostic-8A2BE2.svg?style=for-the-badge&logo=openai&logoColor=white" alt="LLM Agnostic"/>
  <img src="https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge" alt="License"/>
</div>

<h1 align="center">🚀 Auto Mailer v2</h1>
<p align="center">
  <strong>A highly intelligent, autonomous, and LLM-agnostic cold email orchestration engine.</strong>
</p>

<p align="center">
  Designed specifically for engineers, founders, and recruiters who need to scale hyper-personalized outreach. Auto Mailer fuses your local resume data with live web-scraped company context to generate, gate, and deliver bespoke emails without triggering spam filters.
</p>

---

## ✨ Why Auto Mailer?

Most cold email scripts just inject a `{{company_name}}` variable into a static template. **Auto Mailer is different.** It operates as an autonomous agent that researches the company, writes a custom 4-paragraph email referencing their specific domain, grades its own writing against strict heuristics, and safely routes delivery through load-balanced SMTP pools.

<details>
<summary><b>🔥 Click to see the Core Features</b></summary>

### 🧠 Agentic Personalization
- **Web Context Scraping:** Automatically visits the target company's domain, scraping `<title>` and `<meta name="description">` to inject real-world context into the LLM prompt.
- **Dynamic Prompting:** Fuses your markdown `about_me.md` profile with the target company's live context for hyper-personalized output.
- **Multi-Provider Zero-Downtime Cascade:** Load up to 10 API keys across NVIDIA NIM, Groq, OpenRouter, Google Gemini, and Cerebras simultaneously.
- **Intelligent Fallbacks:** Uses a primary LLM model and automatically falls back to a faster/simpler model on reasoning timeouts or rate limits.

### 🧪 Multi-Variant Generation & Quality Gating
- **A/B Testing on the Fly:** Configure `--variant-count N` to generate `N` different email variants in parallel.
- **Strict Quality Gating:** Every generated draft is internally scored (0-100) based on conciseness, relevance, tone, and spam-trigger absence. Only the highest-scoring variant is sent.

### 🛡️ Unmatched Deliverability Protections
- **Pre-Send DNS Check:** Queries `MX` records via `dnspython` to skip domains with no valid mail exchanges.
- **IMAP Bounce Syncing:** Authenticates via IMAP to sync previously bounced emails to a local `bounced_log.json`, actively suppressing historical hard bounces.
- **Jittered Rate Limiting:** Enforces strict delays between actual SMTP sends, applying exponential backoff with random jitter.
- **Connection Pooling:** Cycles connections through a rotating pool of authenticated senders to prevent spam-flagging.

### 📈 Production-Ready Orchestration
- **Atomic Checkpointing:** State is saved atomically (`os.replace`) to prevent JSON corruption during abrupt crashes.
- **Minimalist CLI UI:** A beautiful, non-intrusive Rich terminal UI that tracks concurrency without spamming your logs.
- **Run Reports:** Automatically generates detailed Markdown and JSON execution logs in `runs/YYYY-MM-DD/`.
</details>

---

## ⚙️ Quick Start

### 1. Installation
Clone the repository and install the dependencies:
```bash
git clone https://github.com/zibranxo/auto-mailer.git
cd auto-mailer
pip install -r requirements.txt
```

### 2. Configuration (`.env`)
Create a `.env` file in the root directory. Auto Mailer v2 has moved *all* tunable parameters to environment variables for maximum flexibility.

<details>
<summary><b>📝 View sample .env configuration</b></summary>

```env
# Primary Provider (e.g., NVIDIA NIM)
LLM_API_KEY=nvapi-your-key-here
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=meta/llama-3.3-70b-instruct
LLM_FALLBACK_MODEL=mistralai/mixtral-8x22b-instruct-v0.1

# Sender configuration
SENDER_NAME="Your Name"
SENDER_EMAIL=your.email@gmail.com
SENDER_APP_PASSWORD=your_gmail_app_password

# Limits & Quality Gates
RATE_LIMIT_SECONDS=8
GEN_MAX_TOKENS=1500
EMAIL_MAX_WORDS=400
MIN_QUALITY_SCORE=70
```
> **Note:** If using Gmail, you must use an [App Password](https://myaccount.google.com/apppasswords) with 2FA enabled.
</details>

### 3. Populate Assets
Ensure the following files are populated in the root directory:
- `hr_emails_directory.csv`: Columns `Company`, `Name`, `Email`.
- `about_me.md`: Your detailed candidate profile/pitch.
- `resume.pdf`: The attachment to send.

---

## 🚀 CLI Usage

Auto Mailer's CLI exposes powerful flags to tailor your campaigns.

### 🧪 Safe Previews & Dry Runs (Recommended)
Preview emails locally with the beautiful Rich terminal UI without actually dispatching them:
```bash
python mailer.py --dry-run --company-research --variant-count 2
```

### 🎯 Targeted Campaigns
Process a limited subset of the CSV directory using the limit flag:
```bash
python mailer.py --limit 10
```

### 💥 Full Production Run
A fully optimized production run, complete with deliverability checks, web research, and live telemetry:
```bash
python mailer.py --company-research --check-bounces --workers 4
```

### 🔄 Resuming From Interruptions
If the script is interrupted, safely resume exactly where it left off using atomic checkpoints:
```bash
python mailer.py --resume
```

---

## 🎛️ Advanced Configuration Options

<details>
<summary><b>⚙️ CLI Arguments Reference</b></summary>

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--dry-run` | Generate and print emails to console without sending. | `False` |
| `--company-research` | Enable active `BeautifulSoup4` web scraping. | `False` |
| `--variant-count` | Generate `N` variants concurrently. | `1` |
| `--check-bounces` | Parse IMAP inbox for hard bounces to suppress future attempts. | `False` |
| `--check-mx` | Query DNS for MX records before sending. | `False` |
| `--workers` | Number of concurrent threads for LLM API calls. | `1` |
| `--limit` | Maximum number of successful contacts to process. | All |
| `--resume` | Resume execution from the latest checkpoint in `runs/`. | `False` |

</details>

---

## 📁 Repository Structure

```text
auto-mailer/
├── mailer.py               # 🧠 Core orchestration engine
├── requirements.txt        # 📦 Dependencies (OpenAI, Rich, BS4, etc.)
├── .env                    # 🔑 Global tunable configurations
├── hr_emails_directory.csv # 📇 Target directory
├── about_me.md             # 📝 Identity & Pitch grounding context
├── runs/                   # 📂 State management (logs, checkpoints, outputs)
│   └── YYYY-MM-DD/         # 🕒 Daily run isolation
├── sent_log.json           # 🛑 Persistent deduplication log
└── bounced_log.json        # 🚫 Persistent suppression blocklist
```

---

<div align="center">
  <p><i>Ensure compliance with CAN-SPAM, GDPR, or applicable local outreach regulations when utilizing automated email sequences.</i></p>
  <p>Built with ❤️ for intelligent automation.</p>
</div>
