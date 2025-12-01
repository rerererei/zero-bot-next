import discord
from discord import app_commands
from discord.ext import commands
from utils.rankcard_draw import generate_rank_card 



class ZBCommands(commands.Cog):
    """ZERO BOT の /zb コマンドをまとめたCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # メイングループ /zb
    zb = app_commands.Group(
        name="zb",
        description="ZERO BOT のメインコマンド"
    )

    # -----------------------
    # /zb rank
    # -----------------------
    @zb.command(
        name="rank",
        description="RANK CARDを出力します。"
    )
    async def rank(self, interaction: discord.Interaction):

        # ★ まずインタラクションをデファー（考え中…表示）
        await interaction.response.defer(thinking=True)

        # その後、画像生成に委譲
        await generate_rank_card(self.bot, interaction)

async def setup(bot: commands.Bot):
    await bot.add_cog(ZBCommands(bot))
