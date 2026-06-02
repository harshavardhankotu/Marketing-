# Risks and Limitations Assessment: Automated AI Marketing Suite

This document outlines structural limitations, security risks, system bottlenecks, API quota limits, and areas requiring active human supervision in the Automated AI Marketing Suite.

---

## 1. Sourcing and Anti-Bot Restraints

1.  **CAPTCHAs and Scraping Blocks**:
    *   *Risk*: When resolving third-party product links via `live_links`, retail sites (such as Amazon or Flipkart) frequently deploy Cloudflare, Datadome, or standard CAPTCHA defense walls.
    *   *System Impact*: Headless Playwright Chromium processes can resolve standard URL redirects, but may fail to parse DOM content when confronted with intensive JavaScript validation sweeps.
    *   *Mitigation*: The system falls back to robust localized parsing layers and handles empty scrapings gracefully without crashing the pipeline. However, active scraping of protected pages will require proxies or residential IP rotations for high-volume operators.

---

## 2. API Quotas and External Latencies

1.  **Google Gemini API Quota Limits**:
    *   *Quota Constraint*: Free tiers of the Gemini API are capped by Requests Per Minute (RPM) and Tokens Per Minute (TPM).
    *   *System Impact*: Concurrent pipeline triggers across multiple sectors can trigger `429 Rate Limit` responses.
    *   *Mitigation*: Copywriters are built to fall back immediately to high-fidelity localized template generators when API keys are absent, exhausted, or offline.
2.  **Telegram Bot API Constraints**:
    *   *Rate Limits*: Telegram bots are capped at a maximum of 30 messages per second, and no more than 20 messages per minute to a single group/channel.
    *   *System Impact*: Pushing high-volume graphic posts sequentially across all sectors can trigger rate blocks. The system distributor operates with sequential execution delays to mitigate this.

---

## 3. SQLite Concurrency and Datastore Boundaries

1.  **SQLite Concurrency Contention**:
    *   *Constraint*: SQLite does not support multiple simultaneous writers. During heavy transactional writes, other threads will receive a `database is locked` error.
    *   *System Impact*: If multiple scheduled jobs write logs while active users clicks are being processed by Flask web endpoints, lock failures could arise.
    *   *Mitigation*: All datastore queries are built with a safe connection `timeout=30.0` parameter to serialize writes.
2.  **Storage Scale Boundaries**:
    *   *Growth Constraint*: sqlite files grow larger over time as click logs accumulate. Extremely large databases on minimal servers may experience slower query speeds.
    *   *Mitigation*: Set up a periodic backup strategy (see [DEPLOYMENT.md](file:///c:/marketing/docs/DEPLOYMENT.md)) and delete click log entries older than 180 days during routine server maintenance.

---

## 4. Scheduler Overlap Risks

*   **Job Collision**:
    *   *Risk*: If a pipeline execution takes longer than the scheduled gap (e.g. 19-step sequencing on all sectors under slow networks), the scheduler could trigger a duplicate run of the same pipeline.
    *   *Defense*: APScheduler is configured with `max_instances=1` and `coalesce=True`. In addition, a custom `scheduler_locks` SQLite table provides transactional mutex protection across process boundaries. If a collision is prevented, a lock record is successfully logged to keep the operator informed.

---

## 5. External Affiliate Link Instability

*   **Broken Referral Hops**:
    *   *Risk*: Advertisers frequently modify landing pages, leading to `404 Not Found` responses or broken referral tags.
    *   *System Impact*: Outbound clicks could direct users to broken links.
    *   *Mitigation*: The `affiliate_linker` performs synchronous pre-flight status validation before saving campaigns. However, changes made *after* a campaign is saved cannot be detected dynamically unless a fresh pipeline run is executed.

---

## 6. Open Redirect Redirection Gateway Security

*   **Phishing Vulnerability**:
    *   *Risk*: Since the tracking link gateway `/go/<product_id>` accepts dynamic destination URLs via the `url` parameter, bad actors could exploit this redirection gateway to redirect visitors to malicious sites.
    *   *Defense*: A strict domain whitelist is maintained (`TRUSTED_DOMAINS` in `app.py`). Any link pointing to a domain outside this whitelist is immediately rejected with a `400 Bad Request`.
    *   *Action Required*: System administrators must keep this whitelist updated inside `app.py` as they onboard new affiliate networks.

---

## 7. Areas Still Requiring Manual Monitoring

Despite its autonomous layout, the suite is not a "fire-and-forget" utility. Operators must monitor the following:
*   **Playwright Binary Upkeep**: Retail sites frequently alter their DOM layouts. The HTML parsing logic inside scrapers will require routine visual audits and class adjustments to keep extraction accurate.
*   **Credential Rotations**: Telegram bot tokens and API keys require manual renewal and updating in `.env` to prevent silent distribution disruptions.
*   **SQLite File Size Auditing**: Routine checks are required to ensure the host Windows drive does not run out of space.
