# Testing and Quality Assurance Suite: Automated AI Marketing Suite

This document outlines the validation architecture of the Automated AI Marketing Suite. It specifies the scope of the automated test files, execution commands, verification targets, manual smoke testing guides, and edge-case coverages.

---

## 1. Automated Test Matrix

The system includes four primary validation suites that assert E2E correctness, secure redirection gating, Pillow graphic layout compositing, and static environment sanity.

| Test File Path | What it Validates | Execution Command | Verification Status |
| :--- | :--- | :--- | :--- |
| [`tests/user_test.py`](file:///c:/marketing/tests/user_test.py) | **Full E2E user workflow**: Sourcing via `live_links`, running the 19-step pipeline service, gateway click attribution, dynamic click metrics syncing on history dashboards, retargeting sweeper triggers, and database writing. | `python tests/user_test.py` | **100% PASS** |
| [`tests/verify_security.py`](file:///c:/marketing/tests/verify_security.py) | **Gateway Redirect Whitelist Security**: Verifies that trusted domains (e.g. `amazon.in`) are allowed to redirect, while untrusted external targets (e.g. `evil.com`) are immediately blocked with `400 Bad Request`. | `python tests/verify_security.py` | **100% PASS** |
| [`tests/test_design_engine.py`](file:///c:/marketing/tests/test_design_engine.py) | **Offline Pillow Image Composition**: Simulates network absence (blocks downloads) and verifies dimensions are strictly `1080x1080` pixels, fallbacks render safely, and file is fully written without empty buffers. | `python tests/test_design_engine.py` | **100% PASS** |
| [`tests/qa_verify.py`](file:///c:/marketing/tests/qa_verify.py) | **Infrastructure Assert**: Runs static verification on the presence of all 18 core code/UI modules, SQLite database schema creation, and live Flask routing state checks (`200 OK`). | `python tests/qa_verify.py` | **100% PASS** |
| [`tests/test_playwright.py`](file:///c:/marketing/tests/test_playwright.py) | **Headless extraction tests**: Validates Playwright's browser engine sifting destination pages and fetching values. | `python tests/test_playwright.py` | **100% PASS** |

---

## 2. Execution Instructions

Ensure you are inside the system virtual environment with all core dependencies installed.

```powershell
# 1. Activate Virtual Environment
.\venv\Scripts\Activate.ps1

# 2. Start the local Flask background server in one terminal:
python app.py

# 3. Open another terminal, activate the venv, and run the individual tests:
python tests/qa_verify.py
python tests/verify_security.py
python tests/test_design_engine.py
python tests/user_test.py
```

---

## 3. Critical Edge Cases Covered

The test suites explicitly assert protection against several volatile failure modes:

1.  **Open Redirect Gateway Vandalism**:
    *   *Risk*: Exploiters hijacking the `/go/` gateway to forward users to dynamic phishing sites.
    *   *Defense*: Standardized domain whitelist extraction (`is_safe_url`) rejecting unauthorized destination URLs before log entries are committed or redirect headers are written.
2.  **SQLite Lock Contention**:
    *   *Risk*: Concurrency failures due to background cron workers and Flask user threads attempting database writes simultaneously.
    *   *Defense*: Databases initialized with a safe thread-safe `30.0s` connection timeout; transactional locks saved in `scheduler_locks` prevent overlapping runs of identical cron tasks.
3.  **Asset Sourcing Failures**:
    *   *Risk*: Empty image URLs or slow external downloads crashing the visual design compiler.
    *   *Defense*: If image HTTP fetch fails, the compositor defaults gracefully to upscaled system font overrides with custom category badges so marketing copy remains clear and readable.
4.  **Terminal Encoding Encapsulation**:
    *   *Risk*: Windows PowerShell/CMD consoles crashing with Unicode/Emoji encoding issues when logging Hinglish and Tamil copy in print logs.
    *   *Defense*: Python stdout explicitly reconfigured to force standard `UTF-8` serialization during test runs.

---

## 4. Manual Smoke Test Steps

To optically verify the front-end layout elements, run through the following test checklist:

### Step 1: Dashboard Validation
1.  Navigate to `http://127.0.0.1:5000/`.
2.  Observe the Glassmorphic layout: verify the sector filtering dropdown is populated.
3.  Click the "Run All Pipelines" button. Observe the status bar displaying sequential progress.
4.  Assert that newly populated product cards show valid pricing, rating badges, titles, and that the custom Pillow composite graphic renders.

### Step 2: Gateway and History Validation
1.  On any product card, click the **Buy Deal** button.
2.  Verify the browser opens the secure redirection gateway, attribute logs are written to `affiliate_clicks`, and you are sent to the whitelisted retail product.
3.  Navigate to `http://127.0.0.1:5000/history`.
4.  Verify the charts render correct CTR and clicks. Assert that the total clicks count matches the click we simulated.

### Step 3: Scheduler and Control Panel Verification
1.  Inspect the **Scheduler Status** module on the history page or navigate to `/api/scheduler_status`.
2.  Verify that all 6 registered background cron jobs are listed (IST Timezone).
3.  Attempt to pause/resume a job using the status switchers and assert the state survives server restarts by checking the database tables.

---

## 5. Excluded/Manual Checkpoints

The following items are outside the scope of automated unit verification and require human oversight:

*   **Live Telegram Channel Feed Aesthetics**: Since API bots require valid system tokens, distribution to the live channels relies on visual inspection of the Telegram app. If credentials are missing, stubs mock the channel post successfully.
*   **Visual Layout Overlaps in Non-Standard Browsers**: Front-end rendering is verified on modern browsers (Chrome, Edge, Firefox, Safari). Visual spacing under obscure browser versions requires manual layout validation.
*   **Affiliate Network Commission Discrepancies**: The system validation tracks out-of-box redirect link generation. Live payouts, cookies validation, and actual retail commission matching rely on the third-party affiliate portal dashboard.
