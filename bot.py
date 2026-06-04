"""
Roblox Clothing Template Discord Bot
=====================================
Slash Commands:
  /clothingtemplateget <url>  — Get the PNG template of a Roblox clothing item
  /clothinginfo <url>         — Get info about a clothing item (no download)
  /help                       — Show all commands
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import io
import re
import asyncio
from PIL import Image
import os


# ── Bot setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ── Roblox API endpoints ─────────────────────────────────────────────────────

ROBLOX_ECONOMY_API   = "https://economy.roblox.com/v2/assets/{asset_id}/details"
ROBLOX_THUMBNAIL_API = "https://thumbnails.roblox.com/v1/assets"
ROBLOX_ASSET_URL     = "https://assetdelivery.roblox.com/v1/asset/?id={asset_id}"

CLOTHING_TYPES = {11: "👕 Shirt", 12: "👖 Pants"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def extract_asset_id(text: str) -> int | None:
    """Parse a Roblox catalog URL or raw asset ID into an integer."""
    m = re.search(r"roblox\.com/(?:catalog|library|games)/(\d+)", text)
    if m:
        return int(m.group(1))
    if text.strip().isdigit():
        return int(text.strip())
    return None


async def fetch_asset_details(session: aiohttp.ClientSession, asset_id: int) -> dict:
    url = ROBLOX_ECONOMY_API.format(asset_id=asset_id)
    async with session.get(url) as resp:
        if resp.status != 200:
            raise ValueError(f"Roblox API returned HTTP {resp.status}. Is this a valid asset ID?")
        return await resp.json()


async def fetch_texture_asset_id(session: aiohttp.ClientSession, asset_id: int) -> int:
    """Download the clothing RBXM/XML and extract the inner texture asset ID."""
    url = ROBLOX_ASSET_URL.format(asset_id=asset_id)
    headers = {"User-Agent": "Mozilla/5.0"}

    async with session.get(url, headers=headers, allow_redirects=True) as resp:
        if resp.status != 200:
            raise ValueError(f"Could not download clothing asset (HTTP {resp.status}).")
        raw = await resp.read()

    text = raw.decode("utf-8", errors="ignore")

    patterns = [
        r'<url>https?://(?:www\.)?roblox\.com/asset/\?id=(\d+)</url>',
        r'rbxassetid://(\d+)',
        r'asset/\?id=(\d+)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text)
        candidates = [int(m) for m in matches if int(m) != asset_id]
        if candidates:
            return candidates[0]

    raise ValueError(
        "Could not find the texture inside this clothing item.\n"
        "Make sure the URL points to a **Shirt** or **Pants** catalog item."
    )


async def download_png(session: aiohttp.ClientSession, asset_id: int) -> bytes:
    """Download and validate the PNG texture bytes."""
    url = ROBLOX_ASSET_URL.format(asset_id=asset_id)
    headers = {"User-Agent": "Mozilla/5.0"}

    async with session.get(url, headers=headers, allow_redirects=True) as resp:
        if resp.status != 200:
            raise ValueError(f"Could not download texture PNG (HTTP {resp.status}).")
        data = await resp.read()

    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
    except Exception:
        raise ValueError("The downloaded file is not a valid image.")

    return data


async def fetch_thumbnail(session: aiohttp.ClientSession, asset_id: int) -> str | None:
    """Fetch a small catalog preview thumbnail URL (best-effort)."""
    params = {
        "assetIds": asset_id,
        "size": "420x420",
        "format": "Png",
        "isCircular": "false",
    }
    try:
        async with session.get(ROBLOX_THUMBNAIL_API, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                items = data.get("data", [])
                if items and items[0].get("state") == "Completed":
                    return items[0].get("imageUrl")
    except Exception:
        pass
    return None


async def resolve_clothing(url: str, session: aiohttp.ClientSession):
    """
    Full pipeline: URL/ID → asset details → texture ID → PNG bytes + thumbnail.
    Returns (details, texture_id, png_bytes, thumbnail_url) or raises ValueError.
    """
    asset_id = extract_asset_id(url)
    if asset_id is None:
        raise ValueError(
            "Could not parse an asset ID from that input.\n"
            "Provide a catalog URL like `https://www.roblox.com/catalog/17760282956/...` "
            "or just the numeric ID."
        )

    details = await fetch_asset_details(session, asset_id)
    type_id = details.get("AssetTypeId", 0)

    if type_id not in CLOTHING_TYPES:
        raise ValueError(
            f"That asset is not a clothing item (detected type ID: `{type_id}`).\n"
            "Please link to a **Shirt** or **Pants** catalog item."
        )

    texture_id    = await fetch_texture_asset_id(session, asset_id)
    png_bytes     = await download_png(session, texture_id)
    thumbnail_url = await fetch_thumbnail(session, asset_id)

    return details, texture_id, png_bytes, thumbnail_url


# ── /clothingtemplateget ─────────────────────────────────────────────────────

@tree.command(
    name="clothingtemplateget",
    description="Download the PNG template of a Roblox clothing item."
)
@app_commands.describe(url="Roblox catalog URL or numeric asset ID")
async def clothingtemplateget(interaction: discord.Interaction, url: str):
    await interaction.response.defer(thinking=True)

    async with aiohttp.ClientSession() as session:
        try:
            details, texture_id, png_bytes, thumbnail_url = await resolve_clothing(url, session)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}")
            return

    asset_id     = details.get("AssetId", extract_asset_id(url))
    asset_name   = details.get("Name", f"Asset {asset_id}")
    creator_name = details.get("Creator", {}).get("Name", "Unknown")
    type_id      = details.get("AssetTypeId", 11)
    label        = CLOTHING_TYPES.get(type_id, "🎽 Clothing")

    safe_name  = re.sub(r"[^a-zA-Z0-9_\-]", "_", asset_name)[:60]
    file_name  = f"{safe_name}.png"
    attachment = discord.File(io.BytesIO(png_bytes), filename=file_name)

    embed = discord.Embed(
        title=f"{label} · {asset_name}",
        description=(
            f"**Creator:** {creator_name}\n"
            f"**Asset ID:** `{asset_id}`\n"
            f"**Texture ID:** `{texture_id}`\n\n"
            f"⬇️ Full-resolution template attached below"
        ),
        color=0x00B4D8,
        url=f"https://www.roblox.com/catalog/{asset_id}",
    )
    embed.set_footer(text="Roblox Clothing Bot  •  /clothingtemplateget")
    embed.set_image(url=f"attachment://{file_name}")
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    await interaction.followup.send(embed=embed, file=attachment)


# ── /clothinginfo ─────────────────────────────────────────────────────────────

@tree.command(
    name="clothinginfo",
    description="Show info about a Roblox clothing item without downloading the template."
)
@app_commands.describe(url="Roblox catalog URL or numeric asset ID")
async def clothinginfo(interaction: discord.Interaction, url: str):
    await interaction.response.defer(thinking=True)

    asset_id = extract_asset_id(url)
    if asset_id is None:
        await interaction.followup.send("❌ Could not parse an asset ID from that input.")
        return

    async with aiohttp.ClientSession() as session:
        try:
            details       = await fetch_asset_details(session, asset_id)
            thumbnail_url = await fetch_thumbnail(session, asset_id)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}")
            return

    type_id      = details.get("AssetTypeId", 0)
    asset_name   = details.get("Name", f"Asset {asset_id}")
    creator_name = details.get("Creator", {}).get("Name", "Unknown")
    description  = details.get("Description") or "No description."
    created      = (details.get("Created") or "")[:10]
    updated      = (details.get("Updated") or "")[:10]
    label        = CLOTHING_TYPES.get(type_id, f"🎽 Type {type_id}")

    embed = discord.Embed(
        title=f"{label} · {asset_name}",
        description=description[:200] + ("…" if len(description) > 200 else ""),
        color=0x5865F2,
        url=f"https://www.roblox.com/catalog/{asset_id}",
    )
    embed.add_field(name="Creator",   value=creator_name,        inline=True)
    embed.add_field(name="Asset ID",  value=f"`{asset_id}`",     inline=True)
    embed.add_field(name="Type",      value=label,               inline=True)
    embed.add_field(name="Created",   value=created or "N/A",    inline=True)
    embed.add_field(name="Updated",   value=updated or "N/A",    inline=True)
    embed.add_field(
        name="Get Template",
        value=f"`/clothingtemplateget url:{asset_id}`",
        inline=False,
    )
    embed.set_footer(text="Roblox Clothing Bot  •  /clothinginfo")
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    await interaction.followup.send(embed=embed)


# ── /help ─────────────────────────────────────────────────────────────────────

@tree.command(name="help", description="Show all Roblox Clothing Bot commands.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="👕 Roblox Clothing Bot — Commands",
        color=0x57F287,
    )
    embed.add_field(
        name="</clothingtemplateget>",
        value=(
            "Download the **PNG template** of any Roblox Shirt or Pants item.\n"
            "Usage: `/clothingtemplateget url:<catalog URL or asset ID>`\n"
            "Example: `/clothingtemplateget url:17760282956`"
        ),
        inline=False,
    )
    embed.add_field(
        name="</clothinginfo>",
        value=(
            "Show **info** about a clothing item (creator, type, dates) without downloading.\n"
            "Usage: `/clothinginfo url:<catalog URL or asset ID>`"
        ),
        inline=False,
    )
    embed.add_field(
        name="</help>",
        value="Show this help message.",
        inline=False,
    )
    embed.set_footer(text="Tip: you can paste a full catalog URL or just the numeric ID.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Startup ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅  Bot ready: {bot.user} (ID: {bot.user.id})")
    print(f"    Commands synced globally:")
    print(f"    /clothingtemplateget  /clothinginfo  /help")


if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing DISCORD_TOKEN environment variable.\n"
            "  Windows:  set DISCORD_TOKEN=your_token\n"
            "  Mac/Linux: export DISCORD_TOKEN=your_token"
        )
    bot.run(token)
