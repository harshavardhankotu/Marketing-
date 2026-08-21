# Compliance Matrix — Sector-Wise Legal Obligations

Operating context: single-operator deals publisher driving organic Indian
traffic to Amazon Associates India (amazon.in). This matrix maps every legal
regime that touches the stack to its implemented control and residual duty.
Review quarterly or before entering any new vertical.

Legend: ✅ implemented · 🟡 operator action required (process, not code) · ⚪ not applicable by design

| # | Sector / Activity | Regime | Key obligation | Status | Control / Action |
|---|---|---|---|---|---|
| 1 | Affiliate links & earnings claims | Amazon Associates Operating Agreement (IN) | Per-post earning disclosure; site must be public with robust original content; 3 sales in 180 days; no incentivized traffic; PA-API content statement when API used | ✅ | `/disclosure` + per-post `*Affiliate link…` line; public `/deals` SEO pages; in-app 180-day clock (`/api/revenue_clock`); organic-only adapters |
| 2 | Influencer-style advertising | ASCI Guidelines for Influencer Advertising on Social Media (2023) | Prominent material-connection label on every promo; no fake engagement/proof | ✅ | Disclosure auto-appended to captions, cards, pages via `_ensure_disclosure` guard; engagement never fabricated |
| 3 | Price/discount claims | Consumer Protection Act 2019 + CCPA Misleading Ads & Dark Patterns Guidelines (2022); Legal Metaology (Packaged Commodities) Rules for MRP references | Discount/MRP comparisons must be truthful and substantiated; no fake "was" prices | ✅🟡 | MRP is derived from source discount field + tracked price floor (`deal_scorer`); **Operator duty:** spot-check badges monthly vs live listing |
| 4 | Personal data of visitors | Digital Personal Data Protection Act 2023 (India); IT (Reasonable Security Practices) Rules 2011 until DPDP rules operationalize | Itemized notice; purpose limitation; retention limits; children's data stance; grievance redressal; reasonable security | ✅🟡 | `/privacy` covers collection (IP/UA click telemetry, one session cookie, webhook txns), 24-month retention, children's-data stance, grievance route, security posture. **Operator duty:** name a grievance contact in channel bio |
| 5 | Cookies / trackers | DPDP consent principles; no ad-tech present | Strictly-necessary cookie only, disclosed | ✅ | Single HttpOnly session cookie for operators; zero third-party trackers/ad pixels by design |
| 6 | Security incidents | DPDP breach notification (to Data Protection Board + affected users once notified regime active) | Detect, contain, notify without delay | 🟡 | Technical controls: HMAC webhooks, rate limits, WAL+backups, least privilege. **Operator duty:** incident runbook line — rotate `.env` secrets, restore latest `data/backups` snapshot |
| 7 | Distribution on Telegram | Telegram ToS (no spam/scam), channel best practices | Organic posting only; clear channel purpose | ✅ | Broadcast to own channels; retry caps + daily post cap prevent flooding; mock fallback never fabricates delivery |
| 8 | Distribution on X / Instagram | Platform API terms (X API automation rules; Meta IG Graph policy) | Use approved APIs, respect rate limits, disclose automation where required | ✅🟡 | Official APIs only (tweepy/Graph). **Operator duty:** keep app review records; Playwright-X is a personal-account convenience — disable if platform objects |
| 9 | Bulk messaging / calls | TRAI Regulations (UCC/DLT) | Registration before promotional SMS/calls | ⚪ | No SMS/calling anywhere in the stack; do not add telephony |
| 10 | Third-party content in creatives | Copyright Act 1957 (fair dealing for review/reporting); trademark honest use | Editorial identification use; no wholesale image lifting beyond program-provided content | ✅ | Cards are typographic renders; product images come only from PA-API/RSS fields or our own art; brand names referenced nominatively |
| 11 | Intermediary/content rules | IT Rules 2021 due diligence (unlawful content takedown responsiveness) | Grievance handling for content complaints | ✅🟡 | Grievance route published. **Operator duty:** respond to platform notices promptly |
| 12 | Money earned → taxes | Income-tax Act (business income); GST (affiliate commission as taxable supply/service depending on registration threshold & OIDAR analysis) | Bookkeeping, registrations at thresholds | 🟡 | Commission ledger exists in `affiliate_conversions`. **Operator duty:** consult CA; enable GST when applicable |
| 13 | Future: email newsletter | IT Act + SPDI; anti-spam norms; DPDP e-consent | Opt-in list, unsubscribe, notice | ⚪ | Not built — add opt-in capture only after above controls exist |
| 14 | Future: finance/broker niche affiliates | RBI digital-lending advt code, SEBI influencer norms | Heavy restrictions on financial "recommendations" | ⚪ | Not pursued; requires separate legal review before enabling |
| 15 | Future: health/nutraceutical deals | FSSAI advt claims; Drugs & Magic Remedies Act | No therapeutic claims | ⚪ | Not pursued; sector filter excludes these categories |

## Pre-launch operator checklist

- [ ] Apply to Associates only when ready: ≥10 original posts live (run sweeps until satisfied), then set **Settings → Application Date** to start the monitored 180-day clock.
- [ ] Publish grievance/contact handle in every channel bio (matches `/privacy` §8).
- [ ] Put Caddy HTTPS in front (`deploy/Caddyfile`) and flip `SESSION_COOKIE_SECURE=True`.
- [ ] Rotate `POSTBACK_SECRET`/`FLASK_SECRET_KEY` before go-live; store `.env` off-repo.
- [ ] Verify first imported commission report reconciles with dashboard Channel P&L.
- [ ] Monthly: sample-check 5 posted deals against live Amazon prices (claim substantiation).
