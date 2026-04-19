#!/usr/bin/env python3
"""E-Redes Token Refresher - Home Assistant Add-on"""

import sys
import json
import os
import time
import base64
import random
import traceback
from datetime import datetime, timezone

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

print("[init] Starting E-Redes Token Refresher...", flush=True)

try:
    import requests
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    print("[init] imports OK", flush=True)
except Exception as e:
    print(f"[init] Failed imports: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

CONFIG_PATH = "/data/options.json"
PROFILE_PATH = "/data/chrome-profile"
DASHBOARD_URL = "https://balcaodigital.e-redes.pt/dashboard"
LOGIN_URL_FRAGMENT = "/login"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HA_API = "http://supervisor/core/api"


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception as e:
        print(f"[config] Error: {e}", flush=True)
        return {
            "refresh_margin_min": 5,
            "refresh_margin_max": 25,
            "ntfy_topic": "eredes-token-fn2026",
            "ha_webhook_id": "update_eredes_token",
        }


def jwt_exp(token):
    """Extract 'exp' claim (unix seconds) from a JWT without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64))
        return int(data.get("exp")) if data.get("exp") else None
    except Exception as e:
        print(f"[jwt] decode error: {e}", flush=True)
        return None


def create_driver():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument(f"--user-data-dir={PROFILE_PATH}")
    options.add_argument("--window-size=1280,720")
    options.binary_location = "/usr/bin/chromium-browser"
    service = Service("/usr/bin/chromedriver")
    try:
        return webdriver.Chrome(service=service, options=options)
    except Exception as e:
        print(f"[driver] Error: {e}", flush=True)
        return None


def notify_phone(topic, title, message, priority="default"):
    try:
        resp = requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "key,zap",
            },
            timeout=10,
        )
        print(f"[ntfy] {title} (status {resp.status_code})", flush=True)
    except Exception as e:
        print(f"[ntfy] Error: {e}", flush=True)


def publish_token_to_ha(token):
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "state": "ok",
        "attributes": {
            "token": token,
            "last_refresh": datetime.now().isoformat(timespec="seconds"),
            "source": "eredes-token-addon",
        },
    }
    r = requests.post(
        f"{HA_API}/states/sensor.token_bridge_eredes",
        json=body, headers=headers, timeout=10,
    )
    print(f"[ha] sensor state update: {r.status_code}", flush=True)
    if r.status_code >= 300:
        print(f"[ha] response: {r.text}", flush=True)
        return False
    return True


def fire_webhook(webhook_id):
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    r = requests.post(
        f"{HA_API}/webhook/{webhook_id}",
        json={}, headers=headers, timeout=10,
    )
    print(f"[ha] webhook fire: {r.status_code}", flush=True)
    return r.status_code < 300


def publish_status(status, detail=""):
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "state": status,
        "attributes": {
            "detail": detail,
            "last_update": datetime.now().isoformat(timespec="seconds"),
        },
    }
    try:
        r = requests.post(
            f"{HA_API}/states/sensor.eredes_token_status",
            json=body, headers=headers, timeout=10,
        )
        print(f"[ha] status={status} ({r.status_code})", flush=True)
    except Exception as e:
        print(f"[ha] Error publishing status: {e}", flush=True)


def read_aat_cookie(driver):
    """Le o cookie aat sem navegar. Usado durante o polling de login manual."""
    try:
        cookies = driver.get_cookies()
        return next((c["value"] for c in cookies if c["name"] == "aat"), None)
    except Exception as e:
        print(f"[poll] Error reading cookies: {e}", flush=True)
        return None


def refresh_cookie(driver):
    """Load dashboard, wait for Firebase refresh, extract aat cookie.

    Returns (cookie_value, None) on success, (None, "login_required") if
    redirected to /login, (None, "error: ...") otherwise.
    """
    try:
        print("[refresh] Loading dashboard...", flush=True)
        driver.get(DASHBOARD_URL)

        try:
            WebDriverWait(driver, 20).until(
                lambda d: LOGIN_URL_FRAGMENT in d.current_url
                          or "dashboard" in d.current_url.lower()
            )
        except Exception:
            return (None, "error: timeout aguardando pagina carregar")

        current = driver.current_url
        print(f"[refresh] Current URL: {current}", flush=True)
        if LOGIN_URL_FRAGMENT in current:
            return (None, "login_required")

        time.sleep(5)  # allow Angular bootstrap + aat refresh to complete

        cookies = driver.get_cookies()
        aat = next((c["value"] for c in cookies if c["name"] == "aat"), None)
        if not aat:
            # Sem cookie aat significa sessao nao autenticada (o SPA pode manter
            # URL /dashboard mas render um login overlay sem cookie de sessao).
            return (None, "login_required")
        return (aat, None)
    except Exception as e:
        return (None, f"error: {e}")


def seconds_until_next_refresh(token, margin_min, margin_max):
    """Sleep duration until next refresh: (exp - random[margin_min, margin_max] minutes).

    Falls back to 30 min if token can't be decoded.
    """
    exp = jwt_exp(token)
    if exp is None:
        print("[sched] Could not decode exp; fallback 1800s", flush=True)
        return 1800
    margin_s = random.randint(margin_min * 60, margin_max * 60)
    now_s = datetime.now(timezone.utc).timestamp()
    wait_s = int(exp - margin_s - now_s)
    # Safety clamp: never sleep less than 60s (avoid tight loops) or more than 6h
    wait_s = max(60, min(wait_s, 6 * 3600))
    exp_local = datetime.fromtimestamp(exp, tz=timezone.utc).astimezone()
    print(f"[sched] cookie exp={exp_local.isoformat()} "
          f"margin={margin_s//60}min -> sleep {wait_s}s", flush=True)
    return wait_s


def do_refresh_cycle(driver, config, prev_token=None):
    """Run one refresh cycle. Returns new token on success, None on error.

    Sets a 'stale' attribute on the returned value via tuple to signal when
    the SPA returned the same aat (i.e. Angular did not refresh).
    """
    token, err = refresh_cookie(driver)

    # Transient login_required: SPA may briefly show login overlay while
    # Firebase is mid-refresh. Retry once after 30s before escalating.
    if err == "login_required":
        print("[refresh] login_required on first try; retrying in 30s...", flush=True)
        time.sleep(30)
        token, err = refresh_cookie(driver)

    if err == "login_required":
        publish_status("login_required", "Firebase session expired")
        notify_phone(
            config["ntfy_topic"],
            "E-Redes login necessario",
            "Sessao expirou. Abre noVNC em http://<ha-ip>:6081 para fazer login.",
            priority="urgent",
        )
        # Polling passivo — NAO navegar (so ler cookies) para nao interromper o login manual
        print("[refresh] Polling cookies every 30s until user logs in...", flush=True)
        while True:
            time.sleep(30)
            token = read_aat_cookie(driver)
            if token:
                print("[refresh] Login detected, token captured", flush=True)
                break
    elif err is not None:
        publish_status("error", err)
        notify_phone(
            config["ntfy_topic"],
            "E-Redes refresh falhou",
            err,
            priority="high",
        )
        return None

    stale = (prev_token is not None and token == prev_token)
    if stale:
        print("[refresh] aat unchanged since last cycle (Angular did not refresh yet)", flush=True)

    if not publish_token_to_ha(token):
        publish_status("error", "sensor update failed")
        return None
    if not fire_webhook(config["ha_webhook_id"]):
        publish_status("error", "webhook fire failed")
        return None
    publish_status("ok", "stale" if stale else "refreshed")
    return (token, stale)


def main():
    config = load_config()
    print(f"[main] Config: {config}", flush=True)

    driver = create_driver()
    if driver is None:
        print("[main] Initial driver creation failed, exiting", flush=True)
        sys.exit(1)

    print("[main] Startup refresh...", flush=True)
    last_token = None
    last_result = None
    try:
        last_result = do_refresh_cycle(driver, config)
    except Exception as e:
        print(f"[main] Startup refresh exception: {e}", flush=True)
        traceback.print_exc()
    if last_result:
        last_token, _ = last_result

    margin_min = config.get("refresh_margin_min", 2)
    margin_max = config.get("refresh_margin_max", 8)

    while True:
        if last_result:
            _, stale = last_result
            if stale:
                wait_s = 60  # Angular didn't refresh; try again soon
                print(f"[main] Stale aat — retry in {wait_s}s", flush=True)
            else:
                wait_s = seconds_until_next_refresh(last_token, margin_min, margin_max)
        else:
            wait_s = 300  # sem token, tenta de novo em 5min
            print(f"[main] No token yet, retry in {wait_s}s", flush=True)
        time.sleep(wait_s)

        try:
            last_result = do_refresh_cycle(driver, config, prev_token=last_token)
            if last_result:
                last_token, _ = last_result
        except Exception as e:
            print(f"[main] Cycle exception: {e}", flush=True)
            traceback.print_exc()
            last_result = None
            last_token = None
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            driver = create_driver()
            while driver is None:
                print("[main] Driver recreate failed, retrying in 5min", flush=True)
                time.sleep(300)
                driver = create_driver()


if __name__ == "__main__":
    main()
