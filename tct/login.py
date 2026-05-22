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
