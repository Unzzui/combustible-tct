# Auto-lista de flota desde el portal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el scraper obtenga la lista completa de patentes (~806) desde el portal en cada corrida (fallback a `Flota.xlsx`) y consolide el detalle de toda la flota desde 2025-01-01, con sleep endurecido.

**Architecture:** Un solo login Playwright deja una página autenticada; `flota_portal.obtener_flota(page)` navega al informe "Consumos De Un Producto Por Periodo", hace Buscar y parsea la 1ª columna de la grilla. La misma sesión entrega `ticket + cookies` para la descarga per-patente por `requests` (sin cambios). `main.py` usa el portal como fuente de patentes.

**Tech Stack:** Python 3.12, Playwright 1.49, requests, BeautifulSoup, pandas, pytest.

## Global Constraints

- Un solo login del portal por corrida (evitar reintentos de credenciales).
- `normalizar_patente` es la fuente de verdad del formato `LLLL-NN`.
- No romper los modos existentes `--patentes` / `--pato` / `--flota` ni el circuit breaker.
- Sin dependencias nuevas (todo ya está en `requirements.txt`).

---

### Task 1: Parser puro de patentes de la grilla

**Files:**
- Create: `tct/flota_portal.py`
- Test: `tests/test_flota_portal.py`

**Interfaces:**
- Produces: `parsear_patentes(valores: list[str]) -> list[str]` — normaliza con
  `config.normalizar_patente`, filtra a `^[A-Z]{4}-\d{2}$`, dedup preservando orden.

- [ ] **Step 1: Test que falla** (`tests/test_flota_portal.py`)

```python
from tct import flota_portal

def test_parsear_patentes_filtra_y_normaliza():
    crudo = ["Patente", "LRBJ-98", "lwyj50", "LRBJ-98", "Total", "", "VYJH-62"]
    assert flota_portal.parsear_patentes(crudo) == ["LRBJ-98", "LWYJ-50", "VYJH-62"]
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/test_flota_portal.py -v`
Expected: FAIL (`ModuleNotFoundError: tct.flota_portal`)

- [ ] **Step 3: Implementación mínima del parser**

```python
"""Obtiene la lista de patentes de la flota desde el informe agrupado del portal.

El informe "Consumos De Un Producto Por Periodo" lista una fila por patente. Solo
lo usamos para la LISTA (no para datos): el detalle se baja per-patente aparte.
La navegación es un postback cruzado de WebForms (la función `enviar` del portal).
"""
import re

from tct import config

_PATENTE_RE = re.compile(r"^[A-Z]{4}-\d{2}$")


def parsear_patentes(valores):
    """Normaliza, filtra a patentes válidas (LLLL-NN) y dedup preservando orden."""
    out, vistos = [], set()
    for v in valores:
        p = config.normalizar_patente(v)
        if _PATENTE_RE.match(p) and p not in vistos:
            vistos.add(p)
            out.append(p)
    return out
```

- [ ] **Step 4: Correr y ver que pasa**

Run: `python -m pytest tests/test_flota_portal.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tct/flota_portal.py tests/test_flota_portal.py
git commit -m "feat(flota): parser de patentes de la grilla del portal"
```

---

### Task 2: Navegación WebForms + `obtener_flota(page)`

**Files:**
- Modify: `tct/flota_portal.py`

**Interfaces:**
- Consumes: `parsear_patentes` (Task 1).
- Produces: `obtener_flota(page) -> list[str]` — recibe una página Playwright ya
  autenticada, devuelve las patentes. `_postback(page, target, arg)` y
  `_navegar(page, ruta)` helpers de WebForms.

No lleva unit test (requiere navegador real); se valida en la corrida integrada
(ya probado en vivo el 2026-07-24: 806 patentes).

- [ ] **Step 1: Agregar helpers y `obtener_flota`** (append a `tct/flota_portal.py`)

