# Descarga consolidada de consumos por patente (TCT Copec) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Descargar el detalle de consumos de una lista de patentes para un rango de fechas y consolidarlo en un solo Excel, sorteando el login cifrado del portal con un navegador headless y usando `requests` para el resto.

**Architecture:** Playwright (Chromium headless) hace SOLO el login (corre el JS que cifra las credenciales) y entrega `ticket` + cookies. Luego `requests` reutiliza esa sesión: por cada patente hace un POST a la página de detalle y un postback de exportación que devuelve el `.xlsx`. Finalmente pandas consolida todos los `.xlsx` en uno.

**Tech Stack:** Python 3.10, Playwright, requests, BeautifulSoup4 (lxml), pandas, openpyxl, python-dotenv. Tests con pytest + responses.

---

## Constantes confirmadas (de capturas reales)

```
BASE        = "https://tctcliente.copec.cl"
URL_LOGIN   = BASE + "/LoginDesk.aspx"
URL_AGRUP   = BASE + "/VistasAdminInformes/AdmInfConsumosAgrupado.aspx"
URL_DETALLE = BASE + "/VistasAdminInformes/AdmInfConsumosPorPatenteDetalle.aspx"
EXPORT_TARGET = "ctl00$Cph1$LinkBtnExportarXls"
```

Body del POST de detalle (campos exactos observados): `ticket, patente, glosaProd,
patTar, hf_fechaInicio, hf_fechaFin, prod, selectedvalue, tipoProducto, anio, mes`.

---

## Estructura de archivos

```
combustible-tct/
├── .env                    # USER_TCT, PASS_TCT (ya existe)
├── .gitignore              # protege .env, descargas/, salidas
├── patentes.txt            # una patente por línea
├── requirements.txt
├── tct/
│   ├── __init__.py
│   ├── config.py           # carga .env, lee patentes.txt, constantes
│   ├── login.py            # Playwright -> ticket + cookies
│   ├── scraper.py          # requests -> descarga .xlsx por patente
│   └── consolidar.py       # pandas -> consolida en un Excel
├── main.py                 # CLI orquestador
├── tests/
│   ├── fixtures/
│   │   └── detalle_sample.html
│   ├── test_config.py
│   ├── test_scraper.py
│   └── test_consolidar.py
├── descargas/              # un .xlsx crudo por patente (respaldo)
└── consolidado_*.xlsx
```

---

### Task 0: Andamiaje del proyecto

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `patentes.txt`
- Create: `tct/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Crear `requirements.txt`**

```text
playwright==1.49.0
requests==2.32.3
beautifulsoup4==4.12.3
lxml==5.3.0
pandas==2.2.3
openpyxl==3.1.5
python-dotenv==1.0.1
pytest==8.3.4
responses==0.25.3
```

- [ ] **Step 2: Crear `.gitignore` (protege credenciales y salidas)**

```text
.env
descargas/
consolidado_*.xlsx
__pycache__/
*.pyc
.pytest_cache/
debug_login.png
```

- [ ] **Step 3: Crear `patentes.txt` (plantilla con la patente de ejemplo)**

```text
PDRF-74
```

- [ ] **Step 4: Crear paquetes vacíos**

`tct/__init__.py` y `tests/__init__.py` como archivos vacíos.

- [ ] **Step 5: Instalar dependencias y navegador**

Run:
```bash
cd /home/unzzui/Proyectos/combustible-tct
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```
Expected: instala sin errores; `playwright install` descarga Chromium.

- [ ] **Step 6: Inicializar git y commit**

Run:
```bash
git init && git add . && git commit -m "chore: andamiaje del proyecto"
```
Expected: commit creado. Verificar con `git status` que `.env` NO aparece trackeado.

---

### Task 1: `config.py` — carga de credenciales, patentes y constantes

**Files:**
- Create: `tct/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_config.py
from tct import config


