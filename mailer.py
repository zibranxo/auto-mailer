"""
Job Application Email Automator
Uses NVIDIA NIM API to generate personalized cold emails, then sends via SMTP.

Usage:
  python mailer.py --dry-run                  # Preview all emails, no sending
  python mailer.py --limit 5                  # Send to first 5 companies
  python mailer.py --filter-region India      # Only India companies
  python mailer.py --filter-tag AI/ML         # Only AI/ML tagged companies
  python mailer.py --filter-region India --filter-tag Fintech --limit 3
"""

import os
import csv
import time
import json
import logging
import argparse
import smtplib
import ssl
import sys
import re
import hashlib
import random
import threading
import tempfile
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from urllib.parse import quote as url_quote
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv
import imaplib
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
import dns.resolver

# ── Rich UI ───────────────────────────────────────────────────────────────────
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress, SpinnerColumn, BarColumn,
    TextColumn, TimeElapsedColumn, TaskProgressColumn,
)
from rich.logging import RichHandler
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich.columns import Columns
from rich import box

console = Console(highlight=False)

load_dotenv()

# Ensure Windows console can print Unicode safely
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
CSV_FILE      = BASE_DIR / "hr_emails_directory.csv"
ABOUT_ME      = BASE_DIR / "about_me.md"
RESUME_PDF    = BASE_DIR / "resume.pdf"
SENT_LOG      = BASE_DIR / "sent_log.json"            # Global deduplication
BOUNCED_LOG   = BASE_DIR / "bounced_log.json"         # Global blocklist
COMPANY_CACHE = BASE_DIR / "company_context_cache.json"
GEN_CACHE     = BASE_DIR / "generation_cache.json"    # Persistent generation cache

# Run-specific paths (initialized in main via setup_run_dir)
RUN_DIR: Path = None
LOG_FILE: Path = None
CHECKPOINT_FILE: Path = None
STATUS_LOG: Path = None

_company_cache = {}
_company_cache_lock = threading.Lock()

PRESET_SUBJECTS = [
    "Software Engineering Internship Application — DTU '28",
    "SDE Intern Application — B.Tech Software Engineering, DTU",
    "Internship Inquiry: Software Development Role",
    "Application for Software Engineer Intern Position",
    "SDE Internship — Second-Year Engineering Student, DTU",
    "Software Engineering Intern — CGPA 8.75, Available for Internship",
    "Internship Application: Backend/Systems Development",
    "SDE Intern Inquiry — Production Systems Experience",
    "Software Development Internship — DTU Sophomore",
    "Application for SDE Internship — System Design & Backend Focus",
    "Internship Application: Software Engineer, DTU '28",
    "SDE Intern — Hackathon Finalist Seeking Internship Opportunity",
    "Software Engineering Internship — Available Summer/Off-Cycle",
    "Internship Application: Full-Stack Development Role",
    "SDE Intern Application — National Hackathon Finalist (SIH 2025)",
    "Software Development Internship Inquiry — DTU Student",
    "Application for Software Engineering Internship — Immediate Availability",
    "SDE Intern — System Design, APIs, and Backend Infrastructure",
    "Internship Application: Software Engineer Role, DTU",
    "Software Engineering Internship — Strong Academic + Project Record",
    "Application for SDE Internship — Coordinator, Business Bulls DTU",
    "SDE Intern Inquiry — Distributed Systems & Backend Projects",
    "Internship Application: Software Developer, 2028 Graduate",
    "Software Engineering Internship — Open to Remote/On-site",
    "SDE Intern Application — Systems & Infrastructure Focus",
]


def get_preset_subject(email_addr: str) -> str:
    h = int(hashlib.md5(email_addr.lower().strip().encode('utf-8')).hexdigest(), 16)
    return PRESET_SUBJECTS[h % len(PRESET_SUBJECTS)]


OVERRIDE_WITH_PRESET_SUBJECTS = True


def _get_env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or not val.strip():
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default

def _get_env_float(key: str, default: float) -> float:
    val = os.getenv(key)
    if val is None or not val.strip():
        return default
    try:
        return float(val.strip())
    except ValueError:
        return default


# ── Config from .env ──────────────────────────────────────────────────────────────────
LLM_API_KEY          = os.getenv("LLM_API_KEY")
LLM_BASE_URL         = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
model_val            = os.getenv("LLM_MODEL")
LLM_MODEL            = model_val.strip() if model_val and model_val.strip() else "default"
fallback_val         = os.getenv("LLM_FALLBACK_MODEL")
LLM_FALLBACK_MODEL   = fallback_val.strip() if fallback_val and fallback_val.strip() else "default"
LLM_TEMPERATURE      = _get_env_float("LLM_TEMPERATURE", 0.2)
LLM_TOP_P            = _get_env_float("LLM_TOP_P", 0.95)
LLM_REASONING_EFFORT = (os.getenv("LLM_REASONING_EFFORT") or "low").strip().lower()
LLM_TIMEOUT_S        = _get_env_float("LLM_TIMEOUT_S", 45.0)
LLM_RETRIES          = _get_env_int("LLM_RETRIES", 2)
LLM_BACKOFF_S        = _get_env_float("LLM_BACKOFF_S", 1.5)
LLM_MAX_BACKOFF_S    = _get_env_float("LLM_MAX_BACKOFF_S", 30.0)
LLM_QUARANTINE_S     = _get_env_float("LLM_QUARANTINE_S", 600.0)

SMTP_HOST            = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT            = _get_env_int("SMTP_PORT", 587)
SMTP_MAX_RETRIES     = _get_env_int("SMTP_MAX_RETRIES", 2)
SMTP_RETRY_DELAY_S   = _get_env_float("SMTP_RETRY_DELAY_S", 5.0)

SENDER_NAME          = os.getenv("SENDER_NAME", "Arnav")
RATE_LIMIT_S         = _get_env_float("RATE_LIMIT_SECONDS", 8.0)
GEN_MAX_TOKENS       = _get_env_int("GEN_MAX_TOKENS", 1500)
EMAIL_MAX_WORDS      = _get_env_int("EMAIL_MAX_WORDS", 400)
EMAIL_MAX_SUBJECT_LEN = _get_env_int("EMAIL_MAX_SUBJECT_LEN", 100)
MIN_CONTACT_SCORE    = _get_env_int("MIN_CONTACT_SCORE", 2)
MIN_QUALITY_SCORE    = _get_env_int("MIN_QUALITY_SCORE", 70)
VARIANT_COUNT        = _get_env_int("VARIANT_COUNT", 1)
COMPANY_CONTEXT_MAX_CHARS = _get_env_int("COMPANY_CONTEXT_MAX_CHARS", 800)

# SMTP connection pooling (from env)
_SMTP_CONNECTION_MAX_AGE  = _get_env_int("SMTP_CONN_MAX_AGE_S", 300)
_SMTP_CONNECTION_MAX_USES = _get_env_int("SMTP_CONN_MAX_USES", 50)

# Adaptive rate limiting (from env)
_base_delay               = RATE_LIMIT_S
_current_delay            = RATE_LIMIT_S
_max_delay                = _get_env_float("RATE_LIMIT_MAX_DELAY_S", 60.0)
_delay_multiplier         = _get_env_float("RATE_LIMIT_DELAY_MULTIPLIER", 1.5)
_delay_decay_factor       = _get_env_float("RATE_LIMIT_DECAY_FACTOR", 0.9)
_rate_success_threshold   = _get_env_int("RATE_LIMIT_SUCCESS_THRESHOLD", 5)
_consecutive_successes    = 0
_consecutive_errors       = 0

from dataclasses import dataclass, field
from openai import OpenAI

@dataclass
class LLMProvider:
    name: str
    client: OpenAI
    model: str
    fallback_model: str
    exhausted_until: float = 0.0
    last_inference_time: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

_provider_pool: list[LLMProvider] = []
_provider_pool_lock = threading.Lock()

def get_active_provider() -> LLMProvider:
    while True:
        with _provider_pool_lock:
            now = time.time()
            for p in _provider_pool:
                if now >= p.exhausted_until:
                    return p
            # All exhausted — find soonest recovery time outside the lock
            earliest_wake = min(p.exhausted_until for p in _provider_pool)
        sleep_time = earliest_wake - time.time()
        if sleep_time > 0:
            log.warning(f"All LLM providers are rate-limited. Sleeping for {sleep_time:.1f}s...")
            time.sleep(sleep_time)
def _env_bool(name: str, default: bool | None = None) -> bool | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "y", "on"}:
        return True
    if val in {"0", "false", "no", "n", "off"}:
        return False
    return default


@dataclass
class SenderAccount:
    name: str
    email: str
    password: str
    connection: smtplib.SMTP | None = None
    last_used: float = 0
    uses_count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

_senders_pool: list[SenderAccount] = []
_sender_idx = 0
_sender_pool_lock = threading.Lock()

def get_next_sender() -> SenderAccount:
    global _sender_idx
    with _sender_pool_lock:
        if not _senders_pool:
            raise RuntimeError("No sender accounts configured")
        sender = _senders_pool[_sender_idx]
        _sender_idx = (_sender_idx + 1) % len(_senders_pool)
        return sender

@contextmanager
def get_smtp_connection(sender: SenderAccount):
    """Context manager for SMTP connection with pooling per sender."""
    with sender.lock:
        now = time.time()
        if (sender.connection is None or
            (now - sender.last_used) > _SMTP_CONNECTION_MAX_AGE or
            sender.uses_count >= _SMTP_CONNECTION_MAX_USES):

            if sender.connection is not None:
                try:
                    sender.connection.quit()
                except Exception:
                    pass
                sender.connection = None

            try:
                _ssl_ctx = ssl.create_default_context()
                sender.connection = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
                sender.connection.ehlo()
                sender.connection.starttls(context=_ssl_ctx)
                sender.connection.login(sender.email, sender.password)
                sender.last_used = now
                sender.uses_count = 0
                log.debug(f"SMTP connection established/renewed for {sender.email}")
            except Exception as e:
                log.error(f"Failed to establish SMTP connection for {sender.email}: {e}")
                raise

        sender.uses_count += 1
        try:
            yield sender.connection
        except Exception as e:
            log.warning(f"SMTP connection error for {sender.email}: {e}")
            try:
                sender.connection.quit()
            except Exception:
                pass
            sender.connection = None
            raise


def update_rate_limit(success: bool, smtp_error: Optional[Exception] = None):
    """Update the rate limit delay based on success/error history."""
    global _current_delay, _consecutive_successes, _consecutive_errors

    if success:
        _consecutive_successes += 1
        _consecutive_errors = 0
        # Gradually decrease delay after successes, but not below base delay
        if _consecutive_successes > _rate_success_threshold:
            _current_delay = max(_base_delay, _current_delay * _delay_decay_factor)
            log.debug(f"Rate delay decreased to {_current_delay:.2f}s after {_consecutive_successes} successes")
    else:
        _consecutive_errors += 1
        _consecutive_successes = 0
        # Increase delay after errors
        if smtp_error and hasattr(smtp_error, 'smtp_code'):
            # Handle specific SMTP errors
            code = smtp_error.smtp_code
            if code in (421, 450, 451, 452):  # Service not available, mailbox busy, etc.
                _current_delay = min(_max_delay, _current_delay * _delay_multiplier * 2)
                log.warning(f"SMTP error {code}: Increasing rate delay to {_current_delay:.2f}s")
            elif code in (500, 501, 502, 503, 504, 550, 551, 552, 553, 554):  # Permanent errors
                # Don't increase delay for permanent errors - they won't be fixed by waiting
                pass
            else:
                _current_delay = min(_max_delay, _current_delay * _delay_multiplier)
                log.warning(f"SMTP error {code}: Increasing rate delay to {_current_delay:.2f}s")
        else:
            # General error
            _current_delay = min(_max_delay, _current_delay * _delay_multiplier)
            log.warning(f"SMTP error: Increasing rate delay to {_current_delay:.2f}s")