```python
URL_INFORME = "../VistasAdminInformes/AdmInfConsumosProdPeriodo.aspx"
_ACEPTAR_POLITICAS = "ctl00$Cph1$LinkBtnAceptarPoliticas"
_BUSCAR = "ctl00$Cph1$LinkBtnBuscar"

# `enviar()` del portal: reescribe form.action, renombra __VIEWSTATE→NOVIEWSTATE y
# postea BtnCliente. Lo replicamos con submit() directo para evitar el strict-mode
# de Telerik (__doPostBack accede a `arguments`, prohibido en funciones inyectadas).
_JS_SET = "const sh=(n,v)=>{let e=f.elements[n];if(!e){e=document.createElement('input');e.type='hidden';e.name=n;f.appendChild(e);}e.value=v;};"


def _postback(page, target, arg=""):
    page.evaluate(
        "(o)=>{const f=document.forms[0];" + _JS_SET +
        "sh('__EVENTTARGET',o.target);sh('__EVENTARGUMENT',o.arg);"
        "HTMLFormElement.prototype.submit.call(f);}",
        {"target": target, "arg": arg},
    )


def _navegar(page, ruta):
    page.evaluate(
        "(ruta)=>{const f=document.forms[0];" + _JS_SET +
        "f.action=ruta;if(f.__VIEWSTATE)f.__VIEWSTATE.name='NOVIEWSTATE';"
        "sh('__EVENTTARGET','BtnCliente');sh('__EVENTARGUMENT','esPostCliente');"
        "HTMLFormElement.prototype.submit.call(f);}",
        ruta,
    )


def obtener_flota(page):
    """Navega al informe por periodo, genera la grilla y devuelve las patentes.

    Requiere `page` ya autenticada (post-login). Best-effort: si algo cambia en
    el portal levanta excepción y el caller decide el fallback.
    """
    with page.expect_navigation(wait_until="domcontentloaded", timeout=45000):
        _postback(page, _ACEPTAR_POLITICAS)
    page.wait_for_timeout(2000)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=45000):
        _navegar(page, URL_INFORME)
    page.wait_for_timeout(3000)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=45000):
        _postback(page, _BUSCAR)
    page.wait_for_timeout(3000)
    col0 = page.evaluate(
        "() => { const q=s=>Array.from(document.querySelectorAll(s));"
        "let big=null,mx=0; q('table').forEach(t=>{const r=t.querySelectorAll('tr').length; if(r>mx){mx=r;big=t;}});"
        "return big? Array.from(big.querySelectorAll('tr')).map(tr=>{const c=tr.querySelector('td'); return c?(c.innerText||'').trim():'';}) : []; }"
    )
    return parsear_patentes(col0)
```

- [ ] **Step 2: Sanidad de import**

Run: `python -c "from tct import flota_portal; print(flota_portal.URL_INFORME)"`
Expected: imprime la ruta sin error.

- [ ] **Step 3: Tests siguen verdes**

Run: `python -m pytest tests/test_flota_portal.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tct/flota_portal.py
git commit -m "feat(flota): navegacion WebForms y obtener_flota(page)"
```

---

### Task 3: Refactor de login — `obtener_sesion_con_flota()`

**Files:**
- Modify: `tct/login.py`

**Interfaces:**
- Consumes: `flota_portal.obtener_flota` (Task 2).
- Produces: `obtener_sesion_con_flota() -> tuple[str, dict, list[str]]` →
  `(ticket, cookies, patentes)`. Un solo login. Si `obtener_flota` falla,
  devuelve `patentes=[]` pero conserva `ticket, cookies`.

- [ ] **Step 1: Extraer `_login_en_page(page, usuario, clave)`**

Refactor: mover el cuerpo de login (goto, escribir credenciales, esperar
ticket/rechazo) de `obtener_sesion` a un helper `_login_en_page(page, usuario,
clave)` que deja la página autenticada o levanta `PortalBlockedError` /
`LoginTimeoutError`. `obtener_sesion` pasa a: abrir browser → `_login_en_page` →
extraer ticket+cookies → cerrar. (Mantiene su firma y comportamiento actuales.)

- [ ] **Step 2: Agregar `obtener_sesion_con_flota`**

```python
def obtener_sesion_con_flota(usuario=None, clave=None, headless=True):
    """Un solo login: devuelve (ticket, cookies, patentes_del_portal).

    Si el scraping de la flota falla, patentes=[] (el caller usa fallback) pero
    ticket+cookies quedan disponibles para descargar igual."""
    from tct import flota_portal
    usuario = usuario or config.USER_TCT
    clave = clave or config.PASS_TCT
    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=headless)
        ctx = navegador.new_context()
        page = ctx.new_page()
        _login_en_page(page, usuario, clave)
        try:
            patentes = flota_portal.obtener_flota(page)
        except Exception as e:  # noqa: BLE001 — el caller decide el fallback
            import logging
            logging.getLogger("tct").warning("No se pudo leer la flota del portal: %s", e)
            patentes = []
        ticket = page.input_value("input[name=ticket]")
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        navegador.close()
    if not ticket or len(ticket) < 20:
        raise LoginTimeoutError("No se obtuvo 'ticket' tras el login.")
    return ticket, cookies, patentes
```

- [ ] **Step 3: Tests existentes verdes**

Run: `python -m pytest tests/ -v`
Expected: PASS (login no tiene unit test que abra navegador; test_config etc. pasan).

- [ ] **Step 4: Commit**

```bash
git add tct/login.py
git commit -m "refactor(login): obtener_sesion_con_flota reutilizando la sesion"
```

---

### Task 4: Integración en `main.py` (fuente portal + fallback + sleep + --desde)

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

**Interfaces:**
- Consumes: `login.obtener_sesion_con_flota` (Task 3).

- [ ] **Step 1: Actualizar test del default `--desde`** (`tests/test_main.py`)

Cambiar la aserción de `test_parse_args_defaults_rango_y_flota`:
```python
    assert args.desde == "2025-01-01"
```

