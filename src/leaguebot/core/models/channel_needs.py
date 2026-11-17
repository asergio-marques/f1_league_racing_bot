"""The permissions a channel the bot saves needs it to hold: plain data, read wherever a channel
is judged before the bot posts to it or confirms it (`approval_checks.channel_presence`).

A need is a pair, the attribute `discord.Permissions` holds it under and the name Discord's own
interface gives it, so that a league finds what a line asks for in the channel's settings without
translating. `Needs` is the pairs a channel's use asks for, and each module states its own
channels' in its own services, from the named permissions here.

**Where each answer comes from.** View Channel and Send Messages are asked of every channel the
bot posts in. Read Message History is asked where the bot reposts and takes down what it posted
(results, standings, verdicts and the attendance sheet), which finds its last post with it. Embed
Links is asked where the bot posts an embed (the lineup, the check-in call, the hub and the signup
post). Attach Files is asked where the image module could attach a file to the posting, which the
module answers by aspect (`ImageDrawing.aspect_attaches_files`). Manage Channel and Manage
Permissions are asked where the bot sets who may see the channel (the hub, the signup channel).
No permission to mention is asked of any channel.
"""
from __future__ import annotations

#: The permissions a channel's use asks for, in the order a line names them.
Needs = tuple[tuple[str, str], ...]

VIEW: tuple[str, str] = ("view_channel", "View Channel")
SEND: tuple[str, str] = ("send_messages", "Send Messages")
READ_HISTORY: tuple[str, str] = ("read_message_history", "Read Message History")
EMBED: tuple[str, str] = ("embed_links", "Embed Links")
ATTACH: tuple[str, str] = ("attach_files", "Attach Files")
MANAGE_CHANNEL: tuple[str, str] = ("manage_channels", "Manage Channel")
MANAGE_PERMISSIONS: tuple[str, str] = ("manage_roles", "Manage Permissions")