def test_cargar_patentes_ignora_vacias_y_espacios(tmp_path):
    f = tmp_path / "patentes.txt"
    f.write_text("PDRF-74\n\n  ABCD-12  \n\n")
    assert config.cargar_patentes(str(f)) == ["PDRF-74", "ABCD-12"]


def test_constantes_urls():
    assert config.URL_DETALLE.endswith("AdmInfConsumosPorPatenteDetalle.aspx")
    assert config.EXPORT_TARGET == "ctl00$Cph1$LinkBtnExportarXls"
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `pytest tests/test_config.py -v`
Expected: FAIL con `AttributeError`/`ImportError` (módulo o funciones inexistentes).

- [ ] **Step 3: Implementar `tct/config.py`**

```python
# tct/config.py
import os
from dotenv import load_dotenv

load_dotenv()

BASE = "https://tctcliente.copec.cl"
URL_LOGIN = BASE + "/LoginDesk.aspx"
URL_AGRUPADO = BASE + "/VistasAdminInformes/AdmInfConsumosAgrupado.aspx"
URL_DETALLE = BASE + "/VistasAdminInformes/AdmInfConsumosPorPatenteDetalle.aspx"
EXPORT_TARGET = "ctl00$Cph1$LinkBtnExportarXls"

USER_TCT = os.getenv("USER_TCT", "")
PASS_TCT = os.getenv("PASS_TCT", "")


def cargar_patentes(ruta: str) -> list[str]:
    """Lee patentes (una por línea), ignora líneas vacías y recorta espacios."""
    with open(ruta, encoding="utf-8") as fh:
        return [linea.strip() for linea in fh if linea.strip()]
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

Run: `pytest tests/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tct/config.py tests/test_config.py
git commit -m "feat: config con constantes y carga de patentes"
```

---

### Task 2: `scraper.py` — derivar parámetros del body de detalle (puro, TDD)

**Files:**
- Create: `tct/scraper.py`
- Test: `tests/test_scraper.py`

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_scraper.py
from tct import scraper


def test_build_detail_body_deriva_anio_y_mes():
    body = scraper.build_detail_body(
        ticket="TICKET123",
        patente="PDRF-74",
        desde="2026-05-01",
        hasta="2026-05-21",
        glosa_prod="Diésel",
    )
    assert body["ticket"] == "TICKET123"
    assert body["patente"] == "PDRF-74"
    assert body["hf_fechaInicio"] == "2026-05-01"
    assert body["hf_fechaFin"] == "2026-05-21"
    assert body["anio"] == "2026"
    assert body["mes"] == "5"          # sin cero a la izquierda
    assert body["prod"] == "001"
    assert body["selectedvalue"] == "patente"
    assert body["tipoProducto"] == "TCT"
    assert body["patTar"] == "PDRF-74"
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `pytest tests/test_scraper.py::test_build_detail_body_deriva_anio_y_mes -v`
Expected: FAIL (`AttributeError: module 'tct.scraper' has no attribute 'build_detail_body'`).

- [ ] **Step 3: Implementar `build_detail_body` en `tct/scraper.py`**

```python
# tct/scraper.py
from datetime import datetime


def build_detail_body(ticket, patente, desde, hasta, glosa_prod="Diésel"):
    """Construye el body del POST a la página de detalle de una patente.

    `desde`/`hasta` en formato YYYY-MM-DD. `anio`/`mes` se derivan de `desde`
    (mes sin cero a la izquierda, como lo envía el portal).
    """
    inicio = datetime.strptime(desde, "%Y-%m-%d")
    return {
        "ticket": ticket,
        "patente": patente,
        "glosaProd": glosa_prod,
        "patTar": patente,
        "hf_fechaInicio": desde,
        "hf_fechaFin": hasta,
        "prod": "001",
        "selectedvalue": "patente",
        "tipoProducto": "TCT",
        "anio": str(inicio.year),
        "mes": str(inicio.month),
    }
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

