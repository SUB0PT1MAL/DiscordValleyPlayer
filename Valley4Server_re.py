import discord
from discord.ext import commands, tasks
import yt_dlp
import asyncio
import os
import shutil
import time
from collections import defaultdict
from threading import Lock

# Configuration
TOKEN = os.getenv("BOT_TOKEN")
PREFIX = "!"
COLOR = 0xFF0000  # Default bot color
IDLE_TIMEOUT = 300  # 5 minutes

# Intents and bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Thread-safe data structures
class ThreadSafeDict(defaultdict):
    def __init__(self):
        super().__init__(dict)
        self._lock = Lock()

    def __getitem__(self, key):
        with self._lock:
            return super().__getitem__(key)

    def __setitem__(self, key, value):
        with self._lock:
            super().__setitem__(key, value)

    def pop(self, key, default=None):
        with self._lock:
            return super().pop(key, default)

# Global queues and locks
queues = ThreadSafeDict()  # {guild_id: [(file_path, metadata)]}
last_activity = ThreadSafeDict()  # {guild_id: last_active_timestamp}
download_locks = defaultdict(Lock)
download_queues = ThreadSafeDict()  # {guild_id: asyncio.Queue()}

# Utility functions
def get_voice_client(guild):
    return discord.utils.get(bot.voice_clients, guild=guild)

async def safe_disconnect(voice_client):
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()

async def cleanup_guild(guild_id):
    if guild_id in queues:
        queues.pop(guild_id)
    if guild_id in download_queues:
        queue = download_queues.pop(guild_id)
        await queue.put(None)
    shutil.rmtree(f"./dl/{guild_id}/", ignore_errors=True)

# Bot commands
@bot.command(name="valley", aliases=["v"])
async def play(ctx, *, query):
    voice_state = ctx.author.voice
    if not voice_state:
        return await ctx.send("You need to be in a voice channel to use this command.")

    if not ctx.guild.voice_client:
        await voice_state.channel.connect()

    voice_client = ctx.guild.voice_client
    guild_id = ctx.guild.id

    if guild_id not in download_queues:
        download_queues[guild_id] = asyncio.Queue()
        bot.loop.create_task(download_worker(guild_id))

    await ctx.send(f"Searching for `{query}`...")
    try:
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'paths': {'home': f'./dl/{guild_id}'},
            'outtmpl': '%(id)s.%(ext)s',
            'ignoreerrors': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if "youtu.be" not in query and "www.youtube" not in query:
                query = f"ytsearch:{query}"
            info = ydl.extract_info(query, download=False)
            if "entries" in info:  # Handle ytsearch results
                info = info["entries"][0]
            await download_queues[guild_id].put((info, ctx, voice_client))
    except Exception as e:
        await ctx.send(f"Error processing query: {e}")

@bot.command(name="skip", aliases=["s"])
async def skip(ctx):
    guild_id = ctx.guild.id
    voice_client = get_voice_client(ctx.guild)
    if not voice_client or not voice_client.is_playing():
        return await ctx.send("No track is currently playing.")

    voice_client.stop()
    await ctx.send("Skipped the current track.")

@bot.command(name="queue", aliases=["q"])
async def show_queue(ctx):
    guild_id = ctx.guild.id
    if guild_id not in queues or not queues[guild_id]:
        return await ctx.send("The queue is empty.")

    queue_list = [f"**{i+1}.** {track[1].get('title', 'Unknown')}" for i, track in enumerate(queues[guild_id])]
    embed = discord.Embed(title="Queue", description="\n".join(queue_list), color=COLOR)
    await ctx.send(embed=embed)

# Background tasks
@tasks.loop(seconds=30)
async def idle_checker():
    current_time = time.time()
    for voice_client in bot.voice_clients:
        guild_id = voice_client.guild.id
        if guild_id in last_activity:
            idle_time = current_time - last_activity[guild_id]
            if idle_time > IDLE_TIMEOUT and not voice_client.is_playing():
                await safe_disconnect(voice_client)
                await cleanup_guild(guild_id)

async def download_worker(guild_id):
    queue = download_queues[guild_id]
    while True:
        task = await queue.get()
        if task is None:
            break

        info, ctx, voice_client = task
        try:
            file_path = await download_track(info, guild_id)
            if guild_id not in queues:
                queues[guild_id] = []

            queues[guild_id].append((file_path, info))
            if not voice_client.is_playing():
                play_next_track(voice_client, guild_id)
        except Exception as e:
            await ctx.send(f"Failed to process track: {e}")

async def download_track(info, guild_id):
    ydl_opts = {
        "format": "bestaudio",
        "outtmpl": f"./dl/{guild_id}/%(id)s.%(ext)s",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([info["webpage_url"]])

    return f"./dl/{guild_id}/{info['id']}.m4a"

def play_next_track(voice_client, guild_id):
    if guild_id not in queues or not queues[guild_id]:
        return

    next_track = queues[guild_id].pop(0)[0]
    voice_client.play(
        discord.FFmpegPCMAudio(next_track),
        after=lambda e: play_next_track(voice_client, guild_id) if e is None else None
    )

# Event handlers
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    idle_checker.start()

if __name__ == "__main__":
    bot.run(TOKEN)
