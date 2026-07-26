# combustible-tct

Descarga el detalle de consumos por patente desde el portal TCT de Copec y lo
acumula de forma **incremental** en un maestro Parquet (más una copia Excel).

## Requisitos
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Configuración
- `.env` con `USER_TCT` y `PASS_TCT`.
- `data/Flota.xlsx` con una columna `Patente` (con o sin guión; se normaliza).

## Uso

**Primera vez (construye el histórico desde 2024):**
```bash
python main.py
```

**Actualizar (incremental):** volvé a correr lo mismo cuando quieras.
```bash
python main.py
```
Solo descarga lo nuevo: para cada patente parte desde su última transacción
guardada (con 7 días de solape) hasta hoy, y fusiona deduplicando por N° de guía.
No re-descarga todo el período.

**Reconstruir desde cero** (ignora el maestro):
```bash
python main.py --rehacer --desde 2024-01-01
```

**Rango / fuente específicos** (opcional):
```bash
python main.py --desde 2025-01-01 --hasta 2025-12-31
python main.py --patentes patentes.txt   # usa un .txt en vez de Flota.xlsx
```

## Salidas
- `data/consolidado.parquet` — maestro acumulado (fuente de verdad).
- `data/consolidado.xlsx` — misma data, para abrir en Excel.

## Si el portal rechaza el login
El primer rechazo es el último intento. Al recibir un `PortalBlockedError` (clave
incorrecta, cuenta bloqueada, o el portal devolviendo el formulario en silencio) el
proceso:

1. escribe `data/.circuit_breaker.json` **sin fecha de expiración** — no se levanta solo;
2. aborta con código de salida ≠ 0 y el marcador `LOGIN_BLOQUEADO` en el mensaje.

El orquestador (mini-server) reconoce ese marcador y manda al toque una alerta de
Telegram de "LOGIN RECHAZADO", en vez del genérico "FALLÓ". Mientras el flag exista,
toda corrida corta de entrada sin tocar el portal: **cada intento fallido extra acerca
la cuenta al bloqueo del proveedor**, así que no se reintenta ni por cron ni por
reintentos del orquestador.

Para volver a habilitarlo, después de corregir la clave y confirmar con Copec que la
cuenta está desbloqueada:

```bash
python main.py --reset-bloqueo          # o TCT_RESET_BLOQUEO=1 (útil desde el panel)
```

Un bloqueo con TTL (`_cb_disparar(..., ttl_hours=N)`) queda disponible para fallas
transitorias, pero el rechazo de credenciales usa siempre el modo sticky.

## Notas
- El login usa un navegador headless (Playwright) porque el portal cifra las
  credenciales con JavaScript; hay que escribirlas carácter por carácter. El
  resto usa `requests`.
- Si el login falla, corré `obtener_sesion(debug=True)` y revisá `debug_login.png`
  para ajustar los selectores en `tct/login.py`. El estado del último rechazo queda
  en `data/debug_login_{rechazo,timeout}.{html,png}`.
