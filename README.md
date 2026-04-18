# E-Redes Token Refresher

Addon Home Assistant que renova automaticamente o cookie `aat` do Balcão Digital
E-Redes usando Chromium headless com perfil persistente.

## Instalação

1. Settings → Add-ons → ⋮ → Local add-ons → Install
2. Arrancar addon; abrir `http://<ha-ip>:6081` (noVNC)
3. Fazer login em `balcaodigital.e-redes.pt`
4. Perfil fica persistido; daí em diante o refresh é automático às 01:45

## Configuração

- `refresh_hour` / `refresh_minute`: hora do refresh diário
- `ntfy_topic`: tópico ntfy.sh para alertas de login necessário
- `ha_webhook_id`: webhook que recebe o cookie (default `update_eredes_token`)
