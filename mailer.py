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
import sys
import re
import hashlib
import random
import threading
from contextlib import contextmanager
import functools
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv
import imaplib
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
import dns.resolver

load_dotenv()

# Ensure Windows console can print Unicode safely
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
CSV_FILE     = BASE_DIR / "hr_emails_directory.csv"
ABOUT_ME     = BASE_DIR / "about_me.md"
RESUME_PDF   = BASE_DIR / "resume.pdf"
SENT_LOG     = BASE_DIR / "sent_log.json"
LOG_FILE     = BASE_DIR / "mailer.log"
GEN_CACHE    = BASE_DIR / "generation_cache.json"
CHECKPOINT_FILE = BASE_DIR / ".mailer_checkpoint.json"
BOUNCED_LOG  = BASE_DIR / "bounced_log.json"
COMPANY_CACHE = BASE_DIR / "company_context_cache.json"


# ── Config from .env ──────────────────────────────────────────────────────────
LLM_API_KEY   = os.getenv("LLM_API_KEY")
LLM_BASE_URL  = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
LLM_MODEL     = os.getenv("LLM_MODEL", "meta/llama-3.3-70b-instruct")
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "deepseek-ai/deepseek-v4-flash")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_TOP_P = float(os.getenv("LLM_TOP_P", "0.95"))
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low").strip().lower()

SENDER_EMAIL  = os.getenv("SENDER_EMAIL")
SENDER_PASS   = os.getenv("SENDER_APP_PASSWORD")   # Gmail app password
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))

SENDER_NAME   = os.getenv("SENDER_NAME", "Arnav")
RATE_LIMIT_S  = float(os.getenv("RATE_LIMIT_SECONDS", "8"))  # seconds between sends
GEN_MAX_TOKENS = int(os.getenv("GEN_MAX_TOKENS", "420"))

# SMTP connection pooling and rate limiting
_smtp_lock = threading.Lock()
_smtp_connection = None
_smtp_last_used = 0
_SMTP_CONNECTION_MAX_AGE = 300  # 5 minutes
_SMTP_CONNECTION_MAX_USES = 50  # Renew connection after this many sends
_smtp_uses_count = 0

# Adaptive rate limiting
_base_delay = RATE_LIMIT_S
_current_delay = RATE_LIMIT_S
_max_delay = 60.0  # Maximum delay between sends
_delay_multiplier = 1.5  # Increase delay after errors
_delay_decay_factor = 0.9  # Gradually decrease delay after success
_consecutive_successes = 0
_consecutive_errors = 0

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
    with _provider_pool_lock:
        while True:
            now = time.time()
            # Try to find a healthy provider
            for p in _provider_pool:
                if now >= p.exhausted_until:
                    return p
            
            # All providers are exhausted! Wait for the soonest one to recover.
            earliest_wake = min(p.exhausted_until for p in _provider_pool)
            sleep_time = earliest_wake - now
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


@contextmanager
def get_smtp_connection():
    """Context manager for SMTP connection with pooling."""
    global _smtp_connection, _smtp_last_used, _smtp_uses_count

    with _smtp_lock:
        # Check if we need to renew the connection
        now = time.time()
        if (_smtp_connection is None or
            (now - _smtp_last_used) > _SMTP_CONNECTION_MAX_AGE or
            _smtp_uses_count >= _SMTP_CONNECTION_MAX_USES):

            # Close existing connection if any
            if _smtp_connection is not None:
                try:
                    _smtp_connection.quit()
                except Exception:
                    pass  # Ignore errors on close
                _smtp_connection = None

            # Create new connection
            try:
                _smtp_connection = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
                _smtp_connection.ehlo()
                _smtp_connection.starttls()
                _smtp_connection.login(SENDER_EMAIL, SENDER_PASS)
                _smtp_last_used = now
                _smtp_uses_count = 0
                log.debug("SMTP connection established/renewed")
            except Exception as e:
                log.error(f"Failed to establish SMTP connection: {e}")
                raise

        _smtp_uses_count += 1
        try:
            yield _smtp_connection
        except Exception as e:
            # Mark connection as potentially bad on error
            log.warning(f"SMTP connection error: {e}")
            try:
                _smtp_connection.quit()
            except Exception:
                pass
            _smtp_connection = None
            raise


