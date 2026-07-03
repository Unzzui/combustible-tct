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
        page.goto(config.URL_LOGIN, wait_until="load")

        # Campos visibles del login (ids estáticos del portal). Hay que ESCRIBIR
        # carácter por carácter: el JS del sitio cifra los valores en handlers de
        # teclado, así que un fill() directo no los dispara y el login falla.
        try:
            page.locator("#TxbUsuario").wait_for(state="visible", timeout=30000)
        except Exception:
            page.screenshot(path="debug_login_fail.png", full_page=True)
            with open("debug_login_fail.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            raise RuntimeError(
                f"No apareció #TxbUsuario. URL actual: {page.url} | título: {page.title()!r}. "
                "Revisá debug_login_fail.png y debug_login_fail.html."
            )
        page.locator("#TxbUsuario").press_sequentially(usuario, delay=30)
        page.locator("#TxbClave").press_sequentially(clave, delay=30)
        page.locator("#TxbClave").blur()
        # El botón Ingresar dispara el cifrado JS y el postback.
        page.click("#BtnIngresar")

        # Tras autenticar, el portal redirige a la página de inicio y rellena el
        # input oculto 'ticket'. Esperamos a que ese campo tenga valor (vía JS),
        # que es la señal más confiable de que la sesión quedó establecida.
        page.wait_for_function(
            "() => { const t = document.querySelector('input[name=ticket]'); "
            "return t && t.value && t.value.length > 20; }",
            timeout=30000,
        )
        page.wait_for_load_state("networkidle")
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
