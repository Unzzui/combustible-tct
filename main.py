import argparse
import logging
import os
import time
from datetime import date

from tct import config, login, scraper, almacen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tct")

CARPETA_SALIDA = "data"
FLOTA_POR_DEFECTO = "data/Flota.xlsx"
PARQUET = "data/consolidado.parquet"
XLSX = "data/consolidado.xlsx"


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Descarga incremental de consumos por patente (TCT) a Parquet.")
    p.add_argument("--desde", default="2024-01-01",
                   help="Inicio del histórico la primera vez / con --rehacer (def: 2024-01-01)")
    p.add_argument("--hasta", default=date.today().isoformat(),
                   help="Fecha fin YYYY-MM-DD (def: hoy)")
    p.add_argument("--flota", default=FLOTA_POR_DEFECTO,
                   help="Excel de flota con columna 'Patente' (def: data/Flota.xlsx)")
    p.add_argument("--patentes", default=None,
                   help="Alternativa: archivo .txt con una patente por línea")
    p.add_argument("--rehacer", action="store_true",
                   help="Ignora el maestro y reconstruye todo desde --desde")
    return p.parse_args(argv)


def cargar_lista(args):
    if args.patentes:
        return config.cargar_patentes(args.patentes)
    if not os.path.exists(args.flota):
        raise SystemExit(f"No existe {args.flota}. Usá --flota o --patentes.")
    return config.cargar_patentes_flota(args.flota)


def main(argv=None):
    args = parse_args(argv)
    patentes = cargar_lista(args)
    if not patentes:
        raise SystemExit("No hay patentes para procesar.")

    maestro = None if args.rehacer else almacen.leer_maestro(PARQUET)
    filas_previas = 0 if maestro is None else len(maestro)
    log.info("Patentes: %d | maestro previo: %d filas | hasta: %s",
             len(patentes), filas_previas, args.hasta)

    ticket, cookies = login.obtener_sesion()
    log.info("Login OK (ticket %d chars)", len(ticket))
    sesion = scraper.nueva_sesion(cookies)

    nuevos_marcos, fallidas = [], []
    for patente in patentes:
        desde_p = almacen.inicio_incremental(maestro, patente, args.desde)
        try:
            df = scraper.descargar_patente_df(sesion, ticket, patente, desde_p, args.hasta)
            nuevos_marcos.append(df)
            log.info("OK %s desde %s (%d filas)", patente, desde_p, len(df))
        except Exception as e:  # tolerancia: anota y sigue
            fallidas.append(patente)
            log.error("FALLO %s: %s", patente, e)
        time.sleep(1.5)  # cortesía con el servidor

    import pandas as pd
    nuevos = pd.concat(nuevos_marcos, ignore_index=True) if nuevos_marcos else pd.DataFrame()
    total = almacen.fusionar(maestro, nuevos)
    if total.empty:
        raise SystemExit("No hay datos para guardar.")

    os.makedirs(CARPETA_SALIDA, exist_ok=True)
    almacen.guardar(total, PARQUET, XLSX)
    agregadas = len(total) - filas_previas
    log.info("Maestro: %s (%d filas, +%d nuevas, %d fallidas)",
             PARQUET, len(total), agregadas, len(fallidas))
    log.info("Copia Excel: %s", XLSX)
    if fallidas:
        log.warning("Patentes con error: %s", ", ".join(fallidas))


if __name__ == "__main__":
    main()
