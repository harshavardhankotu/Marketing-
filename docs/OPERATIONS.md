# Operations Runbook: Automated AI Marketing Suite

This document acts as an operational guide and emergency runbook for system administrators and business operators. It outlines manual task execution, log locations, database metrics verification, and resolution procedures for external service interruptions.

---

## 1. Day-to-Day Operations Checklist

As a daily operator, perform the following verification sweeps:
1.  **Dashboard Check**: Open the central console `http://127.0.0.1:5000/` and verify that the front-end layout displays campaign stats and product categories without layout shifting.
2.  **Scheduler Auditing**: Navigate to the **History Page** (`/history`) and verify that the 6 registered background jobs (IST time) are active and running.
3.  **Click Metrics Audit**: Review the click counter cards to verify that total click-through rates (CTR) are registering and sync dynamically with individual campaign listings.

---

## 2. Manual Task Execution

### 2.1 Manually Execute the Pipeline for a Single Sector
If you want to manually trigger the full 19-step campaign pipeline for a specific category without waiting for the daily scheduler, you can do so in two ways:

#### Option A: Web Console (Recommended)
1. Navigate to `http://127.0.0.1:5000/`.
2. Under the dropdown selector, choose your desired category (e.g. `smartphones`, `laptops`, `mens-watches`, or `live_links`).
3. Click the **Run Sector Pipeline** button.
4. The system will trigger the subprocess pipeline. Track execution steps dynamically via the progress tracker on-screen.

#### Option B: Terminal Command Line
You can execute individual scrapers inside the virtual environment:
```powershell
# Sourcing smartphones sector:
python scrapers/product_scraper.py smartphones

# Sourcing custom links list from input_links.txt:
python scrapers/product_scraper.py live_links
```

### 2.2 Manually Trigger a Retargeting Sweep
To sweep for unconverted user sessions (clicks logged without matching conversion timestamps) and fire follow-up marketing sweeps:
1.  **Web Control**: Navigate to the history panel `/history` and click **Trigger Retargeting Sweep**.
2.  **API Control**: Perform a HTTP POST request using a tool like Postman or curl:
    ```powershell
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5000/api/run_retargeting"
    ```

---

## 3. Log Inspection and Diagnostics

### 3.1 Core System Log Files
If execution errors occur, review the following local files inside `c:\marketing\data\output\`:

*   **`market_analysis.json`**: Captures sector demand computations, competitive pricing averages, and seasonal scoring metrics.
*   **`post_data.json`**: Contains final marketing captions, product titles, image links, and tracking parameters.
*   **`segmented_products.json` & `explained_products.json`**: Capture demographical data, buyer profiles, and honest product trade-off descriptions.
*   **`video_scripts.json`**: Contains structured video scripts formatted for Reels/TikTok shorts.
*   **`email_newsletter.html`**: Contains the latest dynamic daily round-up HTML email layout.

### 3.2 Database Auditing
Open the sqlite datastore using an administrative shell to audit click attributes:
```powershell
# Open campaigns SQLite database
sqlite3 c:\marketing\data\campaigns.db

# 1. Audit logged clicks
sqlite> SELECT product_title, sector, channel, clicked_at FROM affiliate_clicks ORDER BY clicked_at DESC LIMIT 5;

# 2. Check retargeting executions
sqlite> SELECT product_id, strategy, retargeted_at FROM retargeting_logs ORDER BY retargeted_at DESC LIMIT 5;

# 3. Check Scheduler Job Runs
sqlite> SELECT job_id, started_at, finished_at, status, duration_secs FROM scheduler_runs ORDER BY started_at DESC LIMIT 10;
```

---

## 4. Recovering from Failed Jobs and Errors

### 4.1 SQLite Lock Mutex Contention
*   *Symptom*: Background pipelines fail with `locked — skipping overlapping run`.
*   *Cause*: A previous subprocess execution run terminated abnormally, leaving an un-cleared entry in the database.
*   *Resolution*: Clear the locks table manually in SQLite to restore standard schedules:
    ```sql
    DELETE FROM scheduler_locks;
    ```
    *Note*: The scheduler engine is equipped with an auto-recovery watchdog that automatically deletes stale locks older than 30 minutes.

### 4.2 Gemini API Interruptions
*   *Symptom*: Generative AI copywriting prompts fail due to quota limits or missing API credentials.
*   *Handling*: The pipeline service detects credential absences or network timeouts, falling back to Hinglish/Tamil templates dynamically without halting or failing the pipeline.
*   *Resolution*: If you wish to restore live copy, update `GEMINI_API_KEY` in your `.env` file, reload the Flask process, and trigger the pipeline.

### 4.3 Telegram Bot Unconfigured
*   *Symptom*: Distributor steps register successful execution, but no updates are posted to Telegram.
*   *Handling*: `bots/distributor.py` detects unconfigured credentials, logging mock payloads locally in `data/output/distribution_logs.json` to preserve execution safety.
*   *Resolution*: Provide the verified `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` and restart the process to activate live channel routing.

### 4.4 Playwright Browser Crashing
*   *Symptom*: Pipeline fails during the Playwright scraping phase.
*   *Cause*: The local Chromium engine is missing system libraries, or the local browser binary is corrupted.
*   *Resolution*: Force-reinstall Playwright engines inside your active virtual environment:
    ```powershell
    playwright install --with-deps chromium
    ```
