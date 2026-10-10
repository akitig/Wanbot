from wanbot import __main__ as entrypoint
from wanbot.config import ConfigError, parse_config


def fake_config():
    return parse_config({
        "DISCORD_TOKEN": "private-test-token",
        "TEST_GUILD_ID": "200",
    })


def test_check_config_succeeds_without_disclosing_token(monkeypatch, capsys):
    config = fake_config()
    monkeypatch.setattr(entrypoint, "load_config", lambda path: config)

    async def must_not_connect(config):
        raise AssertionError("設定確認では接続してはいけません")

    monkeypatch.setattr(entrypoint, "run_bot", must_not_connect)

    result = entrypoint.main(["--check-config"])
    captured = capsys.readouterr()

    assert result == 0
    assert "成功" in captured.out
    assert "test: 有効な機能 = なし" in captured.out
    assert config.discord_token not in captured.out
    assert config.discord_token not in captured.err


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


def test_normal_start_calls_runtime(monkeypatch):
    config = fake_config()
    received = []

    monkeypatch.setattr(entrypoint, "load_config", lambda path: config)
    monkeypatch.setattr(entrypoint, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(entrypoint, "log_event", lambda *args, **kwargs: None)

    async def fake_run(value):
        received.append(value)
        return 0

    monkeypatch.setattr(entrypoint, "run_bot", fake_run)

    assert entrypoint.main([]) == 0
    assert received == [config]
