# utils/rankcard_draw.py

import discord
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageColor
from typing import Optional, Tuple
from utils.rankcard_s3 import load_rank_bg_from_s3
from data.store import get_rank_bg_key

from data.store import (
    get_voice_xp,
    get_text_xp,
    calc_level_from_xp,
    get_guild_user_stats,
)

DEFAULT_BG = "default.png"

# ★ rank 生成の本体関数（外から呼び出す）
async def generate_rank_card(bot, interaction: discord.Interaction):

    guild = interaction.guild
    user = interaction.user
    guild_id = guild.id
    user_id = user.id

    # ===== XP & レベル・ランク計算 =====
    voice_xp = get_voice_xp(guild_id, user_id)
    text_xp = get_text_xp(guild_id, user_id)

    v_lv, v_cur, v_need = calc_level_from_xp(voice_xp)
    t_lv, t_cur, t_need = calc_level_from_xp(text_xp)

    stats = get_guild_user_stats(guild_id)

    # ランキング（XP>0 のみ対象）
    voice_entries = [
        (uid, data.get("voice_xp", 0.0))
        for uid, data in stats.items()
        if data.get("voice_xp", 0.0) > 0
    ]
    voice_entries.sort(key=lambda x: x[1], reverse=True)
    v_rank = None
    if voice_xp > 0 and voice_entries:
        for idx, (uid, _) in enumerate(voice_entries, start=1):
            if uid == user_id:
                v_rank = (idx, len(voice_entries))
                break

    text_entries = [
        (uid, data.get("text_xp", 0.0))
        for uid, data in stats.items()
        if data.get("text_xp", 0.0) > 0
    ]
    text_entries.sort(key=lambda x: x[1], reverse=True)
    t_rank = None
    if text_xp > 0 and text_entries:
        for idx, (uid, _) in enumerate(text_entries, start=1):
            if uid == user_id:
                t_rank = (idx, len(text_entries))
                break

    # ===== レイアウト用の定数（ここをいじれば見た目が変わる）=====
    CARD_WIDTH = 700        # ★カード全体の横幅
    CARD_HEIGHT = 300       # ★カード全体の高さ
    CARD_MARGIN = 20         # ★外枠からカードまでの余白
    CARD_RADIUS = 24        # ★カード角丸の半径

    AVATAR_SIZE = 150        # ★ユーザーアイコンの直径
    GUILD_ICON_SIZE = 80    # ★サーバーアイコンの直径（右上）

    BAR_WIDTH = 430         # ★経験値バーの長さ（短くしたいならここ）
    BAR_HEIGHT = 23         # ★経験値バーの太さ（太くしたいならここ）
    BAR_VERTICAL_GAP = 60   # ★上のバーと下のバーの縦の間隔
    RIGHT_PADDING = 40       # ★右端からさらに左にずらす余白（total_x の調整で使用）

    # カラーを hex で定義し、必要に応じて alpha を付与して RGBA に変換する
    CARD_BG_HEX = "#ffffff"
    CARD_BG_ALPHA = 0
    GUILD_ICON_HEX = "#5865F2"
    LABEL_HEX = "#282828"
    NAME_HEX = "#282828"
    ID_HEX = "#282828"
    SMALL_TEXT_HEX = "#282828"
    BAR_BG_HEX = "#C2C2C2"
    BAR_TEXT_HEX = "#282828"
    ORANGE_HEX = "#F5A623"
    CYAN_HEX = "#00D8FF"

    # カラー変換関数
    def hex_to_rgba(hex_str: str, alpha: Optional[int] = None) -> Tuple[int, int, int, int]:
        rgb = ImageColor.getrgb(hex_str)
        if alpha is None:
            return (rgb[0], rgb[1], rgb[2], 255)
        return (rgb[0], rgb[1], rgb[2], alpha)

    CARD_BG_FILL = hex_to_rgba(CARD_BG_HEX, CARD_BG_ALPHA)
    GUILD_ICON_FILL = hex_to_rgba(GUILD_ICON_HEX)
    LABEL_COLOR = hex_to_rgba(LABEL_HEX)
    NAME_COLOR = hex_to_rgba(NAME_HEX)
    ID_COLOR = hex_to_rgba(ID_HEX)
    SMALL_TEXT_COLOR = hex_to_rgba(SMALL_TEXT_HEX)
    BAR_BG_COLOR = hex_to_rgba(BAR_BG_HEX)
    BAR_TEXT_COLOR = hex_to_rgba(BAR_TEXT_HEX)
    ORANGE_COLOR = hex_to_rgba(ORANGE_HEX)
    CYAN_COLOR = hex_to_rgba(CYAN_HEX)

    # ===== 画像生成開始 =====
    bg_key = get_rank_bg_key(guild_id, user_id) or DEFAULT_BG

    try:
        bg = load_rank_bg_from_s3(bg_key)
    except Exception as e:
        # ログだけ吐いてデフォルト背景にフォールバック
        print(f"[rankcard] failed to load bg '{bg_key}', fallback to default: {e}")
        bg = load_rank_bg_from_s3(DEFAULT_BG)

    bg = bg.resize((CARD_WIDTH, CARD_HEIGHT), Image.LANCZOS)

    # ★ オーバーレイレイヤーを作る（完全透明）
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    card_rect = (
        CARD_MARGIN,
        CARD_MARGIN,
        CARD_WIDTH - CARD_MARGIN,
        CARD_HEIGHT - CARD_MARGIN,
    )

    # ★ オーバーレイ側に「半透明カード」を描く
    odraw.rounded_rectangle(
        card_rect,
        radius=CARD_RADIUS,
        fill=CARD_BG_FILL,
    )

    # ★ 背景(bg)とオーバーレイ(overlay)を合成
    bg = Image.alpha_composite(bg, overlay)

    # ここから先は今まで通り bg にアイコンや文字を描いてOK
    draw = ImageDraw.Draw(bg)

    # フォント設定
    FONT_PATH_MAIN = "assets/fonts/NotoSansJP-Regular.ttf"
    FONT_PATH_BOLD = "assets/fonts/NotoSansJP-Bold.ttf"
    FONT_PATH_AUDIO = "assets/fonts/Audiowide-Regular.ttf"

    font_name = ImageFont.truetype(FONT_PATH_BOLD, 30)
    font_id = ImageFont.truetype(FONT_PATH_MAIN, 16)
    font_rank = ImageFont.truetype(FONT_PATH_MAIN, 20)
    font_total = ImageFont.truetype(FONT_PATH_MAIN, 16)
    font_bartext = ImageFont.truetype(FONT_PATH_MAIN, 16)
    font_label = ImageFont.truetype(FONT_PATH_AUDIO, 45)
    font_lvl_num = ImageFont.truetype(FONT_PATH_BOLD, 26)
    font_lvl_text = ImageFont.truetype(FONT_PATH_MAIN, 16)

    # ===== 左側：ユーザーアイコン =====
    avatar_size = AVATAR_SIZE  # ★アイコンの直径

    avatar_bytes = await user.display_avatar.read()
    avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
    avatar = avatar.resize((avatar_size, avatar_size))

    mask = Image.new("L", (avatar_size, avatar_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, avatar_size, avatar_size), fill=255)

    avatar_x = card_rect[0] + 18
    # ★高さ方向はカード中央に寄せる
    card_h = card_rect[3] - card_rect[1]
    avatar_y = card_rect[1] + (card_h - avatar_size) // 2

    bg.paste(avatar, (avatar_x, avatar_y - 20), mask)

    # ===== サーバーアイコン（カード右上） =====
    guild_icon_size = GUILD_ICON_SIZE

    if guild.icon:
        g_bytes = await guild.icon.read()
        g_img = Image.open(BytesIO(g_bytes)).convert("RGBA")
        g_img = g_img.resize((guild_icon_size, guild_icon_size))

        g_mask = Image.new("L", (guild_icon_size, guild_icon_size), 0)
        g_mask_draw = ImageDraw.Draw(g_mask)
        g_mask_draw.ellipse((0, 0, guild_icon_size, guild_icon_size), fill=255)

        # ★カード右上あたりに固定配置
        g_x = card_rect[2] - guild_icon_size - 16  # 右からの余白
        g_y = card_rect[1] + 12                    # 上からの余白
        bg.paste(g_img, (g_x, g_y), g_mask)
    else:
        g_x = card_rect[2] - guild_icon_size - 16
        g_y = card_rect[1] + 12
        draw.ellipse(
            (g_x, g_y, g_x + guild_icon_size, g_y + guild_icon_size),
            fill=GUILD_ICON_FILL,
        )

    # ===== 右側：テキスト情報 =====
    content_left = avatar_x + avatar_size + 24
    content_right = card_rect[2] - 16

    # 小さめラベル（ZERO BOT RANK）
    label_y = card_rect[1] + 10
    label_text = "RANK CARD"

    # テキストの幅だけ取得する
    label_bbox = draw.textbbox((0, 0), label_text, font=font_label)
    label_w = label_bbox[2] - label_bbox[0]

    card_center_x = card_rect[0] + ((card_rect[2] - card_rect[0]) // 2)
    label_x = card_center_x - (label_w // 2)

    draw.text(
        (label_x, label_y),
        label_text,
        font=font_label,
        fill=LABEL_COLOR,
    )

    # ユーザー名
    name_y = label_y + 50
    draw.text(
        (content_left, name_y),
        user.display_name,
        font=font_name,
        fill=NAME_COLOR,
    )

    # ユーザーID（任意ID用の位置）
    id_y = name_y + 35
    # ★ここを「任意で付けてもらうID」に差し替える
    # 例: custom_id = get_custom_id(guild_id, user_id) など
    id_text = f"ID: {user.name}"
    draw.text(
        (content_left, id_y),
        id_text,
        font=font_id,
        fill=ID_COLOR,
    )

    # 共通：バー描画用関数
    def draw_bar_block(
        title_text: str,
        top_y: int,
        lvl: int,
        cur: float,
        need: float,
        xp_total: float,
        rank_info,
        bar_color: tuple[int, int, int, int],
    ):
        # 左側「LVL 1」
        lvl_x = content_left
        lvl_y = top_y
        draw.text(
            (lvl_x, lvl_y),
            title_text,
            font=font_lvl_text,
            fill=SMALL_TEXT_COLOR,
        )
        draw.text(
            (lvl_x, lvl_y + 15),
            str(lvl),
            font=font_lvl_num,
            fill=NAME_COLOR,
        )

        # 右側ブロックの左端
        info_left = lvl_x + 60  # ★LVLブロックとバー側の距離

        # Rank / Total 行
        if rank_info is not None:
            rank_no, total_people = rank_info
            rank_text = f"Rank: #{rank_no}"
        else:
            rank_text = "Rank: -"

        total_text = f"Total: {int(xp_total)}"

        draw.text(
            (info_left, top_y),
            rank_text,
            font=font_rank,
            fill=SMALL_TEXT_COLOR,
        )

        total_bbox = draw.textbbox((0, 0), total_text, font=font_total)
        total_w = total_bbox[2] - total_bbox[0]
        # 右端から total_w を引いた位置からさらに RIGHT_PADDING 分だけ左にずらす
        total_x = content_right - total_w - RIGHT_PADDING
        # content_left を越えないようにクリップ
        total_x = max(content_left, total_x)
        draw.text(
            (total_x, top_y),
            total_text,
            font=font_total,
            fill=SMALL_TEXT_COLOR,
        )

        # ===== バー本体 =====
        bar_top = top_y + 30              # ★バーの縦位置（全体をもっと下げたい時ここ）
        bar_height = BAR_HEIGHT           # ★バーの太さ
        bar_x1 = info_left
        # ★バーの長さ。content_right からはみ出さないように min をかける
        bar_x2 = min(bar_x1 + BAR_WIDTH, content_right)
        bar_y1 = bar_top
        bar_y2 = bar_top + bar_height

        # 背景バー
        draw.rounded_rectangle(
            (bar_x1, bar_y1, bar_x2, bar_y2),
            radius=bar_height // 2,
            fill=BAR_BG_COLOR,
        )

        ratio = (cur / need) if need > 0 else 0.0
        ratio = max(0.0, min(1.0, ratio))
        fill_x = bar_x1 + int((bar_x2 - bar_x1) * ratio)

        draw.rounded_rectangle(
            (bar_x1, bar_y1, fill_x, bar_y2),
            radius=bar_height // 2,
            fill=bar_color,
        )

        # バー上のテキスト「0 / 20」など
        bar_text = f"{int(cur)} / {int(need)}"
        bar_text_bbox = draw.textbbox((0, 0), bar_text, font=font_bartext)
        bar_text_w = bar_text_bbox[2] - bar_text_bbox[0]
        bar_text_h = bar_text_bbox[3] - bar_text_bbox[1]
        bar_text_x = bar_x1 + (bar_x2 - bar_x1 - bar_text_w) // 2

        TEXT_VERTICAL_ADJUST = -5  # ★バー内テキストを少しだけ上にずらす

        bar_text_y = bar_y1 + (bar_height - bar_text_h) // 2 + TEXT_VERTICAL_ADJUST
        draw.text(
            (bar_text_x, bar_text_y),
            bar_text,
            font=font_bartext,
            fill=BAR_TEXT_COLOR,
        )

    # 上段：テキストXPバー（位置を少し下げた）
    block_top_text = id_y + 32  # ★バー全体の開始位置（もっと下げたいなら大きく）

    draw_bar_block(
        title_text="Text",
        top_y=block_top_text,
        lvl=t_lv,
        cur=t_cur,
        need=t_need,
        xp_total=text_xp,
        rank_info=t_rank,
        bar_color=ORANGE_COLOR,
    )

    # 下段：ボイスXPバー
    block_top_voice = block_top_text + BAR_VERTICAL_GAP  # ★バー同士の縦間隔
    draw_bar_block(
        title_text="Voice",
        top_y=block_top_voice,
        lvl=v_lv,
        cur=v_cur,
        need=v_need,
        xp_total=voice_xp,
        rank_info=v_rank,
        bar_color=CYAN_COLOR,
    )

    # 最後に送信
    buffer = BytesIO()
    bg.save(buffer, format="PNG")
    buffer.seek(0)

    file = discord.File(buffer, filename="rank-card.png")

    await interaction.response.send_message(
        content="",
        file=file,
        ephemeral=False,
    )
