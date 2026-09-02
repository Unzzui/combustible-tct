import argparse
import json
import logging
import os
import random
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

# Circuit breaker: cuando el portal rechaza el login, escribimos un flag para saltear
# las próximas corridas. Evita amplificar el bloqueo con los reintentos automáticos del
# orquestador (schedule 06:00 y 18:00).
#
# El bloqueo es STICKY (sin expiración): una clave rechazada no se arregla sola con el
# paso del tiempo, y el TTL de 6h que había antes sólo servía para que la corrida
# siguiente volviera a quemar un intento contra una cuenta que ya estaba en problemas.
# Se levanta explícitamente con --reset-bloqueo (o TCT_RESET_BLOQUEO=1), después de
# corregir la credencial y confirmar que la cuenta está desbloqueada.
CB_FILENAME = ".circuit_breaker.json"
# Marcador que el orquestador (mini-server/core/flows.py) reconoce para mandar la
# alerta de Telegram de "login rechazado" en vez del genérico "FALLÓ". No cambiarlo
# sin actualizar _LOGIN_FAIL_RE allá.
MARCADOR_LOGIN = "LOGIN_BLOQUEADO"


def _cb_path(carpeta: str) -> str:
    return os.path.join(carpeta, CB_FILENAME)


def _cb_activo(path: str):
    """Devuelve el dict del circuit breaker si sigue vigente; None si no existe,
    está corrupto o ya expiró (en esos casos borra el archivo).

    `blocked_until: null` ⇒ bloqueo sticky: vigente hasta que alguien lo levante
    a mano con --reset-bloqueo."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        crudo = data.get("blocked_until")
        until = datetime.fromisoformat(crudo) if crudo else None
    except Exception:
        _cb_borrar(path)
        return None
    if until is None or datetime.now(timezone.utc) < until:
        return data
    _cb_borrar(path)
    return None


def _cb_disparar(path: str, motivo: str, ttl_hours: int | None = None) -> None:
    """Arma el circuit breaker. `ttl_hours=None` ⇒ sticky (sin expiración)."""
    until = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)) if ttl_hours else None
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "blocked_until": until.isoformat() if until else None,
            "reason": motivo[:300],
            "hit_at": datetime.now(timezone.utc).isoformat(),
            "ttl_hours": ttl_hours,
        }, fh, indent=2)


def _cb_borrar(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _abortar_por_login(cb_path: str, exc: Exception):
    """Primer rechazo de login = último intento. Arma el bloqueo sticky y aborta con
    código != 0 para que el orquestador alerte por Telegram en el acto.

    Reintentar es lo peor que se puede hacer acá: la clave no cambia sola entre
    intentos y el portal bloquea la cuenta por intentos fallidos acumulados."""
    _cb_disparar(cb_path, str(exc))
    log.error("Portal rechazó el login (%s). No se reintentará.", exc)
    raise SystemExit(
        f"{MARCADOR_LOGIN}: {exc} · circuit breaker armado, el servicio no volverá a "
        "intentar hasta que se corrija la credencial y se corra con --reset-bloqueo."
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Descarga incremental de consumos por patente (TCT) a Parquet.")
    p.add_argument("--desde", default="2025-01-01",
                   help="Inicio del histórico la primera vez / con --rehacer (def: 2025-01-01)")
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
    p.add_argument("--reset-bloqueo", action="store_true",
                   default=os.getenv("TCT_RESET_BLOQUEO", "").strip().lower()
                   in ("1", "true", "yes"),
                   help="Levanta el circuit breaker de login antes de correr. Usar SOLO "
                        "tras corregir la clave y confirmar que la cuenta está desbloqueada "
                        "(también con TCT_RESET_BLOQUEO=1)")
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


def _descargar_cliente(sesion, ticket, patentes, codigo, maestro, args):
    """Descarga todas las patentes de un cliente y devuelve (marcos, fallidas).

    Cada fila descargada se etiqueta con su `Cliente` (código) para que el maestro
    distinga a qué cliente pertenece cada consumo — clave porque una misma patente
    puede existir bajo dos clientes y llevar su propio avance incremental. Con
    `codigo=None` (modo --patentes/--pato, un solo cliente) no se etiqueta: el
    Cliente se deriva luego de la Tarjeta al fusionar.
    """
    marcos, fallidas = [], []
    for patente in patentes:
        desde_p = almacen.inicio_incremental(maestro, patente, args.desde, cliente=codigo)
        try:
            df = scraper.descargar_patente_df(sesion, ticket, patente, desde_p, args.hasta)
            if not df.empty and codigo is not None:
                df[almacen.COL_CLIENTE] = codigo
            marcos.append(df)
            log.info("OK %s cliente %s desde %s (%d filas)",
                     patente, codigo, desde_p, len(df))
        except Exception as e:  # tolerancia: anota y sigue
            fallidas.append(f"{patente}/{codigo}" if codigo is not None else patente)
            log.error("FALLO %s cliente %s: %s", patente, codigo, e)
        # Cortesía con el servidor + anti-bloqueo: sleep configurable con jitter
        # para no golpear a ritmo constante toda la flota.
        base = float(os.getenv("TCT_SLEEP_SEG", "3.0"))
        time.sleep(max(0.5, base + random.uniform(-0.5, 0.5)))
    return marcos, fallidas


def main(argv=None):
    args = parse_args(argv)

    carpeta_salida = PATO_CARPETA if args.pato else CARPETA_SALIDA
    parquet_path = PATO_PARQUET if args.pato else PARQUET
    xlsx_path = PATO_XLSX_OUT if args.pato else XLSX

    cb_path = _cb_path(carpeta_salida)

    if args.reset_bloqueo:
        if _cb_activo(cb_path):
            log.warning("--reset-bloqueo: levantando el circuit breaker de login.")
        _cb_borrar(cb_path)

    # Si el portal ya rechazó el login, cortar sin gastar otro intento. Se sale con
    # error (no con return 0): antes esto terminaba en "corrida OK" para el
    # orquestador, que cargaba el parquet viejo y no avisaba nada — el problema de
    # credenciales quedaba invisible mientras el schedule seguía quemando intentos.
    cb = _cb_activo(cb_path)
    if cb:
        hasta = cb.get("blocked_until") or "que se levante a mano (--reset-bloqueo)"
        raise SystemExit(
            f"{MARCADOR_LOGIN}: circuit breaker activo hasta {hasta}. "
            f"Motivo del bloqueo: {cb.get('reason', '?')}. "
            "Corregí la credencial, confirmá que la cuenta está desbloqueada y volvé "
            "a correr con --reset-bloqueo."
        )

    mapa_ot = (
        config.cargar_mapa_patente_ot(PATO_XLSX, col_ot=PATO_COL_OT, hoja=PATO_HOJA)
        if args.pato else None
    )

    maestro = None if args.rehacer else almacen.leer_maestro(parquet_path)
    filas_previas = 0 if maestro is None else len(maestro)
    log.info("Maestro previo: %d filas | hasta: %s", filas_previas, args.hasta)

    # Fuente de patentes + sesión. En el modo flota por defecto (sin --patentes ni
    # --pato) la cuenta puede acceder a VARIOS clientes (p. ej. OCA ENSAYOS 754405 y
    # OCA GLOBAL 799127); cada uno tiene su propia flota y ve SOLO sus consumos, así
    # que hay que recorrerlos todos: por cliente, login → su flota del portal →
    # descarga per-patente en su contexto. Con --patentes/--pato se usa el archivo y
    # un login simple (un solo cliente, sin recorrer la pasarela).
    usa_portal = not args.patentes and not args.pato
    nuevos_marcos, fallidas = [], []

    if usa_portal:
        try:
            clientes = login.listar_clientes()
        except login.PortalBlockedError as e:
            _abortar_por_login(cb_path, e)
        if not clientes:                    # cuenta de un solo cliente, sin ventana
            clientes = [(None, "default")]
        log.info("Clientes accesibles: %s",
                 ", ".join(f"{cod}({nom})" for cod, nom in clientes))

        for codigo, nombre in clientes:
            try:
                ticket, cookies, patentes, _ = login.obtener_sesion_con_flota(cliente=codigo)
            except login.PortalBlockedError as e:
                _abortar_por_login(cb_path, e)
            if not patentes:
                log.warning("Flota vacía para cliente %s; fallback a %s.",
                            codigo, args.flota)
                patentes = cargar_lista(args)
            log.info("Cliente %s (%s): %d patentes | login OK (ticket %d chars)",
                     codigo, nombre, len(patentes), len(ticket))
            marcos, fall = _descargar_cliente(
                scraper.nueva_sesion(cookies), ticket, patentes, codigo, maestro, args)
            nuevos_marcos += marcos
            fallidas += fall
    else:
        patentes = cargar_lista(args)
        try:
            ticket, cookies = login.obtener_sesion()
        except login.PortalBlockedError as e:
            _abortar_por_login(cb_path, e)
        if not patentes:
            raise SystemExit("No hay patentes para procesar.")
        log.info("Login OK (ticket %d chars) | Patentes: %d", len(ticket), len(patentes))
        marcos, fall = _descargar_cliente(
            scraper.nueva_sesion(cookies), ticket, patentes, None, maestro, args)
        nuevos_marcos += marcos
        fallidas += fall

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
