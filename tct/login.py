"""Login en TCT con navegador headless. Devuelve ticket + cookies para requests.

El portal cifra usuario/clave con JavaScript y usa nombres de campo que rotan,
por lo que el login no es replicable con requests puro. Playwright corre el JS
del sitio igual que un humano y nos entrega la sesión autenticada.
"""
import os

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from tct import config


class PortalBlockedError(RuntimeError):
    """El portal rechazó el login: bloqueo por intentos, cuenta bloqueada,
    clave incorrecta. NO conviene reintentar rápido: acelerar los intentos
    agrava el bloqueo. El circuit breaker en main.py pausa el servicio N
    horas cuando se levanta esta excepción."""


class LoginTimeoutError(RuntimeError):
    """Timeout sin señal explícita del portal (portal caído, red, cambio de
    layout). Distinto de bloqueo: reintentar es seguro."""


# Polling en el DOM: (a) ticket con valor → sesión OK, (b) mensaje de error
# visible → rechazo del portal, (c) `null` → seguir esperando.
_JS_ESPERAR_DESENLACE = """
() => {
    const t = document.querySelector('input[name=ticket]');
    if (t && t.value && t.value.length > 20) return {ok: true};
    const patron = /bloque|inten|incorr|super[oó]|contact|espere|no coincide|inv[aá]lid/i;
    const errores = Array.from(document.querySelectorAll('span, div, label, td, p'))
        .filter(el => el.offsetParent !== null)
        .map(el => (el.innerText || '').trim())
        .filter(txt => txt && txt.length < 300 && patron.test(txt));
    if (errores.length) return {ok: false, msg: errores[0]};
    return null;
}
"""


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
        page.goto(config.URL_LOGIN, wait_until="load")

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
        page.locator("#TxbUsuario").press_sequentially(usuario, delay=30)
        page.locator("#TxbClave").press_sequentially(clave, delay=30)
        page.locator("#TxbClave").blur()
        page.click("#BtnIngresar")

        # Race entre "ticket rellenado" y "mensaje de error visible". El primero
        # que ocurra termina el wait; timeout ⇒ portal silencioso.
        try:
            resultado = page.wait_for_function(
                _JS_ESPERAR_DESENLACE, timeout=30000
            ).json_value()
        except PlaywrightTimeoutError:
            _dump_debug(page, "timeout")
            if "LoginDesk" in page.url:
                raise PortalBlockedError(
                    f"Timeout sin ticket, sigue en {page.url}. "
                    "Portal silencioso — probable bloqueo por intentos."
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

        ticket = page.input_value("input[name=ticket]")
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        navegador.close()

    if not ticket or len(ticket) < 20:
        raise LoginTimeoutError(
            "No se obtuvo 'ticket' tras el login. Revisá credenciales."
        )
    return ticket, cookies


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