Run: `pytest tests/test_scraper.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tct/scraper.py tests/test_scraper.py
git commit -m "feat: build_detail_body con derivacion de anio/mes"
```

---

### Task 3: `scraper.py` — cosechar campos del formulario y armar el postback de exportación (puro, TDD)

**Files:**
- Create: `tests/fixtures/detalle_sample.html`
- Modify: `tct/scraper.py`
- Modify: `tests/test_scraper.py`

- [ ] **Step 1: Crear fixture HTML mínimo representativo**

`tests/fixtures/detalle_sample.html`:
```html
<html><body>
<form method="post" action="AdmInfConsumosPorPatenteDetalle.aspx" id="form1">
  <input type="hidden" name="__VIEWSTATE" value="ABC123" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="1BB77434" />
  <input type="hidden" name="ctl00$Cph1$hf_patente" value="PDRF-74" />
  <select name="ctl00$Cph1$RcbxTipoDescargaPatente">
    <option value="resumen">Resumen</option>
    <option value="detalle" selected>Detalle</option>
  </select>
  <input type="text" name="ctl00$Cph1$TxtBusqueda" value="buscar..." />
</form>
</body></html>
```

- [ ] **Step 2: Escribir los tests que fallan**

Agregar a `tests/test_scraper.py`:
```python
import pathlib

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "detalle_sample.html"


def test_harvest_form_fields_recoge_inputs_y_selects():
    html = FIXTURE.read_text(encoding="utf-8")
    campos = scraper.harvest_form_fields(html)
    assert campos["__VIEWSTATE"] == "ABC123"
    assert campos["__VIEWSTATEGENERATOR"] == "1BB77434"
    assert campos["ctl00$Cph1$hf_patente"] == "PDRF-74"
    # select toma la opción marcada selected
    assert campos["ctl00$Cph1$RcbxTipoDescargaPatente"] == "detalle"
    assert campos["ctl00$Cph1$TxtBusqueda"] == "buscar..."


def test_build_export_payload_setea_event_target():
    html = FIXTURE.read_text(encoding="utf-8")
    payload = scraper.build_export_payload(html)
    # conserva todos los campos del formulario
    assert payload["__VIEWSTATE"] == "ABC123"
    # y agrega/sobrescribe los del postback de exportación
    assert payload["__EVENTTARGET"] == "ctl00$Cph1$LinkBtnExportarXls"
    assert payload["__EVENTARGUMENT"] == ""
```

- [ ] **Step 3: Ejecutar y verificar que fallan**

Run: `pytest tests/test_scraper.py -k "harvest or export" -v`
Expected: FAIL (funciones inexistentes).

- [ ] **Step 4: Implementar en `tct/scraper.py`**

Agregar al inicio del archivo:
```python
from bs4 import BeautifulSoup
from tct.config import EXPORT_TARGET
```

Agregar las funciones:
```python
def harvest_form_fields(html: str) -> dict:
    """Devuelve todos los campos del <form> (inputs y selects) como name->value.

    Es el patrón estándar de WebForms: para hacer un postback válido hay que
    reenviar TODOS los campos (incluido __VIEWSTATE y los hidden de la grilla),
    no solo algunos.
    """
    soup = BeautifulSoup(html, "lxml")
    campos: dict[str, str] = {}
    for inp in soup.select("input"):
        name = inp.get("name")
        if not name:
            continue
        tipo = (inp.get("type") or "text").lower()
        if tipo in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        campos[name] = inp.get("value", "")
    for sel in soup.select("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.select_one("option[selected]") or sel.select_one("option")
        campos[name] = opt.get("value", "") if opt else ""
    return campos


def build_export_payload(detail_html: str) -> dict:
    """Toma el HTML de la página de detalle y arma el body del postback que
    dispara la exportación a Excel."""
    payload = harvest_form_fields(detail_html)
    payload["__EVENTTARGET"] = EXPORT_TARGET
    payload["__EVENTARGUMENT"] = ""
    return payload
```

