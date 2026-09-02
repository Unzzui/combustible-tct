"""Regresión: un fallo de la flota (best-effort) no debe tumbar el login.

Bug de producción: `obtener_flota` navega el portal con postbacks; cuando falla
dejaba la página en una vista SIN input[name=ticket], y la lectura del ticket
—hecha DESPUÉS de la flota— hacía timeout y mataba la corrida (exit 1) en vez de
caer al fallback Flota.xlsx. El fix lee ticket+cookies ANTES de scrapear la flota.
"""
import sys
import types

import tct
from tct import login


class _Ctx:
    def cookies(self):
        return [{"name": "sesion", "value": "abc123"}]


class _Page:
    """Simula la landing autenticada.

    Reproduce el bug real: obtener_flota navega el portal (postbacks WebForms) y
    esa navegación borra input[name=ticket] de la página. Aquí, una vez que la
    flota marca `navegado`, leer el ticket lanza (como el Timeout de Playwright
    esperando el locator inexistente). Ese error NO está bajo el try/except de la
    flota en obtener_sesion_con_flota, así que con el orden viejo se propaga y
    tumba la corrida; con el orden nuevo el ticket ya se leyó antes de navegar."""

    def __init__(self):
        self.navegado = False

    def input_value(self, selector):
        assert selector == "input[name=ticket]"
        if self.navegado:
            raise RuntimeError(
                "Timeout 30000ms exceeded esperando locator('input[name=ticket]')"
            )
        # 40 chars: pasa el umbral len<20 de obtener_sesion_con_flota
        return "T" * 40

    def evaluate(self, *a, **k):
        # _leer_clientes(page): cuenta de un solo cliente (sin ventana) → []
        return []


def _instalar_flota(monkeypatch, fn):
    """Inyecta un `tct.flota_portal` falso para no depender de Playwright real."""
    fake = types.ModuleType("tct.flota_portal")
    fake.obtener_flota = fn
    # `from tct import flota_portal` resuelve por atributo del paquete tct; si el
    # módulo real ya se importó, sys.modules solo no basta. Parcheamos ambos.
    monkeypatch.setitem(sys.modules, "tct.flota_portal", fake)
    monkeypatch.setattr(tct, "flota_portal", fake, raising=False)


class _Playwright:
    """Context manager que imita sync_playwright(): entrega un chromium cuyo
    new_context/new_page devuelven nuestros dobles."""

    def __init__(self, page, ctx):
        self._page, self._ctx = page, ctx

    def __enter__(self):
        page, ctx = self._page, self._ctx

        class _Nav:
            def new_context(self_):
                return ctx

            def close(self_):
                return None

        class _Chromium:
            def launch(self_, **_kw):
                return _Nav()

        return types.SimpleNamespace(chromium=_Chromium())

    def __exit__(self, *a):
        return False


def _correr(monkeypatch, obtener_flota_fn):
    page, ctx = _Page(), _Ctx()
    ctx.new_page = lambda: page
    monkeypatch.setattr(login, "sync_playwright", lambda: _Playwright(page, ctx))
    monkeypatch.setattr(login, "_login_en_page", lambda *a, **k: None)
    _instalar_flota(monkeypatch, obtener_flota_fn)
    return page, login.obtener_sesion_con_flota(usuario="u", clave="c")


def test_flota_falla_pero_devuelve_ticket_y_cae_a_fallback(monkeypatch):
    def flota_rota(page):
        # La navegación WebForms ya movió la página (borrando el input) antes de
        # que el postback siguiente reventara con el error JS real del portal.
        page.navegado = True
        raise RuntimeError("Cannot read properties of undefined (reading 'elements')")

    page, (ticket, cookies, patentes, clientes) = _correr(monkeypatch, flota_rota)
    assert ticket == "T" * 40
    assert cookies == {"sesion": "abc123"}
    assert patentes == []  # el caller (main.py) cae a Flota.xlsx
    assert clientes == []


def test_flota_ok_devuelve_patentes(monkeypatch):
    page, (ticket, cookies, patentes, clientes) = _correr(
        monkeypatch, lambda page: ["LRBJ-98", "VYJH-62"]
    )
    assert ticket == "T" * 40
    assert patentes == ["LRBJ-98", "VYJH-62"]
