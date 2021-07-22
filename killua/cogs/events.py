import json
import topgg
import discord

from aiohttp import ClientSession
from datetime import datetime
from discord.utils import find
from discord.ext import commands, tasks
from killua.checks import p
from killua.classes import User, Guild
from killua.constants import PREMIUM_ROLES, PATREON_TIERS, teams, guilds

from typing import List, Union

with open('config.json', 'r') as config_file:
	config = json.loads(config_file.read())

class Patrons:

    def __init__(self, patrens:List[dict]):
        self.patrons = patrons
        self.invalid = [x for x in self.patrons if x["discord"] is None]

    def __iter__(self):
        self.pos = 0
        return self
  
    def __next__(self): # This is to check `if id in Patrons`
        self.pos += 1

        if self.pos > len(self.patrons):
            raise StopIteration

        return self.patreons[self.pos-1]["discord"]


class Patreon:

    def __init__(self, session:ClientSession, token:str, campain_id:Union[str, int]):
        self.session = session
        self.token = token
        self.campain_id = campain_id
        self.url = f"https://www.patreon.com/api/oauth2/v2/campaigns/{self.campain_id}/members?page%5B100%5D&include=currently_entitled_tiers%2Cuser&fields%5Bmember%5D=full_name%2Cis_follower%2Clast_charge_date%2Clast_charge_status%2Clifetime_support_cents%2Ccurrently_entitled_amount_cents%2Cpatron_status%2Cpledge_relationship_start&fields%5Buser%5D=social_connections&page%5Bcount%5D=100"

    def _int_else(self, i) -> Union[int, None]:
        return int(i) if i else None

    def _catch(self, d:dict) -> Union[str, None]:
        try:
            return d["attributes"]["social_connections"]["discord"]["user_id"]
        except KeyError: # In case this happens I still want to have a value, just None
            return None

    async def _make_request(self, url:str) -> dict:
        res = await self.session.get(url, headers={"Authorization": f"Bearer {self.token}"})
        return await res.json()

    async def _paginate(self, data:dict, prev:List[dict]) -> List[dict]:
        if "links" in data.keys():
            res = await self._make_request(data["links"]["next"])
            patrons = [*prev, *self._format_patrons(res)]
            return await self._paginate(res, patrons)
        else:
            return prev

    async def _get(self, data:dict) -> List[dict]:
        prev:List[dict] = self._format_patrons(data)
        if "links" in data.keys():
            return await self._paginate(data, prev)
        else:
            return prev

    def _get_user_info(self, data:list, user:str) -> dict:
        return [x for x in data if "user" in x["relationships"].keys() and x["relationships"]["user"]["data"]["id"] == user][0] # Think this is stupid? Thank Python and Patreon

    def _format_patrons(self, data:dict) -> List[dict]:
        res:List[dict] = []
        for i in data["included"]:
            if i["type"] == "user":
                user = self._get_user_info(data["data"], i["id"])
                try:
                    res.append({"discord": self._int_else(self._catch(i)), "tier": sorted(user["relationships"]["currently_entitled_tiers"]["data"], key=lambda x: int(x["id"]))[0]["id"]})
                except Exception: # If this happens something with the tier went wrong. This means they are no longer subscribed and I want to ignore that case
                    pass

        return res

    async def get_patrons(self) -> List[dict]:
        res = await self._make_request(self.url)
        valid = await self._get(res)
        # return valid
        return Patrons(valid)

