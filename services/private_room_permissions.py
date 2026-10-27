# services/private_room_permissions.py
"""
プライベートルーム機能の権限（チャンネル個別上書き）まわり。

方針（プライベートルーム機能.md 6章・33章）:
- @everyone: 全種別で「チャンネルを見る」を拒否。
- 入会済みロール（新人・member）: パブリック系（招待制ではない部屋）のみ
  「チャンネルを見る・VCへ接続」を明示許可する（PUBLIC_ROLE_OVERWRITE）。
  表個室は「チャンネルを見る」だけ許可し「VCへ接続」は拒否する
  （VISIBLE_ROLE_OVERWRITE）。
  @everyoneへ許可すると入場者・申請中など入会前の人にも見えてしまうため、
  ロール単位で許可する。どのロールに付けるかは呼び出し側が決める。
- 作成者・招待ユーザー: 閲覧・接続・発言・VCインチャ投稿/履歴のみ明示許可。
  管理系権限（チャンネル管理・権限管理・移動/ミュート等）は付与しない。
- Bot: チャンネル管理・権限管理ができるよう明示許可。
- 上記以外の一般的なVC権限（カメラ・画面共有等）には一切触れず、
  既存ロール権限をそのまま引き継がせる。
- Administrator権限者はDiscordの権限体系上チャンネル上書きを
  バイパスするため、個別の上書きは不要。

修復（33章）は「期待される状態と実際の状態を比較し、差分がある部分だけ
編集する」実装にする。差分が無ければAPI呼び出し自体をしないため、
Bot自身の修復編集がまた変更イベントを発火させて無限ループになる
心配もない。

このシステムが個別に上書きを作るのは @everyone・入会済みロール・作成者・
招待ユーザー・Bot自身の5種類のみ。したがって、ロール上書きは
@everyone・入会済みロール以外一切触れず、
それ以外のメンバー個別上書き（解除済みユーザーの残存分・管理外の不明な
個別ユーザー権限）は無条件で削除してよい、という判定にしている。
"""

from typing import Dict, Iterable, List, Optional

import discord

# 修復時に比較・強制するフィールドのみ。それ以外（カメラ等）は一切見ない。
_MANAGED_FIELDS = (
    "view_channel",
    "connect",
    "speak",
    "send_messages",
    "read_message_history",
    "manage_channels",
    "manage_permissions",
)

EVERYONE_OVERWRITE = discord.PermissionOverwrite(view_channel=False)

# パブリック系（招待制ではない）部屋用。入会済みロールに付けて、
# 入会した人なら誰でも見える・入れるようにする。
PUBLIC_ROLE_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True, connect=True
)

# 表個室用。入会済みロールに付けて、見えるが入れない（接続できるのは
# 作成者・招待ユーザーだけ）ようにする。
VISIBLE_ROLE_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True, connect=False
)

MEMBER_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True,
    connect=True,
    speak=True,
    send_messages=True,
    read_message_history=True,
)

BOT_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True,
    connect=True,
    manage_channels=True,
    manage_permissions=True,
)


def build_expected_overwrites(
    guild: discord.Guild,
    owner_id: int,
    invited_user_ids: Iterable[str],
    bot_member: discord.Member,
    role_overwrites: Optional[
        Dict[discord.Role, discord.PermissionOverwrite]
    ] = None,
) -> Dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    """このルームで本来あるべき権限上書きの全体を組み立てる。"""
    expected: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: EVERYONE_OVERWRITE,
        bot_member: BOT_OVERWRITE,
    }
    expected.update(role_overwrites or {})

    owner = guild.get_member(owner_id)
    if owner is not None:
        expected[owner] = MEMBER_OVERWRITE

    for raw_uid in invited_user_ids:
        member = guild.get_member(int(raw_uid))
        if member is not None:
            expected[member] = MEMBER_OVERWRITE

    return expected


def _overwrite_matches(
    current: discord.PermissionOverwrite,
    expected: discord.PermissionOverwrite,
) -> bool:
    return all(
        getattr(current, field) == getattr(expected, field)
        for field in _MANAGED_FIELDS
    )


def _target_label(target: discord.abc.Snowflake) -> str:
    if isinstance(target, discord.Role):
        return f"role:{target.name}"
    return f"user:{getattr(target, 'display_name', target.id)}({target.id})"


async def apply_room_permissions(
    channel: discord.VoiceChannel,
    guild: discord.Guild,
    owner_id: int,
    invited_user_ids: Iterable[str],
    bot_member: discord.Member,
    *,
    reason: str,
    role_overwrites: Optional[
        Dict[discord.Role, discord.PermissionOverwrite]
    ] = None,
) -> List[str]:
    """
    期待される権限状態との差分だけを実際に編集する。

    戻り値: 実際に変更した内容の説明（操作ログ用）。空リストなら無変更。
    """
    expected = build_expected_overwrites(
        guild, owner_id, invited_user_ids, bot_member, role_overwrites
    )
    changed: List[str] = []

    for target, expected_ow in expected.items():
        current_ow = channel.overwrites_for(target)
        if not _overwrite_matches(current_ow, expected_ow):
            await channel.set_permissions(
                target, overwrite=expected_ow, reason=reason
            )
            changed.append(f"{_target_label(target)} の権限を修復")

    expected_member_ids = {
        member.id
        for member in expected
        if isinstance(member, discord.Member)
    }

    for target in list(channel.overwrites.keys()):
        if (
            isinstance(target, discord.Member)
            and target.id not in expected_member_ids
        ):
            await channel.set_permissions(
                target, overwrite=None, reason=reason
            )
            changed.append(
                f"{_target_label(target)} の想定外の個別権限を削除"
            )

    return changed


async def remove_member_overwrite(
    channel: discord.VoiceChannel,
    member: discord.abc.Snowflake,
    *,
    reason: str,
) -> None:
    try:
        await channel.set_permissions(
            member, overwrite=None, reason=reason
        )
    except discord.HTTPException:
        pass
