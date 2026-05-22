import os
from dotenv import load_dotenv

load_dotenv()

BASE = "https://tctcliente.copec.cl"
URL_LOGIN = BASE + "/LoginDesk.aspx"
URL_AGRUPADO = BASE + "/VistasAdminInformes/AdmInfConsumosAgrupado.aspx"
URL_DETALLE = BASE + "/VistasAdminInformes/AdmInfConsumosPorPatenteDetalle.aspx"
EXPORT_TARGET = "ctl00$Cph1$LinkBtnExportarXls"

USER_TCT = os.getenv("USER_TCT", "")
PASS_TCT = os.getenv("PASS_TCT", "")


def cargar_patentes(ruta: str) -> list[str]:
    """Lee patentes (una por línea), ignora líneas vacías y recorta espacios."""
    with open(ruta, encoding="utf-8") as fh:
        return [linea.strip() for linea in fh if linea.strip()]