class Events(commands.Cog):

    def __init__(self, client):
        self.client = client
        self.token = config['dbl']['token']
        self.topggpy = topgg.DBLClient(self.client, self.token)
        self.status.start()
        self.get_patrons.start()

    async def _post_guild_count(self):
        if self.client.user.id != 758031913788375090: # Not posting guild count with dev bot
            await self.topggpy.post_guild_count()

    def _get_differences(self, current:Patrons, saved:List[dict]) -> List[dict]:
        """Returns a list of dictionaries containing a user id and the badge to assign. If the badge is None, they will loose their premium badges"""
        new_patrons = [{"id": x["discord"], "badge": PATREON_TIERS[x["tier"]]["name"]} for x in current.patrons if x["discord"] not in set([x["id"] for x in saved])]
        removed_patrons = [{"id": x["discord"], "badge": None} for x in saved if x["id"] not in set(current.patrons)]
        different_badges = [{"id": x["discord"], "badge": PATREON_TIERS[x["tier"]]["name"]} for x in current if PATREON_TIERS[x["tier"]]["name"] not in saved["badges"]]
        return [*new_patrons, *removed_patrons, *different_badges]

    def _assign_badges(self, diff:List[dict]) -> None:
        for d in diff:
            user = teams.find_one({"id": d["id"]})
            premium_guilds = user["premium_guilds"]
            badges = user["badges"]
            for k, v in PATREON_TIERS.items():
                badges.remove(v["name"])

            if d["badge"] == None:
                badges.remove("premium")
                guilds.update_many({"id": {"$in": premium_guilds}}, {"$set": {"premium": False}})
                premium_guilds = []
            else:
                badges.append(d["badge"])
            teams.update_one({"id": d["id"]}, {"$set": {"badges": badges, "premium_guilds": premium_guilds}})

    @commands.Cog.listener()
    async def on_ready(self):
        print('------')
        print('Logged in as: ' + self.client.user.name + f" (ID: {self.client.user.id})")
        print('------')
        self.client.startup_datetime = datetime.now()

    @tasks.loop(hours=12)
    async def status(self):
        await p(self)
        await self._post_guild_count()

    @tasks.loop(minutes=5)
    async def get_patrons(self):
        current_patrons = await Patreon(self.client.session, config["patreon"], "5394117").get_patrons()
        saved_patrons = [x for x in teams.find({"badges": {"$in": ["premium"]}})]

        diff = self._get_differences(current_patrons, saved_patrons)
        self._assign_badges(diff)

    @status.before_loop
    async def before_status(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        #Changing the status
        await p(self)
        Guild.add_default(guild.id)

        general = find(lambda x: x.name == 'general',  guild.text_channels)
        if general and general.permissions_for(guild.me).send_messages:
            embed = discord.Embed.from_dict({
                'title': 'Hello {}!'.format(guild.name),
                'description': f'Hi, my name is Killua, thank you for choosing me! \n\nTo get some info about me, use `k!info`\n\nTo change the server prefix, use `k!prefix <new prefix>` (you need administrator perms for that\n\nFor more commands, use `k!help` to see every command\n\nPlease consider leaving feeback with `k!fb` as this helps me improve Killua',
                'color': 0x1400ff
            })
            await general.send(embed=embed)
        await self._post_guild_count()

    @commands.Cog.listener()
    async def on_connect(self):
        #Changing Killua's status
        await p(self)
        await self._post_guild_count()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        #Changing Killua's status
        await p(self)
        Guild(guild.id).delete()
        await self._post_guild_count()

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        # The purpose of this function is to kinda automate premium badges
        if not before.guild.id == 715358111472418908:
            return

        badges = teams.find_one({"id": after.id})["badges"]

        if 769622564648648744 in [x.id for x in before.roles] and not 769622564648648744 in [x.id for x in after.roles]:
            if set(["premium", "one_star_hunter"]).intersection(badges) == ["premium", "one_star_hunter"]:
                guilds.update_many({"id": {"$in": premium_guilds}}, {"$set": {"premium": False}}) # Remove all premium guilds assosiated with that account
                teams.update_one({"id": after.id}, {"$pull": {"badges": ["premium", "one_star_hunter"]}, "$set": {"premium_guilds": []}})
                

        if 769622564648648744 in [x.id for x in after.roles] and not 769622564648648744 in [x.id for x in before.roles]:
            tba = []
            (tba.append(x) for x in ["premium", "one_star_hunter"] if not x in badges)
            teams.update_one({"id": after.id}, {"$and": {"$push": {"badges": tba}}})


    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        #This handels the k!bug cooldown
        if isinstance(error, commands.CommandOnCooldown):
            m, s = divmod(round(ctx.command.get_cooldown_retry_after(ctx)), 60)

            return await ctx.send(f'Wait {m:02d} minutes and {s:02d} seconds before using the command again, thank you for helping to improve killua :3')

        if isinstance(error, commands.BotMissingPermissions):
            return await ctx.send(f"I don\'t have the required permissions to use this command! (`{', '.join(error.missing_perms)}`)")

        if isinstance(error, commands.MissingPermissions):
            return await ctx.send(f"You don\'t have the required permissions to use this command! (`{', '.join(error.missing_perms)}`)")

        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f"Seems like you missed a required argument for this command: `{str(error.param).split(':')[0]}`")

        if isinstance(error, commands.UserInputError):
            return await ctx.send(f"Seems like you provided invalid arguments for this command. This is how you use it: `{self.client.command_prefix(self.client, ctx.message)[2]}{ctx.command.usage}`")

        if isinstance(error, commands.NotOwner):
            return await ctx.send("Sorry, but you need to be the bot owner to use this command")

        if isinstance(error, commands.BadArgument):
            return await ctx.send(f"Could not process arguments. Here is the command should be used: {self.client.command_prefix(self.client, ctx.message)[2]}{ctx.command.usage}``")

        if isinstance(error, commands.CommandNotFound): # I don't care if this happens
            return 

        guild = ctx.guild.id if ctx.guild else "dm channel with "+ str(ctx.author.id)
        command = ctx.command.name if ctx.command else "Error didn't occur during a command"
        print('------------------------------------------')
        print(f'An error occured\nGuild id: {guild}\nCommand name: {command}\nError: {error}')
        print('------------------------------------------')

Cog = Events

def setup(client):
    client.add_cog(Events(client))
