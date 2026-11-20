"""Login en TCT con navegador headless. Devuelve ticket + cookies para requests.

El portal cifra usuario/clave con JavaScript y usa nombres de campo que rotan,
por lo que el login no es replicable con requests puro. Playwright corre el JS
del sitio igual que un humano y nos entrega la sesión autenticada.
"""
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from tct import config
from tct.mail_reader import MailReader

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
    // 2FA: el modal "#popup-otp" (class popup-overlay) se muestra con
    // display:flex y se oculta con display:none. Es la señal fiable de que el
    // portal pide el código. NO usar offsetParent del campo: el modal es un
    // overlay posicionado y offsetParent puede ser null aunque esté visible.
    const modal = document.querySelector('#popup-otp');
    if (modal && getComputedStyle(modal).display !== 'none') return {otp: true};
    const candidatos = (%s)();
    const nuevos = candidatos.filter(txt => !previos.includes(txt));
    if (nuevos.length) return {ok: false, msg: nuevos[0]};
    return null;
}
""" % _JS_CANDIDATOS

# Igual que el anterior pero SIN detectar OTP: se usa tras enviar el código, para
# esperar el desenlace final (ticket o rechazo) sin volver a entrar al flujo OTP
# mientras el modal de 2FA se re-renderiza en el postback.
_JS_ESPERAR_TICKET = """
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


# Telemetría de terceros que no aporta nada al scraping. Los beacons de New
# Relic (bam.nr-data.net) quedan colgados sin responder desde 2026-09-22 y, con
# ellos abiertos, `networkidle` nunca llega: la selección de cliente moría por
# timeout de 30s aunque la página ya estaba lista. Imágenes/fuentes/media
# tampoco se usan (el banner en S3 de Copec también quedaba colgado).
_HOSTS_BLOQUEADOS = re.compile(r"(^|\.)(nr-data\.net|newrelic\.com)$", re.I)
_TIPOS_BLOQUEADOS = {"image", "media", "font"}


def _filtrar_request(route):
    req = route.request
    host = urlparse(req.url).hostname or ""
    if req.resource_type in _TIPOS_BLOQUEADOS or _HOSTS_BLOQUEADOS.search(host):
        route.abort()
    else:
        route.continue_()


def _nuevo_contexto(navegador):
    """Contexto de navegador con la telemetría y los recursos pesados bloqueados."""
    ctx = navegador.new_context()
    ctx.route("**/*", _filtrar_request)
    return ctx


