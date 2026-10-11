"""Welcomeチャンネルの名前を作る。"""

import re
import unicodedata


def welcome_channel_name(member) -> str:
    # サーバーニックネームがあれば、display_nameに反映される。
    name = f"{member.display_name}-{member.name}"

    # 改行・空白をハイフンにし、制御文字などを取り除く。
    name = re.sub(r"\s+", "-", name)
    name = "".join(
        char
        for char in name
        if not unicodedata.category(char).startswith("C")
    )

    # チャンネル名の長さを制限する。
    return name[:100]
