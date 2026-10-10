from wanbot import __main__ as entrypoint
from wanbot.config import ConfigError, parse_config


def test_check_config_succeeds_without_disclosing_token(monkeypatch, capsys):
    token = "private-test-token"
    config = parse_config({
        "DISCORD_TOKEN": token,
        "TEST_GUILD_ID": "200",
    })
    monkeypatch.setattr(entrypoint, "load_config", lambda path: config)

    result = entrypoint.main(["--check-config"])
    captured = capsys.readouterr()

    assert result == 0
    assert "成功" in captured.out
    assert "test: 有効な機能 = なし" in captured.out
    assert token not in captured.out
    assert token not in captured.err


def test_invalid_config_returns_error(monkeypatch, capsys):
    def fail(path):
        raise ConfigError("DISCORD_TOKEN: 設定が必要です")

    monkeypatch.setattr(entrypoint, "load_config", fail)

    assert entrypoint.main(["--check-config"]) == 1
    assert "設定エラー" in capsys.readouterr().err


def test_unreadable_file_hides_exception_details(monkeypatch, capsys):
    def fail(path):
        raise OSError("private-detail")

    monkeypatch.setattr(entrypoint, "load_config", fail)

    assert entrypoint.main(["--check-config"]) == 1
    captured = capsys.readouterr()
    assert "読み込めません" in captured.err
    assert "private-detail" not in captured.err


def test_without_option_does_not_load_config(monkeypatch, capsys):
    def unexpected(path):
        raise AssertionError("設定を読んではいけません")

    monkeypatch.setattr(entrypoint, "load_config", unexpected)

    assert entrypoint.main([]) == 2
    assert "未実装" in capsys.readouterr().err
