"""Login en TCT con navegador headless. Devuelve ticket + cookies para requests.

El portal cifra usuario/clave con JavaScript y usa nombres de campo que rotan,
por lo que el login no es replicable con requests puro. Playwright corre el JS
del sitio igual que un humano y nos entrega la sesión autenticada.
"""
import logging
import os
import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from tct import config

log = logging.getLogger("tct")


class PortalBlockedError(RuntimeError):
    """El portal rechazó el login: bloqueo por intentos, cuenta bloqueada,
    clave incorrecta. NO conviene reintentar rápido: acelerar los intentos
    agrava el bloqueo. El circuit breaker en main.py pausa el servicio N
    horas cuando se levanta esta excepción."""


class LoginTimeoutError(RuntimeError):
    """Timeout sin señal explícita del portal (portal caído, red, cambio de
    layout). Distinto de bloqueo: reintentar es seguro."""


# Textos que delatan un rechazo del portal. `contact` NO va en la lista: el pie
# de página dice "contacta a tu ejecutivo de cuenta" de forma permanente, así
# que no distingue nada — con él, todo intento se leía como bloqueo.
_PATRON_ERROR = r"bloque|inten|incorr|super[oó]|espere|no coincide|inv[aá]lid|deshabilitad|suspendid"

# Recolector de mensajes de error VISIBLES. Se queda con el nodo más profundo
# que calza: si no, un contenedor arrastra el pie de página entero y el texto
# reportado no permite saber qué pasó.
_JS_CANDIDATOS = """
() => {
    const patron = /%s/i;
    const visible = (el) => el.offsetParent !== null;
    const texto = (el) => (el.innerText || '').trim();
    return Array.from(document.querySelectorAll('span, div, label, td, p'))
        .filter(el => visible(el))
        .filter(el => {
            const txt = texto(el);
            return txt && txt.length < 300 && patron.test(txt);
        })
        .filter(el => !Array.from(el.querySelectorAll('span, div, label, td, p'))
            .some(hijo => visible(hijo) && patron.test(texto(hijo))))
        .map(texto);
}
""" % _PATRON_ERROR

# Polling en el DOM: (a) ticket con valor → sesión OK, (b) mensaje de error
# NUEVO respecto del que ya estaba antes de enviar → rechazo del portal,
# (c) `null` → seguir esperando.
_JS_ESPERAR_DESENLACE = """
(previos) => {
    const t = document.querySelector('input[name=ticket]');
    if (t && t.value && t.value.length > 20) return {ok: true};
    const candidatos = (%s)();
    const nuevos = candidatos.filter(txt => !previos.includes(txt));
    if (nuevos.length) return {ok: false, msg: nuevos[0]};
    return null;
}
""" % _JS_CANDIDATOS


# --- Selección de cliente (pasarela RadWindowClientes) ------------------------
# Tras el login, la cuenta puede acceder a VARIOS clientes (p. ej. OCA ENSAYOS
# 754405 y OCA GLOBAL 799127). El portal muestra una ventana con una fila por
# cliente; cada uno ve SOLO sus consumos y su flota. El `ticket` es el mismo para
# todos: el contexto de cliente vive en las cookies de sesión que cambian al
# seleccionar la fila. Sin seleccionar, el portal usa un cliente por defecto (por
# eso, hasta ahora, solo se bajaban los consumos de ese cliente).

_JS_LEER_CLIENTES = """
() => Array.from(document.querySelectorAll("a[id*='LinkBtnIngresar']")).map(a => {
    const tr = a.closest('tr');
    return {id: a.id, texto: ((tr ? tr.innerText : a.innerText) || '').replace(/\\s+/g,' ').trim()};
})
"""

# "754405 OCA ENSAYOS INSPECCIONES Y" -> ("754405", "OCA ENSAYOS INSPECCIONES Y")
_RE_FILA_CLIENTE = re.compile(r"^\s*(\d{4,})\s+(.*?)\s*$")


def parse_clientes(rows):
    """Convierte las filas de la grilla (dicts con id/texto) en tuplas
    (codigo, nombre, anchor_id). Ignora filas sin código numérico."""
    out = []
    for r in rows:
        texto = re.sub(r"\s*Ingresar\s*$", "", (r.get("texto") or "").strip())
        m = _RE_FILA_CLIENTE.match(texto)
        if m:
            out.append((m.group(1), m.group(2).strip(), r.get("id")))
    return out


