import pathlib

import requests
import responses

from tct import config, scraper

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "detalle_sample.html"


def test_build_detail_body_deriva_anio_y_mes():
    body = scraper.build_detail_body(
        ticket="TICKET123",
        patente="PDRF-74",
        desde="2026-05-01",
        hasta="2026-05-21",
        glosa_prod="Diésel",
    )
    assert body["ticket"] == "TICKET123"
    assert body["patente"] == "PDRF-74"
    assert body["hf_fechaInicio"] == "2026-05-01"
    assert body["hf_fechaFin"] == "2026-05-21"
    assert body["anio"] == "2026"
    assert body["mes"] == "5"          # sin cero a la izquierda
    assert body["prod"] == "001"
    assert body["selectedvalue"] == "patente"
    assert body["tipoProducto"] == "TCT"
    assert body["patTar"] == "PDRF-74"


def test_harvest_form_fields_recoge_inputs_y_selects():
    html = FIXTURE.read_text(encoding="utf-8")
    campos = scraper.harvest_form_fields(html)
    assert campos["__VIEWSTATE"] == "ABC123"
    assert campos["__VIEWSTATEGENERATOR"] == "1BB77434"
    assert campos["ctl00$Cph1$hf_patente"] == "PDRF-74"
    # select toma la opción marcada selected
    assert campos["ctl00$Cph1$RcbxTipoDescargaPatente"] == "detalle"
    assert campos["ctl00$Cph1$TxtBusqueda"] == "buscar..."


def test_build_export_payload_setea_event_target():
    html = FIXTURE.read_text(encoding="utf-8")
    payload = scraper.build_export_payload(html)
    # conserva todos los campos del formulario
    assert payload["__VIEWSTATE"] == "ABC123"
    # y agrega/sobrescribe los del postback de exportación
    assert payload["__EVENTTARGET"] == "ctl00$Cph1$LinkBtnExportarXls"
    assert payload["__EVENTARGUMENT"] == ""


@responses.activate
def test_descargar_patente_hace_dos_posts_y_devuelve_bytes():
    # POST (a): página de detalle -> HTML con formulario
    responses.add(
        responses.POST, config.URL_DETALLE,
        body=FIXTURE.read_text(encoding="utf-8"),
        status=200, content_type="text/html",
    )
    # POST (b): exportación -> bytes del xlsx
    responses.add(
        responses.POST, config.URL_DETALLE,
        body=b"PK\x03\x04xlsx-bytes", status=200,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    sesion = requests.Session()
    contenido = scraper.descargar_patente(
        sesion, ticket="T1", patente="PDRF-74",
        desde="2026-05-01", hasta="2026-05-21",
    )

    assert contenido.startswith(b"PK")          # firma de un .xlsx
    assert len(responses.calls) == 2
    # el primer POST llevó el body de detalle
    assert "hf_fechaInicio=2026-05-01" in responses.calls[0].request.body
    # el segundo POST llevó el event target de exportación
    assert "LinkBtnExportarXls" in responses.calls[1].request.body


def test_guardar_xlsx_escribe_archivo(tmp_path):
    ruta = scraper.guardar_xlsx(b"PK\x03\x04datos", "PDRF-74", str(tmp_path))
    assert ruta.endswith("PDRF-74.xlsx")
    with open(ruta, "rb") as fh:
        assert fh.read() == b"PK\x03\x04datos"


def test_nueva_sesion_carga_cookies():
    s = scraper.nueva_sesion({"ASP.NET_SessionId": "abc", "AWSALB": "xyz"})
    nombres = {c.name for c in s.cookies}
    assert {"ASP.NET_SessionId", "AWSALB"} <= nombres
