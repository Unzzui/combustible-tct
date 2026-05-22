# Diseño: Descarga consolidada de consumos por patente (TCT Copec)

Fecha: 2026-05-21

## Objetivo

Descargar el **detalle** de consumos de combustible de una lista de patentes
definida por el usuario, para un rango de fechas arbitrario, y consolidar todo
en un solo archivo Excel.

Motivación: en el portal `tctcliente.copec.cl`, la descarga de detalle de
**todas** las patentes juntas no se descarga directo (se envía por correo a otra
persona). En cambio, la descarga de detalle de **una patente individual** sí se
descarga al momento. La estrategia es pedir el detalle patente por patente y
consolidar.

## Contexto técnico

El portal es ASP.NET WebForms (Telerik). Hechos confirmados a partir de capturas
de red reales:

1. **Login** (`POST /LoginDesk.aspx`): cifra usuario y clave con JavaScript en el
   navegador y usa nombres de campo aleatorios que rotan en cada carga
   (`JF34fB2wNI`, `y5sGjVCLq9`, etc.). No es replicable con `requests` puro sin
   reversar el JS. **Se resuelve con un navegador headless (Playwright)** que
   corre el JS del sitio.

2. **Detalle por patente** (`POST /VistasAdminInformes/AdmInfConsumosPorPatenteDetalle.aspx`):
   body pequeño, sin `__VIEWSTATE`. Campos observados:
   ```
   ticket, patente, glosaProd, patTar, hf_fechaInicio, hf_fechaFin,
   prod, selectedvalue, tipoProducto, anio, mes
   ```
   La respuesta es el HTML de la página de detalle (incluye su propio
   `__VIEWSTATE` y la grilla de transacciones).

3. **Exportación a Excel**: postback sobre la página de detalle con
   `__EVENTTARGET=ctl00$Cph1$LinkBtnExportarXls` + el `__VIEWSTATE` de esa
   página. Devuelve un `.xlsx` (`content-disposition: attachment`) con el
   detalle completo (sin límite de paginación).

4. **Autenticación de sesión**: las peticiones autenticadas usan la cookie
   `ASP.NET_SessionId` (+ `AWSALB`) y el campo `ticket` (token de 40 hex).
   Ambos se obtienen tras el login.

## Arquitectura

Tres componentes con responsabilidades aisladas, orquestados por un CLI.

### 1. `login.py` — obtención de sesión (Playwright)
- Entrada: usuario y clave desde `.env` (`USER_TCT`, `PASS_TCT`).
- Lanza Chromium headless, navega a `LoginDesk.aspx`, completa el formulario y
  envía (el JS del sitio cifra las credenciales).
- Tras autenticar, navega a una página autenticada y extrae:
  - el valor del campo oculto `ticket`,
  - las cookies del contexto (`ASP.NET_SessionId`, `AWSALB`).
- Salida: un objeto/sesión con `ticket` + cookies, listo para `requests`.
- Depende de: Playwright, credenciales en `.env`.

### 2. `scraper.py` — descarga por patente (requests)
- Entrada: `ticket`, cookies, lista de patentes, `desde`, `hasta`.
- Crea un `requests.Session` sembrado con las cookies del login.
- Para cada patente, dos POST:
  - **(a)** a `AdmInfConsumosPorPatenteDetalle.aspx` con el body pequeño
    (ticket, patente, fechas en `YYYY-MM-DD`, `prod=001`, `selectedvalue=patente`,
    `tipoProducto=TCT`, `anio`, `mes`) → HTML del detalle.
  - **(b)** del HTML extrae `__VIEWSTATE`, `__VIEWSTATEGENERATOR` (y
    `__EVENTVALIDATION` si existe) y hace el postback con
    `__EVENTTARGET=ctl00$Cph1$LinkBtnExportarXls` → bytes del `.xlsx`.
- Guarda cada `.xlsx` en `descargas/<patente>.xlsx` (respaldo).
- Salida: lista de rutas de archivos descargados.
- Depende de: requests; cookies+ticket de `login.py`.

Nota sobre fechas: el campo del detalle usa `hf_fechaInicio`/`hf_fechaFin` en
formato `YYYY-MM-DD`. `anio` y `mes` se derivan de la fecha de inicio
(`mes` sin cero a la izquierda, como `5`).

### 3. `consolidar.py` — consolidación (pandas)
- Entrada: rutas de los `.xlsx` descargados.
- Lee cada archivo, agrega columna `Patente`, concatena en un solo DataFrame.
- Escribe `consolidado_<desde>_<hasta>.xlsx`.
- Depende de: pandas, openpyxl.

### `main.py` — CLI orquestador
- Argumentos: `--desde YYYY-MM-DD --hasta YYYY-MM-DD`
  (opcional `--patentes patentes.txt`, por defecto `patentes.txt`).
- Flujo: `login` → `scraper` (loop patentes) → `consolidar`.

## Estructura de archivos

```
combustible-tct/
├── .env                 # USER_TCT, PASS_TCT (ya existe)
├── patentes.txt         # una patente por línea
├── requirements.txt     # requests, playwright, pandas, openpyxl, python-dotenv
├── login.py
├── scraper.py
├── consolidar.py
├── main.py
├── descargas/           # un .xlsx crudo por patente (respaldo)
└── consolidado_*.xlsx   # resultado final
```

## Manejo de errores

- **Patente sin datos / falla individual**: se registra en log y se continúa con
  las demás patentes (no aborta todo el proceso).
- **Sesión expirada** (login inválido a mitad de proceso): reintentar el login
  una vez y reanudar.
- **Cortesía con el servidor**: pausa corta (p. ej. 1–2 s) entre patentes.
- **Validación de entrada**: fechas en formato correcto y archivo de patentes no
  vacío antes de iniciar.

## Fuera de alcance (YAGNI)

- Descubrimiento automático de patentes (el usuario entrega la lista).
- Base de datos / histórico acumulado (solo Excel consolidado por corrida).
- Programación/agendado automático.
- Soporte para informes distintos al de "Consumos Por Patente".

## Uso

```bash
pip install -r requirements.txt
playwright install chromium
python main.py --desde 2026-05-01 --hasta 2026-05-21
```
