"""Almacén incremental del histórico de consumos en Parquet.

El maestro (un .parquet) acumula todas las transacciones entre corridas. En cada
corrida solo se descarga lo nuevo (desde la última fecha por patente, con días de
solape) y se fusiona deduplicando por N° de guía.
"""
import os

import pandas as pd

COL_FECHA = "Fecha Transacción"
COL_GUIA = "Guía de Despacho"
COL_PATENTE = "Patente"


def leer_maestro(ruta_parquet: str):
    """Devuelve el DataFrame maestro si el .parquet existe, si no None."""
    if os.path.exists(ruta_parquet):
        return pd.read_parquet(ruta_parquet)
    return None


def inicio_incremental(maestro, patente: str, default_inicio: str,
                       overlap_dias: int = 7) -> str:
    """Fecha (YYYY-MM-DD) desde la que descargar para una patente.

    Si el maestro ya tiene filas de esa patente, parte unos días antes de su
    última transacción (solape para no perder cargas que entraron tarde). Si no,
    usa `default_inicio`.
    """
    if maestro is None or COL_FECHA not in maestro.columns:
        return default_inicio
    sub = maestro[maestro[COL_PATENTE] == patente]
    if sub.empty:
        return default_inicio
    ultima = pd.to_datetime(sub[COL_FECHA]).max()
    inicio = (ultima - pd.Timedelta(days=overlap_dias)).date()
    return inicio.isoformat()


def fusionar(maestro, nuevos) -> pd.DataFrame:
    """Une maestro + nuevos y deduplica por (Patente, N° de guía), quedándose con
    la última versión. Ordena por patente y fecha."""
    marcos = [df for df in (maestro, nuevos) if df is not None and not df.empty]
    if not marcos:
        return pd.DataFrame()
    total = pd.concat(marcos, ignore_index=True)
    subset = [c for c in (COL_PATENTE, COL_GUIA) if c in total.columns]
    if subset:
        total = total.drop_duplicates(subset=subset, keep="last")
    orden = [c for c in (COL_PATENTE, COL_FECHA) if c in total.columns]
    if orden:
        total = total.sort_values(orden)
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
