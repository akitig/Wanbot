## Issue 01：起動・設定・運用手順

### 構成

- 同じWanbotを本番・テストサーバーで使用する。
- 原則1プロセスで起動し、同じトークンで二重起動しない。
- 本番設定は `PROD_`、テスト設定は `TEST_` で区別する。
- 同じプロセスのため、再起動や共通部分の障害は両サーバーに影響する。

### 初回セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

`.env.example`を`.env`へコピーし、トークンと必要な設定を記入する。
既存の`.env`は上書きしない。

```bash
chmod 600 .env
```

`.env`、運用データ、生成ログはGitへ追加しない。

### 設定

- `DISCORD_TOKEN`：Wanbotのトークン。
- `PROD_GUILD_ID` / `TEST_GUILD_ID`：参加先サーバー。
- `PROD_ADMIN_ROLE_IDS` / `TEST_ADMIN_ROLE_IDS`：Wanbotの管理者ロール。
- 管理者ロールはカンマ区切りで複数指定できる。
- `ENABLED_FEATURES`が空欄なら、そのサーバーの業務機能は無効。
- 設定変更は再起動で反映する。

現段階では業務機能は未実装のため、両方の`ENABLED_FEATURES`を
空欄にし、`DM_FORWARD_ENABLED=false`を維持する。

### 設定形式の確認

```bash
python -m wanbot --check-config
```

このコマンドはDiscordへ接続しない。
IDの実在・所属・Botの権限は、接続後に別途確認する。

### 起動・停止

```bash
python -m wanbot
```

停止は `Ctrl+C`。

起動時にサーバーと入力済みのロール・カテゴリ・チャンネルを確認する。
有効な機能に必要なBot権限も確認する。
現在は設定先の検証に失敗すると起動を停止する。

### 接続確認専用の起動

```bash
python -m wanbot.connection
```

業務機能を無効にした状態で使用する。

### 動作確認コマンド

テストサーバー限定で `/wanbot_status` を登録する。
指定された管理者ロールのメンバーだけが実行でき、
結果は実行者だけに表示される。

現段階のコマンド同期はテストサーバーのみ。
本番の業務コマンドは、各機能の実装時に追加する。

### テスト・コード検査

```bash
python -m pytest -q
python -m ruff check wanbot tests
```

自動テストは実際のトークンやDiscord接続なしで実行する。

### ログ

- 時刻はUTCで記録する。
- トークンを伏せ、一般ログへ回答・DM本文を渡さない。
- 例外本文ではなく、例外の種類と処理名を記録する。
- 現段階では標準出力へ出力する。

### 後続Issueで追加するもの

- 業務機能本体と、そのコマンド登録。
- サーバー別の運用データ保存・復元。
- 管理者専用チャンネルの閲覧範囲の検証。
- ルール投稿やリアクションの検証。
- 各操作直前の権限・ロール順位の再確認。
- 全キック共通の管理者承認フロー。

機能の実装時は、設定上の有効化だけでなく、
実際の起動状態と操作者の権限を確認して処理する。

## Issue 02：サーバー別データ保存

### 保存先

`.env` の `RUNTIME_DATA_DIR` 配下に、サーバーID別で保存する。

```text
runtime/
  <本番サーバーID>/
    welcome.json
  <テストサーバーID>/
    welcome.json
```

同じユーザーが両サーバーにいても、保存データを共有しない。
未登録のサーバーには保存できない。

### 保存形式

```json
{
  "schema_version": 1,
  "guild_id": 200,
  "document": "welcome",
  "data": {}
}
```

- `schema_version` は共通保存形式のバージョン。
- `guild_id` と `document` が保存先と一致するか確認する。
- `data` は文字列キーの辞書。
- ユーザーIDを辞書のキーにする場合は `str(user_id)` を使う。
- 質問回答や予約など、機能固有のデータ構造の検証は各機能で実装する。

### 同期版と非同期版

- `GuildJsonStore`：同期的な読み書きの共通処理。
- `AsyncGuildJsonStore`：Discordの非同期処理から使う窓口。

Discordのイベントやコマンドからは、原則として非同期版を使用する。

```python
from wanbot.storage.async_store import AsyncGuildJsonStore

async def example(config, guild_id):
    async with AsyncGuildJsonStore(config, guild_id, "welcome") as store:
        current = await store.read()

        def change(data):
            data["example"] = {"step": "waiting"}
            return data

        await store.update(change)
```

常設の機能では、機能開始時に保存窓口を作り、
停止時に `await store.close()` を呼ぶ。

### 更新時の注意

- 更新は `update()` の中で最新データを読み、変更して保存する。
- `read()` で取得した辞書を変更しただけでは保存されない。
- 更新関数は通常の `def` で作る。
- 更新関数の中でDiscordへの通信や長時間の処理を行わない。
- 外側の古い読み取り結果で全体を置き換えると、更新を失う可能性がある。
- 呼び出し元がキャンセルされても、開始済みの保存は継続する。
- キャンセル後の再実行前には、保存状態を確認する。

### 安全性と制限

- 一時ファイルへ書き終えてから、本来のファイルと置き換える。
- 壊れたJSONや未対応の保存形式を勝手に初期化しない。
- 保存先のサーバーフォルダー・ファイルにシンボリックリンクを許可しない。
- 同じプロセス内の更新を、ファイル単位で順番に実行する。
- 非同期版は、同じイベントループ内で使用する。
- 同じデータを複数プロセスから同時更新する用途には対応しない。
- 強制終了や停電時の完全な永続化、バックアップは別途対策が必要。
- `close()` は開始済み処理の終了を待つ。保存失敗はログに記録される。
- 通常の更新呼び出しでは、保存失敗の例外を呼び出し元へ返す。

### 復元の範囲

この基盤で保存データを読み戻せる。
Welcomeの進行状態、予約、ボタン、期限などを読み戻して
実際の動作へ復帰させる処理は、各機能のIssueで実装する。

### テスト

一時ディレクトリと架空のデータを使用する。
実際の運用データやDiscord接続は使用しない。

```bash
python -m pytest -q
python -m ruff check wanbot tests
```