def send_email(to_addr: str, subject: str, body: str, company_name: str = "", dry_run: bool = False) -> bool:
    """Send an email with connection pooling, retry logic, and intelligent rate limiting."""
    if not RESUME_PDF.exists():
        raise FileNotFoundError(f"resume.pdf not found at {RESUME_PDF}")

    sender = get_next_sender()
    msg = MIMEMultipart()
    msg["From"]    = f"{SENDER_NAME} <{sender.email}>"
    msg["To"]      = to_addr
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain"))

    # Generate and add X-Entity-Ref-ID tracking header
    if company_name:
        ref_string = f"{company_name}:{to_addr}:{time.time()}"
        ref_id = hashlib.sha256(ref_string.encode()).hexdigest()[:16]
        msg["X-Entity-Ref-ID"] = ref_id

    # Attach resume
    with open(RESUME_PDF, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    
    company_name_clean = re.sub(r'[^a-zA-Z0-9]', '_', company_name) if company_name else ""
    filename = f"{SENDER_NAME}_Resume_{company_name_clean}.pdf" if company_name_clean else f"{SENDER_NAME}_Resume.pdf"
    part.add_header(
        "Content-Disposition",
        f'attachment; filename="{filename}"',
    )
    msg.attach(part)

    if dry_run:
        return True  # pretend success

    # Apply intelligent rate limiting
    time.sleep(_current_delay)

    max_send_attempts = SMTP_MAX_RETRIES
    for attempt in range(1, max_send_attempts + 1):
        try:
            with get_smtp_connection(sender) as server:
                server.sendmail(sender.email, to_addr, msg.as_string())

            # Success - update rate limiting
            update_rate_limit(True)
            return True
        except Exception as e:
            # Determine if error is fatal
            is_fatal = False
            code = getattr(e, 'smtp_code', None)
            
            if code:
                # 5xx SMTP codes are permanent failures
                if 500 <= code < 600:
                    is_fatal = True
            elif isinstance(e, (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused)):
                is_fatal = True

            if is_fatal:
                update_rate_limit(False, e)
                log.error(f"Fatal SMTP error sending to {to_addr} from {sender.email}: {e}")
                return False

            if attempt < max_send_attempts:
                log.warning(f"Temporary SMTP error sending to {to_addr} from {sender.email} (attempt {attempt}/{max_send_attempts}): {e}. Retrying in {SMTP_RETRY_DELAY_S}s...")
                time.sleep(SMTP_RETRY_DELAY_S)
                with sender.lock:
                    if sender.connection is not None:
                        try:
                            sender.connection.quit()
                        except Exception:
                            pass
                        sender.connection = None
                continue
            else:
                update_rate_limit(False, e)
                log.error(f"SMTP error sending to {to_addr} from {sender.email} after {max_send_attempts} attempts: {e}")
                return False



LLM_THINKING = _env_bool("LLM_THINKING", False)

# ── Logging & Status ──────────────────────────────────────────────────────────
log = logging.getLogger(__name__)

def log_status(status: str, message: str):
    if STATUS_LOG:
        with open(STATUS_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{status}] {message}\n")


def _atomic_write(path: Path, content: str) -> None:
    """Atomically write content to a file to prevent corruption on crash."""
    dir_ = path.parent
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=dir_, delete=False,
                                         suffix=".tmp", encoding="utf-8") as tf:
            tf.write(content)
            tmp_path = Path(tf.name)
        os.replace(tmp_path, path)
    except Exception as e:
        # Best-effort cleanup
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ── Sent log helpers ───────────────────────────────────────────────────
def load_sent_log() -> dict:
    if SENT_LOG.exists():
        try:
            data = json.loads(SENT_LOG.read_text())
            # Normalize keys to lowercase and stripped
            return {k.strip().lower(): v for k, v in data.items() if k}
        except Exception as e:
            log.warning(f"Failed to load sent_log: {e}")
    return {}


def save_sent_log(sent: dict):
    _atomic_write(SENT_LOG, json.dumps(sent, indent=2))


def mark_sent(sent: dict, email: str, company: str):
    email_key = email.strip().lower()
    sent[email_key] = {"company": company, "sent_at": datetime.now().isoformat()}
    save_sent_log(sent)



# ── Generation cache helpers ──────────────────────────────────────────────────
def _cache_key(company: dict, about_me: str) -> str:
    """Create a stable hash key for generation cache."""
    raw = f"{company['Company']}:{company['Email']}:{about_me[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def load_generation_cache() -> dict:
    if GEN_CACHE.exists():
        return json.loads(GEN_CACHE.read_text())
    return {}


def save_generation_cache(cache: dict):
    _atomic_write(GEN_CACHE, json.dumps(cache, indent=2))


def get_cached_email(cache: dict, company: dict, about_me: str) -> Optional[dict]:
    key = _cache_key(company, about_me)
    entry = cache.get(key)
    if entry:
        log.info(f"  Using cached generation for {company['Company']}")
        return {"subject": entry["subject"], "body": entry["body"]}
    return None


def cache_email(cache: dict, company: dict, about_me: str, result: dict):
    key = _cache_key(company, about_me)
    cache[key] = {
        "company": company["Company"],
        "email": company["Email"],
        "subject": result["subject"],
        "body": result["body"],
        "cached_at": datetime.now().isoformat(),
    }
    save_generation_cache(cache)


# ── API health check ──────────────────────────────────────────────────────────
def check_api_health(client: OpenAI, model: str = None, timeout: float = 10) -> bool:
    """Test if the API is responsive before starting a batch."""
    try:
        response = client.chat.completions.create(
            model=model or LLM_MODEL,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
            timeout=timeout,
        )
        if response and response.choices and response.choices[0].message:
            return True
    except Exception as e:
        log.warning(f"API health check failed for {model or LLM_MODEL}: {e}")
    return False


# ── CSV loader ────────────────────────────────────────────────────────────────
def load_companies() -> list[dict]:
    if not CSV_FILE.exists():
        raise FileNotFoundError(f"CSV not found: {CSV_FILE}")
    rows = []
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # Clean keys/values
            cleaned_row = {k.strip(): v.strip() for k, v in row.items() if k}
            # Ensure name defaults to "Hiring Team" if empty
            if not cleaned_row.get("Name"):
                cleaned_row["Name"] = "Hiring Team"
            rows.append(cleaned_row)
    return rows


# ── About-me loader ───────────────────────────────────────────────────────────
def load_about_me() -> str:
    if not ABOUT_ME.exists():
        raise FileNotFoundError(f"about_me.md not found at {ABOUT_ME}")
    return ABOUT_ME.read_text(encoding="utf-8")


DISPOSABLE_DOMAINS = {
    "mailinator.com", "trashmail.com", "tempmail.com", "yopmail.com",
    "dispostable.com", "10minutemail.com", "guerrillamail.com",
    "sharklasers.com", "getairmail.com", "burnermail.io"
}


_mx_cache = {}

def has_valid_mx_record(domain: str) -> bool:
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        answers = dns.resolver.resolve(domain, 'MX')
        is_valid = len(answers) > 0
    except Exception:
        is_valid = False
    _mx_cache[domain] = is_valid
    return is_valid

def is_valid_email(email: str) -> bool:
    """Validate email format using regex and disposable domains."""
    if not email or not isinstance(email, str):
        return False
    email = email.strip().lower()
    if not email:
        return False
    
    if ".." in email:
        return False
        
    if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email):
        return False
        
    try:
        parts = email.split("@")
        if len(parts) == 2:
            domain = parts[1]
            if domain in DISPOSABLE_DOMAINS:
                return False
    except Exception:
        return False
        
    return True

