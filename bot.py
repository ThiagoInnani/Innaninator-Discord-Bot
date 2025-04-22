import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import yt_dlp
from collections import deque
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import random


load_dotenv()
TOKEN = os.getenv('discord_token')

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
SONG_QUEUES = {}
LAST_USER_LEFT_TIME = {}

# Busca assíncrona com yt_dlp
async def search_ytdlp_async(query, ydl_options):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_options).extract_info(query, download=False))

# Toca próxima música
async def play_next_song(voice_client, guild_id, text_channel):
    queue = SONG_QUEUES.get(guild_id)
    if not queue or not queue:
        await text_channel.send("✅ Fila finalizada.")
        await voice_client.disconnect()
        return

    url, title = queue.popleft()

    # Transmitir o áudio
    ffmpeg_options = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn'
    }

    source = await discord.FFmpegOpusAudio.from_probe(url, **ffmpeg_options)
    voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, text_channel), bot.loop))

    await text_channel.send(f"🎵 Tocando agora: **{title}**")

@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Comandos sincronizados: {len(synced)}")
    except Exception as e:
        print(f"Erro ao sincronizar comandos: {e}")
    auto_disconnect_check.start()

@bot.tree.command(name="play", description="Toca uma música ou playlist do YouTube")
@app_commands.describe(song_query="Link do YouTube ou nome da música")
async def play(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer()

    voice_channel = getattr(interaction.user.voice, "channel", None)
    if voice_channel is None:
        await interaction.followup.send("Você precisa estar em um canal de voz para usar este comando.")
        return

    voice_client = interaction.guild.voice_client
    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    ydl_options = {
        "format": "bestaudio[abr<=96]/bestaudio",
        "noplaylist": False,
        "extract_flat": False,
        "quiet": True,
        "nocheckcertificate": True,
        "ignoreerrors": True,
        "default_search": "ytsearch",
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
    }

    query = song_query.strip()
    if not query.startswith("http"):
        query = f"ytsearch1:{query}"

    results = await search_ytdlp_async(query, ydl_options)
    entries = results.get("entries") if "entries" in results else [results]
    if not entries:
        await interaction.followup.send("Nenhum resultado encontrado.")
        return

    guild_id = str(interaction.guild_id)
    if guild_id not in SONG_QUEUES:
        SONG_QUEUES[guild_id] = deque()

    queue = SONG_QUEUES[guild_id]
    added_titles = []

    for entry in entries:
        if not entry:
            continue
        title = entry.get("title", "Sem título")
        audio_url = entry.get("url")
        if not audio_url:
            continue
        queue.append((audio_url, title))
        added_titles.append(title)

    if not voice_client.is_playing() and not voice_client.is_paused():
        await play_next_song(voice_client, guild_id, interaction.channel)

    if len(added_titles) == 1:
        await interaction.followup.send(f"🎶 Adicionada: **{added_titles[0]}**")
    else:
        preview = "\n".join([f"- {t}" for t in added_titles[:3]])
        more = f"\n...e mais {len(added_titles)-3} músicas adicionadas!" if len(added_titles) > 3 else ""
        await interaction.followup.send(f"🎵 Playlist adicionada:\n{preview}{more}")

@bot.tree.command(name="queue", description="Mostra a fila de músicas")
async def queue(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    queue = SONG_QUEUES.get(guild_id, [])
    if not queue:
        await interaction.response.send_message("A fila está vazia.")
        return
    message = "\n".join([f"{i+1}. {title}" for i, (_, title) in enumerate(queue)])
    await interaction.response.send_message(f"🎶 Fila atual:\n{message}")

@bot.tree.command(name="pause", description="Pausa a música que está atualmente tocando.")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if the bot is in a voice channel
    if voice_client is None:
        return await interaction.response.send_message("Não estou em um canal de voz.")

    # Check if something is actually playing
    if not voice_client.is_playing():
        return await interaction.response.send_message("Nada está tocando atualmente.")
    
    # Pause the track
    voice_client.pause()
    await interaction.response.send_message("Música pausada!")

@bot.tree.command(name="resume", description="Despausa a música.")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if the bot is in a voice channel
    if voice_client is None:
        return await interaction.response.send_message("Não estou em um canal de voz.")

    # Check if it's actually paused
    if not voice_client.is_paused():
        return await interaction.response.send_message("Não estou pausado agora.")
    
    # Resume playback
    voice_client.resume()
    await interaction.response.send_message("Música despausada!")

@bot.tree.command(name="shuffle", description="Embaralha as músicas na fila.")
async def shuffle(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)

    if guild_id not in SONG_QUEUES or len(SONG_QUEUES[guild_id]) < 2:
        await interaction.response.send_message("Não há músicas suficientes na fila para embaralhar.")
        return

    queue = SONG_QUEUES[guild_id]
    random.shuffle(queue)
    await interaction.response.send_message("Shuffled the song queue!")

@bot.tree.command(name="skip", description="Pula a música atual")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await interaction.response.send_message("⏭️ Pulando música...")
    else:
        await interaction.response.send_message("Não há nenhuma música tocando.")

@bot.tree.command(name="stop", description="Para a música e limpa a fila")
async def stop(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    SONG_QUEUES[guild_id] = deque()
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
    await interaction.response.send_message("⏹️ Música parada e fila limpa.")

@bot.tree.command(name="np", description="Mostra a música tocando agora")
async def np(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        guild_id = str(interaction.guild_id)
        current = SONG_QUEUES.get(guild_id)
        if current:
            await interaction.response.send_message(f"🎧 Tocando agora: **{current[0][1]}**")
        else:
            await interaction.response.send_message("Nenhuma informação sobre a música atual.")
    else:
        await interaction.response.send_message("Não há nenhuma música tocando.")

@bot.tree.command(name="delete", description="Remove a música N da fila")
@app_commands.describe(position="Posição da música (1 = primeiro)")
async def delete(interaction: discord.Interaction, position: int):
    guild_id = str(interaction.guild_id)
    queue = SONG_QUEUES.get(guild_id, deque())
    if 1 <= position <= len(queue):
        removed = queue[position-1]
        del queue[position-1]
        await interaction.response.send_message(f"❌ Música removida: **{removed[1]}**")
    else:
        await interaction.response.send_message("Posição inválida.")

@bot.tree.command(name="forceremove", description="Remove todas as músicas com um título específico")
@app_commands.describe(keyword="Palavra-chave no título da música")
async def forceremove(interaction: discord.Interaction, keyword: str):
    guild_id = str(interaction.guild_id)
    queue = SONG_QUEUES.get(guild_id, deque())
    original_len = len(queue)
    queue = deque([item for item in queue if keyword.lower() not in item[1].lower()])
    SONG_QUEUES[guild_id] = queue
    removed = original_len - len(queue)
    await interaction.response.send_message(f"🔍 Removidas {removed} músicas contendo: **{keyword}**")

@tasks.loop(seconds=30)
async def auto_disconnect_check():
    for guild in bot.guilds:
        voice_client = guild.voice_client
        if voice_client and voice_client.is_connected():
            if len(voice_client.channel.members) == 1:
                guild_id = str(guild.id)
                if guild_id not in LAST_USER_LEFT_TIME:
                    LAST_USER_LEFT_TIME[guild_id] = datetime.utcnow()
                elif datetime.utcnow() - LAST_USER_LEFT_TIME[guild_id] > timedelta(minutes=5):
                    await voice_client.disconnect()
                    print(f"Desconectado de {guild.name} por inatividade.")
            else:
                LAST_USER_LEFT_TIME[guild.id] = datetime.utcnow()

bot.run(TOKEN)
