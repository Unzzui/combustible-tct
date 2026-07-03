import os
import re
from dotenv import load_dotenv

load_dotenv()

BASE = "https://tctcliente.copec.cl"
URL_LOGIN = BASE + "/"
URL_AGRUPADO = BASE + "/VistasAdminInformes/AdmInfConsumosAgrupado.aspx"
URL_DETALLE = BASE + "/VistasAdminInformes/AdmInfConsumosPorPatenteDetalle.aspx"
EXPORT_TARGET = "ctl00$Cph1$LinkBtnExportarXls"

def _env(nombre: str) -> str:
    # docker --env-file no interpreta comillas: `USER=\"x\"` llega como `\"x\"` literal.
    return os.getenv(nombre, "").strip().strip('"').strip("'")


USER_TCT = _env("USER_TCT")
PASS_TCT = _env("PASS_TCT")


def cargar_patentes(ruta: str) -> list[str]:
    """Lee patentes (una por línea), ignora líneas vacías y recorta espacios."""
    with open(ruta, encoding="utf-8") as fh:
        return [linea.strip() for linea in fh if linea.strip()]


def normalizar_patente(valor) -> str:
    """Normaliza una patente al formato del portal (con guión).

    El portal usa `LLLL-NN` (p. ej. PDRF-74), pero la flota puede venir sin
    guión (PDRF74). Quita espacios/guiones, pasa a mayúsculas y, si calza con
    4 letras + 2 dígitos, inserta el guión. Si no calza, devuelve el texto
    limpio tal cual.
    """
    s = re.sub(r"[^A-Za-z0-9]", "", str(valor)).upper()
    m = re.match(r"^([A-Z]{4})(\d{2})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return s


def cargar_patentes_flota(ruta: str, columna: str = "Patente",
                          hoja: str | int | None = None) -> list[str]:
    """Lee las patentes desde un Excel de flota (columna `Patente`),
    las normaliza y elimina duplicados conservando el orden."""
    import pandas as pd

    df = pd.read_excel(ruta, sheet_name=hoja) if hoja is not None else pd.read_excel(ruta)
    if columna not in df.columns:
        raise ValueError(
            f"La columna '{columna}' no está en {ruta}"
            f"{f' (hoja {hoja!r})' if hoja is not None else ''}. "
            f"Columnas disponibles: {list(df.columns)}"
        )
    out, vistos = [], set()
    for valor in df[columna].dropna():
        pat = normalizar_patente(valor)
        if pat and pat not in vistos:
            vistos.add(pat)
            out.append(pat)
    return out


def cargar_mapa_patente_ot(ruta: str, col_pat: str = "Patente",
                           col_ot: str = "Proyecto",
                           hoja: str | int | None = None) -> dict[str, str]:
    """Devuelve {patente_normalizada: OT} leído de un Excel con columnas
    `Patente` y `Proyecto`. Si una patente se repite, gana la primera."""
    import pandas as pd

    df = pd.read_excel(ruta, sheet_name=hoja) if hoja is not None else pd.read_excel(ruta)
    for c in (col_pat, col_ot):
        if c not in df.columns:
            raise ValueError(
                f"La columna '{c}' no está en {ruta}"
                f"{f' (hoja {hoja!r})' if hoja is not None else ''}. "
                f"Columnas disponibles: {list(df.columns)}"
            )
    mapa: dict[str, str] = {}
    for _, fila in df[[col_pat, col_ot]].dropna(subset=[col_pat]).iterrows():
        pat = normalizar_patente(fila[col_pat])
        if not pat or pat in mapa:
            continue
        ot = str(fila[col_ot]).strip()
        if ot:
            mapa[pat] = ot
    return mapa
