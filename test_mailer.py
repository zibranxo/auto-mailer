import unittest
from unittest.mock import patch, MagicMock, mock_open
import json

# Import the functions to test
import mailer
mailer.OVERRIDE_WITH_PRESET_SUBJECTS = False
from mailer import (
    is_valid_email,
    calculate_contact_score,
    _parse_email_json,
    calculate_quality_score,
    load_checkpoint,
    save_checkpoint,
    JSONParseError
)

class TestMailer(unittest.TestCase):

    def test_is_valid_email(self):
        # Valid cases
        self.assertTrue(is_valid_email("arnavsagar1510@gmail.com"))
        self.assertTrue(is_valid_email("recruit@stripe.com"))
        self.assertTrue(is_valid_email("hr@google.co.in"))
        
        # Invalid format cases
        self.assertFalse(is_valid_email("plainaddress"))
        self.assertFalse(is_valid_email("@missingusername.com"))
        self.assertFalse(is_valid_email("username@.com"))
        self.assertFalse(is_valid_email("username@missingtld"))
        self.assertFalse(is_valid_email("username@domain..com"))
        self.assertFalse(is_valid_email("username @domain.com"))
        self.assertFalse(is_valid_email(None))
        self.assertFalse(is_valid_email(""))
        
        # Disposable email cases
        self.assertFalse(is_valid_email("test@mailinator.com"))
        self.assertFalse(is_valid_email("hello@tempmail.com"))
        self.assertFalse(is_valid_email("user@trashmail.com"))

    def test_calculate_contact_score(self):
        # Case 1: Ideal contact (valid email, corporate domain, HR note, relevant tag, preferred region)
        co_ideal = {
            "Company": "AI Corp",
            "Email": "hr@aicorp.com",
            "Note": "HR Manager",
            "Region": "India",
            "Tag": "AI/ML"
        }
        # score = 2 (valid email) + 2 (corporate) + 1 (not contacted) = 5
        self.assertEqual(calculate_contact_score(co_ideal), 5)

        # Case 2: Generic gmail
        co_generic = {
            "Company": "Startup",
            "Email": "founder@gmail.com"
        }
        # score = 2 (valid email) + 0 (generic) + 1 (not contacted) = 3
        self.assertEqual(calculate_contact_score(co_generic), 3)

        # Case 3: Already contacted penalty
        sent_log = {"founder@gmail.com": {"company": "Startup"}}
        # score = 2 (valid email) + 0 (generic) - 1 (contacted) = 1
        self.assertEqual(calculate_contact_score(co_generic, sent_log), 1)

        # Case 4: Multiple valid corporate emails
        co_multi = {
            "Company": "AI Corp",
            "Email": "hr@aicorp.com, boss@aicorp.com",
        }
        # score = 2 (valid emails) + 2 (corporate domain of primary) + 1 (not contacted) = 5
        self.assertEqual(calculate_contact_score(co_multi), 5)

        # Case 5: Multiple emails with one contacted
        sent_log_multi = {"boss@aicorp.com": {"company": "AI Corp"}}
        # score = 2 (valid emails) + 2 (corporate domain of primary) - 1 (one of them contacted) = 3
        self.assertEqual(calculate_contact_score(co_multi, sent_log_multi), 3)

    def test_parse_email_json(self):
        # Case 1: Clean JSON
        raw_clean = '{"subject": "Hello", "body": "This is a body."}'
        parsed = _parse_email_json(raw_clean)
        self.assertEqual(parsed["subject"], "Hello")
        self.assertEqual(parsed["body"], "This is a body.")

        # Case 2: Markdown fences and extra wrapper text
        raw_fence = 'Here is the JSON: ```json\n{\n  "subject": "Fence Subject",\n  "body": "Fence Body"\n}\n``` Enjoy!'
        parsed = _parse_email_json(raw_fence)
        self.assertEqual(parsed["subject"], "Fence Subject")
        self.assertEqual(parsed["body"], "Fence Body")

        # Case 3: Single quotes and trailing comma
        raw_lenient = "{\n  'subject': 'Lenient Subject',\n  'body': 'Lenient Body',\n}"
        parsed = _parse_email_json(raw_lenient)
        self.assertEqual(parsed["subject"], "Lenient Subject")
        self.assertEqual(parsed["body"], "Lenient Body")

        # Case 4: Unterminated string
        raw_unterminated = '{"subject": "Unterminated Subject", "body": "Unterminated'
        parsed = _parse_email_json(raw_unterminated)
        self.assertEqual(parsed["subject"], "Unterminated Subject")
        self.assertTrue(parsed["body"].startswith("Unterminated"))

        # Case 5: Empty/invalid throws JSONParseError
        with self.assertRaises(JSONParseError):
            _parse_email_json("")
        with self.assertRaises(JSONParseError):
            _parse_email_json("Not JSON at all")

    def test_calculate_quality_score(self):
        company = {"Company": "Google", "Tag": "AI/ML", "Note": "Hiring manager"}
        company_context = "Google is a major technology company offering cloud platforms and web search services."
        
        # High quality email (passes word count check, mentions Google, cloud context, and LLMs)
        subject_good = "AI Engineering Intern - Arnav Sagar"
        body_good = (
            "Hi Google Team, I am Arnav Sagar, a second-year Software Engineering student from DTU. "
            "I worked on advanced AI/ML systems including LLMs, RAG, and YOLO. I have experience "
            "building cloud platforms and low-latency moderations. I would love to join Google as an AI/ML intern "
            "to work on your large-scale web search and indexing challenges."
        )
        score_good = calculate_quality_score(subject_good, body_good, company, company_context=company_context)
        
        # Word counts: 64 words (sanity 50-500: +5 pts, subject <= 100: +5 pts) -> 10
        # Mentions google (+20) and matches cloud context (+15) -> 35
        # Mentions LLM/RAG/YOLO (+25) -> 25
        # Tone clean (+15) -> 15
        # Spam clean (+15) -> 15
        # Total score: 100
        self.assertEqual(score_good, 100)

        # Low quality email (stiff greeting, spam triggers, missing keywords, too short)
        subject_bad = "URGENT!!! MUST READ APPLICATION FOR INTERNSHIP OPPORTUNITY AT GOOGLE"
        body_bad = "Dear Hiring Manager, please find attached my CV for your kind perusal. I am looking for a job. 100% guaranteed results."
        score_bad = calculate_quality_score(subject_bad, body_bad, company, company_context=company_context)
        self.assertTrue(score_bad < 70)

    @patch("mailer._atomic_write")
    @patch("mailer.CHECKPOINT_FILE")
    def test_checkpoint_logic(self, mock_checkpoint_path, mock_atomic_write):
        # Setup mock file operations
        mock_checkpoint_path.exists.return_value = True
        
        # Test load checkpoint
        fake_data = {
            "last_processed_index": 5,
            "generated_cache": {"1": {"subject": "S", "body": "B"}},
            "sent_log_snapshot": {},
            "timestamp": mailer.datetime.now().isoformat()
        }
        mock_checkpoint_path.read_text.return_value = json.dumps(fake_data)
        
        loaded = load_checkpoint()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["last_processed_index"], 5)
        
        # Test save checkpoint
        save_checkpoint(10, {1: {"subject": "S", "body": "B"}}, {})
        mock_atomic_write.assert_called_once()

    @patch("mailer.OpenAI")
    def test_generate_email_mock(self, mock_openai):
        mock_provider = MagicMock()
        mock_client = MagicMock()
        mock_provider.client = mock_client
        mock_provider.lock = MagicMock()
        mock_provider.last_inference_time = 0.0
        mock_openai.return_value = mock_client
        
        # Setup mock response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"subject": "Mocked Subject", "body": "Mocked Body"}'
        mock_client.chat.completions.create.return_value = mock_response
        
        company = {"Company": "MockInc", "Tag": "Tech", "Region": "US", "Note": "", "Email": "hi@mock.inc"}
        result = mailer.generate_email(
            mock_provider,
            about_me="I am a coder",
            company=company,
            max_tokens=100
        )
        self.assertEqual(result["subject"], "Mocked Subject")
        self.assertTrue(result["body"].startswith("Mocked Body"))

    @patch("mailer.get_next_sender")
    @patch("mailer.RESUME_PDF")
    @patch("builtins.open", new_callable=mock_open, read_data=b"mock pdf content")
    @patch("mailer.smtplib.SMTP")
    def test_send_email_mock(self, mock_smtp, mock_open_file, mock_resume_pdf, mock_get_next_sender):
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server
        mock_resume_pdf.exists.return_value = True
        
        mock_sender = MagicMock()
        mock_sender.email = "me@gmail.com"
        mock_sender.uses_count = 0
        mock_sender.last_used = 0.0
        mock_get_next_sender.return_value = mock_sender
        
        result = mailer.send_email(
            "test@example.com",
            "Test Subj",
            "Test Body",
            company_name="TestCorp",
            dry_run=False
        )
        self.assertTrue(result)
        mock_server.login.assert_called_once()
        mock_server.sendmail.assert_called_once()
        args, kwargs = mock_server.sendmail.call_args
        self.assertEqual(args[0], "me@gmail.com")
        self.assertEqual(args[1], ["test@example.com"])

    @patch("mailer.get_next_sender")
    @patch("mailer.RESUME_PDF")
    @patch("builtins.open", new_callable=mock_open, read_data=b"mock pdf content")
    @patch("mailer.smtplib.SMTP")
    def test_send_email_multiple_mock(self, mock_smtp, mock_open_file, mock_resume_pdf, mock_get_next_sender):
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server
        mock_resume_pdf.exists.return_value = True
        
        mock_sender = MagicMock()
        mock_sender.email = "me@gmail.com"
        mock_sender.uses_count = 0
        mock_sender.last_used = 0.0
        mock_get_next_sender.return_value = mock_sender
        
        result = mailer.send_email(
            "test1@example.com, test2@example.com",
            "Test Subj",
            "Test Body",
            company_name="TestCorp",
            dry_run=False
        )
        self.assertTrue(result)
        mock_server.sendmail.assert_called_once()
        args, kwargs = mock_server.sendmail.call_args
        self.assertEqual(args[0], "me@gmail.com")
        self.assertEqual(args[1], ["test1@example.com", "test2@example.com"])

if __name__ == "__main__":
    unittest.main()