def sync_bounces(username, password, imap_server="imap.gmail.com") -> list:
    """Fetch bounced emails via IMAP and add them to BOUNCED_LOG."""
    try:
        log.info("Connecting to IMAP to sync bounces...")
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(username, password)
        mail.select("inbox")
        
        status, messages = mail.search(None, '(FROM "mailer-daemon")')
        bounced_emails = set()
        
        if status == "OK" and messages[0]:
            import email as pyemail
            for num in messages[0].split():
                try:
                    _, msg_data = mail.fetch(num, "(RFC822)")
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = pyemail.message_from_bytes(response_part[1])
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        body += part.get_payload(decode=True).decode(errors='ignore')
                            else:
                                body = msg.get_payload(decode=True).decode(errors='ignore')
                                
                            matches = re.findall(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", body)
                            bounced_emails.update(m.lower() for m in matches if m.lower() != username.lower())
                except Exception as e:
                    log.debug(f"Failed to parse a bounce message: {e}")
        
        mail.close()
        mail.logout()
        
        existing = []
        if BOUNCED_LOG.exists():
            try:
                existing = json.loads(BOUNCED_LOG.read_text())
            except Exception:
                pass
        combined = list(set(existing) | bounced_emails)
        BOUNCED_LOG.write_text(json.dumps(combined, indent=2))
        if bounced_emails:
            log.info(f"Synced {len(bounced_emails)} newly bounced emails.")
        return combined
    except Exception as e:
        log.warning(f"IMAP bounce sync failed: {e}")
        return []

def load_bounces() -> set:
    if BOUNCED_LOG.exists():
        try:
            return set(json.loads(BOUNCED_LOG.read_text()))
        except Exception:
            pass
    return set()

def _scrape_page_context(url: str, timeout: int = 6) -> str:
    """Scrape a single page and return the best available textual description."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        snippets = []

        # 1. OG description (usually the best marketing copy)
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            snippets.append(og_desc["content"].strip())

        # 2. Standard meta description fallback
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            val = meta_desc["content"].strip()
            if val not in snippets:
                snippets.append(val)

        # 3. JSON-LD schema.org description
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict) and data.get("description"):
                    desc = data["description"].strip()
                    if desc not in snippets:
                        snippets.append(desc)
                        break
            except Exception:
                pass

        # 4. Page title
        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        # 5. For /careers pages, try to grab "we're looking for" / tech-stack paragraphs
        if "/careers" in url or "/jobs" in url:
            for tag in soup.find_all(["p", "li", "h2", "h3"]):
                text = tag.get_text(separator=" ", strip=True)
                if any(kw in text.lower() for kw in ["looking for", "we build", "we use", "tech stack", "ideal candidate", "you will"]):
                    if len(text) > 30:
                        snippets.append(text[:300])
                        break

        result = ""
        if title:
            result += f"Website Title: {title}\n"
        if snippets:
            result += "\n".join(snippets[:3])  # Top 3 snippets max

        return result.strip()
    except Exception:
        return ""


def _ddg_company_snippet(company_name: str, domain: str) -> str:
    """Fall back to a DuckDuckGo search snippet to get company description."""
    try:
        query = f"{company_name} {domain} company about"
        url = f"https://html.duckduckgo.com/html/?q={url_quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, timeout=8, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")
        results = soup.find_all("a", class_="result__snippet")
        if results:
            return results[0].get_text(separator=" ", strip=True)[:400]
    except Exception:
        pass
    return ""


def fetch_company_context(company_name: str, domain: str) -> str:
    """Fetch rich company context: homepage + /about + /careers, with OG tags and DDG fallback."""
    with _company_cache_lock:
        if domain in _company_cache:
            return _company_cache[domain]

    log.info(f"  Fetching web context for {company_name} from {domain}...")

    collected: list[str] = []

    # Pages to try in order
    candidate_urls = [
        f"https://{domain}",
        f"https://{domain}/about",
        f"https://{domain}/about-us",
        f"https://{domain}/careers",
    ]

    homepage_ok = False
    for url in candidate_urls:
        snippet = _scrape_page_context(url)
        if snippet:
            if url == f"https://{domain}":
                homepage_ok = True
            for line in snippet.split("\n"):
                if line.strip() and line.strip() not in collected:
                    collected.append(line.strip())
        if len(collected) >= 6:  # Enough context
            break

    # DuckDuckGo fallback if homepage failed or gave nothing useful
    if not homepage_ok or len(collected) < 2:
        ddg = _ddg_company_snippet(company_name, domain)
        if ddg and ddg not in collected:
            collected.append(f"[Search snippet] {ddg}")

    if not collected:
        with _company_cache_lock:
            _company_cache[domain] = ""
            try:
                _atomic_write(COMPANY_CACHE, json.dumps(_company_cache, indent=2))
            except Exception as e:
                log.debug(f"Failed to save company context cache: {e}")
        return ""

    # Assemble context, capped to env-configured max chars
    context = "\n".join(collected)
    if len(context) > COMPANY_CONTEXT_MAX_CHARS:
        context = context[:COMPANY_CONTEXT_MAX_CHARS - 3] + "..."

    # Scrub prompt-injection patterns before injecting into LLM prompt
    injection_patterns = [
        r'ignore (all |previous |prior )?instructions',
        r'disregard (all |previous |prior )?instructions',
        r'you are now',
        r'new persona',
        r'system prompt',
    ]
    for pattern in injection_patterns:
        context = re.sub(pattern, '[redacted]', context, flags=re.IGNORECASE)

    with _company_cache_lock:
        _company_cache[domain] = context
        try:
            _atomic_write(COMPANY_CACHE, json.dumps(_company_cache, indent=2))
        except Exception as e:
            log.debug(f"Failed to save company context cache: {e}")

    return context




def calculate_contact_score(company: dict, sent_log: dict = None) -> int:
    """Calculate a contact score based on email validity and domain type (0-5)."""
    score = 0
    email = company["Email"].strip().lower()

    # Email format validity (2 points)
    if is_valid_email(email):
        score += 2

    # Domain type analysis (2 points)
    email_domain = email.split('@')[-1] if '@' in email else ""
    domain_parts = email_domain.split('.')
    domain_name = '.'.join(domain_parts[:-1]) if len(domain_parts) > 1 else email_domain
    tlds = domain_parts[-1:] if len(domain_parts) > 1 else []
    
    corporate_tlds = {'co', 'org', 'gov', 'edu', 'ac'}
    corporate_keywords = {'company', 'corp', 'inc', 'ltd', 'llc'}
    generic_domains = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com'}

    if (any(kw in domain_name for kw in corporate_keywords) or 
        any(tld in corporate_tlds for tld in tlds)):
        score += 2
    elif email_domain in generic_domains:
        score += 0
    else:
        score += 1

    # Communication history check (1 point if not contacted, -1 if contacted)
    if sent_log and email in sent_log:
        score -= 1
    else:
        score += 1

    return max(0, min(5, score))


# ── NIM email generator ───────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are writing a cold job-application email on behalf of Arnav Sagar, a 2nd-year B.Tech Software Engineering student at Delhi Technological University (DTU), CGPA 8.75.

## Who Arnav Is
Arnav is a highly capable student developer. He completed two research internships before the end of his first year — both producing real, deployed systems:
- At AIMS-DTU: built a 3-stage LLM moderation pipeline (regex → semantic embeddings → fine-tuned DistilBERT + XGBoost) with sub-10ms filtering, served via FastAPI in production.
- At 5G Lab, DoT DTU: built a sub-25ms P95 on-device vision inference pipeline using YOLOv8, CUDA, and a self-supervised trajectory autoencoder. Presented at PEC Chandigarh.
His strongest projects (CLASP, Regavis, YTRAG, LLM Safety Shield, JAILS, ZeroFall+, CAF-OTSRNet) involve real engineering — not tutorials. He can cite concrete metrics: 0.9996 accuracy, +15.74% PSNR vs SOTA, 4× model compression.

## Email Structure (MANDATORY — 4 paragraphs, do not include sign-off)
The email must consist exactly of a salutation and four body paragraphs. Do NOT generate the sign-off block (like 'Thanks for your time, Arnav Sagar...'); this will be appended programmatically by Python code.

1. **SALUTATION:** Start with "Hi [HR Name]," (or "Hi Hiring Team," if Name is not specified in the prompt).
2. **PARAGRAPH 1 (INTRO & HOOK):** Open with an introduction (e.g., "I'm Arnav Sagar, a second-year Software Engineering student at Delhi Technological University, and I'm reaching out about AI/ML intern opportunities at [Company]."). Connect your interest directly to the target company's mission/product.
3. **PARAGRAPH 2 & 3 (VALUE PROP):** Pitch 2-3 of your strongest matching projects from the candidate brief in a narrative style (not bullet points). Cite specific technical details and concrete metrics (e.g. rate-limiting, token-buckets, latency reductions, accuracy percentages) to show real engineering depth.
4. **PARAGRAPH 4 (ASK):** Express specific interest in the scale/challenges of the company and make a clear request for a brief chat or opportunity to share more details.

## Hard Rules
- **Word count:** Aim for 200–350 words in the body paragraphs.
- **Subject line:** Create a compelling, professional, and specific subject line in the format: "2nd-Year DTU Engineer — [Specific Tech Detail/Project Hook]" (e.g., "2nd-Year DTU Engineer — Rate-Limiting a Multi-Provider LLM Proxy Across 18 APIs"). Do not make it generic or spammy.
- **BANNED filler phrases**: "passionate about", "excited to", "highly motivated", "quick learner", "team player", "I believe I can", "I feel I would be a great fit", "demonstrate", "showcase"
- Do NOT generate any sign-off text (no "Best regards", no name, no phone, no links). Just stop after Paragraph 4.

## Output Format
Return ONLY valid JSON with exactly these keys:
{"subject":"...","body":"..."}
No markdown. No code fences. No extra keys. No explanation.
"""



def _extract_content_text(response) -> str:
    """Extract text from NIM API response with comprehensive None/empty handling."""
    # Guard: response itself might be None or malformed
    if response is None or not hasattr(response, "choices"):
        raise ValueError("API response is None or missing 'choices' attribute")

    if not response.choices:
        raise ValueError("API response.choices is empty")

    first_choice = response.choices[0]
    if first_choice is None or not hasattr(first_choice, "message"):
        raise ValueError("First choice is None or missing 'message' attribute")

    msg = first_choice.message
    if msg is None:
        # Check if the choice has a finish_reason indicating why
        finish_reason = getattr(first_choice, "finish_reason", "unknown")
        if finish_reason == "length":
            raise ValueError("Model stopped due to token limit (reasoning consumed all tokens)")
        elif finish_reason == "content_filter":
            raise ValueError("Content filtered by API safety system")
        raise ValueError(f"Message is None (finish_reason={finish_reason})")

    # Extract content with multiple fallback strategies
    content = getattr(msg, "content", None)

    # Try alternative attributes if content is None
    if content is None:
        alternatives = ["text", "value", "output", "generated_text"]
        for attr in alternatives:
            val = getattr(msg, attr, None)
            if val is not None:
                content = val
                break

    if content is None:
        # Last resort: check for reasoning content (some models store output here)
        reasoning = getattr(msg, "reasoning", None)
        if reasoning:
            raise ValueError("Model returned reasoning-only content (no final answer)")
        raise ValueError("Message content is None and no alternative fields found")

    # Normalize content types
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text", ""))
            else:
                parts.append(getattr(part, "text", str(part)))
        return "".join(parts).strip()
    if isinstance(content, dict):
        # Try to extract text from a dict (could be response_format artifact)
        if "text" in content:
            return str(content["text"]).strip()
        return str(content).strip()

    # Fallback for any other type
    return str(content).strip()


class JSONParseError(ValueError):
    """Custom exception raised when JSON parsing fails, storing raw content for analysis."""
    def __init__(self, message: str, raw_content: str):
        super().__init__(message)
        self.raw_content = raw_content


def _parse_email_json(raw: str) -> dict:
    """Parse JSON from model response with repair capabilities for common LLM errors."""
    if not raw or not raw.strip():
        raise JSONParseError("Empty response from model", raw)

    txt = raw.strip()

    # Fast path: direct JSON parse if it starts with { and ends with }
    if txt.startswith("{") and txt.endswith("}"):
        try:
            data = json.loads(txt)
            if isinstance(data, dict) and "subject" in data and "body" in data:
                return {"subject": str(data["subject"]).strip(), "body": str(data["body"]).strip()}
        except Exception:
            pass

    # JSON5 lenient parsing fallback
    try:
        import json5
        # Clean potential markdown block formatting from outer boundary if needed
        clean_txt = txt
        if clean_txt.startswith("```"):
            clean_txt = re.sub(r"^```(?:json)?", "", clean_txt, flags=re.IGNORECASE).strip()
            clean_txt = re.sub(r"```$", "", clean_txt).strip()
        data = json5.loads(clean_txt)
        if isinstance(data, dict) and "subject" in data and "body" in data:
            subject = str(data["subject"]).strip()
            body = str(data["body"]).strip()
            if subject and body:
                return {"subject": subject, "body": body}
    except Exception:
        pass

    # Handle markdown code fences
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?", "", txt, flags=re.IGNORECASE).strip()
        txt = re.sub(r"```$", "", txt).strip()

    # Extract JSON object if surrounded by explanatory text (last well-formed object)
    start_idx = txt.find("{")
    end_idx = txt.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        json_candidate = txt[start_idx:end_idx + 1]
        if json_candidate.count("{") == json_candidate.count("}"):
            txt = json_candidate

    # Comprehensive repair strategies
    repair_strategies = [
        lambda x: x,
        lambda x: re.sub(r'\*\*|`|_|<br>', '', x).strip(),  # Strip markdown
        lambda x: re.sub(r',\s*([}\]])', r'\1', x),  # Fix trailing commas
        lambda x: re.sub(r'}\s*{', r'},{', x),  # Fix missing commas between objects
        lambda x: re.sub(r'\]\s*\[', '],[', x),  # Fix missing commas between arrays
        lambda x: re.sub(r"(?<!\\)'", '"', x),  # Single → double quotes
        lambda x: _balance_braces(x),  # Balance braces
        lambda x: re.sub(r'[^\x20-\x7E\s]', '', x),  # Remove non-printable chars
    ]

    last_exception = None
    for i, strategy in enumerate(repair_strategies):
        try:
            repaired = strategy(txt)
            if i > 0 and repaired == txt:
                continue
            data = json.loads(repaired)

            if not isinstance(data, dict):
                raise ValueError("Model output JSON is not an object")

            subject = str(data.get("subject", "")).strip()
            body = str(data.get("body", "")).strip()

            if not subject or not body:
                raise ValueError("Model JSON missing non-empty 'subject' or 'body'")

            return {"subject": subject, "body": body}

        except (json.JSONDecodeError, ValueError) as e:
            last_exception = e
            continue

    # Try unterminated string fixer
    try:
        return _fix_unterminated_strings(txt)
    except Exception:
        pass

    # Try JSON5 one last time on fully cleaned/repaired text
    try:
        import json5
        data = json5.loads(txt)
        if isinstance(data, dict):
            subject = str(data.get("subject", "")).strip()
            body = str(data.get("body", "")).strip()
            if subject and body:
                return {"subject": subject, "body": body}
    except Exception:
        pass

    raise JSONParseError(
        f"Failed to parse JSON from model response after {len(repair_strategies)} repair attempts. "
        f"Last error: {last_exception}.",
        raw
    )



def _balance_braces(s: str) -> str:
    """Balance opening and closing braces in a JSON string."""
    count = 0
    result = []
    in_string = False
    escape_next = False

    for ch in s:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue

        if ch == '\\\\':
            result.append(ch)
            escape_next = True
            continue

        if not in_string:
            if ch == '{':
                count += 1
                result.append(ch)
            elif ch == '}':
                if count > 0:
                    count -= 1
                    result.append(ch)
            elif ch == '"':
                in_string = True
                result.append(ch)
            else:
                result.append(ch)
        else:
            if ch == '"':
                in_string = False
            result.append(ch)

    while count > 0:
        result.append('}')
        count -= 1

    return ''.join(result)


def _fix_unterminated_strings(json_str: str) -> dict:
    """Attempt to fix unterminated strings in JSON by balancing quotes."""
    # We'll use a simple approach: if we have an odd number of unescaped quotes, add one at the end

    # Track whether we're inside a string and whether the next quote is escaped
    in_string = False
    escape_next = False
    quote_positions = []

    for i, char in enumerate(json_str):
        if escape_next:
            escape_next = False
            continue

        if char == '\\':
            escape_next = True
            continue

        if char == '"' and not escape_next:
            if not in_string:
                # Opening quote
                in_string = True
                quote_positions.append(i)
            else:
                # Closing quote
                in_string = False
                quote_positions.pop()

    # If we're still in a string at the end, we need to add a closing quote
    if in_string:
        json_str += '"'

    json_str = _balance_braces(json_str)

    # Try parsing again
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # If that still failed, try a more aggressive approach: add quotes at the end
        # until we either succeed or have tried too many times
        for _ in range(3):  # Try up to 3 times
            json_str += '"'
            try:
                data = json.loads(json_str)
                break
            except json.JSONDecodeError:
                continue
        else:
            # If we still failed, re-raise the last exception
            raise

    # Validate we got what we expect
    if not isinstance(data, dict):
        raise ValueError("Model output JSON is not an object")

    subject = str(data.get("subject", "")).strip()
    body = str(data.get("body", "")).strip()

    if not subject or not body:
        raise ValueError("Model JSON missing non-empty 'subject' or 'body'")

    return {"subject": subject, "body": body}


# Template-based fallback if LLM completely fails
EMAIL_TEMPLATE = """Hi {name},

I hope this email finds you well. I am writing to express my strong interest in contributing as an AI/ML Engineering Intern at {company}.

I am currently a 2nd-year BTech Software Engineering student at Delhi Technological University (DTU). I have already completed two research internships:
• At AIMS-DTU, I built a 3-stage LLM moderation pipeline using DistilBERT + XGBoost with sub-10ms filtering.
• At the 5G Lab (DTU), I developed edge AI threat detection models using YOLOv8.

In addition to my internships, I have built several metric-driven engineering projects:
• CLASP — a rate-limit-aware multi-provider LLM proxy with token-bucket limiting and an async priority queue (934 passing tests).
• Regavis — a two-stage audio deepfake detection cascade (LFCC-LCNN + XLSR-53 + AASIST) with Indic speech bootstrapping.
• YTRAG — a semantic retrieval YouTube chatbot leveraging FAISS, BM25, and RRF fusion.

I would love the opportunity to bring my hands-on experience in building robust, low-latency AI pipelines to the team at {company}. I have attached my resume for your review and would appreciate the chance to discuss how I can contribute to your goals.

GitHub: github.com/zibranxo
Resume: attached

Thanks for your time,
Arnav Sagar
+91-6284962948
arnavsagar1510@gmail.com
"""


# ── Domain-to-project routing ─────────────────────────────────────────────────
# Maps a company's Tag field to the most relevant Arnav projects/internships.
# Edit this as new projects are completed or priorities shift.
DOMAIN_PROJECT_MAP: dict[str, list[str]] = {
    "AI/ML":        ["CLASP", "AIMS-DTU internship", "Retrieval Augmentation System"],
    "NLP":          ["YTRAG", "Retrieval Augmentation System", "AIMS-DTU internship"],
    "Security":     ["Regavis deepfake detection", "JAILS", "LLM Safety Shield"],
    "Fintech":      ["CLASP", "YTRAG", "AIMS-DTU internship"],
    "Audio":        ["Regavis deepfake detection", "5G Lab internship"],
    "Infrastructure": ["CLASP", "5G Lab internship", "ZeroFall+"],
    "Vision/Aerospace": ["CAF-OTSRNet", "5G Lab internship"],
    "Data":         ["Retrieval Augmentation System", "AI vs Human classifier", "YTRAG"],
    "SaaS":         ["CLASP", "YTRAG", "LLM Safety Shield"],
    "5G/Telecom":   ["5G Lab internship", "ZeroFall+", "CAF-OTSRNet"],
    "default":      ["CLASP", "AIMS-DTU internship", "Retrieval Augmentation System"],
}

# Maps project/internship names to a short, metric-rich description the LLM can cite.
PROJECT_BRIEFS: dict[str, str] = {
    "AIMS-DTU internship":
        "Built a 3-stage LLM moderation pipeline (regex → DistilBERT + XGBoost + LOF) "
        "achieving sub-10ms filtering across 5 harm categories, served via FastAPI in production.",
    "5G Lab internship":
        "Engineered a sub-25ms P95 on-device YOLOv8 inference pipeline via 4× model compression "
        "and a self-supervised trajectory autoencoder; presented original research at PEC Chandigarh.",
    "LLM Safety Shield":
        "Session-aware jailbreak classifier with MiniLM similarity, SHAP explainability, "
        "adversarial normalisation (homoglyphs, base64), Redis caching for sub-ms repeat queries, and ONNX inference.",
    "YTRAG":
        "Full end-to-end RAG pipeline over YouTube transcripts: semantic chunking → "
        "text-embedding-3-small → IndexedDB → cosine retrieval (TOP_K=3) → LLM generation. Dual provider support.",
    "JAILS":
        "Hybrid jailbreak/prompt-injection detector combining semantic similarity, TF-IDF, LOF for zero-day attacks, "
        "and fully interpretable output with confidence score + feature-level reasoning.",
    "ZeroFall+":
        "Unified WAF + EDR pipeline with 6 autonomous agents — RoBERTa for anomaly detection, "
        "blockchain behavioural hashing for O(1) immutable threat memory, LoRA fine-tuning.",
    "AI vs Human classifier":
        "14-model benchmark on ~200K samples; RoBERTa fine-tune hit 0.9996 accuracy. "
        "GPU pipeline: 35 min → 6 min (5.8× speedup).",
    "Retrieval Augmentation System":
        "Multi-stage RAG over PDFs: FAISS + BM25 hybrid retrieval, RRF fusion, cross-encoder reranking, "
        "HyDE query expansion, and CRAG-based hallucination suppression.",
    "CAF-OTSRNet":
        "Triple-encoder cross-attention fusion for thermal super-resolution. "
        "PSNR +15.74%, SSIM +8.22% vs SOTA on ISRO dataset. National Finalist, Smart India Hackathon 2025.",
    "CLASP":
        "Built a rate-limit-aware multi-provider LLM proxy with token-bucket limiting across multi-key pools, "
        "circuit breakers, and an async priority queue with SSE keep-alive absorption; two-tier LRU/SQLite/FAISS cache; 934 passing tests.",
    "Regavis deepfake detection":
        "Designed a two-stage audio deepfake detection cascade — LFCC-LCNN for fast first-pass screening, "
        "frozen XLSR-53 + AASIST for high-precision second-stage verification — with a Hindi/Indic data bootstrapping strategy for underrepresented accents."
}


def infer_company_tag(company_context: str) -> str:
    """Infer the most relevant company tag based on scraped context keywords."""
    if not company_context:
        return "default"
    
    ctx_lower = company_context.lower()
    
    # Category keyword mapping
    keyword_map = {
        "Fintech": {"bank", "payment", "lending", "fraud", "credit", "finance", "fintech", "transaction", "wealth"},
        "Security": {"security", "cyber", "threat", "vulnerability", "breach", "firewall", "safety", "defense", "hack", "penetration", "exploit", "leakage"},
        "Audio": {"audio", "voice", "speech", "call", "deepfake", "acoustic", "sound", "dsp"},
        "Vision/Aerospace": {"vision", "satellite", "isro", "aerospace", "image", "thermal", "yolo", "opencv", "camera", "deformable"},
        "5G/Telecom": {"5g", "telecom", "cellular", "telecommunications", "edge", "latency", "mec", "network"},
        "AI/ML": {"llm", "rag", "nlp", "chatbot", "generative", "model", "inference", "prompt", "train", "embeddings", "classification"}
    }
    
    best_tag = "default"
    max_matches = 0
    
    # Split context into clean lowercase tokens
    tokens = set(re.findall(r'[a-z0-9]+', ctx_lower))
    
    for tag, keywords in keyword_map.items():
        matches = len(tokens & keywords)
        if matches > max_matches:
            max_matches = matches
            best_tag = tag
            
    return best_tag


def build_candidate_context(about_me: str, company_tag: str) -> str:
    """Build a compact, domain-ranked candidate context block for the LLM user prompt.

    Instead of sending the full 7,600-byte about_me.md, this extracts:
    - The top 2–3 most relevant projects for the given company domain tag
    - Their metric-rich brief descriptions
    - Core identity facts (school, CGPA, year, internship count)

    Returns a ~250-token structured string ready to inject into the user prompt.
    """
    # Normalize tag: remove spaces around slashes, lowercase for matching
    tag_normalised = company_tag.strip() if company_tag else "default"
    tag_key = re.sub(r'\s*/\s*', '/', tag_normalised)  # "AI / ML" → "AI/ML", "AI / NLP" → "AI/NLP"

    # Exact match first
    project_keys = DOMAIN_PROJECT_MAP.get(tag_key) or DOMAIN_PROJECT_MAP.get(tag_normalised)
    if not project_keys:
        # Fuzzy match: check each map key against normalized tag tokens
        tag_tokens = set(re.split(r'[/\s]+', tag_key.lower()))  # {"ai", "ml"}
        best_key = None
        best_overlap = 0
        for key in DOMAIN_PROJECT_MAP:
            if key == "default":
                continue
            key_tokens = set(re.split(r'[/\s]+', key.lower()))
            overlap = len(tag_tokens & key_tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best_key = key
        if best_key and best_overlap > 0:
            project_keys = DOMAIN_PROJECT_MAP[best_key]
    if not project_keys:
        project_keys = DOMAIN_PROJECT_MAP["default"]

    # Build project bullets (top 3 max)
    project_bullets = []
    for name in project_keys[:3]:
        brief = PROJECT_BRIEFS.get(name)
        if brief:
            project_bullets.append(f"  • {name}: {brief}")

    projects_block = "\n".join(project_bullets)

    return f"""## Arnav Sagar — Candidate Brief
- DTU 2nd Year, B.Tech Software Engineering | CGPA: 8.75/10
- 2 research internships shipped before end of Year 1 (AIMS-DTU LLM Safety + 5G Lab DoT)
- National Finalist, Smart India Hackathon 2025 (ISRO problem statement)

## Most Relevant Work for This Company's Domain ({tag_normalised})
{projects_block}

## Contact
arnavsagar1510@gmail.com | +91-6284962948 | github.com/zibranxo | linkedin.com/in/arnvsr"""



def generate_email(
    provider: LLMProvider,
    about_me: str,
    company: dict,
    max_tokens: int,
    timeout_s: float | None = None,
    model: str | None = None,
    temperature: float = LLM_TEMPERATURE,
    top_p: float = LLM_TOP_P,
    thinking: bool | None = LLM_THINKING,
    reasoning_effort: str | None = LLM_REASONING_EFFORT,
    company_context: str = "",
) -> dict:
    co_name    = company["Company"]
    co_tag     = company.get("Tag", "") or infer_company_tag(company_context)
    co_email   = company["Email"]
    co_hr_name = company.get("Name", "Hiring Team")

    # Build curated candidate context (domain-aware, ~250 tokens vs 7,600 bytes raw)
    candidate_ctx = build_candidate_context(about_me, co_tag)

    user_prompt = f"""## Your Task
Write a cold job-application email on behalf of Arnav Sagar for the company below.
Use the candidate brief and company context to make it specific and metric-grounded.

---

{candidate_ctx}

---

## Target Company
- Name: {co_name}
- Industry / Domain: {co_tag}
- Contact: {co_hr_name}
"""

    if company_context:
        user_prompt += f"""
## Company Context (scraped from website)
{company_context}
"""
    else:
        user_prompt += f"""
## Company Context
No website context available — use your knowledge of {co_name} if known, otherwise focus on the industry domain.
"""

    user_prompt += f"""
---

## Output Requirements
- Subject line: Create a compelling, professional, and specific subject line in the format: "2nd-Year DTU Engineer — [Specific Tech Detail/Project Hook]" (e.g., "2nd-Year DTU Engineer — Rate-Limiting a Multi-Provider LLM Proxy Across 18 APIs"). Do not make it generic or spammy.
- Body structure (4 body paragraphs exactly, no sign-off block):
  1. SALUTATION: Start exactly with "Hi {co_hr_name},"
  2. PARAGRAPH 1 (INTRO & HOOK): Open with an introduction (e.g., "I'm Arnav Sagar, a second-year Software Engineering student at Delhi Technological University, and I'm reaching out about AI/ML intern opportunities at {co_name}."). Connect your interest directly to the target company's mission/product.
  3. PARAGRAPH 2 & 3 (VALUE PROP): Pitch 2-3 of your strongest matching projects from the candidate brief in a narrative style (not bullet points). Cite specific technical details and concrete metrics (e.g. rate-limiting, token-buckets, latency reductions, accuracy percentages) to show real engineering depth.
  4. PARAGRAPH 4 (ASK): Express specific interest in the scale/challenges of the company and make a clear request for a brief chat or opportunity to discuss further.
- Word count: Aim for 200–350 words in the body paragraphs.
- BANNED filler phrases: "passionate about", "excited to", "highly motivated", "quick learner", "team player", "I believe I can", "I feel I would be a great fit", "demonstrate", "showcase"
- Do NOT generate any sign-off block or contact details (e.g. "Thanks for your time", your name, email, phone, links) at the end. Just stop after paragraph 4.

Return ONLY: {{"subject": "...", "body": "..."}}
"""
    request = {
        "model": model or provider.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    chat_kwargs: dict = {}
    if thinking is not None:
        chat_kwargs["thinking"] = thinking
    if reasoning_effort and reasoning_effort != "none":
        chat_kwargs["reasoning_effort"] = reasoning_effort
    if chat_kwargs:
        request["extra_body"] = {"chat_template_kwargs": chat_kwargs}

    if timeout_s is not None and timeout_s > 0:
        request["timeout"] = timeout_s

    # Apply pacing per-provider
    with provider.lock:
        now = time.time()
        elapsed = now - provider.last_inference_time
        if elapsed < 2.5:
            time.sleep(2.5 - elapsed)
        provider.last_inference_time = max(provider.last_inference_time, time.time())

    try:
        response = provider.client.chat.completions.create(**request)
    except Exception as e:
        # Some providers/models may not support response_format / chat_template_kwargs
        err = str(e).lower()
        can_retry_without_flags = False
        if "response_format" in err and "response_format" in request:
            request.pop("response_format", None)
            can_retry_without_flags = True
        if any(k in err for k in ["chat_template_kwargs", "reasoning_effort", "thinking", "extra_body"]) and "extra_body" in request:
            request.pop("extra_body", None)
            can_retry_without_flags = True

        if can_retry_without_flags:
            response = provider.client.chat.completions.create(**request)
        else:
            raise

    raw = _extract_content_text(response)
    if not raw:
        reason = response.choices[0].finish_reason
        reasoning = getattr(response.choices[0].message, "reasoning", None)
        if reason == "length" and reasoning:
            raise ValueError("Model returned empty content (reasoning consumed tokens before final answer)")
        raise ValueError("Model returned empty content")

    parsed = _parse_email_json(raw)
    
    # Override subject with professional preset
    if OVERRIDE_WITH_PRESET_SUBJECTS:
        parsed["subject"] = get_preset_subject(co_email)
    
    # Programmatically append the fixed sign-off block
    sign_off = (
        "\n\nGitHub: github.com/zibranxo\n"
        "Resume: attached\n\n"
        "Thanks for your time,\n"
        "Arnav Sagar\n"
        "+91-6284962948"
    )
    parsed["body"] = parsed["body"].strip() + sign_off
    return parsed


def _template_fallback_email(company: dict) -> dict:
    """Return a template-based email when LLM generation completely fails."""
    if OVERRIDE_WITH_PRESET_SUBJECTS:
        subject = get_preset_subject(company["Email"])
    else:
        subject = "Internship Application — Arnav Sagar (DTU) — AI/ML Engineering"
    name = company.get("Name", "") or "Hiring Team"
    body = EMAIL_TEMPLATE.format(name=name, company=company["Company"])
    log.warning(f"  {company['Company']}: Using template fallback (LLM generation failed)")
    return {"subject": subject, "body": body, "template": True}


def _is_fatal_llm_error(e: Exception) -> bool:
    """Determine if an LLM API error is fatal (meaning retrying will not resolve it)."""
    err_type = type(e).__name__
    if err_type in ("BadRequestError", "AuthenticationError", "PermissionDeniedError", "NotFoundError"):
        return True
    
    msg = str(e).lower()
    if any(k in msg for k in ["api key", "unauthorized", "bad request", "invalid API key", "not found"]):
        return True
        
    return False


def generate_email_with_retry(
    about_me: str,
    company: dict,
    max_tokens: int,
    timeout_s: float | None,
    max_retries: int,
    backoff_base: float,
    temperature: float,
    top_p: float,
    thinking: bool | None,
    reasoning_effort: str,
    company_context: str = "",
) -> dict:
    last_error = None
    attempts = max_retries + 1

    for attempt in range(1, attempts + 1):
        provider = get_active_provider()
        try:
            return generate_email(
                provider,
                about_me,
                company,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                model=provider.model,
                temperature=temperature,
                top_p=top_p,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                company_context=company_context,
            )
        except Exception as e:
            last_error = e
            error_msg = str(e).lower()

            if "429" in error_msg or "too many requests" in error_msg:
                log.warning(f"  [{company['Company']}] 429 Rate Limit hit on {provider.name}. Quarantining provider for {LLM_QUARANTINE_S:.0f}s and switching...")
                provider.exhausted_until = time.time() + LLM_QUARANTINE_S
                continue

            if _is_fatal_llm_error(e):
                log.error(f"  Fatal LLM error for {company['Company']}: {e}. Aborting retries.")
                break

            # Empty content / reasoning failure → try fallback model immediately
            if provider.fallback_model and provider.fallback_model != provider.model:
                if any(k in error_msg for k in ["reasoning consumed tokens", "empty content", "none", "timed out", "timeout"]):
                    log.warning(
                        f"  {company['Company']}: primary model failed; trying fallback '{provider.fallback_model}' on {provider.name}"
                    )
                    try:
                        return generate_email(
                            provider,
                            about_me,
                            company,
                            max_tokens=max_tokens,
                            timeout_s=timeout_s,
                            model=provider.fallback_model,
                            temperature=temperature,
                            top_p=top_p,
                            thinking=thinking,
                            reasoning_effort=reasoning_effort,
                            company_context=company_context,
                        )
                    except Exception as fallback_e:
                        last_error = fallback_e
                        log.warning(f"  {company['Company']}: fallback also failed: {fallback_e}")

            # JSON parse failure can sometimes be fixed with simpler prompt on fallback
            if provider.fallback_model and provider.fallback_model != provider.model and "json" in error_msg:
                log.warning(
                    f"  {company['Company']}: JSON parse failed; retrying with fallback '{provider.fallback_model}' on {provider.name} (no JSON format)"
                )
                try:
                    return generate_email(
                        provider,
                        about_me,
                        company,
                        max_tokens=max_tokens,
                        timeout_s=timeout_s,
                        model=provider.fallback_model,
                        temperature=0.1,  # Lower temperature for more deterministic output
                        top_p=0.9,
                        thinking=False,
                        reasoning_effort="none",
                        company_context=company_context,
                    )
                except Exception as fallback_e:
                    last_error = fallback_e

            if attempt == attempts:
                break
            # Exponential backoff with jitter and max delay
            base_delay = backoff_base * (2 ** (attempt - 1))
            # Apply jitter: random factor between 0.5 and 1.5 to prevent thundering herd
            jittered_delay = base_delay * (0.5 + random.random())
            # Cap at maximum delay to prevent excessively long waits
            max_delay = LLM_MAX_BACKOFF_S
            sleep_s = min(jittered_delay, max_delay)
            log.warning(
                f"  Generation retry {attempt}/{max_retries} for {company['Company']}: {e} | sleeping {sleep_s:.1f}s"
            )
            time.sleep(sleep_s)

    if last_error:
        raise last_error
    raise ValueError("generate_email_with_retry failed but last_error was None")

LOW_QUALITY_QUEUE: Path = None  # Set to RUN_DIR / "low_quality_queue.json" in main


def calculate_quality_score(subject: str, body: str, company: dict, company_context: str = "") -> int:
    """Calculate pre-send quality gate score (0-100) for a generated email."""
    score = 0
    body_lower = body.lower()
    words = body.split()
    
    # 1. Subject length and Word count sanity (10 points total)
    if len(subject) <= EMAIL_MAX_SUBJECT_LEN:
        score += 5
    if 50 <= len(words) <= 500:
        score += 5
        
    # 2. Relevance (35 points total)
    co_name = company.get("Company", "").lower()
    if co_name and co_name in body_lower:
        score += 20
        
    # Match keywords from scraped company context
    context_matched = False
    if company_context:
        # Clean and tokenize company context
        context_tokens = set(re.findall(r'[a-z]{4,}', company_context.lower()))
        # Exclude very common words
        stopwords = {"about", "other", "their", "there", "would", "could", "should", "these", "those", "which", "where", "while", "during", "under", "above", "through", "company", "services", "solutions", "technology", "platform", "systems", "products"}
        meaningful_tokens = context_tokens - stopwords
        if meaningful_tokens:
            if any(token in body_lower for token in meaningful_tokens):
                context_matched = True
    elif co_name: # Fallback for testing when context is not provided
        context_matched = True
        
    if context_matched:
        score += 15
        
    # 3. Personalization (25 points)
    # Common keywords from about_me that match DTU BTech profile (including CLASP & Regavis)
    keywords = ["llm", "rag", "safety", "threat", "classification", "yolo", "safety shield", "bert", "roberta", "xgboost", "faiss", "proxy", "deepfake", "verification", "accents", "latency"]
    matched_keywords = [kw for kw in keywords if kw in body_lower]
    if matched_keywords:
        score += 25
        
    # 4. Tone (15 points)
    stiff_phrases = ["dear hiring manager", "respected sir", "dear sir", "kind perusal", "please find attached my cv"]
    if not any(phrase in body_lower for phrase in stiff_phrases):
        score += 15
        
    # 5. Spam likelihood (15 points)
    spam_triggers = ["100%", "guaranteed", "urgent", "!!!", "free", "no risk", "earn"]
    has_spam = any(trigger in body_lower for trigger in spam_triggers)
    has_excessive_caps = any(w.isupper() for w in words if len(w) > 4 and w.isalpha())
    if not has_spam and not has_excessive_caps:
        score += 15
        
    return score


def log_low_quality(company: dict, email_data: dict, score: int):
    """Log low quality email drafts to a local JSON file for review."""
    queue = []
    if LOW_QUALITY_QUEUE.exists():
        try:
            queue = json.loads(LOW_QUALITY_QUEUE.read_text())
        except Exception:
            pass
    queue.append({
        "company": company["Company"],
        "email": company["Email"],
        "subject": email_data.get("subject"),
        "body": email_data.get("body"),
        "score": score,
        "timestamp": datetime.now().isoformat()
    })
    try:
        LOW_QUALITY_QUEUE.write_text(json.dumps(queue, indent=2))
    except Exception as e:
        log.error(f"Failed to log low quality email to queue: {e}")


def generate_and_gate_email(
    about_me: str,
    company: dict,
    max_tokens: int,
    timeout_s: float | None,
    max_retries: int,
    backoff_base: float,
    temperature: float,
    top_p: float,
    thinking: bool | None,
    reasoning_effort: str,
    min_quality_score: int = 70,
    company_context: str = "",
    variant_count: int = 1,
) -> dict:
    """Generate email(s), run through quality gate, pick best or regenerate, otherwise fall back."""
    
    if variant_count <= 1:
        # Standard single generation path
        result = generate_email_with_retry(
            about_me, company, max_tokens, timeout_s, max_retries,
            backoff_base, temperature, top_p, thinking, reasoning_effort, company_context
        )
        if result.get("template"):
            return result
            
        score = calculate_quality_score(result["subject"], result["body"], company, company_context=company_context)
        result["quality_score"] = score
        
        if score >= min_quality_score:
            return result
    else:
        # Multi-variant generation path
        log.info(f"  {company['Company']}: Generating {variant_count} variants in parallel...")
        variants = []
        
        def _gen_var(i):
            var_temp = min(1.0, temperature + (i * 0.1))
            try:
                res = generate_email_with_retry(
                    about_me, company, max_tokens, timeout_s, max_retries,
                    backoff_base, var_temp, top_p, thinking, reasoning_effort, company_context
                )
                if not res.get("template"):
                    sc = calculate_quality_score(res["subject"], res["body"], company, company_context=company_context)
                    res["quality_score"] = sc
                    return res
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=variant_count) as executor:
            futures = [executor.submit(_gen_var, i) for i in range(variant_count)]
            for future in as_completed(futures):
                res = future.result()
                if res:
                    variants.append(res)
                    
        if variants:
            best_variant = max(variants, key=lambda x: x.get("quality_score", 0))
            if best_variant.get("quality_score", 0) >= min_quality_score:
                log.info(f"  {company['Company']}: Best variant scored {best_variant.get('quality_score')}")
                return best_variant
            
            result = best_variant
            score = best_variant.get("quality_score", 0)
        else:
            return _template_fallback_email(company)
            
    log.warning(f"  {company['Company']}: Draft quality score low ({score}/{min_quality_score}). Attempting regeneration with strict prompt...")
    
    try:
        provider = get_active_provider()
        strict_result = generate_email(
            provider,
            about_me,
            company,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            model=provider.model,
            temperature=0.1,
            top_p=0.9,
            thinking=False,
            reasoning_effort="none",
            company_context=company_context,
        )
        strict_score = calculate_quality_score(strict_result["subject"], strict_result["body"], company, company_context=company_context)
        strict_result["quality_score"] = strict_score
        
        if strict_score >= min_quality_score:
            log.info(f"  {company['Company']}: Regeneration successful. New score: {strict_score}")
            return strict_result
        else:
            log.warning(f"  {company['Company']}: Regenerated draft also low quality ({strict_score}). Quarantining and using static template.")
            log_low_quality(company, strict_result, strict_score)
    except Exception as e:
        log.warning(f"  {company['Company']}: Regeneration failed with error: {e}. Quarantining original low-quality draft.")
        log_low_quality(company, result, score)
        
    return _template_fallback_email(company)


# ── Rich terminal UI helpers ───────────────────────────────────────────────────────────

def print_banner(run_dir: Path, dry_run: bool, company_count: int, model: str,
                 sender_count: int, resume: bool):
    """Print a minimal startup header."""
    mode_text = "[bold dim]DRY RUN[/]" if dry_run else "[bold dim]LIVE SEND[/]"
    resume_text = " [dim]·[/] [bold dim]RESUME[/]" if resume else ""

    console.print()
    console.print(f"  [bold white]Auto Mailer[/] [dim]v2[/]  [dim]·[/]  {mode_text}{resume_text}")
    console.print(f"  [dim]dir:[/] {run_dir}  [dim]·[/]  [dim]model:[/] {model}  [dim]·[/]  [dim]targets:[/] {company_count}")
    console.print()


def print_preview(company: dict, subject: str, body: str, idx: int, total: int):
    """Minimal text-based email preview."""
    co_name  = company["Company"]
    co_email = company["Email"]
    score    = company.get("contact_score", "N/A")
    score_str = f"{score}/10" if isinstance(score, (int, float)) else str(score)
    q_score  = company.get("quality_score", "N/A")

    console.print(f"\n  [dim]┌─[/] [bold white]{idx}/{total}[/] [bold white]{co_name}[/] [dim]→ {co_email}[/]")
    console.print(f"  [dim]│[/]  [dim]Score:[/] {score_str}  [dim]·[/]  [dim]Quality:[/] {q_score}")
    console.print(f"  [dim]│[/]  [dim]Subject:[/] [white]{subject}[/]")
    console.print(f"  [dim]│[/]")
    
    # Indent body
    for line in body.split("\n"):
        console.print(f"  [dim]│[/]  [dim]{line}[/]")
        
    console.print(f"  [dim]└─[/]")


def print_send_status(co_name: str, to_addr: str, sent_ok: bool, dry_run: bool):
    """Print a compact per-email send result line."""
    if dry_run:
        status = "[dim yellow]skip[/]"
    elif sent_ok:
        status = "[dim green]sent[/]"
    else:
        status = "[dim red]fail[/]"

    console.print(f"     {status} [dim]·[/] [white]{co_name}[/] [dim]→ {to_addr}[/]")


def print_summary(success_count: int, fail_count: int, stats: dict, dry_run: bool,
                  run_dir: Path, report_path: str = ""):
    """Minimal final summary."""
    console.print()
    console.print(f"  [bold white]Run complete[/]")
    
    if dry_run:
        console.print("  [dim]Dry run — no emails were actually sent.[/]")
        
    console.print(f"  [dim]Sent:[/]      [white]{stats.get('send_success', success_count)}[/]")
    if fail_count > 0:
        console.print(f"  [dim]Failed:[/]    [red]{stats.get('send_failed', fail_count)}[/]")
    if stats.get('skipped_sent', 0) > 0:
        console.print(f"  [dim]Skipped:[/]   [white]{stats.get('skipped_sent', 0)}[/]")
        
    console.print(f"  [dim]Generated:[/] [white]{stats.get('generation_success', 0)}[/]")
    
    console.print()
    console.print(f"  [dim]dir:[/] {run_dir}")
    if report_path:
        console.print(f"  [dim]report:[/] {report_path}")
    console.print()



def generate_run_report(stats: dict, failures: list, emails_attempted: list, args):
    """Generate structured JSON and markdown reports of the mailing run."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = RUN_DIR / f"run_report_{timestamp}.json"
    summary_filename = RUN_DIR / f"run_report_{timestamp}.md"
    
    report_data = {
        "run_id": timestamp,
        "start_time": stats.get("start_time"),
        "end_time": datetime.now().isoformat(),
        "config": {
            "dry_run": args.dry_run,
            "limit": args.limit,
            "workers": args.workers,
            "min_contact_score": args.min_contact_score,
            "min_quality_score": args.min_quality_score
        },
        "statistics": {
            "total_processed": stats.get("total_processed", 0),
            "generation_success": stats.get("generation_success", 0),
            "generation_failed": stats.get("generation_failed", 0),
            "send_success": stats.get("send_success", 0),
            "send_failed": stats.get("send_failed", 0),
            "skipped_sent": stats.get("skipped_sent", 0),
            "skipped_low_score": stats.get("skipped_low_score", 0),
            "skipped_invalid_email": stats.get("skipped_invalid_email", 0),
            "skipped_duplicate_email": stats.get("skipped_duplicate_email", 0)
        },
        "failures": failures,
        "emails": emails_attempted
    }
    
    # Write JSON report
    try:
        report_filename.write_text(json.dumps(report_data, indent=2))
        log.info(f"Structured JSON run report written to {report_filename}")
    except Exception as e:
        log.error(f"Failed to write JSON run report: {e}")
        
    # Write Markdown report
    md_content = f"""# Run Report Summary ({timestamp})

## Parameters
- **Dry Run**: {args.dry_run}
- **Limit**: {args.limit}
- **Min Contact Score**: {args.min_contact_score}
- **Min Quality Score**: {args.min_quality_score}

## Statistics
| Metric | Count |
| :--- | :--- |
| **Total Processed** | {stats.get("total_processed", 0)} |
| **Generation Success** | {stats.get("generation_success", 0)} |
| **Generation Failed** | {stats.get("generation_failed", 0)} |
| **Send Success** | {stats.get("send_success", 0)} |
| **Send Failed** | {stats.get("send_failed", 0)} |
| **Skipped (Already Sent)** | {stats.get("skipped_sent", 0)} |
| **Skipped (Low Score)** | {stats.get("skipped_low_score", 0)} |
| **Skipped (Invalid Email)** | {stats.get("skipped_invalid_email", 0)} |
| **Skipped (CSV Duplicates)** | {stats.get("skipped_duplicate_email", 0)} |

## Failures
"""
    if failures:
        md_content += "\n| Company | Email | Stage | Error |\n| :--- | :--- | :--- | :--- |\n"
        for fail in failures:
            md_content += f"| {fail['company']} | {fail['email']} | {fail['stage']} | {fail['error_message']} |\n"
    else:
        md_content += "\n*No failures during this run.*\n"
        
    try:
        summary_filename.write_text(md_content)
        log.info(f"Markdown summary report written to {summary_filename}")
    except Exception as e:
        log.error(f"Failed to write Markdown summary report: {e}")


def load_checkpoint() -> Optional[dict]:
    """Load checkpoint if it exists and is valid."""
    if CHECKPOINT_FILE and CHECKPOINT_FILE.exists():
        try:
            data = json.loads(CHECKPOINT_FILE.read_text())
            return data
        except Exception as e:
            log.warning(f"Failed to load checkpoint: {e}")
    return None


def save_checkpoint(last_idx: int, generated_cache: dict, sent_log_snapshot: dict):
    """Save run state checkpoint to file."""
    if not CHECKPOINT_FILE:
        return
    try:
        checkpoint_data = {
            "last_processed_index": last_idx,
            "generated_cache_by_email": generated_cache,
            "sent_log_snapshot": sent_log_snapshot,
            "timestamp": datetime.now().isoformat()
        }
        _atomic_write(CHECKPOINT_FILE, json.dumps(checkpoint_data, indent=2))
        log.debug(f"Saved checkpoint")
    except Exception as e:
        log.error(f"Failed to save checkpoint: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Automated job application emailer")
    parser.add_argument("--dry-run",        action="store_true",
                        help="Generate and preview emails without sending")
    parser.add_argument("--limit",          type=int,  default=None,
                        help="Max number of companies to process")

    parser.add_argument("--skip-sent",      action="store_true", default=True,
                        help="Skip companies already emailed (default: on)")
    parser.add_argument("--no-skip-sent",   action="store_false", dest="skip_sent",
                        help="Re-send even to already-emailed companies")
    parser.add_argument("--workers",        type=int, default=1,
                        help="Parallel LLM generation workers (default: 1)")
    parser.add_argument("--llm-timeout",    type=float, default=LLM_TIMEOUT_S,
                        help=f"Per-request timeout in seconds for LLM call (default: {LLM_TIMEOUT_S} from env)")
    parser.add_argument("--llm-retries",    type=int, default=LLM_RETRIES,
                        help=f"Retries per LLM request on failure (default: {LLM_RETRIES} from env)")
    parser.add_argument("--llm-backoff",    type=float, default=LLM_BACKOFF_S,
                        help=f"Base backoff seconds for retries (default: {LLM_BACKOFF_S} from env)")
    parser.add_argument("--max-tokens",     type=int, default=GEN_MAX_TOKENS,
                        help=f"Max tokens for generated email (default: {GEN_MAX_TOKENS} from env)")
    parser.add_argument("--temperature",    type=float, default=LLM_TEMPERATURE,
                        help=f"LLM temperature (default: {LLM_TEMPERATURE} from env)")
    parser.add_argument("--top-p",          type=float, default=LLM_TOP_P,
                        help=f"LLM top_p (default: {LLM_TOP_P} from env)")
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=LLM_THINKING,
                        help=f"Enable/disable model thinking mode (default from env: {LLM_THINKING})")
    parser.add_argument("--reasoning-effort", type=str, default=LLM_REASONING_EFFORT,
                        choices=["none", "low", "medium", "high"],
                        help=f"Reasoning effort level (default: {LLM_REASONING_EFFORT} from env)")
    parser.add_argument("--min-contact-score", type=int, default=MIN_CONTACT_SCORE,
                        help=f"Minimum contact score to process (0-10, default: {MIN_CONTACT_SCORE} from env)")
    parser.add_argument("--min-quality-score", type=int, default=MIN_QUALITY_SCORE,
                        help=f"Minimum email quality score to allow sending (0-100, default: {MIN_QUALITY_SCORE} from env)")
    parser.add_argument("--slowmode", action="store_true",
                        help="Enforce a strict 30 requests/minute LLM rate limit")
    parser.add_argument("--resume", nargs="?", const="", default=None,
                        help="Resume from a run folder. E.g., --resume runs/2026-06-15. Resumes latest if no folder specified.")
    parser.add_argument("--business-hours", action="store_true",
                        help="Only send emails between 9 AM and 5 PM on weekdays")
    parser.add_argument("--company-research", action="store_true",
                        help="Enable website scraping for company context")
    parser.add_argument("--variant-count", type=int, default=VARIANT_COUNT,
                        help=f"Number of email variants to generate and score per company (default: {VARIANT_COUNT} from env)")
    parser.add_argument("--check-bounces", action="store_true",
                        help="Check IMAP for bounce messages to automatically skip them")
    parser.add_argument("--check-mx", action="store_true",
                        help="Verify DNS MX records before generating/sending (default: disabled)")
    parser.add_argument("--health-port", type=int, default=0,
                        help="Port to run a background health check HTTP server on (default 0=disabled)")
    parser.add_argument("--folder", type=str, default=None,
                        help="Specify a folder containing the target CSV and where run logs/reports will be saved")
    args = parser.parse_args()

    start_time_iso = datetime.now().isoformat()
    stats = {
        "start_time": start_time_iso,
        "total_processed": 0,
        "generation_success": 0,
        "generation_failed": 0,
        "send_success": 0,
        "send_failed": 0,
        "skipped_sent": 0,
        "skipped_low_score": 0,
        "skipped_invalid_email": 0,
        "skipped_duplicate_email": 0
    }
    failures = []
    emails_attempted = []

    # ── Setup Run Environment ─────────────────────────────────────────────────
    global RUN_DIR, LOG_FILE, CHECKPOINT_FILE, STATUS_LOG, LOW_QUALITY_QUEUE
    global CSV_FILE, SENT_LOG, BOUNCED_LOG, COMPANY_CACHE, GEN_CACHE, _company_cache, log

    if args.folder:
        folder_dir = Path(args.folder.strip())
        folder_dir.mkdir(parents=True, exist_ok=True)
        
        # Override global paths to be folder-specific
        csv_path = folder_dir / "hr_emails_directory.csv"
        if not csv_path.exists():
            csv_files = list(folder_dir.glob("*.csv"))
            if csv_files:
                csv_path = csv_files[0]
        CSV_FILE = csv_path
        
        SENT_LOG      = folder_dir / "sent_log.json"
        BOUNCED_LOG   = folder_dir / "bounced_log.json"
        COMPANY_CACHE = folder_dir / "company_context_cache.json"
        GEN_CACHE     = folder_dir / "generation_cache.json"
        
        runs_base = folder_dir / "runs"
    else:
        runs_base = BASE_DIR / "runs"

    if COMPANY_CACHE.exists():
        try:
            _company_cache = json.loads(COMPANY_CACHE.read_text(encoding="utf-8"))
            log.info(f"Loaded {len(_company_cache)} cached company contexts from {COMPANY_CACHE.name}")
        except Exception as e:
            log.warning(f"Failed to load company cache: {e}")
            _company_cache = {}
    else:
        _company_cache = {}

    runs_base.mkdir(exist_ok=True)

    if args.resume is not None:
        if isinstance(args.resume, str) and args.resume.strip():
            run_dir = Path(args.resume.strip())
            if not run_dir.exists():
                if (runs_base / run_dir).exists():
                    run_dir = runs_base / run_dir
                else:
                    raise FileNotFoundError(f"Resume directory not found: {args.resume}")
        else:
            dirs = [d for d in runs_base.iterdir() if d.is_dir()]
            if not dirs:
                raise FileNotFoundError("No previous runs found to resume from.")
            run_dir = max(dirs, key=lambda d: d.name)
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        run_dir = runs_base / today
        run_dir.mkdir(exist_ok=True)

    RUN_DIR = run_dir
    LOG_FILE = run_dir / "mailer.log"
    CHECKPOINT_FILE = run_dir / "checkpoint.json"
    STATUS_LOG = run_dir / "status.log"
    LOW_QUALITY_QUEUE = run_dir / "low_quality_queue.json"

    # ── Split logging: RichHandler for console, plain FileHandler for log file ───
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)

    # File handler — plain timestamped text for archiving
    _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
    root_logger.addHandler(_fh)

    # Console handler — Rich formatted, colorful
    _rh = RichHandler(
        console=console,
        rich_tracebacks=True,
        show_path=False,
        markup=True,
        log_time_format="[%H:%M:%S]",
    )
    _rh.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(_rh)

    # Silence noisy third-party loggers on console (keep in file)
    for _noisy in ("httpx", "openai", "urllib3", "requests"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    # Rebind module logger
    log = logging.getLogger(__name__)

    log.info(f"Using run directory: [bold]{RUN_DIR}[/]")

    # ── Validate env & Load Senders ───────────────────────────────────────────
    _senders_pool.clear()
    p_email = os.getenv("SENDER_EMAIL")
    p_pass = os.getenv("SENDER_APP_PASSWORD")
    if p_email and p_pass:
        _senders_pool.append(SenderAccount(name=SENDER_NAME, email=p_email, password=p_pass))
    
    for i in range(2, 11):
        s_email = os.getenv(f"SENDER_{i}_EMAIL")
        s_pass = os.getenv(f"SENDER_{i}_APP_PASSWORD")
        if s_email and s_pass:
            _senders_pool.append(SenderAccount(name=SENDER_NAME, email=s_email, password=s_pass))

    missing = []
    if not LLM_API_KEY:   missing.append("LLM_API_KEY")
    if not args.dry_run and not _senders_pool:
        missing.append("SENDER_EMAIL and SENDER_APP_PASSWORD")
    if missing:
        log.error(f"Missing env vars: {', '.join(missing)}. Set them in .env")
        raise SystemExit(1)

    if not args.dry_run:
        log.info(f"Loaded {len(_senders_pool)} sender accounts for load balancing.")

    if args.health_port > 0:
        def run_health_server():
            class HealthHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "ok", "stats": stats}).encode())
            try:
                server = HTTPServer(("localhost", args.health_port), HealthHandler)
                log.info(f"Health check server running on port {args.health_port}")
                server.serve_forever()
            except Exception as e:
                log.error(f"Health check server failed: {e}")
                
        threading.Thread(target=run_health_server, daemon=True).start()

    bounces = set()
    if args.check_bounces and _senders_pool:
        primary = _senders_pool[0]
        bounces = set(sync_bounces(primary.email, primary.password))
    else:
        bounces = load_bounces()

    # ── Load data ─────────────────────────────────────────────────────────────
    about_me  = load_about_me()
    companies = load_companies()

    # ── Validate emails and remove duplicates ─────────────────────────────────────
    valid_companies = []
    seen_emails = set()
    invalid_email_count = 0
    duplicate_email_count = 0

    for company in companies:
        raw_email = company["Email"].strip().lower()

        # Try to extract the first valid email address from the field (handles multiple emails / notes)
        match = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", raw_email)
        if match:
            email = match.group(0)
            company["Email"] = email
        else:
            email = raw_email

        # Validate email format
        if not is_valid_email(email):
            invalid_email_count += 1
            log.warning(f"Invalid email format, skipping: {company['Company']} -> {raw_email}")
            continue
            
        domain = email.split("@")[1]
        if args.check_mx:
            if not has_valid_mx_record(domain):
                log.warning(f"No valid MX record detected for {domain}, but proceeding anyway: {company['Company']} -> {email}")

        # Check for duplicates within CSV
        if email in seen_emails:
            duplicate_email_count += 1
            log.warning(f"Duplicate email within CSV, skipping: {company['Company']} -> {email}")
            continue

        if email in bounces:
            invalid_email_count += 1
            log.warning(f"Email historically bounced, skipping: {company['Company']} -> {email}")
            continue

        seen_emails.add(email)
        valid_companies.append(company)

    if invalid_email_count > 0:
        log.info(f"Skipped {invalid_email_count} companies with invalid email format")
    if duplicate_email_count > 0:
        log.info(f"Skipped {duplicate_email_count} duplicate email entries within CSV")

    stats["skipped_invalid_email"] = invalid_email_count
    stats["skipped_duplicate_email"] = duplicate_email_count

    companies = valid_companies
    sent_log  = load_sent_log()

    # ── Contact scoring and filtering ────────────────────────────────────────
    scored_companies = []
    for company in companies:
        score = calculate_contact_score(company, sent_log)
        company["contact_score"] = score  # Attach score to company dict for logging
        if score >= args.min_contact_score:
            scored_companies.append(company)
        else:
            log.debug(f"Low contact score ({score}/10), skipping: {company['Company']} -> {company['Email']}")

    stats["skipped_low_score"] = len(companies) - len(scored_companies)

    # Sort companies by contact score descending (highest priority first)
    scored_companies.sort(key=lambda x: x.get("contact_score", 0), reverse=True)

    if args.min_contact_score > 0:
        original_count = len(companies)
        filtered_count = len(scored_companies)
        log.info(f"Contact score filtering: {original_count} -> {filtered_count} companies "
                f"(min score: {args.min_contact_score})")

    companies = scored_companies

    if args.skip_sent:
        before = len(companies)
        companies = [c for c in companies if c["Email"].strip().lower() not in sent_log]
        skipped = before - len(companies)
        stats["skipped_sent"] = skipped
        if skipped:
            log.info(f"Skipping {skipped} already-sent companies.")

    if args.limit:
        companies = companies[:args.limit]

    stats["total_processed"] = len(companies)

    generated = {}
    if args.resume is not None:
        checkpoint = load_checkpoint()
        if checkpoint:
            generated = checkpoint.get("generated_cache_by_email", {})
            snapshot = checkpoint.get("sent_log_snapshot", {})
            if snapshot:
                # Normalize keys
                normalized_snapshot = {k.strip().lower(): v for k, v in snapshot.items() if k}
                sent_log.update(normalized_snapshot)
            log.info(f"Resuming run (loaded {len(generated)} cached generations)")

    if not companies:
        log.info("No companies to process.")
        return

    # Calculate average contact score for logging
    if companies:
        avg_score = sum(c.get("contact_score", 0) for c in companies) / len(companies)
        score_info = f"  |  avg_contact_score={avg_score:.1f}/10"
    else:
        score_info = ""

    log.info(
        f"Processing {len(companies)} companies  |  dry_run={args.dry_run}  |  model={LLM_MODEL}"
        f"  |  fallback={LLM_FALLBACK_MODEL or 'none'}"
        f"  |  temp={args.temperature} top_p={args.top_p}"
        f"  |  thinking={args.thinking} reasoning_effort={args.reasoning_effort}"
        f"{score_info}"
    )

    # ── Confirm before live send ──────────────────────────────────────────────
    if not args.dry_run:
        console.print(f"  [dim]About to send[/] [bold white]{len(companies)}[/] [dim]real emails[/]")
        confirm = console.input("  [dim]Type 'yes' to proceed:[/] ").strip().lower()
        if confirm != "yes":
            log.info("Aborted by user.")
            return
        console.print()

    # ── Multi-Provider Initialization ─────────────────────────────────────────
    global _provider_pool
    _provider_pool.clear()

    if LLM_API_KEY:
        _provider_pool.append(LLMProvider(
            name="Primary",
            client=OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, max_retries=0),
            model=LLM_MODEL,
            fallback_model=LLM_FALLBACK_MODEL
        ))
        
    for i in range(2, 11):
        key = os.getenv(f"LLM_{i}_API_KEY")
        if key:
            _provider_pool.append(LLMProvider(
                name=f"Provider_{i}",
                client=OpenAI(api_key=key, base_url=os.getenv(f"LLM_{i}_BASE_URL"), max_retries=0),
                model=os.getenv(f"LLM_{i}_MODEL", LLM_MODEL),
                fallback_model=os.getenv(f"LLM_{i}_FALLBACK_MODEL", LLM_FALLBACK_MODEL)
            ))
            
    if not _provider_pool:
        log.error("No LLM providers configured. Please set LLM_API_KEY in your .env.")
        return
        
    log.info(f"Loaded [bold]{len(_provider_pool)}[/] LLM provider(s): {', '.join(p.name for p in _provider_pool)}")

    # ── Startup banner ─────────────────────────────────────────────────────
    print_banner(
        run_dir=RUN_DIR,
        dry_run=args.dry_run,
        company_count=len(companies),
        model=LLM_MODEL,
        sender_count=len(_senders_pool),
        resume=args.resume is not None,
    )
    # ── Generate (serial or parallel) ─────────────────────────────────────────────
    workers = max(1, args.workers)
    todo_companies = [c for c in companies if c["Email"].strip().lower() not in generated]
    console.print(Rule(f"[dim]Generating {len(todo_companies)} email(s)[/]", style="dim"))
    console.print()

    _gen_progress = Progress(
        SpinnerColumn(spinner_name="dots", style="dim"),
        TextColumn("[dim]{task.description}"),
        console=console,
        transient=True,
    )

    if workers > 1:
        log.info(f"Parallel generation enabled: workers={workers}")
        with _gen_progress as progress:
            gen_task = progress.add_task("Generating...", total=len(todo_companies))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                def process_company(company):
                    domain = company["Email"].split("@")[-1]
                    ctx = fetch_company_context(company["Company"], domain) if args.company_research else ""
                    return generate_and_gate_email(
                        about_me, company, args.max_tokens, args.llm_timeout,
                        args.llm_retries, args.llm_backoff, args.temperature,
                        args.top_p, args.thinking, args.reasoning_effort,
                        args.min_quality_score, company_context=ctx,
                        variant_count=args.variant_count,
                    )

                futures = {
                    pool.submit(process_company, company): company
                    for company in todo_companies
                }
                for future in as_completed(futures):
                    company = futures[future]
                    co_name  = company["Company"].strip()
                    to_addr  = company["Email"].strip()
                    email_key = to_addr.lower()
                    try:
                        generated[email_key] = future.result()
                        stats["generation_success"] += 1
                        progress.update(gen_task, advance=1,
                                        description=f"done  {co_name}")
                        save_checkpoint(0, generated, sent_log)
                    except Exception as e:
                        stats["generation_failed"] += 1
                        failures.append({"company": co_name, "email": to_addr,
                                         "stage": "generation", "error_message": str(e)})
                        log.error(f"Generation failed for {co_name}: {e}")
                        progress.update(gen_task, advance=1,
                                        description=f"error {co_name}")
                        save_checkpoint(0, generated, sent_log)
    else:
        with _gen_progress as progress:
            gen_task = progress.add_task("Generating...", total=len(todo_companies))
            for idx, company in enumerate(todo_companies, start=1):
                to_addr   = company["Email"].strip()
                email_key = to_addr.lower()
                co_name   = company["Company"].strip()
                progress.update(gen_task,
                                description=f"{co_name}",
                                advance=0)
                log.info(f"[{idx}/{len(todo_companies)}] Generating for [bold]{co_name}[/]")
                domain = to_addr.split("@")[-1]
                ctx = fetch_company_context(co_name, domain) if args.company_research else ""
                try:
                    generated[email_key] = generate_and_gate_email(
                        about_me, company, args.max_tokens, args.llm_timeout,
                        args.llm_retries, args.llm_backoff, args.temperature,
                        args.top_p, args.thinking, args.reasoning_effort,
                        args.min_quality_score, company_context=ctx,
                        variant_count=args.variant_count,
                    )
                    stats["generation_success"] += 1
                    progress.update(gen_task, advance=1,
                                    description=f"done  {co_name}")
                    save_checkpoint(0, generated, sent_log)
                except Exception as e:
                    stats["generation_failed"] += 1
                    failures.append({"company": co_name, "email": to_addr,
                                     "stage": "generation", "error_message": str(e)})
                    log.error(f"Generation failed for [bold]{co_name}[/]: {e}")
                    progress.update(gen_task, advance=1,
                                    description=f"error {co_name}")
                    save_checkpoint(0, generated, sent_log)

    # ── Preview + send (sequential) ─────────────────────────────────────────────
    success_count = 0
    fail_count = 0

    console.print()
    console.print(Rule("[dim]Sending[/]", style="dim"))
    console.print()

    for idx, company in enumerate(companies, start=1):
        to_addr = company["Email"].strip()
        co_name = company["Company"].strip()
        email_key = to_addr.lower()

        result = generated.get(email_key)
        if not result:
            fail_count += 1
            save_checkpoint(0, generated, sent_log)
            continue

        subject = result["subject"]
        body    = result["body"]
        company["quality_score"] = result.get("quality_score")

        print_preview(company, subject, body, idx, len(companies))

        if args.business_hours and not args.dry_run:
            while True:
                now = datetime.now()
                is_weekend = now.weekday() >= 5
                is_business_hour = 9 <= now.hour < 17
                if not is_weekend and is_business_hour:
                    break
                log.info("Outside business hours. Pausing SMTP delivery until next business window...")
                time.sleep(300)

        sent_ok = send_email(to_addr, subject, body, company_name=co_name, dry_run=args.dry_run)

        # Track email attempt
        email_entry = {
            "company": co_name,
            "email": to_addr,
            "subject": subject,
            "quality_score": result.get("quality_score"),
            "status": "sent" if sent_ok else "failed"
        }
        emails_attempted.append(email_entry)

        if sent_ok:
            status = "DRY-RUN" if args.dry_run else "SUCCESS"
            log.info(f"  {status}: {co_name}")
            log_status(status, f"{co_name} -> {to_addr}")
            if not args.dry_run:
                mark_sent(sent_log, to_addr, co_name)
            success_count += 1
            stats["send_success"] += 1
        else:
            log.warning(f"  FAILED: {co_name}")
            log_status("FAILED_SMTP", f"{co_name} -> {to_addr}")
            fail_count += 1
            stats["send_failed"] += 1
            failures.append({"company": co_name, "email": to_addr, "stage": "send", "error_message": "SMTP send failed"})

        # Save checkpoint after each processed index
        save_checkpoint(0, generated, sent_log)

        if not args.dry_run and idx < len(companies):
            log.info(f"  Waiting {RATE_LIMIT_S}s before next send...")
            time.sleep(RATE_LIMIT_S)

    # Generate run reports
    generate_run_report(stats, failures, emails_attempted, args)
    
    # ── Final summary ─────────────────────────────────────────────────────────
    print_summary(
        success_count=success_count,
        fail_count=fail_count,
        stats=stats,
        dry_run=args.dry_run,
        run_dir=RUN_DIR,
        report_path=str(RUN_DIR / "run_report_*.md")  # Will be exact path in real use
    )

    log.info("Checkpoint retained securely.")


if __name__ == "__main__":
    main()
