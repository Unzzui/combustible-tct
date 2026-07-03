import argparse
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

from tct import config, login, scraper, almacen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tct")

CARPETA_SALIDA = "data"
FLOTA_POR_DEFECTO = "data/Flota.xlsx"
PARQUET = "data/consolidado.parquet"
XLSX = "data/consolidado.xlsx"

PATO_XLSX = "data/camionetas_division/camionetas_proyectos_pato.xlsx"
PATO_CARPETA = "data/camionetas_division"
PATO_PARQUET = "data/camionetas_division/consolidado_pato.parquet"
PATO_XLSX_OUT = "data/camionetas_division/consolidado_pato.xlsx"
PATO_HOJA = "BD"
PATO_COL_OT = "OT"

# Circuit breaker: cuando el portal bloquea, escribimos un flag con TTL para
# saltear las próximas corridas hasta que expire. Evita amplificar el bloqueo
# con reintentos automáticos del orquestador (schedule 06:00 y 18:00).
CB_FILENAME = ".circuit_breaker.json"
CB_TTL_HOURS = int(os.getenv("TCT_BLOCK_TTL_HOURS", "6"))


def _cb_path(carpeta: str) -> str:
    return os.path.join(carpeta, CB_FILENAME)


def _cb_activo(path: str):
    """Devuelve el dict del circuit breaker si aún está vigente; None si no
    existe, está corrupto o ya expiró (en esos casos borra el archivo)."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        until = datetime.fromisoformat(data["blocked_until"])
    except Exception:
        _cb_borrar(path)
        return None
    if datetime.now(timezone.utc) < until:
        return data
    _cb_borrar(path)
    return None


def _cb_disparar(path: str, motivo: str) -> None:
    until = datetime.now(timezone.utc) + timedelta(hours=CB_TTL_HOURS)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "blocked_until": until.isoformat(),
            "reason": motivo[:300],
            "hit_at": datetime.now(timezone.utc).isoformat(),
            "ttl_hours": CB_TTL_HOURS,
        }, fh, indent=2)


def _cb_borrar(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


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
    p.add_argument("--pato", action="store_true",
                   help="Usa el Excel de camionetas de proyectos PATO y guarda en data/camionetas_division/")
    p.add_argument("--rehacer", action="store_true",
                   help="Ignora el maestro y reconstruye todo desde --desde")
    return p.parse_args(argv)


def cargar_lista(args):
    if args.pato:
        if not os.path.exists(PATO_XLSX):
            raise SystemExit(f"No existe {PATO_XLSX}.")
        return config.cargar_patentes_flota(PATO_XLSX, hoja=PATO_HOJA)
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

    carpeta_salida = PATO_CARPETA if args.pato else CARPETA_SALIDA
    parquet_path = PATO_PARQUET if args.pato else PARQUET
    xlsx_path = PATO_XLSX_OUT if args.pato else XLSX

    # Si el portal bloqueó recientemente, saltar sin gastar más intentos.
    # Return 0 → el orquestador lo marca como corrida OK y no reintenta.
    cb_path = _cb_path(carpeta_salida)
    cb = _cb_activo(cb_path)
    if cb:
        log.warning(
            "Circuit breaker activo hasta %s (razón: %s). Skipping corrida.",
            cb["blocked_until"], cb.get("reason", "?"),
        )
        return

    mapa_ot = (
        config.cargar_mapa_patente_ot(PATO_XLSX, col_ot=PATO_COL_OT, hoja=PATO_HOJA)
        if args.pato else None
    )

    maestro = None if args.rehacer else almacen.leer_maestro(parquet_path)
    filas_previas = 0 if maestro is None else len(maestro)
    log.info("Patentes: %d | maestro previo: %d filas | hasta: %s",
             len(patentes), filas_previas, args.hasta)

    try:
        ticket, cookies = login.obtener_sesion()
    except login.PortalBlockedError as e:
        _cb_disparar(cb_path, str(e))
        log.error("Portal bloqueó el login (%s). Circuit breaker activado por %dh.",
                  e, CB_TTL_HOURS)
        return
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

    if mapa_ot and almacen.COL_PATENTE in total.columns:
        total["OT"] = total[almacen.COL_PATENTE].map(
            lambda v: mapa_ot.get(config.normalizar_patente(v), "")
        )
        cols = list(total.columns)
        cols.insert(0, cols.pop(cols.index("OT")))
        total = total[cols]

    os.makedirs(carpeta_salida, exist_ok=True)
    almacen.guardar(total, parquet_path, xlsx_path)
    agregadas = len(total) - filas_previas
    log.info("Maestro: %s (%d filas, +%d nuevas, %d fallidas)",
             parquet_path, len(total), agregadas, len(fallidas))
    log.info("Copia Excel: %s", xlsx_path)
    if fallidas:
        log.warning("Patentes con error: %s", ", ".join(fallidas))


if __name__ == "__main__":
    main()