def update_rate_limit(success: bool, smtp_error: Optional[Exception] = None):
    """Update the rate limit delay based on success/error history."""
    global _current_delay, _consecutive_successes, _consecutive_errors

    if success:
        _consecutive_successes += 1
        _consecutive_errors = 0
        # Gradually decrease delay after successes, but not below base delay
        if _consecutive_successes > 5:  # Only after several successes
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

    msg = MIMEMultipart()
    msg["From"]    = f"{SENDER_NAME} <{SENDER_EMAIL}>"
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
    part.add_header(
        "Content-Disposition",
        f'attachment; filename="{SENDER_NAME}_Resume.pdf"',
    )
    msg.attach(part)

    if dry_run:
        return True  # pretend success

    # Apply intelligent rate limiting
    time.sleep(_current_delay)

    max_send_attempts = 2
    for attempt in range(1, max_send_attempts + 1):
        try:
            with get_smtp_connection() as server:
                server.sendmail(SENDER_EMAIL, to_addr, msg.as_string())

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
                log.error(f"Fatal SMTP error sending to {to_addr}: {e}")
                return False

            if attempt < max_send_attempts:
                log.warning(f"Temporary SMTP error sending to {to_addr} (attempt {attempt}/{max_send_attempts}): {e}. Retrying in 5 seconds...")
                time.sleep(5)
                # Invalidate SMTP connection to force reconnection
                global _smtp_connection
                with _smtp_lock:
                    if _smtp_connection is not None:
                        try:
                            _smtp_connection.quit()
                        except Exception:
                            pass
                        _smtp_connection = None
                continue
            else:
                update_rate_limit(False, e)
                log.error(f"SMTP error sending to {to_addr} after {max_send_attempts} attempts: {e}")
                return False



LLM_THINKING = _env_bool("LLM_THINKING", False)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ── Sent log helpers ──────────────────────────────────────────────────────────
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
    SENT_LOG.write_text(json.dumps(sent, indent=2))


def mark_sent(sent: dict, email: str, company: str):
    email_key = email.strip().lower()
    sent[email_key] = {"company": company, "sent_at": datetime.now().isoformat()}
    save_sent_log(sent)



# ── Generation cache helpers ──────────────────────────────────────────────────
def _cache_key(company: dict, about_me: str) -> str:
    """Create a stable hash key for generation cache."""
    raw = f"{company['Company']}:{company['Email']}:{company['Tag']}:{about_me[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def load_generation_cache() -> dict:
    if GEN_CACHE.exists():
        return json.loads(GEN_CACHE.read_text())
    return {}


def save_generation_cache(cache: dict):
    GEN_CACHE.write_text(json.dumps(cache, indent=2))


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
def load_companies(region_filter: str = None, tag_filter: str = None) -> list[dict]:
    if not CSV_FILE.exists():
        raise FileNotFoundError(f"CSV not found: {CSV_FILE}")
    rows = []
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if region_filter and row["Region"].strip().lower() != region_filter.lower():
                continue
            if tag_filter and row["Tag"].strip().lower() != tag_filter.lower():
                continue
            rows.append(row)
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

def fetch_company_context(company_name: str, domain: str) -> str:
    """Fetch website title and meta description for context injection."""
    if COMPANY_CACHE.exists():
        try:
            cache = json.loads(COMPANY_CACHE.read_text())
            if domain in cache and cache[domain]:
                return cache[domain]
        except Exception:
            cache = {}
    else:
        cache = {}
        
    url = f"https://{domain}"
    try:
        log.info(f"  Fetching web context for {company_name} from {url}...")
        resp = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title = soup.title.string if soup.title else ""
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        desc = meta_desc['content'] if meta_desc else ""
        
        context = f"Website Title: {title.strip()}\nDescription: {desc.strip()}"
        cache[domain] = context
        COMPANY_CACHE.write_text(json.dumps(cache, indent=2))
        return context
    except Exception as e:
        log.debug(f"Failed to fetch context for {company_name}: {e}")
        cache[domain] = ""
        COMPANY_CACHE.write_text(json.dumps(cache, indent=2))
        return ""



