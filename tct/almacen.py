"""Almacén incremental del histórico de consumos en Parquet.

El maestro (un .parquet) acumula todas las transacciones entre corridas. En cada
corrida solo se descarga lo nuevo (desde la última fecha por patente, con días de
solape) y se fusiona deduplicando por N° de guía.
"""
import os
import re

import pandas as pd

COL_FECHA = "Fecha Transacción"
COL_GUIA = "Guía de Despacho"
COL_PATENTE = "Patente"
COL_CLIENTE = "Cliente"
COL_TARJETA = "Tarjeta"
COL_RUT_CHOFER = "Rut Chofer"

# Correcciones de RUT del chofer: (patente, rut_en_portal, rut_correcto). Misma
# persona registrada con dos RUT distintos en el portal; se normaliza al correcto.
# Se aplica en cada fusión, así arregla tanto el histórico ya guardado como lo que
# baje después (una re-descarga trae de nuevo el RUT malo del portal).
CORRECCIONES_RUT_CHOFER = [
    ("THJG-11", "18841039-1", "12393967-0"),
]

# El nº de tarjeta trae embebido el código de cliente: "1-799127-00477-3-3".
_RE_CLIENTE_TARJETA = re.compile(r"^\s*\d+-(\d+)-")


def cliente_de_tarjeta(valor) -> str:
    """Extrae el código de cliente del nº de tarjeta ('1-799127-...' -> '799127').
    Devuelve '' si no calza."""
    m = _RE_CLIENTE_TARJETA.match(str(valor or ""))
    return m.group(1) if m else ""


def _vacio(serie) -> "pd.Series":
    """True donde la celda está vacía/NaN/'none' (para backfill idempotente)."""
    txt = serie.astype(str).str.strip().str.lower()
    return serie.isna() | txt.isin(("", "none", "nan"))


def asegurar_cliente(df):
    """Devuelve una copia del df garantizando la columna `Cliente`, rellenándola
    desde `Tarjeta` donde falte. Idempotente: respeta los valores ya presentes."""
    if df is None or df.empty:
        return df
    df = df.copy()
    if COL_CLIENTE not in df.columns:
        df[COL_CLIENTE] = None
    if COL_TARJETA in df.columns:
        faltan = _vacio(df[COL_CLIENTE])
        df.loc[faltan, COL_CLIENTE] = df.loc[faltan, COL_TARJETA].map(cliente_de_tarjeta)
    return df


def leer_maestro(ruta_parquet: str):
    """Devuelve el DataFrame maestro si el .parquet existe, si no None."""
    if os.path.exists(ruta_parquet):
        return pd.read_parquet(ruta_parquet)
    return None


def inicio_incremental(maestro, patente: str, default_inicio: str,
                       cliente: str | None = None, overlap_dias: int = 7) -> str:
    """Fecha (YYYY-MM-DD) desde la que descargar para una patente (y cliente).

    Si el maestro ya tiene filas de esa patente —y, si se indica `cliente`, de ese
    cliente— parte unos días antes de su última transacción (solape para no perder
    cargas que entraron tarde). Si no, usa `default_inicio` (backfill completo).

    El filtro por cliente es clave: una misma patente puede existir bajo dos
    clientes distintos y cada uno lleva su propio avance incremental.
    """
    if maestro is None or COL_FECHA not in maestro.columns:
        return default_inicio
    sub = maestro[maestro[COL_PATENTE] == patente]
    if sub.empty:                       # patente inédita en el maestro → backfill
        return default_inicio
    if cliente is not None:
        # asegurar_cliente sobre un df ya NO vacío garantiza la columna Cliente
        # (sobre uno vacío retornaría sin ella y el filtro daría KeyError).
        sub = asegurar_cliente(sub)
        sub = sub[sub[COL_CLIENTE].astype(str) == str(cliente)]
        if sub.empty:                   # patente existe, pero no en este cliente
            return default_inicio
    ultima = pd.to_datetime(sub[COL_FECHA]).max()
    inicio = (ultima - pd.Timedelta(days=overlap_dias)).date()
    return inicio.isoformat()


def aplicar_correcciones(df):
    """Aplica CORRECCIONES_RUT_CHOFER: normaliza el RUT del chofer para una patente
    puntual. No toca ese RUT en otras patentes. Devuelve una copia."""
    if df is None or df.empty:
        return df
    if COL_PATENTE not in df.columns or COL_RUT_CHOFER not in df.columns:
        return df
    df = df.copy()
    rut = df[COL_RUT_CHOFER].astype(str)
    for patente, malo, bueno in CORRECCIONES_RUT_CHOFER:
        mask = (df[COL_PATENTE] == patente) & (rut == malo)
        df.loc[mask, COL_RUT_CHOFER] = bueno
    return df


def fusionar(maestro, nuevos) -> pd.DataFrame:
    """Une maestro + nuevos y deduplica por (Patente, N° de guía), quedándose con
    la última versión. Ordena por patente y fecha. Aplica correcciones de RUT."""
    marcos = [asegurar_cliente(df) for df in (maestro, nuevos)
              if df is not None and not df.empty]
    if not marcos:
        return pd.DataFrame()
    total = pd.concat(marcos, ignore_index=True)
    subset = [c for c in (COL_PATENTE, COL_GUIA) if c in total.columns]
    if subset:
        total = total.drop_duplicates(subset=subset, keep="last")
    orden = [c for c in (COL_PATENTE, COL_FECHA) if c in total.columns]
    if orden:
        total = total.sort_values(orden)
    total = aplicar_correcciones(total)
    return total.reset_index(drop=True)


def guardar(total: pd.DataFrame, ruta_parquet: str, ruta_xlsx: str = None) -> None:
    """Guarda el maestro en Parquet (fuente de verdad) y, si se indica, también
    una copia .xlsx para abrir en Excel."""
    carpeta = os.path.dirname(ruta_parquet)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    total.to_parquet(ruta_parquet, index=False)
    if ruta_xlsx:
        total.to_excel(ruta_xlsx, index=False)
