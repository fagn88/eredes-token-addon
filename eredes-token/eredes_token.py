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

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)

log("[init] Starting E-Redes Token Refresher...")

try:
    import requests
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
    log("[init] imports OK")
except Exception as e:
    log(f"[init] Failed imports: {e}")
    traceback.print_exc()
    sys.exit(1)

CONFIG_PATH = "/data/options.json"
PROFILE_PATH = "/data/chrome-profile"
# /dashboard e a landing page PUBLICA (sem auth). A area autenticada e /home.
# `driver.get(/home)` deteta `login_required` quando o aat ja nao e enviado/aceite.
# Quando isso acontece, tentamos auto_login (se houver credenciais).
DASHBOARD_URL = "https://balcaodigital.e-redes.pt/home"
LOGIN_URL = "https://balcaodigital.e-redes.pt/login?returnUrl=%2Fhome"
LOGIN_URL_FRAGMENT = "/login"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HA_API = "http://supervisor/core/api"


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception as e:
        log(f"[config] Error: {e}")
        return {
            "refresh_margin_min": 10,
            "refresh_margin_max": 15,
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
        log(f"[jwt] decode error: {e}")
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
    # Enable browser console capture for diagnostics (driver.get_log('browser'))
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    service = Service("/usr/bin/chromedriver")
    try:
        return webdriver.Chrome(service=service, options=options)
    except Exception as e:
        log(f"[driver] Error: {e}")
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
        log(f"[ntfy] {title} (status {resp.status_code})")
    except Exception as e:
        log(f"[ntfy] Error: {e}")


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
    log(f"[ha] sensor state update: {r.status_code}")
    if r.status_code >= 300:
        log(f"[ha] response: {r.text}")
        return False
    return True


def fire_webhook(webhook_id):
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    r = requests.post(
        f"{HA_API}/webhook/{webhook_id}",
        json={}, headers=headers, timeout=10,
    )
    log(f"[ha] webhook fire: {r.status_code}")
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
        log(f"[ha] status={status} ({r.status_code})")
    except Exception as e:
        log(f"[ha] Error publishing status: {e}")


def read_aat_cookie(driver):
    """Le o cookie aat sem navegar. Usado durante o polling de login manual."""
    try:
        cookies = driver.get_cookies()
        return next((c["value"] for c in cookies if c["name"] == "aat"), None)
    except Exception as e:
        log(f"[poll] Error reading cookies: {e}")
        return None


def _summarize_cookies(cookies):
    """Resumo curto dos cookies relevantes (nome + expiry restante) para debug."""
    rel = [c for c in cookies if c["name"] in ("aat", "SimpleSAML", "PHPSESSID", "psmrkio")]
    out = []
    now_s = datetime.now(timezone.utc).timestamp()
    for c in rel:
        exp = c.get("expiry")
        if exp:
            out.append(f"{c['name']}(exp_in={int(exp - now_s)}s)")
        else:
            out.append(f"{c['name']}(session)")
    return ", ".join(out) or "none"


def _save_failure_artifacts(driver, label):
    """Guarda screenshot + page_source para debug pos-mortem em /data/."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        driver.save_screenshot(f"/data/last_failure_{label}_{ts}.png")
        with open(f"/data/last_failure_{label}_{ts}.html", "w") as f:
            f.write(driver.page_source[:50000])
        log(f"[debug] Saved /data/last_failure_{label}_{ts}.{{png,html}}")
    except Exception as e:
        log(f"[debug] Could not save artifacts: {e}")


def get_credentials_from_ha():
    """Le credenciais E-Redes de input_text helpers no HA.

    Espera input_text.eredes_email e input_text.eredes_password.
    Devolve (email, password) ou (None, None) se nao estiverem definidos.
    Mantem credenciais fora das options do addon (nao loga em
    `[main] Config`, nao vai para repositorio publico).
    """
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    out = {}
    for entity in ("input_text.eredes_email", "input_text.eredes_password"):
        try:
            r = requests.get(f"{HA_API}/states/{entity}", headers=headers, timeout=10)
            if r.status_code == 200:
                out[entity] = r.json().get("state", "").strip()
            else:
                log(f"[creds] {entity} -> HTTP {r.status_code}")
                out[entity] = ""
        except Exception as e:
            log(f"[creds] {entity} fetch error: {e}")
            out[entity] = ""
    email = out.get("input_text.eredes_email", "")
    password = out.get("input_text.eredes_password", "")
    if email and password:
        log(f"[creds] HA credentials loaded for {email[:3]}***@***")
        return email, password
    log("[creds] HA credentials not configured; auto-login disabled")
    return None, None


def auto_login(driver, email, password):
    """Faz login programatico no formulario E-Redes. Returns True/False.

    Tenta multiplos selectors porque o site nao tem id estaveis. Em caso de
    falha (form mudou, reCAPTCHA bloqueou, credenciais erradas), retorna False
    e o caller deve fazer fallback para polling de login manual.
    """
    log("[auto_login] Loading login form...")
    try:
        driver.get(LOGIN_URL)
    except Exception as e:
        log(f"[auto_login] driver.get failed: {e}")
        return False

    # /login mostra primeiro selector "Que tipo de cliente e? Particular|Empresarial".
    # O tile clicavel e um <li class="card">. Click via JS evita problemas de hitbox.
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH,
                "//li[contains(@class,'card') and .//*[contains(text(),'Empresarial')]]"))
        )
        driver.execute_script("""
            const li = Array.from(document.querySelectorAll('li.card'))
                .find(e => e.textContent.includes('Empresarial'));
            if (li) li.click();
        """)
        log("[auto_login] Clicked 'Empresarial' card via JS")
        time.sleep(3)
    except Exception as e:
        log(f"[auto_login] No 'Empresarial' tile: {e} — assuming direct form")

    # Wait for Angular SPA to render the form (~5-10s).
    # E-Redes usa input[id=username]/formcontrolname=username (nao "email" em
    # nenhum atributo), apesar do label ser "E-mail".
    email_selectors = [
        "input[id='username']",
        "input[formcontrolname='username']",
        "input[name='username']",
        "input[type='email']",
        "input[formcontrolname*='mail' i]",
        "input[name*='mail' i]",
        "input[id*='mail' i]",
    ]
    email_field = None
    # Espera global de 30s pelo primeiro selector (Angular bootstrap), restantes 5s cada
    for i, sel in enumerate(email_selectors):
        timeout = 30 if i == 0 else 5
        try:
            email_field = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            log(f"[auto_login] Email field located via: {sel}")
            break
        except Exception:
            continue
    if not email_field:
        log("[auto_login] Email field not found")
        _save_failure_artifacts(driver, "auto_login_no_email")
        return False

    try:
        email_field.clear()
        email_field.send_keys(email)
    except Exception as e:
        log(f"[auto_login] Could not fill email: {e}")
        return False

    try:
        pw_field = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pw_field.clear()
        pw_field.send_keys(password)
        log("[auto_login] Password filled")
    except Exception as e:
        log(f"[auto_login] Password field not found/fill failed: {e}")
        _save_failure_artifacts(driver, "auto_login_no_password")
        return False

    submit = None
    for sel in ["button[type='submit']",
                "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'entrar')]",
                "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]",
                "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'iniciar')]"]:
        try:
            if sel.startswith("//"):
                submit = driver.find_element(By.XPATH, sel)
            else:
                submit = driver.find_element(By.CSS_SELECTOR, sel)
            log(f"[auto_login] Submit button via: {sel[:50]}")
            break
        except Exception:
            continue
    if not submit:
        log("[auto_login] Submit button not found")
        _save_failure_artifacts(driver, "auto_login_no_submit")
        return False

    try:
        submit.click()
        log("[auto_login] Submit clicked, waiting for redirect...")
    except Exception as e:
        log(f"[auto_login] Click failed: {e}")
        return False

    # Wait for redirect away from /login (success). Up to 30s.
    try:
        WebDriverWait(driver, 30).until(
            lambda d: LOGIN_URL_FRAGMENT not in d.current_url
        )
        log(f"[auto_login] Redirected to: {driver.current_url}")
    except Exception:
        # Still on /login → check for visible error
        try:
            err_el = driver.find_element(
                By.CSS_SELECTOR,
                ".error, [class*='error'], .alert, [class*='alert']",
            )
            log(f"[auto_login] Login error visible: {err_el.text[:200]!r}")
        except Exception:
            log("[auto_login] Timeout — still on /login, no error visible (reCAPTCHA?)")
        _save_failure_artifacts(driver, "auto_login_no_redirect")
        return False

    # Wait for aat cookie to appear (Angular finishes set after redirect)
    for i in range(20):
        time.sleep(1)
        try:
            cookies = driver.get_cookies()
            if any(c["name"] == "aat" for c in cookies):
                log(f"[auto_login] aat cookie detected after {i+1}s")
                return True
        except Exception as e:
            log(f"[auto_login] get_cookies error: {e}")
    log("[auto_login] aat cookie did NOT appear after 20s")
    _save_failure_artifacts(driver, "auto_login_no_aat")
    return False


def refresh_cookie(driver, debug_label=""):
    """Load /home, wait for SPA SAML refresh, extract aat cookie.

    Returns (cookie_value, None, cookie_expiry_unix) on success,
    (None, "login_required", None) if not authenticated,
    (None, "error: ...", None) otherwise.
    cookie_expiry_unix e o `expires_utc` do cookie aat (mais curto que JWT exp;
    crucial para nao agendar refresh depois do cookie ja ter sido deletado).
    """
    try:
        # Pre-state: log URL + cookies BEFORE driver.get
        try:
            pre_url = driver.current_url
        except Exception:
            pre_url = "(none)"
        pre_aat = None
        try:
            pre_cookies = driver.get_cookies()
            pre_aat = next((c["value"] for c in pre_cookies if c["name"] == "aat"), None)
            pre_aat_exp = jwt_exp(pre_aat) if pre_aat else None
            now_s = datetime.now(timezone.utc).timestamp()
            pre_aat_left = int(pre_aat_exp - now_s) if pre_aat_exp else None
        except Exception:
            pre_cookies, pre_aat_left = [], None
        log(f"[refresh] PRE  url={pre_url} cookies=[{_summarize_cookies(pre_cookies)}] "
            f"aat_jwt_left={pre_aat_left}s")

        log(f"[refresh] Loading {DASHBOARD_URL}...")
        driver.get(DASHBOARD_URL)

        try:
            WebDriverWait(driver, 20).until(
                lambda d: LOGIN_URL_FRAGMENT in d.current_url
                          or "/home" in d.current_url.lower()
                          or "dashboard" in d.current_url.lower()
            )
        except Exception:
            return (None, "error: timeout aguardando pagina carregar")

        current = driver.current_url
        log(f"[refresh] POST url={current}")
        if LOGIN_URL_FRAGMENT in current:
            _save_failure_artifacts(driver, f"redirect_login_{debug_label}")
            return (None, "login_required", None)

        # Allow Angular bootstrap + SAML auto-refresh to complete.
        # 5s era curto demais; 20s da margem para o SDK chamar /saml e re-set cookie.
        time.sleep(20)

        cookies = driver.get_cookies()
        log(f"[refresh] AFTER cookies=[{_summarize_cookies(cookies)}] "
            f"title={(driver.title or '')[:60]!r}")
        aat_cookie = next((c for c in cookies if c["name"] == "aat"), None)
        aat = aat_cookie["value"] if aat_cookie else None
        cookie_exp = aat_cookie.get("expiry") if aat_cookie else None
        if not aat:
            try:
                browser_logs = driver.get_log("browser")[-20:]
                for entry in browser_logs:
                    log(f"[refresh] browser_log: {entry.get('level')} "
                        f"{entry.get('message','')[:200]}")
            except Exception as e:
                log(f"[refresh] Could not get browser logs: {e}")
            _save_failure_artifacts(driver, f"no_aat_{debug_label}")
            return (None, "login_required", None)
        new_exp = jwt_exp(aat)
        if new_exp:
            now_s = datetime.now(timezone.utc).timestamp()
            log(f"[refresh] OK aat_jwt_left={int(new_exp - now_s)}s "
                f"cookie_left={int(cookie_exp - now_s) if cookie_exp else 'none'}s "
                f"renewed={'yes' if aat != pre_aat else 'NO (same as before)'}")
        return (aat, None, cookie_exp)
    except Exception as e:
        return (None, f"error: {e}", None)


def seconds_until_next_refresh(token, cookie_exp, margin_min, margin_max):
    """Sleep duration: refresh antes do MIN(JWT_exp, cookie_expires_utc).

    O cookie aat tem expires_utc tipicamente 30min ANTES do JWT exp claim.
    Quando o cookie e deletado pelo Chrome (passa expires_utc), driver.get
    deixa de o enviar ao servidor; logo o refresh tem de acontecer antes.
    Falls back to 30 min if neither is available.
    """
    jexp = jwt_exp(token)
    candidates = [e for e in (jexp, cookie_exp) if e]
    if not candidates:
        log("[sched] No exp info; fallback 1800s")
        return 1800
    target = min(candidates)
    target_label = "cookie" if cookie_exp and target == cookie_exp else "jwt"
    margin_s = random.randint(margin_min * 60, margin_max * 60)
    now_s = datetime.now(timezone.utc).timestamp()
    wait_s = int(target - margin_s - now_s)
    # Safety clamp: never sleep less than 60s (avoid tight loops) or more than 6h
    wait_s = max(60, min(wait_s, 6 * 3600))
    target_local = datetime.fromtimestamp(target, tz=timezone.utc).astimezone()
    log(f"[sched] target={target_label} exp={target_local.isoformat()} "
        f"margin={margin_s//60}min -> sleep {wait_s}s")
    return wait_s


def do_refresh_cycle(driver, config, prev_token=None):
    """Run one refresh cycle. Returns (token, stale, cookie_exp) on success, None on error.

    cookie_exp is the unix timestamp when the aat cookie expires_utc — used by
    seconds_until_next_refresh para nao agendar refresh apos o Chrome ter
    deletado o cookie.
    """
    token, err, cookie_exp = refresh_cookie(driver, debug_label="try1")

    # Transient login_required: SPA may briefly show login overlay while
    # SAML is mid-refresh. Retry once after 30s before escalating.
    if err == "login_required":
        log("[refresh] login_required on first try; retrying in 30s...")
        time.sleep(30)
        token, err, cookie_exp = refresh_cookie(driver, debug_label="try2")

    if err == "login_required":
        # Antes do polling manual, tentar auto-login se houver credenciais no HA
        ha_email, ha_password = get_credentials_from_ha()
        if ha_email and ha_password:
            log("[refresh] Attempting auto-login with HA-stored credentials...")
            if auto_login(driver, ha_email, ha_password):
                try:
                    cookies = driver.get_cookies()
                    aat_c = next((c for c in cookies if c["name"] == "aat"), None)
                    if aat_c:
                        token = aat_c["value"]
                        cookie_exp = aat_c.get("expiry")
                        err = None
                        log("[refresh] Auto-login OK, aat captured")
                except Exception as e:
                    log(f"[refresh] post-auto-login cookie read failed: {e}")
            else:
                log("[refresh] Auto-login failed — falling back to manual polling")

    if err == "login_required":
        publish_status("login_required", "SAML session expired (auto-login also failed)")
        notify_phone(
            config["ntfy_topic"],
            "E-Redes login necessario",
            "Auto-login falhou. Abre noVNC em http://<ha-ip>:6081 para fazer login manual.",
            priority="urgent",
        )
        # Polling passivo — NAO navegar (so ler cookies) para nao interromper o login manual
        log("[refresh] Polling cookies every 30s until user logs in...")
        while True:
            time.sleep(30)
            try:
                cookies = driver.get_cookies()
                aat_c = next((c for c in cookies if c["name"] == "aat"), None)
                if aat_c:
                    token = aat_c["value"]
                    cookie_exp = aat_c.get("expiry")
                    log("[refresh] Login detected, token captured")
                    break
            except Exception as e:
                log(f"[poll] read cookies error: {e}")
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
        log("[refresh] aat unchanged since last cycle (Angular did not refresh yet)")

    if not publish_token_to_ha(token):
        publish_status("error", "sensor update failed")
        return None
    if not fire_webhook(config["ha_webhook_id"]):
        publish_status("error", "webhook fire failed")
        return None
    publish_status("ok", "stale" if stale else "refreshed")
    return (token, stale, cookie_exp)


def main():
    config = load_config()
    log(f"[main] Config: {config}")

    driver = create_driver()
    if driver is None:
        log("[main] Initial driver creation failed, exiting")
        sys.exit(1)

    log("[main] Startup refresh...")
    last_token = None
    last_cookie_exp = None
    last_result = None
    try:
        last_result = do_refresh_cycle(driver, config)
    except Exception as e:
        log(f"[main] Startup refresh exception: {e}")
        traceback.print_exc()
    if last_result:
        last_token, _, last_cookie_exp = last_result

    margin_min = config.get("refresh_margin_min", 2)
    margin_max = config.get("refresh_margin_max", 8)

    while True:
        if last_result:
            _, stale, _ = last_result
            if stale:
                wait_s = 60  # Angular didn't refresh; try again soon
                log(f"[main] Stale aat — retry in {wait_s}s")
            else:
                wait_s = seconds_until_next_refresh(
                    last_token, last_cookie_exp, margin_min, margin_max)
        else:
            wait_s = 300  # sem token, tenta de novo em 5min
            log(f"[main] No token yet, retry in {wait_s}s")
        time.sleep(wait_s)

        try:
            last_result = do_refresh_cycle(driver, config, prev_token=last_token)
            if last_result:
                last_token, _, last_cookie_exp = last_result
        except Exception as e:
            log(f"[main] Cycle exception: {e}")
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
                log("[main] Driver recreate failed, retrying in 5min")
                time.sleep(300)
                driver = create_driver()


if __name__ == "__main__":
    main()
