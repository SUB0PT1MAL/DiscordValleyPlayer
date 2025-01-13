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
import time
from threading import Lock
from collections import defaultdict
#from dotenv import load_dotenv

# Setting up thread safe dictionaries and queues

# dictionaries
class ThreadSafeDict:
    def __init__(self):
        self._dict = {}
        self._lock = Lock()
    
    def __getitem__(self, key):
        with self._lock:
            return self._dict[key]
    
    def __setitem__(self, key, value):
        with self._lock:
            self._dict[key] = value
    
    def __delitem__(self, key):
        with self._lock:
            del self._dict[key]
    
    def pop(self, key, default=None):
        with self._lock:
            return self._dict.pop(key, default)
    
    def get(self, key, default=None):
        with self._lock:
            return self._dict.get(key, default)
    
    def __contains__(self, key):
        with self._lock:
            return key in self._dict
            
    def keys(self):
        with self._lock:
            # Return a list instead of a view to avoid threading issues
            return list(self._dict.keys())
            
    def values(self):
        with self._lock:
            return list(self._dict.values())
            
    def items(self):
        with self._lock:
            return list(self._dict.items())
            
    def clear(self):
        with self._lock:
            self._dict.clear()
            
    def copy(self):
        with self._lock:
            return self._dict.copy()
            
    def __len__(self):
        with self._lock:
            return len(self._dict)

    def __iter__(self):
        with self._lock:
            # Return a list to avoid threading issues during iteration
            return iter(list(self._dict))

# queue
class GuildQueue:
    def __init__(self):
        self.queue = []
        self.lock = Lock()
    
    def append(self, item):
        with self.lock:
            self.queue.append(item)
    
    def pop(self, index=0):
        with self.lock:
            if self.queue:
                return self.queue.pop(index)
            raise IndexError("pop from empty queue")
    
    def __len__(self):
        with self.lock:
            return len(self.queue)
    
    def __getitem__(self, index):
        with self.lock:
            return self.queue[index]
    
    def __bool__(self):
        with self.lock:
            return bool(self.queue)

# Initializing thread safe dictionaries and queues
queues = ThreadSafeDict()  # {server_id: GuildQueue()}
last_activity = ThreadSafeDict()  # {guild_id: timestamp}
download_locks = defaultdict(Lock)  # {guild_id: Lock()}
download_queues = ThreadSafeDict()  # {guild_id: asyncio.Queue()}

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

def main():
    if TOKEN is None:
        return ("No token provided. Please create a .env file containing the token.")
    try: bot.run(TOKEN)
    except discord.PrivilegedIntentsRequired as error:
        return error

async def check_idle_voice_clients():
    """Thread-safe idle checker"""
    while True:
        current_time = time.time()
        for voice_client in bot.voice_clients:
            guild_id = voice_client.guild.id
            
            # Thread-safe member count check
            member_count = len([m for m in voice_client.channel.members if not m.bot])
            
            if member_count == 0:
                await voice_client.disconnect()
                if guild_id in queues:
                    queues.pop(guild_id)
                    with download_locks[guild_id]:
                        shutil.rmtree(f'./dl/{guild_id}/', ignore_errors=True)
                continue
            
            # Thread-safe activity check
            if guild_id in last_activity:
                idle_time = current_time - last_activity[guild_id]
                if idle_time > 300 and not voice_client.is_playing():
                    if guild_id not in queues or not queues[guild_id]:
                        await voice_client.disconnect()
                        if guild_id in queues:
                            queues.pop(guild_id)
                            with download_locks[guild_id]:
                                shutil.rmtree(f'./dl/{guild_id}/', ignore_errors=True)
        
        await asyncio.sleep(30)

@bot.command(name='valleyqueue', aliases=['q'])
async def queue(ctx: commands.Context, *args):
    try: 
        if ctx.guild.id not in queues or not queues[ctx.guild.id]:
            await ctx.send('the bot isn\'t playing anything')
            return
        queue = queues[ctx.guild.id]
        
        title_str = lambda val: '‣ %s\n\n' % val[1] if val[0] == 0 else '**%2d:** %s\n' % val
        queue_str = ''.join(map(title_str, enumerate([i[1]["title"] for i in queue])))
        embedVar = discord.Embed(color=COLOR)
        embedVar.add_field(name='Now playing:', value=queue_str)
        await ctx.send(embed=embedVar)
        
    except Exception as e:
        await ctx.send(f'Error displaying queue: {str(e)}')
    
    if not await sense_checks(ctx):
        return

@bot.command(name='valleyskip', aliases=['s'])
async def skip(ctx: commands.Context, *args):
    if ctx.guild.id not in queues or not queues[ctx.guild.id]:
        await ctx.send('the bot isn\'t playing anything')
        return
        
    queue = queues[ctx.guild.id]
    queue_length = len(queue)
    
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
        queue.pop(0)
    voice_client.stop()

