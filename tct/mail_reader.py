"""Lee el código OTP de COPEC TCT desde Gmail/Google Workspace (IMAP).

Desde ~oct-2026 el portal TCT intercala un 2FA tras usuario+clave: muestra un
campo de 6 dígitos y envía un correo:

    De:      COPEC TCT <info@copec.cl>
    Asunto:  COPEC TCT: Código de verificación
    Cuerpo:  "...Tu código de verificación para TCT es: 799760 El código vence
             en 15 minutos y es de un solo uso..."

El código llega al buzón `MAIL_USER` (una cuenta @ocaglobal.com servida por
Google Workspace → imap.gmail.com con app-password). El MISMO remitente
`info@copec.cl` manda también los correos de REPORTE, así que NO basta filtrar
por remitente: hay que mirar el asunto decodificado y quedarse con los de
"verificación".

Estrategia (igual que oca-wom) para no leer un código viejo ni el de otro
login paralelo:
  1. Antes de disparar el login se toma una "línea base" de los UID de correos
     OTP ya existentes.
  2. Tras el login se hace polling hasta que aparece un UID nuevo y se extrae
     el código de 6 dígitos.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import re
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header

IMAP_HOST = "imap.gmail.com"
OTP_FROM = "info@copec.cl"
# Substring (minúsculas, sin acentos relevantes) que debe tener el asunto
# decodificado del correo OTP para distinguirlo de los correos de REPORTE del
# mismo remitente.
_SUBJECT_MARCA = "verificaci"

# El código: 6 dígitos. Primario anclado al texto del correo
# ("...verificación para TCT es: 799760 El código vence..."); luego variantes
# más laxas y, como último recurso, cualquier bloque de 6 dígitos.
_RE_ANCLADO = re.compile(r"verificaci[oó]n\s+para\s+TCT\s+es[:\s]*([0-9]{6})", re.I)
_RE_ES = re.compile(r"\bes[:\s]*([0-9]{6})\b", re.I)
_RE_ANY6 = re.compile(r"\b([0-9]{6})\b")


class MailReader:
    def __init__(self, user: str, password: str, host: str = IMAP_HOST):
        self.user = user
        self.password = password
        self.host = host

    def _conn(self) -> imaplib.IMAP4_SSL:
        M = imaplib.IMAP4_SSL(self.host)
        M.login(self.user, self.password)
        M.select("INBOX", readonly=True)
        return M

    @staticmethod
    def _asunto(M: imaplib.IMAP4_SSL, uid: bytes) -> str:
        typ, data = M.uid(
            "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])"
        )
        if not data or not data[0]:
            return ""
        raw = data[0][1].decode("utf-8", "ignore")
        # "Subject: ....." → quitar la etiqueta y decodificar MIME.
        valor = raw.split(":", 1)[1].strip() if ":" in raw else raw.strip()
        try:
            return str(make_header(decode_header(valor)))
        except Exception:
            return valor

    def _otp_uids(self, M: imaplib.IMAP4_SSL) -> list[bytes]:
        """UID de los correos OTP (remitente copec + asunto de verificación).

        El SEARCH de Gmail por SUBJECT es por palabra completa y el asunto va
        MIME-encoded, así que no es fiable para "verificación" (con acento).
        Se busca por remitente y se filtra el asunto decodificado en Python.
        """
        typ, data = M.uid("SEARCH", None, f'(FROM "{OTP_FROM}")')
        candidatos = data[0].split() if data and data[0] else []
        out = []
        for uid in candidatos:
            if _SUBJECT_MARCA in self._asunto(M, uid).lower():
                out.append(uid)
        return out

    def _fecha(self, M: imaplib.IMAP4_SSL, uid: bytes):
        """Fecha (datetime UTC) del correo `uid`, o None si no se puede parsear."""
        typ, data = M.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (DATE)])")
        if not data or not data[0]:
            return None
        raw = data[0][1].decode("utf-8", "ignore")
        val = raw.split(":", 1)[1].strip() if ":" in raw else raw.strip()
        try:
            dt = email.utils.parsedate_to_datetime(val)
        except Exception:
            return None
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def baseline_uids(self) -> set[bytes]:
        """UID de los correos OTP ya existentes (para ignorarlos luego).

        Reintenta ante hipos de IMAP: un baseline vacío por error haría que
        wait_for_new_otp tratara un código VIEJO como nuevo y lo enviara."""
        ultimo = None
        for _ in range(3):
            try:
                M = self._conn()
                try:
                    return set(self._otp_uids(M))
                finally:
                    M.logout()
            except Exception as e:  # noqa: BLE001
                ultimo = e
                time.sleep(2)
        raise ultimo if ultimo else RuntimeError("baseline_uids falló")

    def wait_for_new_otp(
        self,
        baseline: set[bytes],
        timeout: int = 120,
        poll_interval: int = 5,
        desde: "datetime | None" = None,
    ) -> str:
        """Espera un correo OTP nuevo y devuelve el código. Lanza TimeoutError si
        no llega a tiempo.

        "Nuevo" = UID no presente en `baseline` Y, si se pasa `desde`, con fecha
        posterior a `desde` (menos un margen por desfase de reloj). El filtro por
        fecha es la salvaguarda clave: aunque la baseline falle/venga vacía, NUNCA
        se envía un código viejo de una corrida anterior (que el portal rechaza y
        gatilla el circuit breaker)."""
        margen = timedelta(minutes=3)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            M = self._conn()
            try:
                uids = self._otp_uids(M)
                nuevos = [u for u in uids if u not in baseline]
                # del más reciente al más viejo
                for uid in sorted(nuevos, key=lambda b: int(b), reverse=True):
                    if desde is not None:
                        fecha = self._fecha(M, uid)
                        if fecha is not None and fecha < desde - margen:
                            continue   # correo previo al login: no es de esta sesión
                    code = self._extract_code(M, uid)
                    if code:
                        return code
            finally:
                M.logout()
            time.sleep(poll_interval)
        raise TimeoutError("No llegó el código OTP de Copec a tiempo")

    def latest_otp(self) -> str:
        """Devuelve el código del correo OTP más reciente (sin esperar)."""
        M = self._conn()
        try:
            uids = self._otp_uids(M)
            if not uids:
                raise LookupError("No hay correos OTP de Copec en la bandeja")
            uid = sorted(uids, key=lambda b: int(b))[-1]
            code = self._extract_code(M, uid)
            if not code:
                raise LookupError("No se pudo extraer el código del correo")
            return code
        finally:
            M.logout()

    # ---- extracción -------------------------------------------------------

    def _extract_code(self, M: imaplib.IMAP4_SSL, uid: bytes) -> str | None:
        typ, data = M.uid("FETCH", uid, "(RFC822)")
        if not data or not data[0]:
            return None
        msg = email.message_from_bytes(data[0][1])
        text = self._plain_text(msg)
        return self._code_from_text(text)

    @staticmethod
    def _plain_text(msg: email.message.Message) -> str:
        chunks = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        chunks.append(
                            payload.decode(
                                part.get_content_charset() or "utf-8", "ignore"
                            )
                        )
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                chunks.append(
                    payload.decode(msg.get_content_charset() or "utf-8", "ignore")
                )
        body = "\n".join(chunks)
        body = re.sub(r"<[^>]+>", " ", body)          # quitar tags HTML
        body = re.sub(r"&[a-zA-Z#0-9]+;", " ", body)  # entidades
        return re.sub(r"\s+", " ", body)

    @staticmethod
    def _code_from_text(text: str) -> str | None:
        for rx in (_RE_ANCLADO, _RE_ES, _RE_ANY6):
            m = rx.search(text)
            if m:
                return m.group(1)
        return None
