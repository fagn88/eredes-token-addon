# Changelog

## 1.0.1 (2026-04-19)

- Margin default reduzido de 5-25 para 2-8 min (Firebase SDK refresca ~5min antes do exp; margens maiores leem cookies stale).
- Retry automático uma vez após 30s quando o SPA devolve `login_required` na primeira tentativa (trata estados transitórios durante o refresh do Firebase).
- Detecção de `aat` stale: se o cookie devolvido é igual ao anterior (Angular ainda não refrescou), agenda nova tentativa em 60s em vez de esperar pelo próximo exp-margin.

## 1.0.0 (2026-04-18)

- Initial release: refresh diário automático do cookie aat via Chromium + perfil persistente