def _esperar_red_quieta(page, timeout=10000):
    """`networkidle` best-effort: si un tercero deja una conexión abierta no debe
    tumbar la corrida; el caller ya esperó la señal real (el ticket)."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeoutError:
        log.debug("networkidle no llegó en %d ms; se continúa", timeout)


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
    # La señal real de que la vista del cliente sirve es el ticket, no la red.
    page.wait_for_function(
        "() => { const t = document.querySelector('input[name=ticket]');"
        " return t && t.value && t.value.length > 20; }",
        timeout=30000,
    )
    _esperar_red_quieta(page)


def _ingresar_codigo_otp(page, code):
    """Escribe `code` en #TxbOtpCodigo.

    El campo nace `readonly` (el portal lo protege con protegerCampoOtp y lo
    habilita con habilitarCampoOtp en handlers de mouse/teclado). Se dispara con
    un click real y, como respaldo, se le remueve el readonly a mano y se fuerza
    el value con un evento `input` (el oninput del sitio filtra no-dígitos y
    recorta a 6)."""
    campo = page.locator("#TxbOtpCodigo")
    campo.scroll_into_view_if_needed()
    try:
        campo.click()  # onmousedown=habilitarCampoOtp → quita el readonly
    except Exception:
        pass
    page.eval_on_selector("#TxbOtpCodigo", "el => el.removeAttribute('readonly')")
    try:
        campo.fill("")
        campo.press_sequentially(code, delay=60)
    except Exception:
        pass
    # Respaldo: fijar el value y disparar input por si el tipeo no prendió.
    page.eval_on_selector(
        "#TxbOtpCodigo",
        "(el, v) => { el.value = v;"
        " el.dispatchEvent(new Event('input', {bubbles: true})); }",
        code,
    )


def _resolver_otp(page, reader, baseline, previos, desde=None):
    """El portal pidió 2FA: lee el código del correo, lo ingresa y lo valida.

    `desde` (datetime UTC) es el instante del envío del login: solo se aceptan
    correos OTP posteriores, para no mandar nunca un código viejo (que el portal
    rechaza y gatilla el circuit breaker). Devuelve el dict de desenlace final
    (ticket OK o rechazo). Levanta LoginTimeoutError si tras validar no aparece
    ni ticket ni mensaje."""
    log.info("Portal pidió código OTP; esperando el correo de verificación...")
    _dump_debug(page, "otp_pedido")
    try:
        code = reader.wait_for_new_otp(baseline, timeout=90, poll_interval=5,
                                       desde=desde)
    except TimeoutError:
        log.warning("OTP no llegó en 90s; solicito reenvío y espero más.")
        try:
            page.eval_on_selector("#BtnReenviarOtp", "el => el.click()")
        except Exception:
            pass
        code = reader.wait_for_new_otp(baseline, timeout=120, poll_interval=5,
                                       desde=desde)
    log.info("OTP recibido (%d dígitos); validando.", len(code))
    _ingresar_codigo_otp(page, code)
    page.eval_on_selector("#BtnValidarOtp", "el => el.click()")
    try:
        return page.wait_for_function(
            _JS_ESPERAR_TICKET, arg=previos, timeout=30000
        ).json_value()
    except PlaywrightTimeoutError:
        _dump_debug(page, "otp_timeout")
        raise LoginTimeoutError(
            f"Timeout tras validar el OTP; URL={page.url}, "
            f"título={page.title()!r}."
        )


def _login_en_page(page, usuario, clave, debug=False):
    """Loguea en el portal sobre una página Playwright dada, dejándola autenticada.

    Si el portal pide 2FA (código OTP por correo), lo resuelve leyendo el código
    del buzón configurado (MAIL_USER/MAIL_PASS). Levanta PortalBlockedError /
    LoginTimeoutError / RuntimeError. No abre ni cierra el navegador: el caller
    es dueño del ciclo de vida.
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

    # Preparar la lectura del OTP ANTES de enviar: la "línea base" de correos de
    # verificación ya existentes se toma ahora para, si el portal pide 2FA, poder
    # distinguir el código NUEVO de esta sesión (incluso frente a otros logins en
    # la misma corrida). Sin credenciales de correo seguimos igual: si no piden
    # OTP, el login funciona; si lo piden, abajo se levanta un error claro.
    reader, baseline = None, set()
    if config.MAIL_USER and config.MAIL_PASS:
        reader = MailReader(config.MAIL_USER, config.MAIL_PASS, config.MAIL_HOST)
        try:
            baseline = reader.baseline_uids()
        except Exception as e:  # noqa: BLE001 — el OTP (si llega) lo reportará
            log.warning("No se pudo leer la línea base de OTP del correo: %s", e)

    page.locator("#TxbUsuario").press_sequentially(usuario, delay=30)
    page.locator("#TxbClave").press_sequentially(clave, delay=30)
    page.locator("#TxbClave").blur()
    # Marca temporal del envío: el OTP de ESTA sesión llega después de este
    # instante. wait_for_new_otp descarta correos anteriores (código viejo).
    otp_desde = datetime.now(timezone.utc)
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

    # 2FA: el portal intercaló el modal del código OTP. Se resuelve leyendo el
    # código del correo y validándolo; el desenlace pasa a ser el de ESA validación.
    if resultado.get("otp"):
        if reader is None:
            _dump_debug(page, "otp_sin_mail")
            raise RuntimeError(
                "El portal pidió un código OTP pero no hay MAIL_USER/MAIL_PASS "
                "configurados para leerlo del correo. Configurá esas variables "
                "(se reenvían como secrets en service.yaml)."
            )
        resultado = _resolver_otp(page, reader, baseline, previos, desde=otp_desde)

    if not resultado.get("ok"):
        _dump_debug(page, "rechazo")
        raise PortalBlockedError(
            f"Portal rechazó el login: {resultado.get('msg', '?')!r}"
        )

    _esperar_red_quieta(page)
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
        ctx = _nuevo_contexto(navegador)
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
        ctx = _nuevo_contexto(navegador)
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
        ctx = _nuevo_contexto(navegador)
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
