from harness_engine import cli


def test_cli_tools_list_requires_explicit_allowlist(capsys):
    assert cli.main(["tools", "list"]) == 0
    assert capsys.readouterr().out == ""

    assert cli.main(["tools", "list", "--enable", "echo"]) == 0
    output = capsys.readouterr().out
    assert "echo" in output
    assert "Return the supplied text" in output