- [ ] **Step 5: Ejecutar y verificar que pasan**

Run: `pytest tests/test_scraper.py -v`
Expected: PASS (todos).

- [ ] **Step 6: Commit**

```bash
git add tct/scraper.py tests/test_scraper.py tests/fixtures/detalle_sample.html
git commit -m "feat: harvest de campos y payload de exportacion"
```

---

### Task 4: `scraper.py` — descarga de una patente con `requests` (test con `responses`)

**Files:**
- Modify: `tct/scraper.py`
- Modify: `tests/test_scraper.py`

- [ ] **Step 1: Escribir el test que falla (mock de red con `responses`)**

Agregar a `tests/test_scraper.py`:
```python
import responses
import requests
from tct import config


@responses.activate
def test_descargar_patente_hace_dos_posts_y_devuelve_bytes():
    # POST (a): página de detalle -> HTML con formulario
    responses.add(
        responses.POST, config.URL_DETALLE,
        body=FIXTURE.read_text(encoding="utf-8"),
        status=200, content_type="text/html",
    )
    # POST (b): exportación -> bytes del xlsx
    responses.add(
        responses.POST, config.URL_DETALLE,
        body=b"PK\x03\x04xlsx-bytes", status=200,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    sesion = requests.Session()
    contenido = scraper.descargar_patente(
        sesion, ticket="T1", patente="PDRF-74",
        desde="2026-05-01", hasta="2026-05-21",
    )

    assert contenido.startswith(b"PK")          # firma de un .xlsx
    assert len(responses.calls) == 2
    # el primer POST llevó el body de detalle
    assert "hf_fechaInicio=2026-05-01" in responses.calls[0].request.body
    # el segundo POST llevó el event target de exportación
    assert "LinkBtnExportarXls" in responses.calls[1].request.body
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `pytest tests/test_scraper.py::test_descargar_patente_hace_dos_posts_y_devuelve_bytes -v`
Expected: FAIL (`descargar_patente` no existe).

- [ ] **Step 3: Implementar `descargar_patente` en `tct/scraper.py`**

Agregar import al inicio:
```python
from tct.config import URL_DETALLE, URL_AGRUPADO
```

Agregar la función:
```python
def descargar_patente(sesion, ticket, patente, desde, hasta, glosa_prod="Diésel"):
    """Descarga el .xlsx de detalle de UNA patente. Hace dos POST:
    (a) carga la página de detalle, (b) dispara la exportación.
    Devuelve los bytes del archivo .xlsx.
    """
    headers = {"Referer": URL_AGRUPADO}
    body = build_detail_body(ticket, patente, desde, hasta, glosa_prod)
    r1 = sesion.post(URL_DETALLE, data=body, headers=headers, timeout=60)
    r1.raise_for_status()

    payload = build_export_payload(r1.text)
    r2 = sesion.post(URL_DETALLE, data=payload, headers=headers, timeout=60)
    r2.raise_for_status()
    return r2.content
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

Run: `pytest tests/test_scraper.py -v`
Expected: PASS (todos).

- [ ] **Step 5: Agregar helper para guardar y test**

Agregar a `tests/test_scraper.py`:
```python
def test_guardar_xlsx_escribe_archivo(tmp_path):
    ruta = scraper.guardar_xlsx(b"PK\x03\x04datos", "PDRF-74", str(tmp_path))
    assert ruta.endswith("PDRF-74.xlsx")
    with open(ruta, "rb") as fh:
        assert fh.read() == b"PK\x03\x04datos"
```

Agregar a `tct/scraper.py`:
```python
import os


def guardar_xlsx(contenido: bytes, patente: str, carpeta: str) -> str:
    """Guarda los bytes del xlsx en carpeta/<patente>.xlsx y devuelve la ruta."""
    os.makedirs(carpeta, exist_ok=True)
    seguro = patente.strip().replace("/", "-").replace(" ", "")
    ruta = os.path.join(carpeta, f"{seguro}.xlsx")
    with open(ruta, "wb") as fh:
        fh.write(contenido)
    return ruta
```

