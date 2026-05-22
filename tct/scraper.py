"""Descarga del detalle de consumos por patente usando requests.

El login (cifrado con JS) lo resuelve tct.login con Playwright; aquí solo se
reutilizan las cookies + ticket de esa sesión para bajar los .xlsx.
"""
import io
import os
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

from tct.config import EXPORT_TARGET, URL_DETALLE, URL_AGRUPADO

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


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


def nueva_sesion(cookies: dict):
    """Crea una requests.Session con las cookies obtenidas del login."""
    sesion = requests.Session()
    sesion.headers.update({"User-Agent": USER_AGENT})
    for nombre, valor in cookies.items():
        sesion.cookies.set(nombre, valor, domain="tctcliente.copec.cl")
    return sesion


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


def descargar_patente_df(sesion, ticket, patente, desde, hasta, glosa_prod="Diésel"):
    """Como descargar_patente pero devuelve un DataFrame con el detalle.
    DataFrame vacío si la patente no tiene transacciones en el rango."""
    contenido = descargar_patente(sesion, ticket, patente, desde, hasta, glosa_prod)
    try:
        return pd.read_excel(io.BytesIO(contenido))
    except ValueError:
        return pd.DataFrame()


def guardar_xlsx(contenido: bytes, patente: str, carpeta: str) -> str:
    """Guarda los bytes del xlsx en carpeta/<patente>.xlsx y devuelve la ruta."""
    os.makedirs(carpeta, exist_ok=True)
    seguro = patente.strip().replace("/", "-").replace(" ", "")
    ruta = os.path.join(carpeta, f"{seguro}.xlsx")
    with open(ruta, "wb") as fh:
        fh.write(contenido)
    return ruta
