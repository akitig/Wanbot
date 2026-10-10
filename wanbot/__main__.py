"""Wanbotのコマンドライン入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wanbot.config import ConfigError, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wanbot")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="設定の形式を確認します。Discordには接続しません。",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="読み込む設定ファイル。省略時はプロジェクトの.envを使います。",
    )
    args = parser.parse_args(argv)

    if not args.check_config:
        print(
            "Bot接続処理はまだ未実装です。"
            "設定確認には --check-config を指定してください。",
            file=sys.stderr,
        )
        return 2

    try:
        config = load_config(args.env_file)
    except ConfigError as error:
        print(f"設定エラー: {error}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print(
            "設定ファイルを読み込めません。パス・権限・文字コードを確認してください。",
            file=sys.stderr,
        )
        return 1

    print("設定の形式チェックに成功しました。")
    print(f"ログレベル: {config.log_level}")
    print(f"登録サーバー数: {len(config.guilds)}")

    for guild in config.guilds.values():
        features = ", ".join(sorted(guild.enabled_features)) or "なし"
        print(f"- {guild.environment}: 有効な機能 = {features}")

    dm_status = "有効" if config.dm_forward_enabled else "無効"
    print(f"DM転送: {dm_status}")
    print("Discordへの接続は行っていません。")
    print("IDの実在・所属・権限、機能本体の実装状況は別途確認が必要です。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