- [ ] **Step 6: Ejecutar y verificar que pasa**

Run: `pytest tests/test_scraper.py -v`
Expected: PASS (todos).

- [ ] **Step 7: Commit**

```bash
git add tct/scraper.py tests/test_scraper.py
git commit -m "feat: descarga y guardado de xlsx por patente"
```

---

### Task 5: `login.py` — obtener `ticket` + cookies con Playwright (integración)

**Files:**
- Create: `tct/login.py`

> Nota: el login depende del sitio real y de JS que cifra credenciales; no se
> testea con unit tests. Se verifica con una corrida real (Step 3).

- [ ] **Step 1: Implementar `tct/login.py`**

```python
# tct/login.py
"""Login en TCT con navegador headless. Devuelve ticket + cookies para requests.

El portal cifra usuario/clave con JavaScript y usa nombres de campo que rotan,
por lo que el login no es replicable con requests puro. Playwright corre el JS
del sitio igual que un humano y nos entrega la sesión autenticada.
"""
from playwright.sync_api import sync_playwright
from tct import config


def obtener_sesion(usuario=None, clave=None, headless=True, debug=False):
    """Devuelve (ticket, cookies_dict). Lanza RuntimeError si no autentica."""
    usuario = usuario or config.USER_TCT
    clave = clave or config.PASS_TCT
    if not usuario or not clave:
        raise RuntimeError("Faltan USER_TCT/PASS_TCT en el .env")

    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=headless)
        ctx = navegador.new_context()
        page = ctx.new_page()
        page.goto(config.URL_LOGIN, wait_until="networkidle")

        # En una página de login el campo password suele ser único.
        page.fill("input[type=password]", clave)
        # El usuario es el primer input de texto visible.
        page.fill("input[type=text]:visible", usuario)
        # Enviar: Enter dispara el JS de cifrado y el postback.
        page.press("input[type=password]", "Enter")
        page.wait_for_load_state("networkidle")

        # Ir a una página autenticada que expone el campo oculto 'ticket'.
        page.goto(config.URL_AGRUPADO, wait_until="networkidle")
        if debug:
            page.screenshot(path="debug_login.png")

        ticket = page.input_value("input[name=ticket]")
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        navegador.close()

    if not ticket or len(ticket) < 20:
        raise RuntimeError(
            "No se obtuvo 'ticket' tras el login. Revisá credenciales o "
            "ejecutá con debug=True para ver debug_login.png."
        )
    return ticket, cookies
```

- [ ] **Step 2: Sembrar cookies en una `requests.Session` (helper en scraper)**

Agregar a `tct/scraper.py`:
```python
def nueva_sesion(cookies: dict):
    """Crea una requests.Session con las cookies obtenidas del login."""
    sesion = requests.Session()
    sesion.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/148.0.0.0 Safari/537.36"),
    })
    for nombre, valor in cookies.items():
        sesion.cookies.set(nombre, valor, domain="tctcliente.copec.cl")
    return sesion
```

Agregar `import requests` al inicio de `tct/scraper.py` si no está.

Agregar test rápido en `tests/test_scraper.py`:
```python
def test_nueva_sesion_carga_cookies():
    s = scraper.nueva_sesion({"ASP.NET_SessionId": "abc", "AWSALB": "xyz"})
    nombres = {c.name for c in s.cookies}
    assert {"ASP.NET_SessionId", "AWSALB"} <= nombres
```

- [ ] **Step 3: Verificación de integración (login real)**

Run:
```bash
python -c "from tct.login import obtener_sesion; t,c=obtener_sesion(debug=True); print('ticket len', len(t)); print('cookies', list(c))"
```
Expected: imprime `ticket len 40` (aprox.) y una lista que incluye
`ASP.NET_SessionId`. Si falla, abrir `debug_login.png` y ajustar los selectores
de usuario/clave/botón en `login.py`.

