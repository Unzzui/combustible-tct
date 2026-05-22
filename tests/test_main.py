import main


def test_parse_args_lee_fechas():
    args = main.parse_args(["--desde", "2026-05-01", "--hasta", "2026-05-21"])
    assert args.desde == "2026-05-01"
    assert args.hasta == "2026-05-21"
    assert args.patentes == "patentes.txt"
