#!/usr/bin/env python3.10

import os
import discord
from discord.ext import commands

from music import MusicCog

TOKEN = os.environ['BOT_TOKEN']

# Create bot and add cogs
bot = commands.Bot(command_prefix='!')
bot.add_cog(MusicCog(bot))

@bot.event
async def on_ready():
    print(f'{bot.user} is ready!')

@bot.command()
async def valley(ctx, *, search):
    await ctx.invoke(bot.get_command('play'), search=search)

@bot.command() 
async def v(ctx, *, search):
    await ctx.invoke(bot.get_command('play'), search=search)
    
@bot.command()
async def valleyskip(ctx):
    await ctx.invoke(bot.get_command('skip'))
    
@bot.command() 
async def s(ctx):
    await ctx.invoke(bot.get_command('skip'))

@bot.command()  
async def valleyqueue(ctx):
    await ctx.invoke(bot.get_command('queue'))

@bot.command()
async def q(ctx):
    await ctx.invoke(bot.get_command('queue'))

bot.run(TOKEN)