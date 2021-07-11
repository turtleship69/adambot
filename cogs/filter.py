import discord
from discord.ext import commands
import asyncio
import ast  # using ast for literal_eval, stops code injection
import asyncpg
from .utils import DefaultEmbedResponses

class Filter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def check_is_command(self, message):
        """
        Checks whether or not `message` is a valid filter command or not
        """
        ctx = await self.bot.get_context(message)
        content = message.content
        prefixes = await self.bot.get_used_prefixes(message)
        prefixes.sort(key=len, reverse=True)
        for prefix in prefixes:
            if content.startswith(prefix):
                content = content.replace(prefix, "")
                break
        is_command = True if (await self.bot.is_staff(ctx) and content.startswith(
            "filter") and content != message.content) else False
        return is_command

    async def load_filters(self, guild):
        """
        Method used to load filter data for all guilds into self.filters
        """
        async with self.bot.pool.acquire() as connection:
            prop = await connection.fetchval('SELECT filters FROM filter WHERE guild_id = $1', guild.id)
            if not prop:
                await connection.execute('INSERT INTO filter (guild_id, filters) VALUES ($1, $2)', guild.id, "{'filtered':[], 'ignored':[]}")
                self.filters[guild.id] = {'filtered':[], 'ignored': []}
            else:
                prop = ast.literal_eval(prop)
                if type(prop) is not dict or not ("filtered" in prop and "ignored" in prop):
                    await connection.execute('UPDATE filter SET filters = $1 WHERE guild_id = $2', "{'filtered':[], 'ignored':[]}", guild.id)
                    self.filters[guild.id] = {'filtered':[], 'ignored': []}
                else:
                    self.filters[guild.id] = prop

    async def propagate_new_guild_filter(self, guild):
        """
        Method used for pushing filter changes to the DB
        """
        async with self.bot.pool.acquire() as connection:
            await connection.execute('UPDATE filter SET filters = $1 WHERE guild_id = $2', str(self.filters[guild.id]), guild.id)

    # ---LISTENERS---

    @commands.Cog.listener()
    async def on_ready(self):
        # Maybe tweak this so the tables are only created on the fly as and when they are needed?
        self.filters = {}
        success = False
        while not success:  # race condition for table to be created otherwise
            try:
                for guild in self.bot.guilds:
                    await self.load_filters(guild)
                    success = True
            except asyncpg.exceptions.UndefinedTableError:
                success = False
                print("filter table doesn't exist yet, waiting 1 second...")
                await asyncio.sleep(1)

    @commands.Cog.listener()
    async def on_message(self, message):
        """
        Handles messages that are being sent.
        """
        if not self.bot.is_ready() or type(message.channel) != discord.TextChannel: # Only filter on TextChannels
            return  # sometimes barfs on startup
        await self.handle_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        """
        Handles message edits for people trying to get around the filter.
        """
        if not self.bot.is_ready() or type(after.channel) != discord.TextChannel: # Only filter on TextChannels
            return  # sometimes barfs on startup
        await self.handle_message(after)

    async def handle_message(self, message):
        """
        Checks whether a message should be removed.
        """
        ctx = await self.bot.get_context(message)
        is_command = await self.check_is_command(message)
        if not is_command and message.author.id == self.bot.user.id and message.reference:
            is_command = await self.check_is_command(await ctx.fetch_message(message.reference.message_id))

        try:
            filters = self.filters[message.guild.id]
            msg = message.content.lower()
            for ignore in filters["ignored"]:
                msg = msg.replace(ignore.lower(), "")
            if True in [phrase.lower() in msg for phrase in filters["filtered"]] and not is_command:
                # case insensitive is probably the best idea
                await message.delete()
        except KeyError: # If the bot hasn't loaded the filters yet
            pass

    @commands.group()
    @commands.guild_only()
    async def filter(self, ctx):
        if not ctx.invoked_subcommand:
            prefixes = await self.bot.get_used_prefixes(ctx.message) # TODO: Is there a nicer way to get the prefix, if not change all previous occasions of getting the prefix to this
            await ctx.reply(f"Type `{prefixes[2]}help filter` to get info!")


    async def clean_up(self, ctx, text, key, spoiler=True):
        """
        Strictly only cleans up one list. Cross-checks occur specifically within their own scopes.
        This makes sure space isn't wasted by adding random garbage to the filter that will already be in effect anyway
        """
        keys = ["filtered", "ignored"]
        if key not in keys:
            return  # get ignored
        disp_text = f"||{text}||" if spoiler else text
        if True in [phrase in text for phrase in self.filters[ctx.guild.id][key]]:
            await DefaultEmbedResponses.error_embed(self.bot, ctx, "Redundant phrase wasn't added!", desc=f"{disp_text} wasn't added to the {key} list since it contains another phrase that has already been added")
            return
        check = [text in phrase for phrase in self.filters[ctx.guild.id][key]]  # de-duplicate
        removed = []
        while True in check:
            index = check.index(True)
            del check[index]
            removed.append(f"{self.filters[ctx.guild.id][key][index]}")
            del self.filters[ctx.guild.id][key][index]
        message = f"Added *{disp_text}* to the {key} list!"
        the_list = "*\n•  ".join(removed)
        the_list += "*" if not the_list.endswith("*") else ""
        message = message if not removed else (
                    message + f"\n\nRemoved some redundant phrases from the {key} list too:\n\n•  *" + "*\n•  ".join(removed))
        return message

    @filter.command()
    @commands.guild_only()
    async def add(self, ctx, *, text):
        """
        Allows adding a filtered phrase for the guild. Staff role needed.
        """
        if not await self.bot.is_staff(ctx.message):
            await DefaultEmbedResponses.invalid_perms(self.bot, ctx)
            return

        if text not in self.filters[ctx.guild.id]["filtered"]:
            message = await self.clean_up(ctx, text, "filtered")
            if not message:
                return
            self.filters[ctx.guild.id]["filtered"].append(text)
            await self.propagate_new_guild_filter(ctx.guild)
            await DefaultEmbedResponses.success_embed(self.bot, ctx, "Filter added!", desc=message)
        else:
            await DefaultEmbedResponses.error_embed(self.bot, ctx, "That's already in the filtered list!")

    @filter.command()
    @commands.guild_only()
    async def add_ignore(self, ctx, *, text):
        """
        Allows adding a phrase that will be ignored by the filter.
        This will only add the phrase if it will have an effect.
        Staff role needed.
        """
        if not await self.bot.is_staff(ctx.message):
            await DefaultEmbedResponses.invalid_perms(self.bot, ctx)
            return

        if text not in self.filters[ctx.guild.id]["ignored"]:
            message = await self.clean_up(ctx, text, "ignored", spoiler=False)
            if not message:
                return

            if True not in [phrase in text for phrase in self.filters[ctx.guild.id]["filtered"]]:
                await DefaultEmbedResponses.error_embed(self.bot, ctx, "Didn't add redundant phrase to ignored list", desc="The phrase wasn't added since it would have no effect, as it has no filtered phrases within it")
                return

            if True in [phrase==text for phrase in self.filters[ctx.guild.id]["filtered"]]:
                await DefaultEmbedResponses.error_embed(self.bot, ctx, "Couldn't add that phrase to the ignored list", desc="A phrase cannot be both in the filtered list and the ignored list at the same time!")
                return

            self.filters[ctx.guild.id]["ignored"].append(text)
            await self.propagate_new_guild_filter(ctx.guild)
            await DefaultEmbedResponses.success_embed(self.bot, ctx, "Phrase added to ignored list!", desc=message)
        else:
            await DefaultEmbedResponses.error_embed(self.bot, ctx, "That's already in the ignored list!")

    async def generic_remove(self, ctx, text, key, desc=""):
        """
        Method to allow removal of a phrase from either list. Saves almost-duplicate code.
        """
        if not await self.bot.is_staff(ctx.message):
            await DefaultEmbedResponses.invalid_perms(self.bot, ctx)
            return
        if key not in ["filtered", "ignored"]:
            return  # get ignored

        if text not in self.filters[ctx.guild.id][key]:
            await DefaultEmbedResponses.error_embed(self.bot, ctx, f"No such thing is in the {key} list!", desc)

        else:
            del self.filters[ctx.guild.id][key][self.filters[ctx.guild.id][key].index(text)]
            await self.propagate_new_guild_filter(ctx.guild)
            await DefaultEmbedResponses.success_embed(self.bot, ctx, f"Phrase removed from {key} list!", desc)

    @filter.command()
    @commands.guild_only()
    async def remove(self, ctx, *, text):
        """
        Allows removing a filtered phrase. Staff role needed.
        """
        remove = [a for a in self.filters[ctx.guild.id]["ignored"]]
        for phrase in self.filters[ctx.guild.id]["ignored"]:
            for phrasee in self.filters[ctx.guild.id]["filtered"]:
                if phrasee in phrase and phrasee != text:  # not checking for what we're removing
                    del remove[remove.index(phrase)]

        for removal in remove:
            del self.filters[ctx.guild.id]["ignored"][self.filters[ctx.guild.id]["ignored"].index(removal)]
        msg = '*\n•  *'.join(remove)
        msg += "*" if not msg.endswith("*") else ""
        await self.generic_remove(ctx, text, "filtered", desc=f"Removed some redundant phrases from the ignored list too:\n\n•  *{msg}")

    @filter.command()
    @commands.guild_only()
    async def remove_ignore(self, ctx, *, text):
        """
        Allows removing a phrase ignored by the filter. Staff role needed.
        """
        await self.generic_remove(ctx, text, "ignored")


    async def list_generic(self, ctx, key, spoiler=True):
        if not await self.bot.is_staff(ctx.message):
            await DefaultEmbedResponses.invalid_perms(self.bot, ctx)
            return
        if key not in ["filtered", "ignored"]:
            return  # get ignored

        msg_content = ("\n".join([f"• ||{word}||" if spoiler else word for word in self.filters[ctx.guild.id][key]])) if self.filters[ctx.guild.id][key] else "Nothing to show here!"
        await DefaultEmbedResponses.information_embed(self.bot, ctx, f"{ctx.guild.name} {key} phrases list", desc = msg_content)

    @filter.command(name="list")
    @commands.guild_only()
    async def list_filter(self, ctx):
        """
        Lists filtered phrases for a guild. Staff role needed.
        """
        await self.list_generic(ctx, "filtered")

    @filter.command()
    @commands.guild_only()
    async def list_ignore(self, ctx):
        """
        Lists ignored phrases for a guild. Staff role needed.
        """
        await self.list_generic(ctx, "ignored", spoiler=False)


    @filter.command(name="clear")
    @commands.guild_only()
    async def clear_filter(self, ctx):
        """
        Allows clearing the list of filtered and ignored phrases for the guild. Staff role needed.
        """
        if not await self.bot.is_staff(ctx.message):
            await DefaultEmbedResponses.invalid_perms(self.bot, ctx)
            return

        self.filters[ctx.guild.id] = {"filtered":[], "ignored":[]}
        await self.propagate_new_guild_filter(ctx.guild)
        await DefaultEmbedResponses.success_embed(self.bot, ctx, "Filter cleared!", desc = f"The filter for **{ctx.guild.name}** has been cleared!")

def setup(bot):
    bot.add_cog(Filter(bot))
