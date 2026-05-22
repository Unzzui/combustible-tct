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

## Notas
- El login usa un navegador headless (Playwright) porque el portal cifra las
  credenciales con JavaScript; hay que escribirlas carácter por carácter. El
  resto usa `requests`.
- Si el login falla, corré `obtener_sesion(debug=True)` y revisá `debug_login.png`
  para ajustar los selectores en `tct/login.py`.
