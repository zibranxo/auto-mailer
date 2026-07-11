import json
import csv
import sys
import os
import argparse

from mailer import (
    calculate_contact_score, 
    is_valid_email, 
    send_email, 
    mark_sent, 
    load_sent_log, 
    save_sent_log
)

def main():
    parser = argparse.ArgumentParser(description="Send emails directly from saveprogress.json")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without sending actual emails")
    args = parser.parse_args()

    print("Loading HR emails directory...")
    companies = []
    with open('hr_emails_directory.csv', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            companies.append(row)

    valid_companies = []
    seen_emails = set()
    for c in companies:
        email = c['Email'].strip().lower()
        if not is_valid_email(email): continue
        if email in seen_emails: continue
        seen_emails.add(email)
        valid_companies.append(c)

    scored_companies = []
    for c in valid_companies:
        score = calculate_contact_score(c) # No sent log penalty
        c['contact_score'] = score
        if score >= 2:
            scored_companies.append(c)
            
    scored_companies.sort(key=lambda x: x.get('contact_score', 0), reverse=True)
    print(f"Reconstructed {len(scored_companies)} scored companies.")

    if not os.path.exists('saveprogress.json'):
        print("ERROR: saveprogress.json not found!")
        sys.exit(1)

    with open('saveprogress.json', 'r', encoding='utf-8') as f:
        saveprogress = json.load(f)
    
    # Handle both bare dict and dict with 'generated_cache' key
    generated_cache = saveprogress
    if "generated_cache" in saveprogress:
        generated_cache = saveprogress["generated_cache"]
        
    print(f"Loaded {len(generated_cache)} cached emails from saveprogress.json")

    sent_log = load_sent_log()
    print(f"Loaded {len(sent_log)} sent emails.")
    
    bounced_log = set()
    if os.path.exists('bounced_log.json'):
        try:
            with open('bounced_log.json', 'r', encoding='utf-8') as f:
                bounced_log = set(json.load(f))
            print(f"Loaded {len(bounced_log)} bounced emails.")
        except:
            pass

    if args.dry_run:
        print("\n[DRY RUN] Will not actually send emails.")
    else:
        print("\nStarting to SEND REAL EMAILS...")
        
    sent_count = 0
    fail_count = 0
    skip_count = 0
    missing_count = 0

    for idx, company in enumerate(scored_companies, start=1):
        to_addr = company["Email"].strip()
        email_key = to_addr.lower()
        co_name = company["Company"].strip()

        if email_key in sent_log:
            skip_count += 1
            continue

        if email_key in bounced_log:
            print(f"[{idx}/{len(scored_companies)}] Skipping bounced email: {to_addr}")
            skip_count += 1
            continue

        cached_data = generated_cache.get(email_key) or generated_cache.get(str_idx)
        if not cached_data:
            # Keep track of missing ones but don't spam if many
            missing_count += 1
            continue
        subject = cached_data.get("subject", "")
        body = cached_data.get("body", "")

        if not subject or not body:
            print(f"[{idx}/{len(scored_companies)}] ERROR: Empty subject or body for {co_name} -> {to_addr}")
            fail_count += 1
            continue

        co_name_lower = co_name.lower()
        if co_name_lower not in body.lower() and co_name_lower not in subject.lower():
            # Check if at least one significant word matches
            words = [w for w in co_name_lower.split() if len(w) > 3]
            match = any(w in body.lower() or w in subject.lower() for w in words)
            if not match and co_name_lower:
                print(f"[{idx}/{len(scored_companies)}] WARNING: Company name '{co_name}' doesn't seem to appear in the email text. Double-check index mapping!")

        print(f"[{idx}/{len(scored_companies)}] Sending to {co_name} -> {to_addr}...")
        try:
            success = send_email(to_addr, subject, body, company_name=co_name, dry_run=args.dry_run)
            if success:
                mark_sent(sent_log, to_addr, co_name)
                if not args.dry_run:
                    save_sent_log(sent_log)
                sent_count += 1
            else:
                fail_count += 1
        except KeyboardInterrupt:
            print("\nAborted by user.")
            break
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[{idx}/{len(scored_companies)}] Exception sending to {to_addr}: {e}")
            fail_count += 1

    print(f"\nDone. Sent: {sent_count}, Failed: {fail_count}, Skipped: {skip_count}, Missing in cache: {missing_count}")

if __name__ == "__main__":
    main()
