#!/usr/bin/env python3.10

import discord
from discord.ext import commands
import yt_dlp
import urllib
import asyncio
import threading
import os
import shutil
import sys
import subprocess as sp
#from dotenv import load_dotenv

#load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
PRINT_STACK_TRACE = True

try:
    COLOR = int('16711680')
except ValueError:
    print('the BOT_COLOR in .env is not a valid hex color')
    print('using default color ff0000')
    COLOR = 0xff0000

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)
queues = {} # {server_id: [(vid_file, info), ...]}

def main():
    if TOKEN is None:
        return ("No token provided. Please create a .env file containing the token.")
    try: bot.run(TOKEN)
    except discord.PrivilegedIntentsRequired as error:
        return error

@bot.command(name='valleyqueue', aliases=['q'])
async def queue(ctx: commands.Context, *args):
    try: queue = queues[ctx.guild.id]
    except KeyError: queue = None
    if queue == None:
        await ctx.send('the bot isn\'t playing anything')
    else:
        title_str = lambda val: '‣ %s\n\n' % val[1] if val[0] == 0 else '**%2d:** %s\n' % val
        queue_str = ''.join(map(title_str, enumerate([i[1]["title"] for i in queue])))
        embedVar = discord.Embed(color=COLOR)
        embedVar.add_field(name='Now playing:', value=queue_str)
        await ctx.send(embed=embedVar)
    if not await sense_checks(ctx):
        return

@bot.command(name='valleyskip', aliases=['s'])
async def skip(ctx: commands.Context, *args):
    try: queue_length = len(queues[ctx.guild.id])
    except KeyError: queue_length = 0
    if queue_length <= 0:
        await ctx.send('the bot isn\'t playing anything')
    if not await sense_checks(ctx):
        return

    try: n_skips = int(args[0])
    except IndexError:
        n_skips = 1
    except ValueError:
        if args[0] == 'all': n_skips = queue_length
        else: n_skips = 1
    if n_skips == 1:
        message = 'skipping track'
    elif n_skips < queue_length:
        message = f'skipping `{n_skips}` of `{queue_length}` tracks'
    else:
        message = 'skipping all tracks'
        n_skips = queue_length
    await ctx.send(message)

    voice_client = get_voice_client_from_channel_id(ctx.author.voice.channel.id)
    for _ in range(n_skips - 1):
        queues[ctx.guild.id].pop(0)
    voice_client.stop()

@bot.command(name='valley', aliases=['v'])
async def play(ctx: commands.Context, *args):
    print("bot summoned")
    voice_state = ctx.author.voice
    if not await sense_checks(ctx, voice_state=voice_state):
        return

    query = ' '.join(args)
    will_need_search = not urllib.parse.urlparse(query).scheme
    server_id = ctx.guild.id

    # Try to connect to voice first before downloading
    try:
        try: 
            connection = await voice_state.channel.connect(timeout=60.0)  # Increased timeout
        except discord.ClientException: 
            connection = get_voice_client_from_channel_id(voice_state.channel.id)
        
        if not connection:
            await ctx.send("Failed to connect to voice channel. Please try again.")
            return
            
        await ctx.send(f'looking for `{query}`...')
        
        with yt_dlp.YoutubeDL({
            'format': 'worstaudio',
            'source_address': '0.0.0.0',
            'default_search': 'ytsearch',
            'outtmpl': '%(id)s.%(ext)s',
            'noplaylist': True,
            'allow_playlist_files': False,
            'paths': {'home': f'./dl/{server_id}'}
        }) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info:
                info = info['entries'][0]
                
            await ctx.send('downloading ' + (f'https://youtu.be/{info["id"]}' if will_need_search else f'`{info["title"]}`'))
            ydl.download([query])
            
            path = f'./dl/{server_id}/{info["id"]}.{info["ext"]}'
            try: 
                queues[server_id].append((path, info))
            except KeyError:  # first in queue
                queues[server_id] = [(path, info)]
                connection.play(
                    discord.FFmpegOpusAudio(path), 
                    after=lambda error=None, connection=connection, server_id=server_id:
                        after_track(error, connection, server_id)
                )
                
    except TimeoutError:
        await ctx.send("Failed to connect to voice channel (timeout). Please try again.")
        try:
            await connection.disconnect()
        except:
            pass
    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")
        print(f"Error in play command: {str(e)}")

