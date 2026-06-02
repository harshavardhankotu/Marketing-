# Deployment Guide: Automated AI Marketing Suite

This document provides step-by-step deployment instructions for the Automated AI Marketing Suite. It details local workspace preparations, dependency chains, environment variable provisioning, Playwright browser engine configurations, and SQLite database backup strategies.

---

## 1. System Requirements

*   **Operating System**: Windows 10/11 or Windows Server 2019/2022.
*   **Runtime Environment**: Python `3.10` or higher.
*   **Privileges**: Standard user permissions for Python executions; Administrator shell access is recommended during Playwright's system-level dependency installations.

---

## 2. Directory Structure and Workspace Configuration

Ensure the following folders exist and are writable by the execution user. The application will auto-create these directories if they are missing at launch:

```
c:\marketing\
├── data\               <-- Directory for database and txt logs (Read/Write)
│   ├── output\         <-- Directory for generated images, newsletters, and JSON metadata
│   └── campaigns.db    <-- Central SQLite database file
└── assets\             <-- Local fonts and static system graphic resources
```

---

## 3. Step-by-Step Installation

### Step 3.1 Clone & Setup Environment
Open PowerShell as an Administrator and navigate to the project directory:

```powershell
# Navigate to the workspace root
cd c:\marketing\

# Initialize a clean virtual environment
python -m venv venv

# Activate the virtual environment
.\venv\Scripts\Activate.ps1
```

### Step 3.2 Install Dependencies
Install all required libraries specified in the system lock manifest:

```powershell
# Upgrade core packaging tools
python -m pip install --upgrade pip

# Install Python requirements
pip install -r requirements.txt
```

### Step 3.3 Install Playwright Browser Engines
The Playwright engine is used for deep-link scraping, resolving HTTP redirect hops, and extracting product titles. Install the headless Chromium browser binary:

```powershell
# Install the Chromium binary used by scrapers/link_extractor.py
playwright install chromium
```

---

## 4. Environment Variables Configuration

Create a file named `.env` in the root folder (`c:\marketing\.env`). Use the following template, replacing default keys with your official production credentials:

```ini
# Central Configuration File for Automated AI Marketing Suite
# Save in c:\marketing\.env

# ═══════════════════════════════════════════════════════════════════════
# 1. GOOGLE GEMINI CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════
# Required for live Hinglish/Tamil copywriting.
# If omitted or invalid, the suite operates using safe local stubs.
GEMINI_API_KEY=your_production_gemini_api_key_here

# ═══════════════════════════════════════════════════════════════════════
# 2. TELEGRAM CHANNEL DISTRIBUTION CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════
# Required to push creative campaigns to your live channels.
# If omitted or invalid, campaigns are successfully saved to SQLite and mock logged.
TELEGRAM_BOT_TOKEN=8359078338:AAFUocxeGALB7GG3zg53hI83w08Yq10Wi-w
TELEGRAM_CHAT_ID=1288414559
```

---

## 5. Launch Procedures

### Step 5.1 Run the Flask Application & Background Scheduler
Running the Flask web server initiates the APScheduler engine in a separate thread. This registers all 6 cron jobs and applies active database locks.

```powershell
# Ensure virtual environment is active
.\venv\Scripts\Activate.ps1

# Launch the Flask application
python app.py
```

*   **Access Web Panel**: Open your browser and navigate to `http://127.0.0.1:5000/`.
*   **Verification**: At launch, the terminal logs will display:
    ```
    [scheduler] APScheduler started — 6 jobs registered (IST)
    * Running on http://127.0.0.1:5000/ (Press CTRL+C to quit)
    ```

---

## 6. Production Cautions

> [!WARNING]
> *   **Thread Safety in SQLite**: Always access SQLite using a safe connection timeout parameter (e.g. `timeout=30.0`). Standard SQLite engines block during active writes. Setting thread-safe parameters prevents Flask client timeouts during intensive background pipeline execution blocks.
> *   **Port Bindings**: By default, Flask binds to `127.0.0.1:5000`. If deploying to an external Windows Server network, update `app.run(host='0.0.0.0', port=80)` inside `app.py` and configure your Windows Defender Firewall to permit incoming TCP traffic on the designated port.
> *   **Memory Allocations for Playwright**: Headless browsers consume significant CPU and RAM. Ensure the server has a minimum of 2GB available RAM to prevent system crashes during concurrent sector runs.

---

## 7. SQLite Database Backup Guidance

To protect historical click data, attribution logs, and campaign summaries, implement a robust backup cycle:

### 7.1 Online Hot-Backup (Recommended)
Since SQLite supports safe concurrent readers, run the online backup command via the sqlite CLI tool without stopping the Flask service:

```powershell
# Perform a hot backup using the SQLite command-line tool
sqlite3 c:\marketing\data\campaigns.db ".backup 'c:\marketing\data\campaigns_backup_$(date -f yyyyMMdd).db'"
```

### 7.2 Offline File Copy Backup
Alternatively, stop the Flask server temporarily to ensure no database locks exist, copy the file, and restart the server:

```powershell
# Stop Flask (Ctrl+C in terminal)
# Copy the datastore file
Copy-Item -Path "c:\marketing\data\campaigns.db" -Destination "c:\marketing\data\backups\campaigns_$(Get-Date -Format 'yyyyMMdd_HHmmss').db" -Force

# Restart the application
python app.py
```
