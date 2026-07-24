from tct import flota_portal


def test_parsear_patentes_filtra_y_normaliza():
    crudo = ["Patente", "LRBJ-98", "lwyj50", "LRBJ-98", "Total", "", "VYJH-62"]
    assert flota_portal.parsear_patentes(crudo) == ["LRBJ-98", "LWYJ-50", "VYJH-62"]
