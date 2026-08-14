from telethon import events


def register(client):
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def handler(event):
        await event.reply("pong")
