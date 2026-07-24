# Auto-lista de flota desde el portal + consolidación de flota completa

Fecha: 2026-07-24

## Contexto

El scraper `combustible-tct` baja el detalle de consumo **per-patente** desde una
lista manual (`data/Flota.xlsx`, ~580 patentes) posteando al endpoint de detalle
del portal TCT de Copec con `ticket + cookies` obtenidos por Playwright.

Dos problemas detectados:

1. **La flota real en el portal es ~806 patentes, no 580.** La lista manual está
   corta en ~226 → la consolidación no cubre la flota completa.
2. El maestro (`consolidado.parquet`) quedó **congelado desde 2026-07-03** por un
   bloqueo de credenciales (clave vieja). La clave nueva ya está en el `.env` y
   fue validada; el circuit breaker se limpió.

## Objetivo

1. **Derivar la lista de patentes automáticamente desde el portal en cada corrida
   diaria** (sin depender del Excel manual), cubriendo la flota completa (~806).
2. **Consolidar el detalle transacción-por-transacción de toda la flota**, con
   histórico `--desde 2025-01-01` para las patentes nuevas (incremental para las
   existentes).
3. **Controlar el riesgo de bloqueo** (un solo login por corrida, sleep con jitter,
   circuit breaker existente).

## Hallazgos del portal (reverse engineering, validado 2026-07-24)

- Post-login se cae en `VistasAdminCte/AdmCteInicio.aspx`. Hay un gate
  **"Aceptar Políticas"** (`ctl00$Cph1$LinkBtnAceptarPoliticas`).
- La navegación a informes usa un **postback cruzado de WebForms**: la función
  `enviar(ruta)` reescribe `form.action`, renombra `__VIEWSTATE`→`NOVIEWSTATE` y
  hace `__doPostBack('BtnCliente','esPostCliente')`. Un GET directo cae en
  `MensajeSistema.aspx`.
- El informe **"Consumos De Un Producto Por Periodo"**
  (`VistasAdminInformes/AdmInfConsumosProdPeriodo.aspx`) con defaults
  (Diésel `D`, `TCT`, ventana móvil de 12 meses) + clic en **Buscar**
  (`ctl00$Cph1$LinkBtnBuscar`) genera una grilla con **una fila por patente**
  (~806) cuya 1ª columna es la patente (formato `LLLL-NN`). Es un **resumen
  mensual**, no detalle — solo lo usamos para obtener la **lista de patentes**.
- El `ticket` (input hidden) y las cookies de esa misma sesión sirven para la
  descarga per-patente por `requests`. → **un solo login** sirve para lista + detalle.
- Detalle per-patente validado con la clave nueva: columnas `Producto, Tarjeta,
  Tipo de Tarjeta, Patente, N° Vehículo, Tipo de Vehículo, Departamento, Rut
  Chofer, ...`; filas reales por patente.

## Diseño

### Componente nuevo: `tct/flota_portal.py`

- `obtener_flota(page) -> list[str]`: recibe una página Playwright **ya
  autenticada**. Acepta políticas, navega a `AdmInfConsumosProdPeriodo.aspx` (vía
  el postback cruzado), hace clic en Buscar, toma la grilla más grande, extrae la
  1ª columna de cada fila, normaliza con `config.normalizar_patente` y **filtra a
  patentes válidas** (`^[A-Z]{4}-\d{2}$`, descarta header/total), dedup
  preservando orden. Devuelve ~806 patentes.
- Helpers de navegación WebForms (`postback`, `navegar`) encapsulados y
  documentados en este módulo.

### Refactor mínimo: `tct/login.py`

- Extraer la parte "loguear y dejar una página autenticada" para reutilizarla en
  la misma sesión. Se mantiene `obtener_sesion()` (tests actuales) y se agrega una
  vía que, con **un solo login**, permita: (a) scrapear la flota con
  `flota_portal.obtener_flota`, y (b) extraer `ticket + cookies`. Preferencia:
  un contextmanager o función `obtener_sesion_con_flota()` que devuelva
  `(ticket, cookies, patentes)`.
- Las mismas señales de rechazo/bloqueo (`PortalBlockedError`) se conservan.

### `main.py`

- **Fuente de patentes:** portal (`obtener_flota`) con **fallback a `Flota.xlsx`**
  si el paso del portal falla (log de warning, no aborta la corrida).
- `--desde` efectivo **2025-01-01** para la consolidación (incremental para las
  existentes vía `almacen.inicio_incremental`).
- Se conservan los modos `--pato / --patentes / --flota` y el circuit breaker.

### Endurecimiento anti-bloqueo

- `sleep` entre patentes configurable por env (`TCT_SLEEP_SEG`, default 3.0) con
  jitter ±0.5s.
- Un solo login por corrida (la flota sale de la misma sesión del informe).
- Circuit breaker existente sin cambios.

## Testing

- Test del parser de la grilla: fixture HTML → 806 patentes normalizadas, filtra
  header/total y filas no-patente.
- Test de fallback: si `obtener_flota` lanza, `main` usa `Flota.xlsx`.
- Mantener verdes los tests existentes (`tests/test_*.py`).

## Plan de ejecución

1. **Consolidación inicial (one-off, "ahora"):** correr per-patente sobre las ~806
   patentes con `--desde 2025-01-01`, en background y monitoreada. No usa
   `--rehacer` (no borra lo existente). Puede hacerse con el código actual + la
   lista extraída, en paralelo a la implementación.
2. **Productivización:** implementar `flota_portal` + integración + tests, push a
   `main`, rebuild de la imagen `miniserver/combustible-tct:latest`, para que las
   corridas diarias (06:00 / 18:00) refresquen la lista solas.

## Fuera de alcance (YAGNI)

- El informe de resumen mensual masivo como fuente de datos.
- El "detalle masivo" que el portal entrega por correo y su reenvío automático en
  Gmail (flujo aparte, lo maneja el usuario).
