"""
Interactive setup wizard — turns ".env fill-in" into a 5-minute guided flow.

    python setup_wizard.py            # interactive: asks, saves, validates
    python setup_wizard.py --check    # non-interactive: validate current .env

Live validations use only FREE endpoints:
    Telegram   -> getMe + getChatMemberCount (real credential proof)
    SMTP       -> connect + STARTTLS + login (no mail sent)
    Gemini     -> key present (a ping would burn free-tier quota)
    Amazon     -> tag shape + PA-API key presence

Secrets are generated with `secrets` (never random.org, never hardcoded).
Existing .env values are preserved unless you accept a new one.
"""

import os
import sys
import re
import secrets

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

GENERATED_KEYS = ["FLASK_SECRET_KEY", "POSTBACK_SECRET"]


def gen_secret():
    return secrets.token_hex(32)


def valid_email(v):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", v or ""))


def valid_amazon_tag(tag):
    return bool(tag) and "-" in tag and len(tag) >= 5


# ─────────────────────────────────────────────────────────────────────────────
# LIVE VALIDATORS (free endpoints only)
# ─────────────────────────────────────────────────────────────────────────────
def validate_telegram(token, chat_id):
    """Returns (ok, detail). Real API round-trip."""
    if not token or not chat_id:
        return False, "token/chat_id missing"
    try:
        import requests
        r = requests.post(f"https://api.telegram.org/bot{token}/getMe", timeout=8)
        if r.status_code != 200 or not r.json().get("ok"):
            return False, f"getMe failed: {r.text[:80]}"
        bot_name = r.json()["result"].get("username", "?")
        r2 = requests.post(
            f"https://api.telegram.org/bot{token}/getChatMemberCount",
            data={"chat_id": chat_id}, timeout=8)
        if r2.status_code == 200 and r2.json().get("ok"):
            return True, f"@{bot_name} OK · channel has {r2.json()['result']} members"
        return True, f"@{bot_name} OK · chat check pending (bot may need to be added)"
    except Exception as exc:
        return False, str(exc)[:120]


def validate_smtp(host, port, user, password):
    """Login-only probe; sends nothing."""
    if not host:
        return None, "not configured (newsletter confirm links will be logged)"
    try:
        import smtplib
        with smtplib.SMTP(host, int(port), timeout=12) as s:
            try:
                s.starttls()
            except smtplib.SMTPException:
                pass
            if user and password:
                s.login(user, password)
        return True, f"{host}:{port} login OK"
    except Exception as exc:
        return False, str(exc)[:120]


def validate_gemini(key):
    if not key:
        return None, "not configured (template copy will be used)"
    return True, "key present (quota-safe: no ping sent)"


# ─────────────────────────────────────────────────────────────────────────────
# ENV FILE HANDLING
# ─────────────────────────────────────────────────────────────────────────────
def read_env():
    values = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    values[k.strip()] = v.strip()
    return values


def write_env(values):
    lines = []
    for k, v in values.items():
        lines.append(f"{k}={v}")
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# WIZARD FLOW
# ─────────────────────────────────────────────────────────────────────────────
def ask(prompt, current, default="", secret=False):
    shown = "(keep)" if current else default
    suffix = f" [{shown}]" if shown else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    if not raw:
        return current or default
    return raw