- [ ] **Step 2: Cambiar el default en `parse_args`** (`main.py`)

```python
    p.add_argument("--desde", default="2025-01-01",
                   help="Inicio del histórico la primera vez / con --rehacer (def: 2025-01-01)")
```

- [ ] **Step 3: Correr test de args**

Run: `python -m pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 4: Flujo portal + fallback en `main()`**

En `main()`, reemplazar el bloque que llama `cargar_lista` + `login.obtener_sesion`
por: si es el modo flota por defecto (no `--patentes`, no `--pato`), obtener sesión
y flota juntas del portal, con fallback al Excel:

```python
    usa_portal = not args.patentes and not args.pato
    if usa_portal:
        try:
            ticket, cookies, patentes = login.obtener_sesion_con_flota()
        except login.PortalBlockedError as e:
            _cb_disparar(cb_path, str(e))
            log.error("Portal bloqueó el login (%s). Circuit breaker %dh.", e, CB_TTL_HOURS)
            return
        if not patentes:
            log.warning("Flota del portal vacía; usando %s como fallback.", args.flota)
            patentes = cargar_lista(args)
        else:
            log.info("Flota del portal: %d patentes.", len(patentes))
    else:
        patentes = cargar_lista(args)
        try:
            ticket, cookies = login.obtener_sesion()
        except login.PortalBlockedError as e:
            _cb_disparar(cb_path, str(e))
            log.error("Portal bloqueó el login (%s). Circuit breaker %dh.", e, CB_TTL_HOURS)
            return
```

Nota: mover `patentes = cargar_lista(args)` (que hoy está al inicio de `main`) a
dentro de estas ramas, y quitar la validación temprana duplicada. `login.info("Login OK...")`
y `sesion = scraper.nueva_sesion(cookies)` siguen igual después.

- [ ] **Step 5: Sleep endurecido con jitter** (`main.py`)

Agregar `import random` arriba y reemplazar `time.sleep(1.5)` del loop por:
```python
        base = float(os.getenv("TCT_SLEEP_SEG", "3.0"))
        time.sleep(max(0.5, base + random.uniform(-0.5, 0.5)))
```

- [ ] **Step 6: Suite completa verde**

Run: `python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat(main): flota desde el portal con fallback, --desde 2025, sleep con jitter"
```

---

### Task 5: Deploy de la imagen al server

**Files:** ninguno (operacional)

- [ ] **Step 1: Push de la rama y merge a main**

```bash
git push -u origin feat/flota-portal-auto-lista
```
(Merge a `main` vía PR o fast-forward según preferencia del usuario.)

- [ ] **Step 2: En el server, pull + rebuild de la imagen**

```bash
ssh diego_oca_server@100.70.223.21 "cd ~/Proyectos/mini-server/services/combustible-tct/repo && git pull --ff-only && cd .. && docker build -t miniserver/combustible-tct:latest ."
```
Expected: build OK, imagen `miniserver/combustible-tct:latest` actualizada.

---

### Task 6: Corrida de consolidación (one-off, monitoreada)

**Files:** ninguno (operacional)

- [ ] **Step 1: Disparar la consolidación en background**

En el server, correr un contenedor one-off con el mismo env y el bind-mount de
`data/`, entrypoint por defecto (`python main.py --flota /app/flota/Flota.xlsx`,
que ahora usa el portal como fuente). Correr en background y loguear a archivo.

```bash
ssh diego_oca_server@100.70.223.21 "cd ~/Proyectos/mini-server/services/combustible-tct && docker run -d --name combustible-consolida --env-file <(grep -E '^(USER_TCT|PASS_TCT)=' ~/Proyectos/mini-server/.env) -v \$PWD/data:/app/data miniserver/combustible-tct:latest"
```

- [ ] **Step 2: Monitorear**

```bash
ssh diego_oca_server@100.70.223.21 "docker logs -f combustible-consolida"
```
Expected: "Flota del portal: ~806 patentes", luego "OK <patente> ..." y al final
"Maestro: data/consolidado.parquet (N filas, +M nuevas, K fallidas)".

- [ ] **Step 3: Verificar el maestro y limpiar**

```bash
ssh diego_oca_server@100.70.223.21 "docker rm combustible-consolida; ls -la ~/Proyectos/mini-server/services/combustible-tct/data/consolidado.parquet"
```

---

## Self-Review

- **Cobertura del spec:** auto-lista (Tasks 1-2), un login (Task 3), integración+fallback+sleep+--desde (Task 4), deploy (Task 5), consolidación one-off (Task 6). ✓
- **Placeholders:** ninguno; código real en cada step.
- **Consistencia de tipos:** `parsear_patentes(list)->list`, `obtener_flota(page)->list`, `obtener_sesion_con_flota()->(ticket,cookies,patentes)` usados consistentes en Tasks 3-4. ✓
- **Fallback:** cubierto (patentes vacías → `cargar_lista`). ✓
