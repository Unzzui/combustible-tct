import pandas as pd

from tct import config


def test_normalizar_patente_inserta_guion():
    assert config.normalizar_patente("PDRF74") == "PDRF-74"
    assert config.normalizar_patente(" srfg16 ") == "SRFG-16"
    assert config.normalizar_patente("PDRF-74") == "PDRF-74"  # ya tiene guión


def test_cargar_patentes_flota_normaliza_y_dedup(tmp_path):
    f = tmp_path / "Flota.xlsx"
    pd.DataFrame({
        "Patente": ["PDRF74", "SRFG16", "PDRF74", None],
        "Marca": ["PEUGEOT", "RAM", "PEUGEOT", "X"],
    }).to_excel(f, index=False)
    assert config.cargar_patentes_flota(str(f)) == ["PDRF-74", "SRFG-16"]


def test_cargar_patentes_flota_columna_inexistente(tmp_path):
    f = tmp_path / "mala.xlsx"
    pd.DataFrame({"Otra": ["x"]}).to_excel(f, index=False)
    try:
        config.cargar_patentes_flota(str(f))
        assert False, "debió lanzar ValueError"
    except ValueError as e:
        assert "Patente" in str(e)


def test_cargar_patentes_ignora_vacias_y_espacios(tmp_path):
    f = tmp_path / "patentes.txt"
    f.write_text("PDRF-74\n\n  ABCD-12  \n\n")
    assert config.cargar_patentes(str(f)) == ["PDRF-74", "ABCD-12"]


def test_constantes_urls():
    assert config.URL_DETALLE.endswith("AdmInfConsumosPorPatenteDetalle.aspx")
    assert config.EXPORT_TARGET == "ctl00$Cph1$LinkBtnExportarXls"
