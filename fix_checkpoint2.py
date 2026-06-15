import json
import csv
from pathlib import Path
import re

BASE_DIR = Path("c:/code/auto mailer")
CHECKPOINT_FILE = BASE_DIR / ".mailer_checkpoint.json"
CSV_FILE = BASE_DIR / "hr_emails_directory.csv"

DISPOSABLE_DOMAINS = {"mailinator.com", "trashmail.com", "tempmail.com", "yopmail.com", "dispostable.com", "10minutemail.com", "guerrillamail.com", "sharklasers.com", "getairmail.com", "burnermail.io"}
def is_valid_email(email: str) -> bool:
    if not email or not isinstance(email, str): return False
    email = email.strip().lower()
    if not email: return False
    if ".." in email: return False
    if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email): return False
    try:
        parts = email.split("@")
        if len(parts) == 2 and parts[1] in DISPOSABLE_DOMAINS: return False
    except: return False
    return True

companies = []
with open(CSV_FILE, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        companies.append(row)

valid_companies = []
seen_emails = set()

# NO BOUNCES, NO SENT_LOG

for company in companies:
    email = company["Email"].strip().lower()
    if not is_valid_email(email): continue
    if email in seen_emails: continue
    seen_emails.add(email)
    valid_companies.append(company)

def calculate_score(company):
    score = 0
    email = company["Email"].strip().lower()
    if is_valid_email(email): score += 2
    email_domain = email.split('@')[-1] if '@' in email else ""
    domain_parts = email_domain.split('.')
    domain_name = '.'.join(domain_parts[:-1]) if len(domain_parts) > 1 else email_domain
    tlds = domain_parts[-1:] if len(domain_parts) > 1 else []
    corporate_tlds = {'co', 'org', 'gov', 'edu', 'ac'}
    corporate_keywords = {'company', 'corp', 'inc', 'ltd', 'llc'}
    generic_domains = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com'}
    if any(kw in domain_name for kw in corporate_keywords) or any(tld in corporate_tlds for tld in tlds): score += 2
    elif email_domain in generic_domains: score += 0
    else: score += 1
    
    note = company.get("Note", "").lower()
    high_value_roles = ['hr', 'human resources', 'recruiter', 'recruitment', 'talent', 'hiring', 'manager', 'lead', 'director', 'head', 'chief']
    tech_roles = ['engineer', 'developer', 'technical', 'tech', 'ai', 'ml', 'data', 'science']
    if any(role in note for role in high_value_roles): score += 3
    elif any(role in note for role in tech_roles): score += 2
    elif note: score += 1
    
    region = company.get("Region", "").lower()
    if region == "india": score += 1
    tag = company.get("Tag", "").lower()
    relevant_tags = ['ai/ml', 'ai', 'ml', 'artificial intelligence', 'machine learning', 'data', 'analytics', 'research', 'technology', 'software']
    if any(relevant_tag in tag for relevant_tag in relevant_tags): score += 2
    elif tag: score += 1
    return max(0, min(10, score))

scored_companies = []
for c in valid_companies:
    score = calculate_score(c)
    c["contact_score"] = score
    if score >= 2:
        scored_companies.append(c)

scored_companies.sort(key=lambda x: x.get("contact_score", 0), reverse=True)
print("Original list length:", len(scored_companies))

ckpt = json.loads(CHECKPOINT_FILE.read_text())
# Load original cached_gen if available (we might have overwritten it in previous run, let's check)
cached_gen = ckpt.get("generated_cache", {})
if not cached_gen and "generated_cache_by_email" in ckpt:
    print("Oops, generated_cache missing, might have been overwritten. Need to restore from a backup if any, but since we only care about > 147, and they were generated during the new run, actually they are already mapped by email in the new run!")
    
if cached_gen:
    new_cache = {}
    for k, v in cached_gen.items():
        idx = int(k)
        if idx - 1 < len(scored_companies):
            email = scored_companies[idx - 1]["Email"].strip().lower()
            new_cache[email] = v

    ckpt["generated_cache_by_email"] = new_cache
    CHECKPOINT_FILE.write_text(json.dumps(ckpt, indent=2))
    print("Fixed checkpoint with 492 list!")
