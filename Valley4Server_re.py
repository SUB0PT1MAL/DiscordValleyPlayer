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
import glob
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
    """Improved idle checker with consolidated disconnect logic"""
    while True:
        current_time = time.time()
        for voice_client in bot.voice_clients:
            guild_id = voice_client.guild.id
            
            # Check for empty channel
            member_count = len([m for m in voice_client.channel.members if not m.bot])
            if member_count == 0:
                await safe_cleanup(guild_id, voice_client)
                continue
            
            # Check for idle timeout
            if guild_id in last_activity:
                idle_time = current_time - last_activity[guild_id]
                if idle_time > 300 and not voice_client.is_playing():
                    if guild_id not in queues or not queues[guild_id]:
                        await safe_cleanup(guild_id, voice_client)
        
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
async def play_single(ctx: commands.Context, *args):
    query = ' '.join(args)
    server_id = ctx.guild.id
    voice_state = ctx.author.voice

    if not await sense_checks(ctx, voice_state=voice_state):
        return

    if not ctx.guild.voice_client:
        try:
            await voice_state.channel.connect()
        except Exception as e:
            await ctx.send(f"Could not connect to voice channel: {str(e)}")
            return

    voice_client = ctx.guild.voice_client

    if server_id not in download_queues:
        download_queues[server_id] = asyncio.Queue()
        bot.loop.create_task(download_worker(server_id))

    try:
        if "playlist" in query:  # Check if the query is a playlist
            await ctx.send(f"Processing playlist: `{query}`...")
            bot.loop.create_task(playlists_worker(ctx, query, server_id, voice_client))
        else:
            with yt_dlp.YoutubeDL() as ydl:
                info = ydl.extract_info(query, download=False)

            await download_queues[server_id].put((info, ctx, voice_client))
            await ctx.send(f"Added `{info['title']}` to the queue.")
    except Exception as e:
        await ctx.send(f"Error processing query: {str(e)}")

async def playlists_worker(ctx: commands.Context, playlist_url: str, server_id: int, voice_client):
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True}) as ydl:
            playlist_info = ydl.extract_info(playlist_url, download=False)

            if "entries" not in playlist_info or not playlist_info["entries"]:
                await ctx.send("The playlist is empty or could not be processed.")
                return

            entries = playlist_info["entries"]
            valid_entries = []

            await ctx.send(f"Found {len(entries)} tracks. Validating availability...")

            # Validate entries and collect valid ones
            for entry in entries:
                try:
                    with yt_dlp.YoutubeDL() as ydl_detail:
                        info = ydl_detail.extract_info(entry["url"], download=False)
                        valid_entries.append(info)
                except Exception:
                    await ctx.send(f"⚠️ Skipping unavailable or blocked track: `{entry.get('title', 'Unknown')}`")

            if not valid_entries:
                await ctx.send("No valid tracks could be added from this playlist.")
                return

            await ctx.send(f"Adding {len(valid_entries)} valid tracks to the queue...")

            # Add valid tracks to the queue
            for info in valid_entries:
                await download_queues[server_id].put((info, ctx, voice_client))

            await ctx.send(f"Playlist processing complete. {len(valid_entries)} tracks added to the queue.")
    except Exception as e:
        await ctx.send(f"Error processing playlist: {str(e)}")

        
def get_voice_client_from_channel_id(channel_id: int):
    for voice_client in bot.voice_clients:
        if voice_client.channel.id == channel_id:
            return voice_client

async def after_track(error, connection, guild_id):
    """Streamlined track completion handler"""
    if error:
        print(f"Playback error in guild {guild_id}: {error}")
        return
        
    try:
        queue = queues[guild_id]
        path = queue.pop(0)[0]
        
        # Cleanup file if not needed
        with download_locks[guild_id]:
            if not any(path == track[0] for track in queue):
                try: os.remove(path)
                except FileNotFoundError: pass
        
        last_activity[guild_id] = time.time()
        
        # Play next or cleanup
        if queue:
            connection.play(
                discord.FFmpegOpusAudio(queue[0][0]),
                after=lambda error=None: after_track(error, connection, guild_id)
            )
        else:
            await safe_cleanup(guild_id)
            
    except Exception as e:
        print(f"Queue handling error: {e}")

async def download_worker(guild_id):
    queue = download_queues[guild_id]
    while True:
        try:
            track_info, ctx, connection = await asyncio.wait_for(queue.get(), timeout=30)
            if track_info is None:
                break
                
            try:
                await download_track(ctx, track_info, guild_id, connection)
            except Exception as e:
                await ctx.send(f"⚠️ Skipping unavailable track: {track_info.get('title', 'Unknown')}")
                print(f"Download error: {e}")
            finally:
                queue.task_done()
                
        except asyncio.TimeoutError:
            print(f"Download worker timeout for guild {guild_id}")
            break
        except Exception as e:
            print(f"Download worker error: {e}")
            continue

async def download_track(ctx, info, guild_id, connection):
    try:
        video_title = info.get('title', 'Unknown Title')
        webpage_url = info.get('webpage_url', info.get('url', ''))
        
        async def download_with_timeout():
            with yt_dlp.YoutubeDL({
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'paths': {'home': f'./dl/{guild_id}'},
                'outtmpl': '%(id)s.%(ext)s'
            }) as ydl:
                with download_locks[guild_id]:
                    return await asyncio.get_event_loop().run_in_executor(
                        None, ydl.download, [webpage_url]
                    )
                    
        try:
            await asyncio.wait_for(download_with_timeout(), timeout=60)
        except asyncio.TimeoutError:
            raise Exception("Download timed out")

        video_id = info['id']
        downloaded_files = glob.glob(f"./dl/{guild_id}/{video_id}.*")
        if not downloaded_files:
            raise FileNotFoundError(f"Download failed for {video_title}")
            
        path = downloaded_files[0]
        
        if guild_id not in queues:
            queues[guild_id] = GuildQueue()
        
        queue = queues[guild_id]
        queue.append((path, info))
        if not connection.is_playing() and len(queue) == 1:
            connection.play(
                discord.FFmpegOpusAudio(path),
                after=lambda error=None: after_track(error, connection, guild_id)
            )
            last_activity[guild_id] = time.time()
            
    except Exception as e:
        raise e
    return True

async def safe_cleanup(guild_id, voice_client=None):
    """Centralized cleanup function"""
    if guild_id in queues:
        queues.pop(guild_id)
    
    with download_locks[guild_id]:
        shutil.rmtree(f'./dl/{guild_id}/', ignore_errors=True)
    
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
    
    await cleanup_download_queue(guild_id)

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
        path = queue.pop(0)[0]  # Changed from pop() to pop(0) for consistency
        
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
            try:
                connection.play(
                    discord.FFmpegOpusAudio(next_track),
                    after=lambda error=None: after_track(error, connection, server_id)
                )
            except Exception as e:
                print(f"Error playing next track: {e}")
        else:
            queues.pop(server_id)
            
    except (IndexError, KeyError) as e:
        print(f"Queue error: {e}")
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