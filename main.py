import argparse
import logging
import os
import time
from datetime import date

from tct import config, login, scraper, consolidar

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tct")

CARPETA_DESCARGAS = "descargas"
CARPETA_SALIDA = "data"
FLOTA_POR_DEFECTO = "data/Flota.xlsx"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Descarga consolidada de consumos por patente (TCT).")
    p.add_argument("--desde", default="2024-01-01", help="Fecha inicio YYYY-MM-DD (def: 2024-01-01)")
    p.add_argument("--hasta", default=date.today().isoformat(), help="Fecha fin YYYY-MM-DD (def: hoy)")
    p.add_argument("--flota", default=FLOTA_POR_DEFECTO,
                   help="Excel de flota con columna 'Patente' (def: data/Flota.xlsx)")
    p.add_argument("--patentes", default=None,
                   help="Alternativa: archivo .txt con una patente por línea (ignora --flota)")
    return p.parse_args(argv)


def cargar_lista(args):
    """Decide la fuente de patentes: --patentes (txt) tiene prioridad; si no,
    se usa el Excel de flota."""
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
    log.info("Patentes a procesar: %d (%s a %s)", len(patentes), args.desde, args.hasta)

    ticket, cookies = login.obtener_sesion()
    log.info("Login OK (ticket %d chars)", len(ticket))
    sesion = scraper.nueva_sesion(cookies)

    descargados, fallidas = [], []
    for patente in patentes:
        try:
            contenido = scraper.descargar_patente(
                sesion, ticket, patente, args.desde, args.hasta)
            ruta = scraper.guardar_xlsx(contenido, patente, CARPETA_DESCARGAS)
            descargados.append(ruta)
            log.info("OK %s -> %s", patente, ruta)
        except Exception as e:  # tolerancia: anota y sigue
            fallidas.append(patente)
            log.error("FALLO %s: %s", patente, e)
        time.sleep(1.5)  # cortesía con el servidor

    if not descargados:
        raise SystemExit("No se descargó ninguna patente.")

    os.makedirs(CARPETA_SALIDA, exist_ok=True)
    salida = os.path.join(CARPETA_SALIDA, f"consolidado_{args.desde}_{args.hasta}.xlsx")
    total = consolidar.consolidar(descargados, salida)
    log.info("Consolidado: %s (%d filas, %d patentes OK, %d fallidas)",
             salida, total, len(descargados), len(fallidas))
    if fallidas:
        log.warning("Patentes sin datos o con error: %s", ", ".join(fallidas))


if __name__ == "__main__":
    main()