def calculate_contact_score(company: dict, sent_log: dict = None) -> int:
    """Calculate a contact score based on email quality, role relevance, and communications history (0-10)."""
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
    
    # Corporate indicators
    corporate_tlds = {'co', 'org', 'gov', 'edu', 'ac'}
    corporate_keywords = {'company', 'corp', 'inc', 'ltd', 'llc'}
    generic_domains = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com'}

    if (any(kw in domain_name for kw in corporate_keywords) or 
        any(tld in corporate_tlds for tld in tlds)):
        score += 2
    elif email_domain in generic_domains:
        score += 0  # Generic domains get no bonus
    else:
        score += 1  # Other domains (possibly custom) get partial score

    # Communication history check (penalty of -1 point if recently contacted)
    if sent_log and email in sent_log:
        score -= 1

    # Role quality from Note field (3 points)
    note = company.get("Note", "").lower()
    # High-value roles
    high_value_roles = ['hr', 'human resources', 'recruiter', 'recruitment', 'talent', 'hiring',
                       'manager', 'lead', 'director', 'head', 'chief']
    # Technical roles relevant for AI/ML
    tech_roles = ['engineer', 'developer', 'technical', 'tech', 'ai', 'ml', 'data', 'science']

    if any(role in note for role in high_value_roles):
        score += 3
    elif any(role in note for role in tech_roles):
        score += 2
    elif note:  # Has some note but not matching high-value patterns
        score += 1

    # Region preference bonus (1 point) - prioritizing local companies for internship
    region = company.get("Region", "").lower()
    if region == "india":  # Assuming user prefers India-based opportunities
        score += 1

    # Tag relevance bonus (2 points) - prioritizing AI/ML and related tags
    tag = company.get("Tag", "").lower()
    relevant_tags = ['ai/ml', 'ai', 'ml', 'artificial intelligence', 'machine learning',
                    'data', 'analytics', 'research', 'technology', 'software']

    if any(relevant_tag in tag for relevant_tag in relevant_tags):
        score += 2
    elif tag:  # Has some tag but not matching preferred ones
        score += 1

    # Ensure score is within bounds
    return max(0, min(10, score))


# ── NIM email generator ───────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a sharp career assistant helping a CS undergraduate write
cold job-application emails. Your emails are:
- Concise (max 180 words in the body)
- Genuine and specific to the target company, no generic filler
- Written in first person, professional but not stiff
- Structured: 1-line opener about the company, 2-3 lines on relevant projects/skills, 1-line ask
- No subject line inside body text
- No generic opener like 'Dear Hiring Team'
- End with a single clean sign-off line

