import io
import re

import discord
from discord.ext import commands
from PIL import Image


# 「#」+ 6桁の16進数だけに反応
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")

IMAGE_SIZE = 120


class ColorPreview(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Bot自身を含むBotの投稿は無視
        if message.author.bot:
            return

        # DMでは反応しない
        if message.guild is None:
            return

        # ボイスチャンネルのインチャ以外では反応しない
        if not isinstance(message.channel, discord.VoiceChannel):
            return

        color_code = message.content.strip()

        # カラーコード単体の投稿だけに反応
        if not HEX_COLOR_PATTERN.fullmatch(color_code):
            return

        try:
            rgb = self.hex_to_rgb(color_code)
            image_buffer = self.create_color_image(rgb)

            filename = f"color_{color_code[1:].upper()}.png"

            file = discord.File(
                fp=image_buffer,
                filename=filename,
            )

            await message.reply(
                content=f"🎨 **{color_code.upper()}**",
                file=file,
                mention_author=False,
            )

        except Exception as error:
            print(
                "❌ カラー画像の生成に失敗しました: "
                f"guild={message.guild.id}, "
                f"channel={message.channel.id}, "
                f"user={message.author.id}, "
                f"error={error}"
            )

            await message.reply(
                "❌ カラー画像の生成に失敗したよ。",
                mention_author=False,
            )

    @staticmethod
    def hex_to_rgb(color_code: str) -> tuple:
        """#RRGGBBをRGBの数値へ変換する"""

        hex_value = color_code.lstrip("#")

        return (
            int(hex_value[0:2], 16),
            int(hex_value[2:4], 16),
            int(hex_value[4:6], 16),
        )

    @staticmethod
    def create_color_image(rgb: tuple) -> io.BytesIO:
        """120px × 120pxの単色PNG画像を生成する"""

        image = Image.new(
            mode="RGB",
            size=(IMAGE_SIZE, IMAGE_SIZE),
            color=rgb,
        )

        image_buffer = io.BytesIO()
        image.save(image_buffer, format="PNG")
        image_buffer.seek(0)

        return image_buffer


async def setup(bot: commands.Bot):
    await bot.add_cog(ColorPreview(bot))
