# combustible-tct

Descarga el detalle de consumos por patente desde el portal TCT de Copec y lo
consolida en un solo Excel.

## Requisitos
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Configuración
- `.env` con `USER_TCT` y `PASS_TCT`.
- `patentes.txt`: una patente por línea.

## Uso
```bash
python main.py --desde 2026-05-01 --hasta 2026-05-21
```
Resultado: `consolidado_<desde>_<hasta>.xlsx` (y respaldos en `descargas/`).

## Notas
- El login usa un navegador headless (Playwright) porque el portal cifra las
  credenciales con JavaScript. El resto usa `requests`.
- Si el login falla, corré con debug y revisá `debug_login.png` para ajustar los
  selectores en `tct/login.py`.
