import os

import pandas as pd


def consolidar(rutas_xlsx: list[str], ruta_salida: str) -> int:
    """Lee cada .xlsx, agrega columna 'Patente' (del nombre de archivo) y
    concatena todo en `ruta_salida`. Devuelve el total de filas escritas."""
    marcos = []
    for ruta in rutas_xlsx:
        df = pd.read_excel(ruta)
        patente = os.path.splitext(os.path.basename(ruta))[0]
        df.insert(0, "Patente", patente)
        marcos.append(df)
    if not marcos:
        raise ValueError("No hay archivos para consolidar.")
    total = pd.concat(marcos, ignore_index=True)
    total.to_excel(ruta_salida, index=False)
    return len(total)
