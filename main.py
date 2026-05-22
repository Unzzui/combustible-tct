import argparse
import logging
import time

from tct import config, login, scraper, consolidar

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tct")

CARPETA_DESCARGAS = "descargas"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Descarga consolidada de consumos por patente (TCT).")
    p.add_argument("--desde", required=True, help="Fecha inicio YYYY-MM-DD")
    p.add_argument("--hasta", required=True, help="Fecha fin YYYY-MM-DD")
    p.add_argument("--patentes", default="patentes.txt", help="Archivo de patentes")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    patentes = config.cargar_patentes(args.patentes)
    if not patentes:
        raise SystemExit("patentes.txt está vacío.")
    log.info("Patentes a procesar: %d", len(patentes))

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

    salida = f"consolidado_{args.desde}_{args.hasta}.xlsx"
    total = consolidar.consolidar(descargados, salida)
    log.info("Consolidado: %s (%d filas, %d patentes, %d fallidas)",
             salida, total, len(descargados), len(fallidas))
    if fallidas:
        log.warning("Patentes fallidas: %s", ", ".join(fallidas))


if __name__ == "__main__":
    main()