def id_anchor_de_cliente(clientes, codigo):
    """anchor_id de la fila cuyo código == `codigo`, o None."""
    for cod, _nombre, anchor_id in clientes:
        if cod == str(codigo):
            return anchor_id
    return None


def _leer_clientes(page):
    """Lee la ventana de selección; [] si la cuenta tiene un solo cliente."""
    try:
        return parse_clientes(page.evaluate(_JS_LEER_CLIENTES))
    except Exception:
        return []


def _seleccionar_cliente(page, clientes, codigo):
    """Entra al cliente `codigo` haciendo click REAL en su fila (ejecuta el href
    javascript:__doPostBack en contexto normal; page.evaluate(__doPostBack) rompe
    por el strict-mode de ASP.NET AJAX). Deja la página en la vista del cliente."""
    anchor_id = id_anchor_de_cliente(clientes, codigo)
    if not anchor_id:
        disponibles = ", ".join(c[0] for c in clientes) or "(ninguno)"
        raise RuntimeError(
            f"Cliente {codigo!r} no está en la ventana. Disponibles: {disponibles}."
        )
    page.eval_on_selector(f"#{anchor_id}", "el => el.click()")
    try:
        page.wait_for_url("**/AdmCteInicio.aspx", timeout=15000)
    except PlaywrightTimeoutError:
        pass  # algunos clientes aterrizan en otra vista; el ticket igual queda
    page.wait_for_load_state("networkidle")


def _login_en_page(page, usuario, clave, debug=False):
    """Loguea en el portal sobre una página Playwright dada, dejándola autenticada.

    Levanta PortalBlockedError / LoginTimeoutError / RuntimeError. No abre ni
    cierra el navegador: el caller es dueño del ciclo de vida.
    """
    # `domcontentloaded`, no `load`: esperar todos los subrecursos incluye
    # terceros (New Relic, widget de WhatsApp) que a veces no cierran en 30s
    # y tumbaban la corrida sin haber intentado el login siquiera. La señal
    # real de que la página sirve es el formulario, y eso se espera abajo.
    page.goto(config.URL_LOGIN, wait_until="domcontentloaded", timeout=60000)

    # Campos visibles del login (ids estáticos del portal). Hay que ESCRIBIR
    # carácter por carácter: el JS del sitio cifra los valores en handlers de
    # teclado, así que un fill() directo no los dispara y el login falla.
    try:
        page.locator("#TxbUsuario").wait_for(state="visible", timeout=30000)
    except Exception:
        _dump_debug(page, "sin_form")
        raise RuntimeError(
            f"No apareció #TxbUsuario. URL={page.url} título={page.title()!r}."
        )

    # Línea base: lo que ya calzaba con el patrón ANTES de enviar el
    # formulario es decorado de la página, no un veredicto sobre el login.
    previos = page.evaluate(_JS_CANDIDATOS)

    page.locator("#TxbUsuario").press_sequentially(usuario, delay=30)
    page.locator("#TxbClave").press_sequentially(clave, delay=30)
    page.locator("#TxbClave").blur()
    page.click("#BtnIngresar")

    # Race entre "ticket rellenado" y "mensaje de error nuevo". El primero
    # que ocurra termina el wait; timeout ⇒ portal silencioso.
    try:
        resultado = page.wait_for_function(
            _JS_ESPERAR_DESENLACE, arg=previos, timeout=30000
        ).json_value()
    except PlaywrightTimeoutError:
        _dump_debug(page, "timeout")
        # Seguir en una URL de login sin ticket = el postback volvió a
        # renderizar el formulario. El portal rechaza credenciales así, EN
        # SILENCIO: no pinta ningún mensaje. Cuenta como rechazo y arma el
        # circuit breaker — si no, el schedule sigue quemando intentos con
        # una clave mala dos veces al día y termina bloqueando la cuenta.
        if re.search(r"/(Login|LoginDesk|LoginMobile)\.aspx|copec\.cl/?$", page.url, re.I):
            raise PortalBlockedError(
                f"Timeout sin ticket, sigue en {page.url}. Portal rechazó "
                "el login sin mensaje (credenciales inválidas o cuenta bloqueada)."
            )
        raise LoginTimeoutError(
            f"Timeout post-click; URL={page.url}, título={page.title()!r}."
        )

    if not resultado.get("ok"):
        _dump_debug(page, "rechazo")
        raise PortalBlockedError(
            f"Portal rechazó el login: {resultado.get('msg', '?')!r}"
        )

    page.wait_for_load_state("networkidle")
    if debug:
        page.screenshot(path="debug_login.png")


