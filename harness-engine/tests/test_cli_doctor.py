import json

from harness_engine import doctor


def test_doctor_cli_emits_json_without_database(capsys):
    exit_code = doctor.main(["--json"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["checks"]
