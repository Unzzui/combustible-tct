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
