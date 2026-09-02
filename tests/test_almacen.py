import pandas as pd

from tct import almacen


def _df(filas):
    return pd.DataFrame(filas)


def test_inicio_incremental_sin_maestro_usa_default():
    assert almacen.inicio_incremental(None, "PDRF-74", "2024-01-01") == "2024-01-01"


def test_inicio_incremental_patente_nueva_usa_default():
    maestro = _df({"Patente": ["OTRA-99"], "Fecha Transacción": [pd.Timestamp("2025-06-01")]})
    assert almacen.inicio_incremental(maestro, "PDRF-74", "2024-01-01") == "2024-01-01"


def test_inicio_incremental_resta_solape_a_ultima_fecha():
    maestro = _df({
        "Patente": ["PDRF-74", "PDRF-74"],
        "Fecha Transacción": [pd.Timestamp("2026-05-01"), pd.Timestamp("2026-05-15")],
    })
    # última = 2026-05-15, solape 7 días -> 2026-05-08
    assert almacen.inicio_incremental(maestro, "PDRF-74", "2024-01-01", overlap_dias=7) == "2026-05-08"


def test_fusionar_deduplica_por_guia_y_conserva_ultima():
    maestro = _df({
        "Patente": ["PDRF-74", "PDRF-74"],
        "Guía de Despacho": [111, 222],
        "Fecha Transacción": [pd.Timestamp("2026-05-01"), pd.Timestamp("2026-05-08")],
        "Monto": [1000, 2000],
    })
    nuevos = _df({
        "Patente": ["PDRF-74", "PDRF-74"],
        "Guía de Despacho": [222, 333],            # 222 repetida + 333 nueva
        "Fecha Transacción": [pd.Timestamp("2026-05-08"), pd.Timestamp("2026-05-15")],
        "Monto": [2000, 3000],
    })
    total = almacen.fusionar(maestro, nuevos)
    assert len(total) == 3                          # 111, 222, 333 (sin duplicar 222)
    assert set(total["Guía de Despacho"]) == {111, 222, 333}


def test_fusionar_sin_maestro_devuelve_solo_nuevos():
    nuevos = _df({"Patente": ["A-1"], "Guía de Despacho": [9], "Fecha Transacción": [pd.Timestamp("2025-01-01")]})
    total = almacen.fusionar(None, nuevos)
    assert len(total) == 1


def test_guardar_y_leer_parquet_roundtrip(tmp_path):
    ruta = tmp_path / "consolidado.parquet"
    df = _df({"Patente": ["A-1"], "Guía de Despacho": [9], "Monto": [500]})
    almacen.guardar(df, str(ruta))
    leido = almacen.leer_maestro(str(ruta))
    assert leido is not None
    assert len(leido) == 1
    assert leido.iloc[0]["Monto"] == 500


def test_leer_maestro_inexistente_devuelve_none(tmp_path):
    assert almacen.leer_maestro(str(tmp_path / "nada.parquet")) is None


# --- multi-cliente ------------------------------------------------------------

def test_cliente_de_tarjeta_extrae_codigo():
    assert almacen.cliente_de_tarjeta("1-799127-00477-3-3") == "799127"
    assert almacen.cliente_de_tarjeta("1-754405-01311-3-4") == "754405"
    assert almacen.cliente_de_tarjeta("") == ""
    assert almacen.cliente_de_tarjeta(None) == ""


def test_asegurar_cliente_backfill_desde_tarjeta():
    df = _df({
        "Patente": ["THJG-11", "THJG-11"],
        "Tarjeta": ["1-754405-01311-3-4", "1-799127-00477-3-3"],
    })
    out = almacen.asegurar_cliente(df)
    assert list(out["Cliente"]) == ["754405", "799127"]


def test_asegurar_cliente_respeta_valores_existentes():
    df = _df({
        "Patente": ["THJG-11", "THJG-11"],
        "Tarjeta": ["1-754405-01311-3-4", "1-799127-00477-3-3"],
        "Cliente": ["754405", None],           # una ya etiquetada, otra por rellenar
    })
    out = almacen.asegurar_cliente(df)
    assert list(out["Cliente"]) == ["754405", "799127"]


def test_inicio_incremental_filtra_por_cliente():
    # misma patente en dos clientes con fechas distintas
    maestro = _df({
        "Patente": ["THJG-11", "THJG-11"],
        "Cliente": ["754405", "799127"],
        "Fecha Transacción": [pd.Timestamp("2026-08-20"), pd.Timestamp("2026-08-31")],
    })
    # cliente 754405 -> parte de 2026-08-20 menos 7 = 2026-08-13
    assert almacen.inicio_incremental(maestro, "THJG-11", "2024-01-01",
                                      cliente="754405", overlap_dias=7) == "2026-08-13"
    # cliente 799127 -> parte de 2026-08-31 menos 7 = 2026-08-24
    assert almacen.inicio_incremental(maestro, "THJG-11", "2024-01-01",
                                      cliente="799127", overlap_dias=7) == "2026-08-24"


def test_inicio_incremental_cliente_nuevo_usa_default():
    # maestro solo tiene 754405 -> para 799127 no hay filas -> default (backfill)
    maestro = _df({
        "Patente": ["THJG-11"],
        "Cliente": ["754405"],
        "Fecha Transacción": [pd.Timestamp("2026-08-20")],
    })
    assert almacen.inicio_incremental(maestro, "THJG-11", "2024-01-01",
                                      cliente="799127") == "2024-01-01"


def test_inicio_incremental_deriva_cliente_desde_tarjeta_si_falta_columna():
    # maestro viejo sin columna Cliente pero con Tarjeta -> se deriva
    maestro = _df({
        "Patente": ["THJG-11"],
        "Tarjeta": ["1-754405-01311-3-4"],
        "Fecha Transacción": [pd.Timestamp("2026-08-20")],
    })
    assert almacen.inicio_incremental(maestro, "THJG-11", "2024-01-01",
                                      cliente="754405", overlap_dias=7) == "2026-08-13"
    assert almacen.inicio_incremental(maestro, "THJG-11", "2024-01-01",
                                      cliente="799127") == "2024-01-01"


def test_fusionar_conserva_columna_cliente_y_ambos_clientes():
    maestro = _df({
        "Patente": ["THJG-11"],
        "Guía de Despacho": [100],
        "Tarjeta": ["1-754405-01311-3-4"],          # sin columna Cliente (maestro viejo)
        "Fecha Transacción": [pd.Timestamp("2026-08-20")],
    })
    nuevos = _df({
        "Patente": ["THJG-11"],
        "Guía de Despacho": [662435710],
        "Cliente": ["799127"],
        "Tarjeta": ["1-799127-00477-3-3"],
        "Fecha Transacción": [pd.Timestamp("2026-08-31")],
    })
    total = almacen.fusionar(maestro, nuevos)
    assert len(total) == 2
    assert set(total["Cliente"]) == {"754405", "799127"}
