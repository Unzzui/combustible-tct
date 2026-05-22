from tct import config


def test_cargar_patentes_ignora_vacias_y_espacios(tmp_path):
    f = tmp_path / "patentes.txt"
    f.write_text("PDRF-74\n\n  ABCD-12  \n\n")
    assert config.cargar_patentes(str(f)) == ["PDRF-74", "ABCD-12"]


def test_constantes_urls():
    assert config.URL_DETALLE.endswith("AdmInfConsumosPorPatenteDetalle.aspx")
    assert config.EXPORT_TARGET == "ctl00$Cph1$LinkBtnExportarXls"