@bot.command(name='valley', aliases=['v'])
async def play(ctx: commands.Context, *args):
    """Add track(s) to the download and playback queue."""
    query = ' '.join(args)
    server_id = ctx.guild.id
    voice_state = ctx.author.voice

    if not await sense_checks(ctx, voice_state=voice_state):
        return

    if server_id not in download_queues:
        download_queues[server_id] = asyncio.Queue()
        bot.loop.create_task(download_worker(server_id))

    try:
        await ctx.send(f"Searching for `{query}`...")
        with yt_dlp.YoutubeDL({'default_search': 'ytsearch', 'extract_flat': False}) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info:  # Playlist
                await ctx.send(f"Found playlist with {len(info['entries'])} tracks")
                for entry in info['entries']:
                    if entry:
                        await download_queues[server_id].put((entry, ctx, ctx.guild.voice_client))
            else:  # Single track
                await download_queues[server_id].put((info, ctx, ctx.guild.voice_client))
    except Exception as e:
        await ctx.send(f"Error processing query: {str(e)}")

async def process_playlist_tracks(ctx, ydl, entries, server_id, connection, will_need_search):
    """Process remaining playlist tracks in the background"""
    for entry in entries:
        if entry:
            await process_track(ctx, ydl, entry, server_id, connection, will_need_search, is_playlist=True)
        await asyncio.sleep(0.5)  # Small delay to prevent rate limiting

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

async def download_worker(guild_id):
    """Worker to handle downloading for a specific guild."""
    queue = download_queues[guild_id]
    while True:
        track_info, ctx, connection = await queue.get()  # Get the next track to download
        if track_info is None:  # Sentinel value to stop the worker
            break
        try:
            await download_track(ctx, track_info, guild_id, connection)
        except Exception as e:
            await ctx.send(f"Failed to download track: {str(e)}")
        queue.task_done()

async def download_track(ctx, info, guild_id, connection):
    """Download a track and add it to the playback queue."""
    try:
        with yt_dlp.YoutubeDL({
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'paths': {'home': f'./dl/{guild_id}'},
            'outtmpl': '%(id)s.%(ext)s',
            'ignoreerrors': True,
        }) as ydl:
            ydl.download([info['webpage_url']])
        
        path = f'./dl/{guild_id}/{info["id"]}.{info["ext"]}'
        if guild_id not in queues:
            queues[guild_id] = GuildQueue()
        
        # Add to playback queue
        queues[guild_id].append((path, info))
        if not connection.is_playing() and len(queues[guild_id]) == 1:
            connection.play(
                discord.FFmpegOpusAudio(path),
                after=lambda error=None: after_track(error, connection, guild_id)
            )
    except Exception as e:
        raise e

async def cleanup_download_queue(guild_id):
    """Stop and clean up the download queue for a guild."""
    if guild_id in download_queues:
        queue = download_queues.pop(guild_id)
        await queue.put(None)

async def process_track(ctx, ydl, info, server_id, connection, will_need_search, is_playlist=False):
    """Process a single track with error handling"""
    try:
        video_id = info.get('id', 'Unknown')
        video_title = info.get('title', 'Unknown Title')
        video_url = f'https://youtu.be/{video_id}' if will_need_search else info.get('webpage_url', '')

        await ctx.send('downloading ' + (video_url if will_need_search else f'`{video_title}`'))
        
        with download_locks[server_id]:
            ydl.download([info['webpage_url'] if 'webpage_url' in info else info['url']])
        
        path = f'./dl/{server_id}/{info["id"]}.{info["ext"]}'
        
        if server_id not in queues:
            queues[server_id] = GuildQueue()
        
        queue = queues[server_id]
        queue.append((path, info))
        
        # Start playing immediately if this is the first track or not a playlist
        if not connection.is_playing() and (not is_playlist or len(queue) == 1):
            first_track = queue[0][0]
            connection.play(
                discord.FFmpegOpusAudio(first_track),
                after=lambda error=None, connection=connection, server_id=server_id:
                    after_track(error, connection, server_id)
            )
            
    except Exception as e:
        error_message = str(e)
        if "copyright grounds" in error_message or "Video unavailable" in error_message:
            await ctx.send(f"⚠️ Could not download `{video_title}` ({video_url})\nReason: Video is blocked or unavailable in your country.")
        else:
            await ctx.send(f"⚠️ Error downloading `{video_title}`: {error_message}")
        return False
    return True

def after_track(error, connection, server_id):
    """Thread-safe track completion handling"""
    if error is not None:
        print(f"Error in guild {server_id}: {error}")
        return
        
    try:
        queue = queues[server_id]
        path = queue.pop()[0]
        
        # Thread-safe file cleanup
        with download_locks[server_id]:
            if not any(path == track[0] for track in queue):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
        
        # Update activity timestamp
        last_activity[server_id] = time.time()
        
        # Play next track if available
        if queue:
            next_track = queue[0][0]
            connection.play(
                discord.FFmpegOpusAudio(next_track),
                after=lambda error=None, connection=connection, server_id=server_id:
                    after_track(error, connection, server_id)
            )
        else:
            queues.pop(server_id)
            
    except (IndexError, KeyError):
        # Queue is empty or guild was removed
        pass

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
    print("bot color: ", COLOR)
    # Start the idle checker
    bot.loop.create_task(check_idle_voice_clients())

if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemError as error:
        if PRINT_STACK_TRACE:
            raise
        else:
            print(error)