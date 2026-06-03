import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Select, Modal, TextInput
import json
import os
from dotenv import load_dotenv
from config import (
    ADMIN_ROLE_IDS,
    DEFAULT_SUBMISSION_CHANNEL_ID,
    DEFAULT_LOG_CHANNEL_ID,
    LOCAL_TZ,
)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

DATA_FILE = "data.json"

bot = commands.Bot(command_prefix="!", intents=discord.Intents(
    guilds=True,
    members=True,
    dm_messages=True,
    messages=True,
    message_content=True,
    voice_states=True,
))

# In-memory state
submission_channel_id = DEFAULT_SUBMISSION_CHANNEL_ID
log_channel_id = DEFAULT_LOG_CHANNEL_ID
applications = []          # list of dicts: {id, user_id, username, fields, status}
next_app_id = 1

# Persistence helpers
def load_data():
    global submission_channel_id, log_channel_id, applications, next_app_id
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        submission_channel_id = data.get("submission_channel_id", DEFAULT_SUBMISSION_CHANNEL_ID)
        log_channel_id = data.get("log_channel_id", DEFAULT_LOG_CHANNEL_ID)
        applications = data.get("applications", [])
        next_app_id = data.get("next_app_id", 1)
    except Exception as e:
        print(f"[Data] Load error: {e}")

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "submission_channel_id": submission_channel_id,
                "log_channel_id": log_channel_id,
                "applications": applications,
                "next_app_id": next_app_id,
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Data] Save error: {e}")

# Helper to get channels
def get_submission_channel():
    return bot.get_channel(submission_channel_id) if submission_channel_id else None

def get_log_channel():
    return bot.get_channel(log_channel_id) if log_channel_id else None

# Application modal
class ApplicationModal(Modal, title="Новая заявка"):
    name = TextInput(label="Ваше имя", placeholder="Введите своё имя", max_length=50)
    age = TextInput(label="Возраст", placeholder="Например: 20", max_length=3)
    experience = TextInput(label="Опыт (кратко)", placeholder="Опишите ваш опыт", style=discord.TextStyle.paragraph, max_length=300)
    contacts = TextInput(label="Контакты (Discord, Telegram и т.д.)", placeholder="Ваши контакты", max_length=100)

    async def on_submit(self, interaction: discord.Interaction):
        global next_app_id
        app_id = next_app_id
        next_app_id += 1
        fields = {
            "Имя": self.name.value,
            "Возраст": self.age.value,
            "Опыт": self.experience.value,
            "Контакты": self.contacts.value,
        }
        app = {
            "id": app_id,
            "user_id": interaction.user.id,
            "username": str(interaction.user),
            "fields": fields,
            "status": "pending",
        }
        applications.append(app)
        save_data()

        # Notify user
        await interaction.response.send_message(
            "✅ Ваша заявка принята! Ожидайте рассмотрения администрацией.", ephemeral=True
        )

        # Post to submissions channel
        channel = get_submission_channel()
        if channel:
            embed = discord.Embed(
                title=f"Новая заявка #{app_id}",
                colour=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_author(name=interaction.user, icon_url=interaction.user.display_avatar.url)
            for k, v in fields.items():
                embed.add_field(name=k, value=v, inline=False)
            embed.set_footer(text=f"Статус: pending | ID: {app_id}")
            await channel.send(embed=embed)

        # Log
        log = get_log_channel()
        if log:
            await log.send(f"📥 Новая заявка #{app_id} от {interaction.user} ({interaction.user.id})")

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.response.send_message("❌ Произошла ошибка при отправке заявки.", ephemeral=True)
        print(f"[Modal] Error: {error}")

# Persistent view with "Submit Application" button
class SubmitView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Подать заявку", style=discord.ButtonStyle.green, custom_id="submit_app")
    async def submit_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ApplicationModal())