- [ ] **Step 4: Ejecutar tests unitarios**

Run: `pytest tests/test_scraper.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tct/login.py tct/scraper.py tests/test_scraper.py
git commit -m "feat: login con playwright y sesion requests con cookies"
```

---

### Task 6: `consolidar.py` — unir todos los `.xlsx` en uno (TDD)

**Files:**
- Create: `tct/consolidar.py`
- Test: `tests/test_consolidar.py`

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_consolidar.py
import pandas as pd
from tct import consolidar


def _crear_xlsx(ruta, filas):
    pd.DataFrame(filas).to_excel(ruta, index=False)


def test_consolidar_agrega_columna_patente_y_concatena(tmp_path):
    a = tmp_path / "PDRF-74.xlsx"
    b = tmp_path / "ABCD-12.xlsx"
    _crear_xlsx(a, [{"Fecha": "2026-05-01", "Monto": 1000}])
    _crear_xlsx(b, [{"Fecha": "2026-05-02", "Monto": 2000},
                    {"Fecha": "2026-05-03", "Monto": 3000}])

    salida = tmp_path / "consolidado.xlsx"
    n = consolidar.consolidar([str(a), str(b)], str(salida))

    assert n == 3
    df = pd.read_excel(salida)
    assert set(df["Patente"]) == {"PDRF-74", "ABCD-12"}
    assert len(df) == 3
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `pytest tests/test_consolidar.py -v`
Expected: FAIL (`consolidar` no existe).

- [ ] **Step 3: Implementar `tct/consolidar.py`**

```python
# tct/consolidar.py
import os
import pandas as pd


def consolidar(rutas_xlsx: list[str], ruta_salida: str) -> int:
    """Lee cada .xlsx, agrega columna 'Patente' (del nombre de archivo) y
    concatena todo en `ruta_salida`. Devuelve el total de filas escritas."""
    marcos = []
    for ruta in rutas_xlsx:
        df = pd.read_excel(ruta)
        patente = os.path.splitext(os.path.basename(ruta))[0]
        df.insert(0, "Patente", patente)
        marcos.append(df)
    if not marcos:
        raise ValueError("No hay archivos para consolidar.")
    total = pd.concat(marcos, ignore_index=True)
    total.to_excel(ruta_salida, index=False)
    return len(total)
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

Run: `pytest tests/test_consolidar.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tct/consolidar.py tests/test_consolidar.py
git commit -m "feat: consolidacion de xlsx en un solo Excel"
```

---

### Task 7: `main.py` — CLI orquestador con logging y tolerancia a fallos

**Files:**
- Create: `main.py`

- [ ] **Step 1: Implementar `main.py`**

```python
# main.py
import argparse
import logging
import time

from tct import config, login, scraper, consolidar

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tct")

CARPETA_DESCARGAS = "descargas"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Descarga consolidada de consumos por patente (TCT).")
    p.add_argument("--desde", required=True, help="Fecha inicio YYYY-MM-DD")
    p.add_argument("--hasta", required=True, help="Fecha fin YYYY-MM-DD")
    p.add_argument("--patentes", default="patentes.txt", help="Archivo de patentes")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    patentes = config.cargar_patentes(args.patentes)
    if not patentes:
        raise SystemExit("patentes.txt está vacío.")
    log.info("Patentes a procesar: %d", len(patentes))

    ticket, cookies = login.obtener_sesion()
    log.info("Login OK (ticket %d chars)", len(ticket))
    sesion = scraper.nueva_sesion(cookies)

    descargados, fallidas = [], []
    for patente in patentes:
        try:
            contenido = scraper.descargar_patente(
                sesion, ticket, patente, args.desde, args.hasta)
            ruta = scraper.guardar_xlsx(contenido, patente, CARPETA_DESCARGAS)
            descargados.append(ruta)
            log.info("OK %s -> %s", patente, ruta)
        except Exception as e:  # tolerancia: anota y sigue
            fallidas.append(patente)
            log.error("FALLO %s: %s", patente, e)
        time.sleep(1.5)  # cortesía con el servidor

    if not descargados:
        raise SystemExit("No se descargó ninguna patente.")

    salida = f"consolidado_{args.desde}_{args.hasta}.xlsx"
    total = consolidar.consolidar(descargados, salida)
    log.info("Consolidado: %s (%d filas, %d patentes, %d fallidas)",
             salida, total, len(descargados), len(fallidas))
    if fallidas:
        log.warning("Patentes fallidas: %s", ", ".join(fallidas))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test del parser de argumentos**

