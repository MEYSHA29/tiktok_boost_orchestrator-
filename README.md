# tiktok_boost_orchestrator-
Installation & Usage Instructions for LO:
Install dependencies:
bash
pip install -r requirements.txt
playwright install chromium
Configure config.yaml:
Set your TikTok username (for followers)
Set your video_url (for views/likes/shares)
Enable/disable providers as needed
Add CAPTCHA API keys if you want paid solving (optional, OCR works for most)
Add proxy list file for IP rotation (optional but recommended)
Run:
bash
python main.py
What it does:
Automatically rotates between Zefoy, Fireliker, Mytoolstown, and Vipto
When one hits cooldown, immediately switches to the next available provider
Solves CAPTCHAs automatically using OCR (or paid services if configured)
Farms credits on Mytoolstown automatically when needed
Tracks statistics and persists sessions
Runs indefinitely, requeuing services after cooldown
Architecture highlights:
Modular providers: Each site is an isolated adapter
Intelligent rotation: Priority-based with cooldown awareness
Anti-detection: Stealth browser automation, proxy rotation, human-like delays
CAPTCHA pipeline: OCR → paid APIs → manual fallback
Fault tolerance: Auto-restart, health checks, graceful degradation

