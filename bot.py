"""PriceWatch — an autonomous Discord price-drop bot.

Checks watched products on a fixed interval and posts an alert when a price
drops to a new low (or first crosses a target). Manage the watchlist with the
``/watch``, ``/list_watches``, ``/unwatch`` and ``/checknow`` slash commands.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import alerts
import sources
import storage

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pricewatch")


def _int_env(name: str) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = _int_env("DISCORD_CHANNEL_ID")
# Optional: set your server (guild) ID to register slash commands instantly.
# Without it, commands sync globally and can take up to an hour to appear.
GUILD_ID = _int_env("DISCORD_GUILD_ID")
CHECK_INTERVAL_HOURS = float(os.getenv("CHECK_INTERVAL_HOURS") or 1)

bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
tree = bot.tree

_CURRENCY_SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "C$", "AUD": "A$"}


def format_price(price: Optional[float], currency: str = "USD") -> str:
    if price is None:
        return "—"
    symbol = _CURRENCY_SYMBOLS.get((currency or "USD").upper())
    return f"{symbol}{price:,.2f}" if symbol else f"{price:,.2f} {currency}"


# --------------------------------------------------------------------------- #
# Checking + alerting
# --------------------------------------------------------------------------- #


async def _check_all() -> list[tuple[dict, sources.PriceResult, list[str]]]:
    """Fetch every watched item, update price state, and return the outcomes."""
    items = storage.load()
    outcomes: list[tuple[dict, sources.PriceResult, list[str]]] = []
    updated: list[dict] = []

    for item in items:
        fetch = sources.resolve(item.get("source", ""))
        if fetch is None:
            outcomes.append((item, sources.PriceResult(ok=False, error="unknown source"), []))
            continue
        try:
            result = await fetch(item["identifier"])
        except Exception as exc:  # noqa: BLE001 — one bad item shouldn't sink the loop
            log.exception("Fetch failed for %s", item.get("id"))
            outcomes.append((item, sources.PriceResult(ok=False, error=str(exc)), []))
            continue

        reasons: list[str] = []
        if result.ok and result.price is not None:
            reasons = alerts.evaluate(item, result.price)
            updated.append(item)
        outcomes.append((item, result, reasons))

    if updated:
        _persist_price_updates(updated)
    return outcomes


def _persist_price_updates(updated: list[dict]) -> None:
    """Merge fresh price state back into the on-disk list.

    We re-read the file so a ``/watch`` or ``/unwatch`` that landed while we were
    fetching (network I/O can take seconds) isn't clobbered.
    """
    current = storage.load()
    by_id = {it.get("id"): it for it in current}
    for item in updated:
        target = by_id.get(item.get("id"))
        if target is not None:
            target["last_price"] = item.get("last_price")
            target["lowest_price"] = item.get("lowest_price")
            target["alerted_target"] = item.get("alerted_target", False)
    storage.save(current)


def build_alert_embed(item: dict, result: sources.PriceResult, reasons: list[str]) -> discord.Embed:
    link = result.url or (item["identifier"] if item.get("source") == "url" else None)
    embed = discord.Embed(
        title=f"📉 Price drop: {item['label']}",
        description=result.title or None,
        color=discord.Color.green(),
        url=link if link and link.startswith(("http://", "https://")) else None,
    )
    embed.add_field(name="Now", value=format_price(result.price, result.currency), inline=True)
    if item.get("target_price") is not None:
        embed.add_field(name="Target", value=format_price(item["target_price"], result.currency), inline=True)
    if item.get("lowest_price") is not None:
        embed.add_field(name="Lowest seen", value=format_price(item["lowest_price"], result.currency), inline=True)

    why = []
    if "target" in reasons:
        why.append("hit your target price")
    if "low" in reasons:
        why.append("new lowest price")
    if why:
        embed.set_footer(text="Alert: " + " · ".join(why))
    return embed


async def _broadcast_alerts(outcomes: list[tuple[dict, sources.PriceResult, list[str]]]) -> None:
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        if any(reasons for _, _, reasons in outcomes):
            log.warning("Channel %s not found — cannot post alerts", CHANNEL_ID)
        return
    for item, result, reasons in outcomes:
        if not reasons:
            continue
        try:
            await channel.send(embed=build_alert_embed(item, result, reasons))
        except discord.DiscordException:
            log.exception("Failed to post alert for %s", item.get("id"))


# --------------------------------------------------------------------------- #
# Background loop
# --------------------------------------------------------------------------- #


@tasks.loop(hours=CHECK_INTERVAL_HOURS)
async def price_loop() -> None:
    log.info("Running scheduled price check")
    await _broadcast_alerts(await _check_all())


@price_loop.before_loop
async def _before_price_loop() -> None:
    await bot.wait_until_ready()


@bot.event
async def on_ready() -> None:
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
            log.info("Synced %d command(s) to guild %s (appear immediately)", len(synced), GUILD_ID)
        else:
            synced = await tree.sync()
            log.info("Synced %d global command(s) (can take up to 1h to appear)", len(synced))
    except Exception:  # noqa: BLE001
        log.exception("Failed to sync slash commands")
    if not price_loop.is_running():
        price_loop.start()
    log.info("PriceWatch online as %s — checking every %g h", bot.user, CHECK_INTERVAL_HOURS)


# --------------------------------------------------------------------------- #
# Slash commands
# --------------------------------------------------------------------------- #


@tree.command(name="watch", description="Start watching a product for price drops")
@app_commands.describe(
    source="Where to read the price from",
    identifier="Best Buy SKU (e.g. 6565581) or a full product URL",
    label="A friendly name for this item",
    target_price="Optional: alert when the price is at or below this number",
)
@app_commands.choices(
    source=[
        app_commands.Choice(name="Best Buy (SKU)", value="bestbuy"),
        app_commands.Choice(name="Generic URL", value="url"),
    ]
)
async def watch_cmd(
    interaction: discord.Interaction,
    source: app_commands.Choice[str],
    identifier: str,
    label: str,
    target_price: Optional[float] = None,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    fetch = sources.resolve(source.value)
    result = await fetch(identifier) if fetch else sources.PriceResult(ok=False, error="unknown source")

    item = {
        "id": uuid4().hex[:8],
        "source": source.value,
        "identifier": identifier.strip(),
        "label": label.strip(),
        "target_price": float(target_price) if target_price is not None else None,
        # Seed the baseline so adding an item never self-alerts.
        "last_price": result.price if result.ok else None,
        "lowest_price": result.price if result.ok else None,
        "alerted_target": False,
        "added_by": interaction.user.id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    items = storage.load()
    items.append(item)
    storage.save(items)

    lines = [f"✅ Watching **{item['label']}** (`{item['id']}`)"]
    if result.ok:
        lines.append(f"Current price: **{format_price(result.price, result.currency)}**")
        target = item["target_price"]
        if target is not None and result.price is not None:
            if result.price <= target:
                lines.append(
                    f"That's already at/below your target of "
                    f"{format_price(target, result.currency)} — you'll be alerted on the next check."
                )
            else:
                lines.append(f"Target: {format_price(target, result.currency)}")
    else:
        lines.append(f"⚠️ Couldn't read a price yet ({result.error}). I'll keep trying on each check.")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@tree.command(name="list_watches", description="Show everything you're watching")
async def list_watches_cmd(interaction: discord.Interaction) -> None:
    items = storage.load()
    if not items:
        await interaction.response.send_message(
            "Nothing is being watched yet. Use `/watch` to add something.", ephemeral=True
        )
        return

    embed = discord.Embed(title="👀 Watchlist", color=discord.Color.blurple())
    for item in items[:25]:
        src = "Best Buy" if item.get("source") == "bestbuy" else "URL"
        ident = item.get("identifier", "")
        if len(ident) > 64:
            ident = ident[:61] + "…"
        parts = [f"`{item['id']}` · {src}", f"Last: {format_price(item.get('last_price'))}"]
        if item.get("target_price") is not None:
            parts.append(f"Target: {format_price(item['target_price'])}")
        if item.get("lowest_price") is not None:
            parts.append(f"Low: {format_price(item['lowest_price'])}")
        embed.add_field(name=item.get("label", "(unnamed)"), value=" · ".join(parts) + f"\n{ident}", inline=False)
    if len(items) > 25:
        embed.set_footer(text=f"Showing 25 of {len(items)} items")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="unwatch", description="Stop watching an item")
@app_commands.describe(item_id="The id shown by /list_watches")
async def unwatch_cmd(interaction: discord.Interaction, item_id: str) -> None:
    item_id = item_id.strip().strip("`")
    items = storage.load()
    match = next((it for it in items if it.get("id") == item_id), None)
    if match is None:
        await interaction.response.send_message(
            f"No item with id `{item_id}`. Use `/list_watches` to see ids.", ephemeral=True
        )
        return
    storage.save([it for it in items if it.get("id") != item_id])
    await interaction.response.send_message(
        f"🗑️ Stopped watching **{match.get('label')}** (`{item_id}`).", ephemeral=True
    )


@tree.command(name="checknow", description="Check all watched items right now")
async def checknow_cmd(interaction: discord.Interaction) -> None:
    items = storage.load()
    if not items:
        await interaction.response.send_message("Nothing to check yet.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    outcomes = await _check_all()
    await _broadcast_alerts(outcomes)

    embed = discord.Embed(title="🔄 Checked now", color=discord.Color.blurple())
    for item, result, reasons in outcomes[:25]:
        if result.ok:
            value = format_price(result.price, result.currency) + (" 📉" if reasons else "")
        else:
            value = f"⚠️ {result.error}"
        embed.add_field(name=item.get("label", "(unnamed)"), value=value, inline=True)
    await interaction.followup.send(embed=embed, ephemeral=True)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> None:
    missing = [name for name in ("DISCORD_TOKEN", "DISCORD_CHANNEL_ID") if not os.getenv(name)]
    if missing:
        raise SystemExit(
            "Missing required env vars: "
            + ", ".join(missing)
            + ".\nCopy .env.example to .env and fill them in."
        )
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