def run_wizard():
    env = read_env()

    print("=" * 62)
    print(" AFFILIATE SUITE SETUP WIZARD")
    print("=" * 62)
    print("Press Enter to keep any existing value.\n")

    # 1. Secrets first.
    for key in GENERATED_KEYS:
        if not env.get(key) or "change_me" in env.get(key, ""):
            new = gen_secret()
            env[key] = new
            print(f"[auto] {key} generated ({new[:12]}…)")
    if env.get("ADMIN_DEFAULT_PASSWORD", "admin123") == "admin123":
        new_pw = secrets.token_urlsafe(9)
        use = input(f"[!] Admin password is default. Generate '{new_pw}'? [Y/n]: ").strip().lower()
        if use != "n":
            env["ADMIN_DEFAULT_PASSWORD"] = new_pw
            print("[auto] ADMIN_DEFAULT_PASSWORD updated — store it now!")

    # 2. Amazon.
    env["AMAZON_ASSOCIATE_TAG"] = ask(
        "Amazon Associate Tag (e.g. yourname-21)", env.get("AMAZON_ASSOCIATE_TAG", ""))
    env["AMAZON_PAAPI_ACCESS_KEY"] = ask(
        "PA-API Access Key (blank = RSS sourcing)", env.get("AMAZON_PAAPI_ACCESS_KEY", ""))
    env["AMAZON_PAAPI_SECRET_KEY"] = ask(
        "PA-API Secret Key", env.get("AMAZON_PAAPI_SECRET_KEY", ""))

    # 3. Telegram.
    env["TELEGRAM_BOT_TOKEN"] = ask(
        "Telegram Bot Token (from @BotFather)", env.get("TELEGRAM_BOT_TOKEN", ""))
    env["TELEGRAM_CHAT_ID"] = ask(
        "Telegram Channel ID (@handle or -100…)", env.get("TELEGRAM_CHAT_ID", ""))
    env["ADMIN_TELEGRAM_ID"] = ask(
        "Your Telegram user id (for alerts)", env.get("ADMIN_TELEGRAM_ID", ""))

    # 4. Optional networks.
    env["FLIPKART_AFFID"] = ask("Flipkart affid (blank = skip)", env.get("FLIPKART_AFFID", ""))
    env["MYNTRA_AFF_ID"] = ask("Myntra aff_id (blank = skip)", env.get("MYNTRA_AFF_ID", ""))

    # 5. Public URL + grievance.
    env_defaults = {"PUBLIC_BASE_URL": ""}
    env.setdefault("PUBLIC_BASE_URL", "")
    env["PUBLIC_BASE_URL"] = ask(
        "Public site URL (https:// once Caddy is live)",
        env.get("PUBLIC_BASE_URL", ""), "https://deals.example.com")
    env.setdefault("GRIEVANCE_EMAIL", "")
    env["GRIEVANCE_EMAIL"] = ask(
        "Grievance contact email", env.get("GRIEVANCE_EMAIL", ""), "grievances@example.com")

    # 6. SMTP (optional).
    env["SMTP_HOST"] = ask("SMTP host (blank = log links in dev)", env.get("SMTP_HOST", ""))
    if env["SMTP_HOST"]:
        env["SMTP_PORT"] = ask("SMTP port", env.get("SMTP_PORT", "587"), "587")
        env["SMTP_USER"] = ask("SMTP user", env.get("SMTP_USER", ""))
        env["SMTP_PASSWORD"] = ask("SMTP password", env.get("SMTP_PASSWORD", ""))
        env["SMTP_FROM"] = ask("From address", env.get("SMTP_FROM", ""), "deals@example.com")

    write_env(env)
    print("\n[saved] .env written.\n")
    report(env)


def report(env=None):
    env = env or read_env()
    print("-" * 62)
    print(" VALIDATION REPORT")
    print("-" * 62)

    ok, detail = validate_telegram(env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID"))
    print(f" telegram : {'PASS' if ok else 'WARN'}  {detail}")

    state, detail = validate_smtp(env.get("SMTP_HOST"), env.get("SMTP_PORT"),
                                  env.get("SMTP_USER"), env.get("SMTP_PASSWORD"))
    label = {True: "PASS", False: "FAIL", None: "SKIP"}[state]
    print(f" smtp     : {label}  {detail}")

    state, detail = validate_gemini(env.get("GEMINI_API_KEY"))
    label = {True: "PASS", False: "FAIL", None: "SKIP"}[state]
    print(f" gemini   : {label}  {detail}")

    tag = env.get("AMAZON_ASSOCIATE_TAG", "")
    print(f" amazon   : {'PASS' if valid_amazon_tag(tag) else 'WARN'}  "
          f"{tag if tag else 'no associate tag yet'}")
    paapi = bool(env.get("AMAZON_PAAPI_ACCESS_KEY")) and bool(env.get("AMAZON_PAAPI_SECRET_KEY"))
    print(f" pa-api   : {'PASS' if paapi else 'SKIP'}  "
          f"{'keys present' if paapi else 'RSS sourcing until approved'}")

    sec = all(env.get(k) and "change_me" not in env[k] for k in GENERATED_KEYS)
    print(f" secrets  : {'PASS' if sec else 'FAIL'}  flask/postback rotated")
    print("-" * 62)
    print("Next: python app.py  →  login with your admin password.")
    return env


if __name__ == "__main__":
    if "--check" in sys.argv:
        report()
    else:
        try:
            run_wizard()
        except KeyboardInterrupt:
            print("\n[cancelled] nothing broken — rerun anytime.")
