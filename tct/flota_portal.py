"""Obtiene la lista de patentes de la flota desde el informe agrupado del portal.

El informe "Consumos De Un Producto Por Periodo" lista una fila por patente. Solo
lo usamos para la LISTA (no para datos): el detalle se baja per-patente aparte.
La navegación es un postback cruzado de WebForms (la función `enviar` del portal).
"""
import logging
import re

from tct import config

log = logging.getLogger("tct")

_PATENTE_RE = re.compile(r"^[A-Z]{4}-\d{2}$")

URL_INFORME = "../VistasAdminInformes/AdmInfConsumosProdPeriodo.aspx"
_ACEPTAR_POLITICAS = "ctl00$Cph1$LinkBtnAceptarPoliticas"
_BUSCAR = "ctl00$Cph1$LinkBtnBuscar"

# `enviar()` del portal: reescribe form.action, renombra __VIEWSTATE→NOVIEWSTATE y
# postea BtnCliente. Lo replicamos con submit() directo para evitar el strict-mode
# de Telerik (__doPostBack accede a `arguments`, prohibido en funciones inyectadas).
_JS_SET = (
    "const sh=(n,v)=>{let e=f.elements[n];"
    "if(!e){e=document.createElement('input');e.type='hidden';e.name=n;f.appendChild(e);}"
    "e.value=v;};"
)


def parsear_patentes(valores):
    """Normaliza, filtra a patentes válidas (LLLL-NN) y dedup preservando orden."""
    out, vistos = [], set()
    for v in valores:
        p = config.normalizar_patente(v)
        if _PATENTE_RE.match(p) and p not in vistos:
            vistos.add(p)
            out.append(p)
    return out


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
    patentes = parsear_patentes(col0)
    log.info("Flota leída del portal: %d patentes.", len(patentes))
    return patentes
