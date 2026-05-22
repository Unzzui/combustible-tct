import pandas as pd

from tct import consolidar


def _crear_xlsx(ruta, filas):
    pd.DataFrame(filas).to_excel(ruta, index=False)


def test_consolidar_agrega_columna_patente_y_concatena(tmp_path):
    a = tmp_path / "PDRF-74.xlsx"
    b = tmp_path / "ABCD-12.xlsx"
    _crear_xlsx(a, [{"Fecha": "2026-05-01", "Monto": 1000}])
    _crear_xlsx(b, [{"Fecha": "2026-05-02", "Monto": 2000},
                    {"Fecha": "2026-05-03", "Monto": 3000}])

    salida = tmp_path / "consolidado.xlsx"
    n = consolidar.consolidar([str(a), str(b)], str(salida))

    assert n == 3
    df = pd.read_excel(salida)
    assert set(df["Patente"]) == {"PDRF-74", "ABCD-12"}
    assert len(df) == 3
