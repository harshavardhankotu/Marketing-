# 📖 Daily Operator & Virtual Assistant (VA) Manual

Welcome to the **Autonomous Affiliate Marketing Suite**. This guide is written in plain, straightforward language for Daily Operators, Virtual Assistants (VAs), and Agency Managers who manage daily sourcing campaigns, monitor health status, adjust configuration parameters, and download performance reports.

---

## 🏛️ System Core Concept

The marketing suite runs **100% automatically** on a background schedule. Every morning at **08:15 IST**, it crawls popular merchant sectors for hot deals, generates creative graphics, translates localized captions, compiles short-form video reels, and holds them in a **Campaign Preview Gate** for your manual inspection. 

If you are busy or step away, any campaign held in the queue will **automatically publish** after a pre-set expiration timeout (default is **30 minutes**).

---

## 🛡️ Part 1: Managing Dashboard SRE Health

At the top of the **Dashboard** and **History** pages, you will see the **Autonomous SRE Health Monitoring** section. This panel keeps the system healthy by preventing API credits from draining and managing failing social platforms.

### A. Understanding the System Badges
*   🟢 **System Healthy**: All integrations (Gemini AI, Playwright web scrapers, Telegram, Twitter, Instagram, WhatsApp WABA) are running smoothly.
*   🟡 **Degraded Mode Active**: One or more integration channels are experiencing errors or have reached their credit caps. The system has automatically isolated the failing channel to protect the suite, using temporary mocks or fallback graphics instead.

### B. Daily API Quotas & progress Bars
*   **What they are**: Visual bars showing daily credit usage.
*   **Color coding**:
    *   **Green**: Under 80% usage. Safe.
    *   **Orange (Warning)**: Emits warning notifications. Limit is close.
    *   **Red (Cap Blocked)**: The cap limit is reached. The system blocks subsequent API requests to avoid credit charges.
*   **What to do**: Under extreme scenarios, if you want to force-allow more requests, click the corresponding **"Reset"** button on the panel to reset daily usage to zero.

### C. Troubleshooting Stateful Circuit Breakers
If a social platform's API (e.g. Telegram or Instagram) goes down, our stateful circuit breakers immediately trip to **"OPEN"** (Red badge) to prevent pipeline crashes.
*   **The System's Action**: Outbound campaign posting for that channel shifts immediately to safe mocks, while other healthy platforms continue broadcasting live.
*   **Your Action**: 
    1. Check your API credentials, internet network, or system keys.
    2. Once you verify the platform is back online, click the **"Reset"** button next to that breaker's status pill. This forces the breaker to **"CLOSED"** (Green badge), enabling live posts again.

---

## ⏳ Part 2: Reviewing the Campaign Preview Gate

The **Campaign Preview Gate** sits on the main page. This is your high-priority daily task container.

```
+-------------------------------------------------------------------------+
|  ⏳ SRE Preview Gate: Campaigns Pending Approval  [Awaiting Review]      |
+-------------------------------------------------------------------------+
|  [⏰ Auto-publishes in 24m 12s]                                         |
|  Image Preview      Title: Special offer: The Derma Co Skincare Pack    |
|  [Thumbnail]        Price: ₹599    Platform: Direct Store               |
|                     Sector: Beauty & Skincare                           |
|                                                                         |
|  Caption: ✨ Treat your skin! Get The Derma Co Skincare Pack at Rs.599. |
|  [Buy here: https://api.mock-affiliate-network.com/go/...]              |
|                                                                         |
|  +---------------------------+   +-----------------------------------+  |
|  |    ✓ Approve & Post       |   |          ✗ Reject / Skip          |  |
|  +---------------------------+   +-----------------------------------+  |
+-------------------------------------------------------------------------+
```

### Steps to Review Sourced Campaigns:
1.  **Check the Expiry Timer**: Note the **"⏰ Auto-publishes in Xm Ys"** badge. If you do not click anything, the campaign will go live on all active social media channels when the timer hits zero.
2.  **Toggle Audience Languages**: Use the **English / हिन्दी / தமிழ்** toggle buttons at the top of the results block to preview translated copy.
3.  **Perform manual Actions**:
    *   **Approve & Post**: Distributes the creative assets, captions, and links live to Telegram, Twitter, Instagram, and WhatsApp. It updates the database status to `published`.
    *   **Reject / Skip**: Rejects and deletes the campaign. The assets are discarded, ensuring it never posts to social channels.

---

## ⚙️ Part 3: Adjusting Configuration Settings

Go to the **Settings** page via the top navigation bar to configure affiliate IDs and parameters.

### Form Fields & Adjusted Values:
*   **Affiliate Network Tags**:
    *   **Amazon Associate Tag**: Enter your custom store ID (e.g. `marketingai-21`). This dynamically appends to Amazon product links.
    *   **Flipkart Affiliate ID**: Enter your Flipkart network ID (e.g. `marketingai`).
*   **Campaign Preview Gate**:
    *   **Auto-Publish Timeout**: Enter the duration in minutes (e.g. `30` or `60`). Sourced campaigns are held for this duration before automatic posting.
*   **Active Sourcing Sectors**:
    *   **Toggle Checkboxes**: Check or uncheck sectors (e.g. `smartphones`, `fashion_men`, `live_links`) to activate/deactivate crawling during scheduler sweeps.
*   **Legacy Security Configurations**:
    *   **Merchant Commission JSON**: A structured table outlining commission schedules. Do not modify unless merchant contract terms change.
    *   **Conversion Webhook Secret**: The signature key matching affiliate postbacks. Keep confidential.

*Click **"Save Configuration"** to update settings instantly across all running services.*

---

## 📊 Part 4: ROI Reporting & CSV Downloads

Navigate to the **History** page via the top navigation bar.

### A. Downloading the ROI CSV Report
At the top of the history page, click the green **"📈 Download CSV ROI Report"** button. This downloads a comprehensive spreadsheet containing:
*   **Campaign ID & Title**: Sourced item info.
*   **Active Sector**: Target category.
*   **Variant Used (A vs B)**: Indicating whether caption style A or B was presented to buyers.
*   **Total Human Clicks**: Filtered click count (crawler/bot clicks are automatically discarded!).
*   **Verified Conversions**: Count of sales recorded via webhook.
*   **Total Revenue**: Total merchant commissions generated.

### B. Evaluating A/B Variant Performance
At the bottom of the history page, review the **Campaign CTR Performance Tracker** and **A/B Results** panels. 
*   **Epsilon-Greedy Bandit**: The suite automatically displays Variant A or Variant B. Over time, it mathematically analyzes click-through rates (CTR) and conversion rates (CVR), shifting traffic dynamically to the variant that yields the most revenue.
*   **Action**: Use the CSV report data to see which visual style or copy variant (A or B) has the highest conversion rate, helping your team craft stronger copy.

---

🏆 **Final Notes: Daily operation tasks are reduced to under 10 minutes. Health checks run autonomously, ensuring reliable social media publishing.**
