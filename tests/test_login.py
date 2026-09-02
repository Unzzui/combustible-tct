from tct import login


def test_parse_clientes_extrae_codigo_nombre_y_id():
    rows = [
        {"id": "RadWindowClientes_C_RgdClientes_ctl00_ctl04_LinkBtnIngresar",
         "texto": "754405 OCA ENSAYOS INSPECCIONES Y Ingresar"},
        {"id": "RadWindowClientes_C_RgdClientes_ctl00_ctl06_LinkBtnIngresar",
         "texto": "799127 OCA GLOBAL SERVICIOS TECNICOS CHILE S.A Ingresar"},
    ]
    clientes = login.parse_clientes(rows)
    assert clientes == [
        ("754405", "OCA ENSAYOS INSPECCIONES Y",
         "RadWindowClientes_C_RgdClientes_ctl00_ctl04_LinkBtnIngresar"),
        ("799127", "OCA GLOBAL SERVICIOS TECNICOS CHILE S.A",
         "RadWindowClientes_C_RgdClientes_ctl00_ctl06_LinkBtnIngresar"),
    ]


def test_parse_clientes_ignora_filas_sin_codigo():
    rows = [
        {"id": "x", "texto": "Cliente Nombre Ingresar"},   # sin código numérico
        {"id": "y", "texto": "  "},
        {"id": "z", "texto": "800001 EMPRESA TEST"},        # sin 'Ingresar' al final
    ]
    assert login.parse_clientes(rows) == [("800001", "EMPRESA TEST", "z")]


def test_id_anchor_de_cliente_encuentra_por_codigo():
    clientes = [
        ("754405", "OCA ENSAYOS", "id-754405"),
        ("799127", "OCA GLOBAL", "id-799127"),
    ]
    assert login.id_anchor_de_cliente(clientes, "799127") == "id-799127"
    assert login.id_anchor_de_cliente(clientes, "754405") == "id-754405"
    assert login.id_anchor_de_cliente(clientes, "000000") is None
