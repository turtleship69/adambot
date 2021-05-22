import discord
import os
import inspect
from discord.utils import get
from discord.ext import commands
from discord import Embed, Colour
from .utils import GCSE_SERVER_ID, CHANNELS, Permissions, send_text_file
import asyncio


class Eval(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    def split_2000(text):
        chunks = []
        while len(text) > 0:
            chunks.append(text[:2000])
            text = text[2000:]
        return chunks

    @commands.command(name="eval", pass_context=True)
    @commands.has_any_role(*Permissions.MOD)
    async def evaluate(self, ctx, *, command=""):  # command is kwarg to stop it flooding the console when no input is provided
        """
        Allows evaluating strings of code (intended for testing).
        If something doesn't output correctly try wrapping in str()
        """
        try:
            output = eval(command)
            if inspect.isawaitable(output):
                a_output = await output
                if a_output is None:
                    await ctx.message.channel.send("No output (command probably executed correctly)")
                else:
                    if len(a_output) > 2000:
                        a_output = self.split_2000(a_output)
                    if type(a_output) is str:
                        await ctx.message.channel.send(a_output)
                    else:
                        for chunk in a_output:
                            await ctx.message.channel.send(chunk)
            elif output is None:
                await ctx.message.channel.send("No output (command probably executed correctly)")
            else:
                if len(output) > 2000:
                    output = self.split_2000(output)
                if type(output) is str:
                    await ctx.message.channel.send(output)
                else:
                    for chunk in output:
                        await ctx.message.channel.send(chunk)
        except Exception as e:
            e = str(e)
            e.replace(os.getcwd(), ".")
            await ctx.message.channel.send(e)

    @commands.group()
    @commands.guild_only()
    async def sql(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send('```-help sql```')

    @sql.command(pass_context = True)
    @commands.is_owner()
    async def execute(self, ctx, *command):
        async with self.bot.pool.acquire() as connection:
            try:
                await connection.execute(' '.join(command))
            except Exception as e:
                await ctx.send(f"EXCEPTION: {e}")

    @sql.command(pass_context = True)
    @commands.is_owner()
    async def fetch(self, ctx, *command):
        async with self.bot.pool.acquire() as connection:
            try:
                records = await connection.fetch(' '.join(command))
                final_str = ""
                for i in range(len(records)):
                    final_str += str(records[i])
                    if i != len(records) - 1:
                        final_str += "\n"

                await send_text_file(final_str, ctx.channel, "query")
            except Exception as e:
                await ctx.send(f"EXCEPTION: {e}")


def setup(bot):
    bot.add_cog(Eval(bot))