You must return ONLY valid JSON with exactly these keys:
{"subject":"...","body":"..."}
No markdown. No code fences. No extra keys.
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
EMAIL_TEMPLATE = """Subject: {subject}

Dear Hiring Team at {company},

I hope this email finds you well. I am writing to express my strong interest in contributing as an AI/ML Engineering Intern.

I am currently a 2nd-year BTech Software Engineering student at Delhi Technological University (DTU), and I have already completed two research internships:

• At AIMS-DTU, I architected a 3-stage LLM safety pipeline using DistilBERT + XGBoost with sub-10ms filtering.
• At the 5G Lab (Department of Telecommunications, DTU), I built real-time threat detection using YOLOv8 with edge AI deployment.

Beyond internships, I have independently built production-grade systems:
• YTRAG — YouTube RAG chatbot with semantic retrieval and OpenAI embeddings
• Multi-stage RAG pipeline over PDF corpora using FAISS + BM25 + cross-encoder reranking
• LLM Safety Shield with adversarial normalization, Redis caching, and ONNX optimization
• AI vs. Human Text Classifier achieving 0.9996 accuracy with RoBERTa on 200K samples

My strongest areas: Applied AI / LLM systems, LLM safety and red-teaming, edge AI/on-device inference, multi-agent systems, and agentic AI workflows.

I would love the opportunity to contribute to {company}. I have attached my resume for your review and would be grateful for the chance to discuss how I can add value to your team.

Thank you for your time and consideration.

Best regards,
Arnav Sagar
+91-6284962948
arnavsagar1510@gmail.com
linkedin.com/in/arnvsr | github.com/zibranxo
"""


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
    user_prompt = f"""
Candidate profile (Markdown):
{about_me}

Target company:
  Name:    {company['Company']}
  Domain:  {company['Tag']}
  Region:  {company['Region']}
  Note:    {company['Note']}
  Email:   {company['Email']}
"""
    if company_context:
        user_prompt += f"\nCompany Context (Website):\n{company_context}\n"

    user_prompt += """
Write a personalized cold application email for this candidate applying to this company.
Return strictly valid JSON with keys "subject" and "body" only.
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
            response = client.chat.completions.create(**request)
        else:
            raise

    raw = _extract_content_text(response)
    if not raw:
        reason = response.choices[0].finish_reason
        reasoning = getattr(response.choices[0].message, "reasoning", None)
        if reason == "length" and reasoning:
            raise ValueError("Model returned empty content (reasoning consumed tokens before final answer)")
        raise ValueError("Model returned empty content")

    return _parse_email_json(raw)


def _template_fallback_email(company: dict) -> dict:
    """Return a template-based email when LLM generation completely fails."""
    subject = "Internship Application — Arnav Sagar (DTU) — AI/ML Engineering"
    body = EMAIL_TEMPLATE.format(subject=subject, company=company["Company"])
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
                log.warning(f"  [{company['Company']}] 429 Rate Limit hit on {provider.name}. Quarantining provider for 600s and switching...")
                provider.exhausted_until = time.time() + 600.0
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
            max_delay = 30.0  # seconds
            sleep_s = min(jittered_delay, max_delay)
            log.warning(
                f"  Generation retry {attempt}/{max_retries} for {company['Company']}: {e} | sleeping {sleep_s:.1f}s"
            )
            time.sleep(sleep_s)

    if last_error:
        raise last_error
    raise ValueError("generate_email_with_retry failed but last_error was None")

LOW_QUALITY_QUEUE = BASE_DIR / "low_quality_queue.json"


def calculate_quality_score(subject: str, body: str, company: dict) -> int:
    """Calculate pre-send quality gate score (0-100) for a generated email."""
    score = 0
    body_lower = body.lower()
    
    # 1. Conciseness (15 points)
    words = body.split()
    if len(words) <= 180:
        score += 10
    if len(subject) <= 50:
        score += 5
        
    # 2. Relevance (30 points)
    co_name = company.get("Company", "").lower()
    co_tag = company.get("Tag", "").lower()
    co_note = company.get("Note", "").lower()
    
    if co_name and co_name in body_lower:
        score += 15
    if (co_tag and co_tag in body_lower) or (co_note and any(w in body_lower for w in co_note.split() if len(w) > 3)):
        score += 15
        
    # 3. Personalization (25 points)
    # Common keywords from about_me that match DTU BTech profile
    keywords = ["llm", "rag", "safety", "threat", "classification", "yolo", "safety shield", "bert", "roberta", "xgboost", "faiss"]
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
            
        score = calculate_quality_score(result["subject"], result["body"], company)
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
                    sc = calculate_quality_score(res["subject"], res["body"], company)
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
        strict_score = calculate_quality_score(strict_result["subject"], strict_result["body"], company)
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


# ── Pretty print preview ──────────────────────────────────────────────────────


def print_preview(company: dict, subject: str, body: str, idx: int, total: int):
    sep = "-" * 64
    print(f"\n{sep}")
    print(f"  [{idx}/{total}]  {company['Company']}  ->  {company['Email']}")
    tag_info = company.get('Tag', 'N/A')
    region_info = company.get('Region', 'N/A')
    score_info = company.get('contact_score', 'N/A')
    if isinstance(score_info, (int, float)):
        score_info = f"{score_info}/10"
    print(f"  Tag: {tag_info}  |  Region: {region_info}  |  Score: {score_info}")
    print(f"{sep}")
    print(f"  Subject : {subject}")
    print(f"{sep}")
    print(body)
    print(sep)



def generate_run_report(stats: dict, failures: list, emails_attempted: list, args):
    """Generate structured JSON and markdown reports of the mailing run."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = BASE_DIR / f"run_report_{timestamp}.json"
    summary_filename = BASE_DIR / f"run_report_{timestamp}.md"
    
    report_data = {
        "run_id": timestamp,
        "start_time": stats.get("start_time"),
        "end_time": datetime.now().isoformat(),
        "config": {
            "dry_run": args.dry_run,
            "limit": args.limit,
            "filter_region": args.filter_region,
            "filter_tag": args.filter_tag,
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
- **Region Filter**: {args.filter_region}
- **Tag Filter**: {args.filter_tag}
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
    if CHECKPOINT_FILE.exists():
        try:
            data = json.loads(CHECKPOINT_FILE.read_text())
            # Optional: validate freshness (< 2 hours)
            ts_str = data.get("timestamp", "")
            if ts_str:
                ts = datetime.fromisoformat(ts_str)
                if (datetime.now() - ts).total_seconds() < 7200:
                    return data
                else:
                    log.warning("Checkpoint found but is older than 2 hours. Ignoring.")
        except Exception as e:
            log.warning(f"Failed to load checkpoint: {e}")
    return None


def save_checkpoint(last_idx: int, generated_cache: dict, sent_log_snapshot: dict):
    """Save run state checkpoint to file."""
    try:
        data = {
            "last_processed_index": last_idx,
            "generated_cache_by_email": generated_cache,
            "sent_log_snapshot": sent_log_snapshot,
            "timestamp": datetime.now().isoformat()
        }
        CHECKPOINT_FILE.write_text(json.dumps(data, indent=2))
        log.debug(f"Saved checkpoint")
    except Exception as e:
        log.error(f"Failed to save checkpoint: {e}")


def delete_checkpoint():
    """Remove checkpoint file upon successful run completion."""
    if CHECKPOINT_FILE.exists():
        try:
            CHECKPOINT_FILE.unlink()
            log.info("Checkpoint file cleaned up.")
        except Exception as e:
            log.error(f"Failed to clean up checkpoint file: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Automated job application emailer")
    parser.add_argument("--dry-run",        action="store_true",
                        help="Generate and preview emails without sending")
    parser.add_argument("--limit",          type=int,  default=None,
                        help="Max number of companies to process")
    parser.add_argument("--filter-region",  type=str,  default=None,
                        help="Filter by region: India | Global")
    parser.add_argument("--filter-tag",     type=str,  default=None,
                        help="Filter by tag: AI/ML | Fintech | Data | etc.")
    parser.add_argument("--skip-sent",      action="store_true", default=True,
                        help="Skip companies already emailed (default: on)")
    parser.add_argument("--no-skip-sent",   action="store_false", dest="skip_sent",
                        help="Re-send even to already-emailed companies")
    parser.add_argument("--workers",        type=int, default=1,
                        help="Parallel LLM generation workers (default: 1)")
    parser.add_argument("--llm-timeout",    type=float, default=45,
                        help="Per-request timeout in seconds for LLM call")
    parser.add_argument("--llm-retries",    type=int, default=2,
                        help="Retries per LLM request on failure")
    parser.add_argument("--llm-backoff",    type=float, default=1.5,
                        help="Base backoff seconds for retries (exponential)")
    parser.add_argument("--max-tokens",     type=int, default=GEN_MAX_TOKENS,
                        help=f"Max tokens for generated email (default: {GEN_MAX_TOKENS})")
    parser.add_argument("--temperature",    type=float, default=LLM_TEMPERATURE,
                        help=f"LLM temperature (default: {LLM_TEMPERATURE})")
    parser.add_argument("--top-p",          type=float, default=LLM_TOP_P,
                        help=f"LLM top_p (default: {LLM_TOP_P})")
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=LLM_THINKING,
                        help=f"Enable/disable model thinking mode (default from env: {LLM_THINKING})")
    parser.add_argument("--reasoning-effort", type=str, default=LLM_REASONING_EFFORT,
                        choices=["none", "low", "medium", "high"],
                        help=f"Reasoning effort level (default: {LLM_REASONING_EFFORT})")
    parser.add_argument("--min-contact-score", type=int, default=2,
                        help="Minimum contact score to process (0-10, default: 2)")
    parser.add_argument("--min-quality-score", type=int, default=70,
                        help="Minimum email quality score to allow sending (0-100, default: 70)")
    parser.add_argument("--slowmode", action="store_true",
                        help="Enforce a strict 30 requests/minute LLM rate limit")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from the latest checkpoint file if available")
    parser.add_argument("--company-research", action="store_true",
                        help="Enable website scraping for company context")
    parser.add_argument("--variant-count", type=int, default=1,
                        help="Number of email variants to generate and score per company")
    parser.add_argument("--check-bounces", action="store_true",
                        help="Check IMAP for bounce messages to automatically skip them")
    parser.add_argument("--check-mx", action="store_true",
                        help="Verify DNS MX records before generating/sending (default: disabled)")
    parser.add_argument("--health-port", type=int, default=0,
                        help="Port to run a background health check HTTP server on (default 0=disabled)")
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

    # ── Validate env ──────────────────────────────────────────────────────────
    missing = []
    if not LLM_API_KEY:   missing.append("LLM_API_KEY")
    if not args.dry_run:
        if not SENDER_EMAIL: missing.append("SENDER_EMAIL")
        if not SENDER_PASS:  missing.append("SENDER_APP_PASSWORD")
    if missing:
        log.error(f"Missing env vars: {', '.join(missing)}. Set them in .env")
        raise SystemExit(1)

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
    if args.check_bounces and SENDER_EMAIL and SENDER_PASS:
        bounces = set(sync_bounces(SENDER_EMAIL, SENDER_PASS))
    else:
        bounces = load_bounces()

    # ── Load data ─────────────────────────────────────────────────────────────
    about_me  = load_about_me()
    companies = load_companies(args.filter_region, args.filter_tag)

    # ── Validate emails and remove duplicates ─────────────────────────────────────
    valid_companies = []
    seen_emails = set()
    invalid_email_count = 0
    duplicate_email_count = 0

    for company in companies:
        email = company["Email"].strip().lower()

        # Validate email format
        if not is_valid_email(email):
            invalid_email_count += 1
            log.warning(f"Invalid email format, skipping: {company['Company']} -> {email}")
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
    if args.resume:
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
        print(f"\n[WARNING] About to send {len(companies)} real emails from {SENDER_EMAIL}")
        confirm = input("   Type 'yes' to proceed: ").strip().lower()
        if confirm != "yes":
            log.info("Aborted by user.")
            return

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
        
    log.info(f"Loaded {len(_provider_pool)} LLM providers: {', '.join(p.name for p in _provider_pool)}")

    # ── Generate (optionally parallel) ────────────────────────────────────────
    workers = max(1, args.workers)
    if workers > 1:
        log.info(f"Parallel generation enabled: workers={workers}")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            def process_company(company):
                domain = company["Email"].split("@")[-1]
                ctx = fetch_company_context(company["Company"], domain) if args.company_research else ""
                return generate_and_gate_email(
                    about_me,
                    company,
                    args.max_tokens,
                    args.llm_timeout,
                    args.llm_retries,
                    args.llm_backoff,
                    args.temperature,
                    args.top_p,
                    args.thinking,
                    args.reasoning_effort,
                    args.min_quality_score,
                    company_context=ctx,
                    variant_count=args.variant_count,
                )

            futures = {
                pool.submit(process_company, company): company
                for idx, company in enumerate(companies, start=1)
                if company["Email"].strip().lower() not in generated
            }

            for future in as_completed(futures):
                company = futures[future]
                co_name = company["Company"].strip()
                to_addr = company["Email"].strip()
                email_key = to_addr.lower()
                try:
                    generated[email_key] = future.result()
                    stats["generation_success"] += 1
                    log.info(f"Generated email for {co_name}")
                    save_checkpoint(0, generated, sent_log)
                except Exception as e:
                    stats["generation_failed"] += 1
                    failures.append({"company": co_name, "email": to_addr, "stage": "generation", "error_message": str(e)})
                    log.error(f"Generation failed for {co_name}: {e}")
                    save_checkpoint(0, generated, sent_log)
    else:
        for idx, company in enumerate(companies, start=1):
            to_addr = company["Email"].strip()
            email_key = to_addr.lower()
            if email_key in generated:
                continue
            co_name = company["Company"].strip()
            log.info(f"[{idx}/{len(companies)}] Generating email for {co_name} -> {to_addr}")
            domain = to_addr.split("@")[-1]
            ctx = fetch_company_context(co_name, domain) if args.company_research else ""
            try:
                generated[email_key] = generate_and_gate_email(
                    about_me,
                    company,
                    args.max_tokens,
                    args.llm_timeout,
                    args.llm_retries,
                    args.llm_backoff,
                    args.temperature,
                    args.top_p,
                    args.thinking,
                    args.reasoning_effort,
                    args.min_quality_score,
                    company_context=ctx,
                    variant_count=args.variant_count,
                )
                stats["generation_success"] += 1
                save_checkpoint(0, generated, sent_log)
            except Exception as e:
                stats["generation_failed"] += 1
                failures.append({"company": co_name, "email": to_addr, "stage": "generation", "error_message": str(e)})
                log.error(f"  Generation failed for {co_name}: {e}")
                save_checkpoint(0, generated, sent_log)

    # ── Preview + send (sequential) ───────────────────────────────────────────
    success_count = 0
    fail_count = 0

    for idx, company in enumerate(companies, start=1):
        to_addr = company["Email"].strip()
        co_name = company["Company"].strip()
        email_key = to_addr.lower()

        result = generated.get(email_key)
        if not result:
            fail_count += 1
            # Still save checkpoint so we skip this failed generation company on resume
            save_checkpoint(0, generated, sent_log)
            continue

        subject = result["subject"]
        body = result["body"]

        print_preview(company, subject, body, idx, len(companies))

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
            status = "DRY-RUN" if args.dry_run else "SENT"
            log.info(f"  {status}: {co_name}")
            if not args.dry_run:
                mark_sent(sent_log, to_addr, co_name)
            success_count += 1
            stats["send_success"] += 1
        else:
            log.warning(f"  FAILED: {co_name}")
            fail_count += 1
            stats["send_failed"] += 1
            failures.append({"company": co_name, "email": to_addr, "stage": "send", "error_message": "SMTP send failed"})

        # Save checkpoint after each processed index
        save_checkpoint(0, generated, sent_log)

        if not args.dry_run and idx < len(companies):
            log.info(f"  Waiting {RATE_LIMIT_S}s before next send...")
            time.sleep(RATE_LIMIT_S)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'-'*64}")
    print(f"  Done.  Success: {success_count}  |  Failed: {fail_count}")
    if args.dry_run:
        print("  (Dry run — no emails were actually sent)")
    print(f"{'-'*64}\n")

    # Generate run reports
    generate_run_report(stats, failures, emails_attempted, args)

    # Delete checkpoint file on successful complete
    delete_checkpoint()


if __name__ == "__main__":
    main()