def get_voice_client_from_channel_id(channel_id: int):
    for voice_client in bot.voice_clients:
        if voice_client.channel.id == channel_id:
            return voice_client

def after_track(error, connection, server_id):
    if error is not None:
        print(error)
    try: 
        path = queues[server_id].pop(0)[0]
    except KeyError: 
        return # probably got disconnected
    
    # Check if file needs to be removed
    if path not in [i[0] for i in queues[server_id]]: # check that the same video isn't queued multiple times
        try: os.remove(path)
        except FileNotFoundError: pass
    
    # Play next track if queue isn't empty
    try: 
        connection.play(discord.FFmpegOpusAudio(queues[server_id][0][0]), 
                       after=lambda error=None, connection=connection, server_id=server_id:
                             after_track(error, connection, server_id))
    except IndexError: # that was the last item in queue
        queues.pop(server_id) # remove empty queue
        # Only disconnect if channel is empty
        if len([m for m in connection.channel.members if not m.bot]) == 0:
            asyncio.run_coroutine_threadsafe(safe_disconnect(connection), bot.loop).result()

async def safe_disconnect(connection):
    if not connection.is_playing():
        await connection.disconnect()
        
async def sense_checks(ctx: commands.Context, voice_state=None) -> bool:
    if voice_state is None: voice_state = ctx.author.voice 
    if voice_state is None:
        await ctx.send('you have to be in a vc to use this command')
        return False

    if bot.user.id not in [member.id for member in ctx.author.voice.channel.members] and ctx.guild.id in queues.keys():
        await ctx.send('you have to be in the same vc as the bot to use this command')
        return False
    return True

@bot.event
async def on_voice_state_update(member: discord.User, before: discord.VoiceState, after: discord.VoiceState):
    if member != bot.user:
        return
    if before.channel is None and after.channel is not None: # joined vc
        return
    if before.channel is not None and after.channel is None: # disconnected from vc
        # clean up
        server_id = before.channel.guild.id
        try: queues.pop(server_id)
        except KeyError: pass
        try: shutil.rmtree(f'./dl/{server_id}/')
        except FileNotFoundError: pass


@bot.event
async def on_command_error(event: str, *args, **kwargs):
    """Improved error handling without relying on external restart script"""
    type_, value, traceback = sys.exc_info()
    error_message = f'{type_}: {value} raised during {event}, {args=}, {kwargs=}'
    print(error_message)  # Log the error
    
    # Handle specific errors
    if isinstance(value, TimeoutError):
        # Handle timeout errors specifically
        channel_id = args[0].channel.id if args and hasattr(args[0], 'channel') else 'Unknown'
        print(f"Timeout occurred in channel {channel_id}")
    elif isinstance(value, discord.ClientException):
        print(f"Discord client exception: {value}")
    
    # Don't try to restart, just log the error
    sys.stderr.write(error_message + '\n')

def get_voice_client_from_channel_id(channel_id: int):
    """Enhanced voice client getter with additional checks"""
    for voice_client in bot.voice_clients:
        if voice_client.channel.id == channel_id:
            if voice_client.is_connected():
                return voice_client
    return None

@bot.event
async def on_ready():
    print(f'logged in successfully as {bot.user.name}')
    print("stack trace: ", PRINT_STACK_TRACE)
    print("bot color: ",  COLOR)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemError as error:
        if PRINT_STACK_TRACE:
            raise
        else:
            print(error)