Agregar `tests/test_main.py`:
```python
import main


def test_parse_args_lee_fechas():
    args = main.parse_args(["--desde", "2026-05-01", "--hasta", "2026-05-21"])
    assert args.desde == "2026-05-01"
    assert args.hasta == "2026-05-21"
    assert args.patentes == "patentes.txt"
```

Run: `pytest tests/test_main.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: CLI orquestador con logging y tolerancia a fallos"
```

---

### Task 8: Verificación end-to-end real + README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Corrida real completa con la patente de ejemplo**

Run:
```bash
python main.py --desde 2026-05-01 --hasta 2026-05-21
```
Expected: log con `Login OK`, `OK PDRF-74 -> descargas/PDRF-74.xlsx`, y
`Consolidado: consolidado_2026-05-01_2026-05-21.xlsx`. Abrir el Excel y verificar
que trae las transacciones con columna `Patente`.

- [ ] **Step 2: Si el detalle viene vacío o el segundo POST no devuelve un .xlsx**

Diagnóstico: guardar `r1.text` a un archivo y revisar si el formulario trae los
campos esperados; confirmar que `r2.headers["content-type"]` es de spreadsheet.
Si `r2` devuelve HTML en vez de xlsx, comparar los campos de `build_export_payload`
contra la captura real del export y ajustar (puede faltar algún hidden con nombre
dinámico que el harvest ya debería cubrir).

- [ ] **Step 3: Escribir `README.md`**

```markdown
# combustible-tct

Descarga el detalle de consumos por patente desde el portal TCT de Copec y lo
consolida en un solo Excel.

## Requisitos
```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Configuración
- `.env` con `USER_TCT` y `PASS_TCT`.
- `patentes.txt`: una patente por línea.

## Uso
```
python main.py --desde 2026-05-01 --hasta 2026-05-21
```
Resultado: `consolidado_<desde>_<hasta>.xlsx` (y respaldos en `descargas/`).

## Notas
- El login usa un navegador headless (Playwright) porque el portal cifra las
  credenciales con JavaScript. El resto usa `requests`.
- Si el login falla, corré con debug y revisá `debug_login.png` para ajustar los
  selectores en `tct/login.py`.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README con instrucciones de uso"
```

---

## Notas de verificación (self-review)

- **Cobertura del spec:** login Playwright (Task 5) ✓; detalle por patente con 2
  POST (Tasks 2–4) ✓; consolidación pandas (Task 6) ✓; CLI con `--desde/--hasta`
  (Task 7) ✓; manejo de errores por patente + sleep (Task 7) ✓; estructura de
  archivos (Task 0) ✓; protección de `.env` (Task 0 `.gitignore`) ✓.
- **Riesgo conocido:** los selectores del login (`input[type=password]`, etc.) y
  la suficiencia del `harvest_form_fields` para el postback de exportación se
  validan en la corrida real (Tasks 5 y 8); el plan incluye pasos de diagnóstico.
- **Reintento de login expirado:** el spec lo menciona; en esta primera versión
  se cubre con el log de fallo por patente. Si en la corrida real se observa
  expiración a mitad, se agrega un reintento envolviendo `descargar_patente`.
```
