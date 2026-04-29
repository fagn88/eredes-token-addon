# Changelog

## DESCONTINUADO (2026-04-29)

Addon parado e com `boot: manual`. reCAPTCHA do Google bloqueava sistematicamente o auto-login do Selenium/Chromium standard. Renovação de token agora 100% via extensão Chrome `HA Token Bridge` (login manual on-demand). Código mantido no repo caso seja útil reactivar com mitigations anti-bot (undetected-chromedriver, mouse moves, etc.).

## 1.2.2 (2026-04-26)

- Fix selectors do input de email no auto-login: E-Redes usa `id=username`/`formcontrolname=username` (sem "email" em qualquer atributo), apesar do label ser "E-mail". Selectors antigos só procuravam "mail" e nunca encontravam o input. Agora prioriza `id=username`, `formcontrolname=username`, `name=username` (e mantém os "mail" como fallback genérico).

## 1.2.1 (2026-04-26)

- Fix click no tile "Empresarial": elemento e `<li class="card">` (nao `<button>` ou `<a>`). Selector anterior matchava um `<div>` filho sem click handler, ficando preso na pagina de selecao. Agora usa XPath direto para `li.card` + JS `.click()` para evitar problemas de hitbox.

## 1.2.0 (2026-04-26)

- **Credenciais movidas para Home Assistant** (`input_text.eredes_email` + `input_text.eredes_password`). Antes estavam em `eredes_email`/`eredes_password` das options do addon — apareciam em logs `[main] Config: {...}` (potencial fuga em screenshots/issues) e o repositorio do addon e publico. Agora o addon le os helpers via Supervisor REST API; ficam em `/config/.storage/core.entity_registry` (nao versionado).
- Removidos `eredes_email`/`eredes_password` do schema do addon. Caso utilizador queira auto-login, deve criar os 2 input_text helpers em `configuration.yaml`.

## 1.1.1 (2026-04-26)

- Auto-login: clica primeiro o tile "Empresarial" no selector de tipo de cliente (`/login` mostra "Particular | Empresarial" antes do form de email). Sem isto v1.1.0 falhava com "Email field not found" porque o form ainda nao estava na DOM.
- Reduzido timeout dos selectors de email: 30s no primeiro (Angular bootstrap), 5s nos restantes. Antes 5×30s = 150s no pior caso.

## 1.1.0 (2026-04-26)

- **Auto-login programatico:** addon faz login automaticamente quando `login_required` deteta. Novas options `eredes_email` + `eredes_password` (password type, mascarado nos logs do Supervisor). Quando o aat expira, em vez de cair em polling manual, o addon navega `/login`, preenche form, submete e captura o aat resultante.
- Fallback automatico para polling manual se o auto-login falhar (form mudou, reCAPTCHA bloqueou, credenciais erradas).
- Em caso de falha do auto-login, salva screenshot + page_source em `/data/last_failure_auto_login_*` para debug.
- Resolve a causa raiz residual do v1.0.4: o Angular SPA nao renova o aat proactivamente em chamadas a `/home`, só faz refresh quando o utilizador faz login (refresh fresh-after-login funcionou no v1.0.4 mas refreshes subsequentes davam `renewed=NO` ate o aat expirar). Auto-login contorna esta limitacao.

## 1.0.4 (2026-04-26)

- **CAUSA RAIZ definitiva resolvida:** o cookie aat tem `expires_utc` ~30min ANTES do JWT `exp` claim (descoberto: JWT 91min vs cookie 62min de TTL). O scheduling antigo usava só JWT exp, agendando refresh para depois do Chrome ter já apagado o cookie. Sem cookie no profile, `driver.get(/home)` chegava ao servidor sem auth → "login_required". Solução: `seconds_until_next_refresh` agora usa `min(JWT_exp, cookie_expires_utc)` — refresh acontece sempre antes do Chrome limpar o cookie.
- Log do refresh inclui agora ambos `aat_jwt_left` e `cookie_left`.
- Polling pós-login_required agora também extrai `cookie_exp` do aat capturado.

## 1.0.3 (2026-04-26)

- **Bugfix crítico:** URL alvo do refresh era `/dashboard` (landing page PÚBLICA do site, sem auth) — o SPA renderizava a página de boas-vindas com botão "Login" e nunca havia oportunidade do servidor renovar o cookie aat. Mudada para `/home`, a área autenticada real (confirmada pelo `returnUrl=%2Fhome` do redirect de login). Esta era a causa raiz do refresh falhar consistentemente após cada login manual.
- WebDriverWait reconhece agora `/home` (área autenticada) além das URLs anteriores.

## 1.0.2 (2026-04-26)

- Diagnóstico do refresh: timestamps em todos os logs, dump de cookies (nomes + expiry restante) antes/depois do `driver.get`, captura de browser console logs via `goog:loggingPrefs`.
- Sleep pós-`driver.get` aumentado de 5s → 20s para dar tempo ao SAML auto-refresh do SDK Angular.
- Quando `aat` desaparece após carregar /dashboard: salva screenshot + page_source[:50KB] em `/data/last_failure_*.{png,html}` para análise post-mortem.
- Log da renovação: agora mostra `aat_jwt_left=Xs` (PRE) e `aat_new_jwt_left=Xs renewed=yes/NO` (POST).

## 1.0.1 (2026-04-19)

- Margin default reduzido de 5-25 para 2-8 min (Firebase SDK refresca ~5min antes do exp; margens maiores leem cookies stale).
- Retry automático uma vez após 30s quando o SPA devolve `login_required` na primeira tentativa (trata estados transitórios durante o refresh do Firebase).
- Detecção de `aat` stale: se o cookie devolvido é igual ao anterior (Angular ainda não refrescou), agenda nova tentativa em 60s em vez de esperar pelo próximo exp-margin.

## 1.0.0 (2026-04-18)

- Initial release: refresh diário automático do cookie aat via Chromium + perfil persistente
