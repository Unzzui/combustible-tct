import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

import main


def test_parse_args_lee_fechas():
    args = main.parse_args(["--desde", "2026-05-01", "--hasta", "2026-05-21"])
    assert args.desde == "2026-05-01"
    assert args.hasta == "2026-05-21"


def test_parse_args_defaults_rango_y_flota():
    args = main.parse_args([])
    assert args.desde == "2025-01-01"
    assert args.hasta == date.today().isoformat()
    assert args.flota == "data/Flota.xlsx"
    assert args.patentes is None
    assert args.rehacer is False


def test_parse_args_rehacer():
    assert main.parse_args(["--rehacer"]).rehacer is True


def test_parse_args_reset_bloqueo_por_env(monkeypatch):
    monkeypatch.setenv("TCT_RESET_BLOQUEO", "1")
    assert main.parse_args([]).reset_bloqueo is True
    monkeypatch.setenv("TCT_RESET_BLOQUEO", "")
    assert main.parse_args([]).reset_bloqueo is False


def test_cb_sticky_no_expira(tmp_path):
    """Un bloqueo por credenciales no caduca solo: sigue activo por más que pase
    el tiempo. Antes tenía TTL de 6h y la corrida siguiente quemaba otro intento."""
    path = tmp_path / ".cb.json"
    main._cb_disparar(str(path), "clave rechazada")
    assert json.loads(path.read_text())["blocked_until"] is None
    assert main._cb_activo(str(path)) is not None


def test_cb_con_ttl_expira(tmp_path):
    path = str(tmp_path / ".cb.json")
    main._cb_disparar(path, "portal caído", ttl_hours=6)
    assert main._cb_activo(path) is not None

    vencido = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with open(path, "w") as fh:
        json.dump({"blocked_until": vencido, "reason": "x"}, fh)
    assert main._cb_activo(path) is None
    assert not os.path.exists(path)   # el flag vencido se limpia solo


def test_abortar_por_login_sale_con_error_y_arma_bloqueo(tmp_path):
    """El rechazo de login debe terminar en exit != 0 con el marcador que el
    orquestador busca para alertar por Telegram — no en un return silencioso."""
    path = str(tmp_path / ".cb.json")
    with pytest.raises(SystemExit) as ex:
        main._abortar_por_login(path, RuntimeError("Portal rechazó el login"))
    assert main.MARCADOR_LOGIN in str(ex.value)
    assert main._cb_activo(path) is not None