# Admin panel view
class SettingsView(View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Просмотр заявок", style=discord.ButtonStyle.primary, row=0)
    async def view_apps(self, interaction: discord.Interaction, button: Button):
        pending = [a for a in applications if a["status"] == "pending"]
        if not pending:
            await interaction.response.send_message("📭 Нет pending заявок.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Pending заявки",
            colour=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        for app in pending[:10]:  # limit to 10 to avoid embed limits
            fields_str = "\n".join(f"**{k}**: {v}" for k, v in app["fields"].items())
            embed.add_field(
                name=f"Заявка #{app['id']} от {app['username']}",
                value=fields_str or "—",
                inline=False,
            )
        embed.set_footer(text=f"Всего pending: {len(pending)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Установить канал заявок", style=discord.ButtonStyle.secondary, row=0)
    async def set_sub_channel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ChannelModal("Укажите ID канала для заявок", "submission"))

    @discord.ui.button(label="Установить канал логов", style=discord.ButtonStyle.secondary, row=0)
    async def set_log_channel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ChannelModal("Укажите ID канала для логов", "log"))

    @discord.ui.button(label="Обновить", style=discord.ButtonStyle.success, row=1)
    async def refresh(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("🔄 Данные обновлены.", ephemeral=True)

# Modal to set channel ID
class ChannelModal(Modal):
    def __init__(self, title: str, channel_type: str):
        super().__init__(title=title)
        self.channel_type = channel_type
        self.channel_id = TextInput(
            label="ID канала",
            placeholder="Введите числовой ID канала",
            max_length=20,
            required=True,
        )
        self.add_item(self.channel_id)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cid = int(self.channel_id.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ ID должен быть числом.", ephemeral=True)
            return

        if self.channel_type == "submission":
            global submission_channel_id
            submission_channel_id = cid
            save_data()
            await interaction.response.send_message(f"✅ Канал заявок установлен: <#{cid}>", ephemeral=True)
        else:
            global log_channel_id
            log_channel_id = cid
            save_data()
            await interaction.response.send_message(f"✅ Канал логов установлен: <#{cid}>", ephemeral=True)

        # Log change
        log = get_log_channel()
        if log:
            await log.send(f"🔧 Администратор {interaction.user} изменил канал {self.channel_type} на <#{cid}>")

# Approve/Deny handling via buttons on application messages
class AppActionView(View):
    def __init__(self, app_id: int):
        super().__init__(timeout=300)
        self.app_id = app_id

    @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: Button):
        await self._handle(interaction, "approved")

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: Button):
        await self._handle(interaction, "denied")

    async def _handle(self, interaction: discord.Interaction, action: str):
        # Find application
        app = next((a for a in applications if a["id"] == self.app_id), None)
        if not app:
            await interaction.response.send_message("❌ Заявка не найдена.", ephemeral=True)
            return
        if app["status"] != "pending":
            await interaction.response.send_message(f"⚠️ Заявка уже обработана: {app['status']}.", ephemeral=True)
            return

        app["status"] = action
        save_data()

        # Notify applicant
        try:
            user = await bot.fetch_user(app["user_id"])
            await user.send(
                f"✅ Ваша заявка #{app['id']} **одобрена**!" if action == "approved"
                else f"❌ Ваша заявка #{app['id']} **отклонена**."
            )
        except Exception:
            pass

        # Update the message (replace buttons with status)
        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = discord.Color.green() if action == "approved" else discord.Color.red()
        embed.set_footer(text=f"Статус: {action} | ID: {app['id']}")
        await interaction.response.edit_message(embed=embed, view=None)

        # Log
        log = get_log_channel()
        if log:
            await log.send(
                f"🛠️ Администратор {interaction.user} {action} заявку #{app['id']} от {app['username']}"
            )

# Settings command
@bot.command(name="settings")
@commands.has_any_role(*ADMIN_ROLE_IDS)
async def settings(ctx):
    view = SettingsView()
    embed = discord.Embed(
        title="⚙️ Административная панель",
        description="Используйте кнопки ниже для управления заявками и настройками.",
        colour=discord.Color.dark_green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Канал заявок", value=f"<#{submission_channel_id}>" if submission_channel_id else "Не задан", inline=False)
    embed.add_field(name="Канал логов", value=f"<#{log_channel_id}>" if log_channel_id else "Не задан", inline=False)
    await ctx.send(embed=embed, view=view)

# Command to manually post the submit button (optional)
@bot.command(name="setup")
@commands.has_any_role(*ADMIN_ROLE_IDS)
async def setup(ctx):
    view = SubmitView()
    await ctx.send("Нажмите кнопку ниже, чтобы подать заявку:", view=view)

# Error handling
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingAnyRole):
        await ctx.send("❌ У вас нет прав для выполнения этой команды.", ephemeral=True)
    else:
        print(f"[Error] {error}")

# Startup
@bot.event
async def on_ready():
    load_data()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # Ensure the submit view is registered for persistence
    bot.add_view(SubmitView())

bot.run(TOKEN)