def obtener_sesion(usuario=None, clave=None, headless=True, debug=False):
    """Devuelve (ticket, cookies_dict).

    Levanta:
      - PortalBlockedError: rechazo explícito o silencio sostenido en LoginDesk.
      - LoginTimeoutError: timeout sin señal (portal caído / cambio de layout).
      - RuntimeError: config faltante o campo del formulario no encontrado.
    """
    usuario = usuario or config.USER_TCT
    clave = clave or config.PASS_TCT
    if not usuario or not clave:
        raise RuntimeError("Faltan USER_TCT/PASS_TCT en el .env")

    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=headless)
        ctx = navegador.new_context()
        page = ctx.new_page()
        _login_en_page(page, usuario, clave, debug=debug)
        ticket = page.input_value("input[name=ticket]")
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        navegador.close()

    if not ticket or len(ticket) < 20:
        raise LoginTimeoutError(
            "No se obtuvo 'ticket' tras el login. Revisá credenciales."
        )
    return ticket, cookies


def obtener_sesion_con_flota(usuario=None, clave=None, cliente=None, headless=True):
    """Un solo login: devuelve (ticket, cookies, patentes_del_portal, clientes).

    Reutiliza la MISMA sesión para (a) scrapear la lista de flota del informe y
    (b) extraer ticket+cookies para la descarga per-patente. Si el scraping de la
    flota falla, patentes=[] (el caller decide el fallback) pero ticket+cookies
    quedan disponibles. Levanta las mismas excepciones que obtener_sesion si el
    login mismo falla.

    `clientes` es la lista [(codigo, nombre, anchor_id)] de la ventana de selección
    (vacía si la cuenta tiene un solo cliente). Si `cliente` (código) se indica, se
    entra a ESE cliente antes de leer ticket/cookies/flota, de modo que todo quede
    en su contexto (sus cookies y su flota).
    """
    from tct import flota_portal

    usuario = usuario or config.USER_TCT
    clave = clave or config.PASS_TCT
    if not usuario or not clave:
        raise RuntimeError("Faltan USER_TCT/PASS_TCT en el .env")

    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=headless)
        ctx = navegador.new_context()
        page = ctx.new_page()
        _login_en_page(page, usuario, clave)
        clientes = _leer_clientes(page)
        if cliente is not None:
            _seleccionar_cliente(page, clientes, cliente)
        # Capturar ticket+cookies AQUÍ, sobre la landing recién autenticada donde
        # el login ya confirmó input[name=ticket] con valor. obtener_flota navega
        # el portal (postbacks WebForms) y puede dejar la página en una vista sin
        # ese input; si leyéramos el ticket DESPUÉS, un fallo de la flota
        # (best-effort) haría timeout esperando el input y tumbaría toda la corrida
        # en vez de caer al fallback Flota.xlsx. La sesión ya vive en las cookies
        # del contexto, así que la navegación de la flota no la invalida.
        ticket = page.input_value("input[name=ticket]")
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        try:
            patentes = flota_portal.obtener_flota(page)
        except Exception as e:  # noqa: BLE001 — el caller decide el fallback
            log.warning("No se pudo leer la flota del portal: %s", e)
            patentes = []
        navegador.close()

    if not ticket or len(ticket) < 20:
        raise LoginTimeoutError(
            "No se obtuvo 'ticket' tras el login. Revisá credenciales."
        )
    return ticket, cookies, patentes, clientes


def listar_clientes(usuario=None, clave=None, headless=True):
    """Devuelve [(codigo, nombre)] de los clientes accesibles con la cuenta.
    Un login liviano que solo lee la ventana de selección (sin scrapear flota)."""
    usuario = usuario or config.USER_TCT
    clave = clave or config.PASS_TCT
    if not usuario or not clave:
        raise RuntimeError("Faltan USER_TCT/PASS_TCT en el .env")

    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=headless)
        ctx = navegador.new_context()
        page = ctx.new_page()
        _login_en_page(page, usuario, clave)
        clientes = _leer_clientes(page)
        navegador.close()
    return [(cod, nombre) for cod, nombre, _id in clientes]


def _dump_debug(page, sufijo: str) -> None:
    """Screenshot + HTML del estado actual para postmortem. Best-effort."""
    dest = os.environ.get("TCT_DEBUG_DIR", "data")
    try:
        os.makedirs(dest, exist_ok=True)
        page.screenshot(path=f"{dest}/debug_login_{sufijo}.png", full_page=True)
        with open(f"{dest}/debug_login_{sufijo}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception:
        pass
