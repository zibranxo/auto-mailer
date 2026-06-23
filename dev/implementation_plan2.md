# Final Implementation Plan: Resilient State & Advanced Sending Features

## 1. Unified Run Directories & State Management
- All run-specific outputs will be stored inside a dedicated `runs/YYYY-MM-DD/` folder.
- **Permanent Checkpoints**: Generated drafts are saved to `checkpoint.json` inside the run folder and are **never deleted**. This ensures you never waste API tokens regenerating an email if the script stops.
- **Status Log**: A clean `status.log` file will be maintained in the run folder, appending `[SUCCESS]`, `[FAILED_GENERATION]`, or `[FAILED_SMTP]` line-by-line as the pipeline executes.
- **Global Deduplication**: To ensure you don't email the same HR rep twice across different campaigns, `sent_log.json` and `bounced_log.json` will remain in the root directory.
- **Resume Flag**: Running `--resume` will automatically detect the most recent run folder, load its `checkpoint.json`, and pick up exactly where it left off. You can also specify a folder explicitly: `--resume runs/2026-06-15`.

## 2. Multi-Sender Account Rotation
- Just like the LLM fallback system, your `.env` will support multiple sender accounts:
  - `SENDER_EMAIL` / `SENDER_APP_PASSWORD`
  - `SENDER_2_EMAIL` / `SENDER_2_APP_PASSWORD`
  - `SENDER_3_EMAIL` / `SENDER_3_APP_PASSWORD`
- The script will dynamically load all available senders and **round-robin** outgoing emails across them. This distributes your sending volume, dramatically lowering the risk of spam filters flagging a single Gmail account.

## 3. Business-Hours Pacing (Smart Scheduler)
- A new `--business-hours` CLI flag will be added.
- When enabled, the script will rapidly generate all email drafts (even overnight), but the SMTP delivery loop will check the clock. If it's outside of 9 AM to 5 PM or on a weekend, it will gracefully "sleep" and hold the queue until the next business window opens, ensuring your emails land at the top of the inbox during working hours.

## 4. Attachment Personalization
- Before dispatching the email, the script will read your root `resume.pdf` but dynamically rename it in the email metadata. 
- The attachment will appear to the HR representative as `Arnav_Sagar_Resume_[CompanyName].pdf` (e.g., `Arnav_Sagar_Resume_Two99.pdf`), making the outreach feel bespoke and highly targeted.

## User Review Required
> [!IMPORTANT]
> The plan incorporates all your requested features and constraints. If this looks good, please approve and I will begin the execution phase immediately!
