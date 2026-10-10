"""1プロセスで使用する、サーバー別のJSON保存処理。"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wanbot.config import AppConfig


SCHEMA_VERSION = 1
DOCUMENT_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")

_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class StorageError(RuntimeError):
    """保存処理のエラー。個人のデータ内容はメッセージに含めない。"""


def _path_lock(path: Path) -> threading.RLock:
    # 同じファイルを扱う別インスタンスにも、同じロックを渡す。
    with _LOCKS_GUARD:
        if path not in _LOCKS:
            _LOCKS[path] = threading.RLock()
        return _LOCKS[path]


def _reject_constant(value: str) -> None:
    # JSONの標準外であるNaN・Infinityなどを拒否する。
    raise ValueError("Non-standard JSON value")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _validate_json_value(value: Any) -> None:
    """保存可能なJSON型だけを許可し、暗黙の型変換を防ぐ。"""
    if value is None or type(value) in (str, bool, int, float):
        return

    if type(value) is list:
        for item in value:
            _validate_json_value(item)
        return

    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            _validate_json_value(item)
        return

    raise ValueError("Unsupported JSON value type")


class GuildJsonStore:
    def __init__(
        self,
        config: AppConfig,
        guild_id: int,
        document: str,
    ) -> None:
        if type(guild_id) is not int or config.guild_for(guild_id) is None:
            raise StorageError("未登録のサーバーには保存できません。")

        if not DOCUMENT_NAME.fullmatch(document):
            raise StorageError("保存名には英小文字・数字・アンダースコアを使ってください。")

        self.guild_id = guild_id
        self.document = document
        self.root = config.runtime_data_dir.resolve()
        self.directory = self.root / str(guild_id)
        self.path = self.directory / f"{document}.json"
        self._lock = _path_lock(self.path)

    def _check_paths(self) -> None:
        # サーバーフォルダーや保存ファイルを別の場所へ向けさせない。
        if self.directory.is_symlink() or self.path.is_symlink():
            raise StorageError("保存先にシンボリックリンクは使用できません。")
        if self.directory.resolve().parent != self.root:
            raise StorageError("保存先がデータディレクトリの外です。")

    def read(self) -> dict[str, Any]:
        """保存済みデータを読む。未作成なら空の辞書を返す。"""
        with self._lock:
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        self._check_paths()

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                envelope = json.load(
                    handle,
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_constant,
                )
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, RecursionError):
            raise StorageError(
                "データを読み込めません。元のファイルは変更していません。"
            ) from None

        if type(envelope) is not dict:
            raise StorageError("保存データの形式が不正です。")

        if set(envelope) != {"schema_version", "guild_id", "document", "data"}:
            raise StorageError("保存データの項目が不正です。")

        if (
            type(envelope["schema_version"]) is not int
            or envelope["schema_version"] != SCHEMA_VERSION
        ):
            raise StorageError("未対応の保存形式バージョンです。")

        if (
            type(envelope["guild_id"]) is not int
            or envelope["guild_id"] != self.guild_id
            or envelope["document"] != self.document
        ):
            raise StorageError("保存データのサーバーまたは保存名が一致しません。")

        if type(envelope["data"]) is not dict:
            raise StorageError("保存データ本体は辞書である必要があります。")

        return envelope["data"]

    def update(
        self,
        change: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """最新データを読み、変更して保存するまでを一つの処理として行う。"""
        with self._lock:
            current = self._read_unlocked()

            # 変更処理が例外を出した場合は、書き込みへ進まない。
            updated = change(current)
            if type(updated) is not dict:
                raise StorageError("更新処理は辞書を返す必要があります。")

            envelope = {
                "schema_version": SCHEMA_VERSION,
                "guild_id": self.guild_id,
                "document": self.document,
                "data": updated,
            }

            try:
                _validate_json_value(updated)
                text = json.dumps(
                    envelope,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                ) + "\n"
            except (TypeError, ValueError, RecursionError):
                raise StorageError("JSONとして保存できない値が含まれています。") from None

            self._write_atomic(text)

    def _write_atomic(self, text: str) -> None:
        temporary_path: Path | None = None

        try:
            self._check_paths()
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._check_paths()

            # 同じディレクトリに作ることで、置き換えを原子的に行う。
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f".{self.document}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temporary_path, self.path)
            temporary_path = None
        except (OSError, UnicodeError):
            raise StorageError(
                "保存に失敗しました。保存先と空き容量を確認してください。"
            ) from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    # 元の保存エラーを、後片付けのエラーで置き換えない。
                    pass
