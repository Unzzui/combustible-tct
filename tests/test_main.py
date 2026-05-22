from datetime import date

import main


def test_parse_args_lee_fechas():
    args = main.parse_args(["--desde", "2026-05-01", "--hasta", "2026-05-21"])
    assert args.desde == "2026-05-01"
    assert args.hasta == "2026-05-21"


def test_parse_args_defaults_rango_y_flota():
    args = main.parse_args([])
    assert args.desde == "2024-01-01"
    assert args.hasta == date.today().isoformat()
    assert args.flota == "data/Flota.xlsx"
    assert args.patentes is None
    assert args.rehacer is False


def test_parse_args_rehacer():
    assert main.parse_args(["--rehacer"]).rehacer is True
