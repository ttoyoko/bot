import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import json
import os
import re

# ========== НАСТРОЙКИ ==========
TOKEN = "MTUwOTUyNzAxMzk0NDE5NzM2Mg.GX7lwW.0W6wQVxjGqhhEjsEYqdQZ7HGPNqkm1O2HVd9EM"  # лучше вынести в переменную окружения
ADMIN_ROLE_IDS = [1374277168435036251,1446618536687698032,1505940772648914954]
SUBMISSION_CHANNEL_ID = 1509590686003888258
GUILD_ID = 763029275514765363  # <-- замените на ID вашего сервера
TOURNAMENT_ROLE_ID =  1472598202711805972  # <-- замените на ID роли "турнир"
# Роли для проверки наличия роли "вышка"
CURATOR_ROLE_ID = 111111111111111111  # <-- замените на ID роли "куратор"
GA_ROLE_ID = 222222222222222222      # <-- замените на ID роли "га"
ZGA_ROLE_ID = 333333333333333333      # <-- замените на ID роли "зга"
VYSHKA_ROLE_ID = 444444444444444444  # <-- замените на ID роли "вышка"
# Роли для проверки наличия роли "вышка"
CURATOR_ROLE_ID = 111111111111111111  # <-- замените на ID роли "куратор"
GA_ROLE_ID = 222222222222222222      # <-- замените на ID роли "га"
ZGA_ROLE_ID = 333333333333333333      # <-- замените на ID роли "зга"
VYSHKA_ROLE_ID = 444444444444444444  # <-- замените на ID роли "вышка"

# Voice tracking
DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)
VOICE_SESSIONS_FILE = os.path.join(DATA_DIR, "voice_sessions.json")

# Channels for reports
REPORT_TEXT_CHANNEL_ID = 1509590686003888258  # <-- замените на ID текстового канала для отчётов !отчёт

LOCAL_TZ = timezone(timedelta(hours=3))  # пример: Москва UTC+3

# ========== ИНТЕНТЫ ==========
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
intents.voice_states = True
intents.members = True  # needed to fetch member objects for role assignment

bot = commands.Bot(command_prefix='!', intents=intents)

# ========== ХРАНИЛИЩЕ ТУРНИРА ==========
tournament = {
    'active': False,
    'start': None,
    'end': None,
    'instruction': None
}
users_who_asked_instruction = set()
submissions = []  # keep raw submissions for backup
submissions_sent = False
banned_users = set()
tournament_ended = False  # to avoid double finalize

# Tournament teams parsing
tournament_teams = {}   # team_name -> list of (player_name, steam_id)
team_order = []         # preserve insertion order of team names

# Tournament role tracking
tournament_role_users = set()  # set of member IDs who have been given the tournament role

# Roles for role requirement checking
CURATOR_ROLE_ID = 111111111111111111  # <-- replace with ID of role "куратор"
GA_ROLE_ID = 222222222222222222      # <-- replace with ID of role "га"
ZGA_ROLE_ID = 333333333333333333      # <-- replace with ID of role "зга"
VYSHKA_ROLE_ID = 444444444444444444  # <-- replace with ID of role "вышка"

# Background task to enforce role requirements
@tasks.loop(minutes=5)
async def role_check_task():
    """Periodically check that members with curator/ga/zga roles also have vyshka role; if not, remove the curator/ga/zga role."""
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        try:
            guild = await bot.fetch_guild(GUILD_ID)
        except Exception as e:
            print(f"[RoleCheck] Could not fetch guild: {e}")
            return
    if guild is None:
        print("[RoleCheck] Guild not found.")
        return

    vyshka_role = guild.get_role(VYSHKA_ROLE_ID)
    if vyshka_role is None:
        print(f"[RoleCheck] Vyshka role with ID {VYSHKA_ROLE_ID} not found.")
        return

    roles_to_check = [
        (CURATOR_ROLE_ID, "куратор"),
        (GA_ROLE_ID, "га"),
        (ZGA_ROLE_ID, "зга"),
    ]

    for role_id, role_name in roles_to_check:
        role = guild.get_role(role_id)
        if role is None:
            print(f"[RoleCheck] {role_name} role with ID {role_id} not found.")
            continue
        # Iterate over members that have this role
        for member in role.members:
            # If member lacks vyshka role, remove the current role
            if vyshka_role not in member.roles:
                try:
                    await member.remove_roles(role)
                    print(f"[RoleCheck] Removed {role_name} role from {member} ({member.id}) because they lack vyshka role.")
                except Exception as e:
                    print(f"[RoleCheck] Failed to remove {role_name} role from {member}: {e}")

# ========== ГОЛОСОВАЯ СТАТИСТИКА ==========
voice_sessions = []  # list of dict: {'user_id': int, 'join': iso_str, 'leave': iso_str or None}

def load_voice_data():
    global voice_sessions
    try:
        if os.path.exists(VOICE_SESSIONS_FILE):
            with open(VOICE_SESSIONS_FILE, 'r', encoding='utf-8') as f:
                voice_sessions = json.load(f)
        else:
            voice_sessions = []
    except Exception as e:
        print(f"Ошибка загрузки сессий голоса: {e}")
        voice_sessions = []

def save_voice_data():
    try:
        with open(VOICE_SESSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(voice_sessions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения сессий голоса: {e}")

def is_admin():
    async def predicate(ctx):
        return any(role.id in ADMIN_ROLE_IDS for role in ctx.author.roles)
    return commands.check(predicate)

# ========== ПАРСИНГ ЗАЯВОК ==========
def parse_submission(text: str):
    """
    Ожидаемый формат (пример):
    Команда: Название команды
    Игрок1: Имя игрока (STEAM_0:1:12345)
    Игрок2: Другое имя (STEAM_0:1:67890)
    Может быть произвольный порядок и другие строки, но мы ищем:
    - строку содержащую 'Команда:' (без учёта регистра) -> название после неё
    - строки содержащие 'Игрок' и число (Игрок1, Игрок2 и т.д.) -> имя и стим ID в скобках
    Возвращает (team_name, [(player_name, steam_id), ...]) либо None если не распознано.
    """
    lines = text.splitlines()
    team_name = None
    players = []
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        lower = line_stripped.lower()
        # Команда
        if lower.startswith('команда:'):
            # Возможно есть пробел после :
            parts = line_stripped.split(':', 1)
            if len(parts) == 2:
                team_name = parts[1].strip()
                if team_name:
                    team_name = team_name.strip()
        # Игрок
        elif lower.startswith('игрок'):
            # ищем имя и стим айди в скобках
            # Пример: "Игрок1: Nick (STEAM_0:1:12345)"
            # или "Игрок 2: Name (STEAM_0:1:67890)"
            # Извлекаем всё после первого ':'
            if ':' in line_stripped:
                after_colon = line_stripped.split(':', 1)[1].strip()
                # ищем подстроку вида что-то (STEAM_...)
                match = re.search(r'(.+?)\s*\((STEAM_[0-9]:[0-9]:[0-9]+)\)', after_colon)
                if match:
                    player_name = match.group(1).strip()
                    steam_id = match.group(2).strip()
                    players.append((player_name, steam_id))
                else:
                    # если нет скобок, можем считать всё после ':' как имя без стим
                    # но тогда стим будет пустым
                    player_name = after_colon.strip()
                    if player_name:
                        players.append((player_name, ""))
    if team_name is not None and players:
        return team_name, players
    return None

# ========== ФОРМИРОВАНИЕ И ОТПРАВКА ИТОГОВОГО СПИСКА ==========
def build_final_report():
    """
    Возвращает строку со списком команд и игроков, а также сетку матчей.
    Формат:
    1. Команда1:
    игрок1(стим айди)
    игрок2(стим айди)
    2. Команда2:
    ...
    Сетка матчей:
    Матч 1: Команда1 vs Команда2
    Матч 2: Команда3 vs Команда4
    (если нечётное число — последняя команда получает bye)
    """
    if not team_order:
        return "Нет зарегистрированных команд."
    lines = []
    # Список команд
    for idx, name in enumerate(team_order, start=1):
        lines.append(f"{idx}.{name}:")
        players = tournament_teams.get(name, [])
        if players:
            for pname, steam in players:
                if steam:
                    lines.append(f"{pname}({steam})")
                else:
                    lines.append(f"{pname}")
        else:
            lines.append("(нет игроков)")
    lines.append("")  # пустая строка перед сеткой
    # Сетка матчей
    lines.append("Сетка матчей:")
    n = len(team_order)
    match_num = 1
    i = 0
    while i < n:
        if i + 1 < n:
            lines.append(f"Матч {match_num}: {team_order[i]} vs {team_order[i+1]}")
            i += 2
        else:
            lines.append(f"Матч {match_num}: {team_order[i]} vs Bye (автоматический проход)")
            i += 1
        match_num += 1
    return "\n".join(lines)

# ========== РОЛЕВЫЕ ФУНКЦИИ ==========
async def give_tournament_role(member: discord.Member):
    """Give the tournament role to a member if they don't have it."""
    if member.id in tournament_role_users:
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        # fallback: try to get guild from member
        guild = member.guild
    if guild is None:
        return
    role = guild.get_role(TOURNAMENT_ROLE_ID)
    if role is None:
        print(f"Tournament role with ID {TOURNAMENT_ROLE_ID} not found in guild {guild.id}")
        return
    if role not in member.roles:
        try:
            await member.add_roles(role)
            tournament_role_users.add(member.id)
            print(f"Gave tournament role to {member} ({member.id})")
        except Exception as e:
            print(f"Failed to give tournament role to {member}: {e}")

async def remove_tournament_role(member: discord.Member):
    """Remove the tournament role from a member if they have it."""
    if member.id not in tournament_role_users:
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        guild = member.guild
    if guild is None:
        return
    role = guild.get_role(TOURNAMENT_ROLE_ID)
    if role is None:
        print(f"Tournament role with ID {TOURNAMENT_ROLE_ID} not found in guild {guild.id}")
        return
    if role in member.roles:
        try:
            await member.remove_roles(role)
            tournament_role_users.discard(member.id)
            print(f"Removed tournament role from {member} ({member.id})")
        except Exception as e:
            print(f"Failed to remove tournament role from {member}: {e}")

async def remove_tournament_role_from_all():
    """Remove the tournament role from all users who have it."""
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    role = guild.get_role(TOURNAMENT_ROLE_ID)
    if role is None:
        print(f"Tournament role with ID {TOURNAMENT_ROLE_ID} not found in guild {guild.id}")
        return
    to_remove = list(tournament_role_users)
    for uid in to_remove:
        member = guild.get_member(uid)
        if member is None:
            continue
        if role in member.roles:
            try:
                await member.remove_roles(role)
                tournament_role_users.discard(uid)
                print(f"Removed tournament role from {member} ({uid})")
            except Exception as e:
                print(f"Failed to remove tournament role from {member}: {e}")

async def finalize_tournament(source=None):
    """
    Формирует итоговый список и посылает его в канал заявок.
    source может быть объектом контекста (для ответа в том же канале) или None.
    Если tournament_ended уже True — не делаем ничего.
    Также снимает роль турнира со всех пользователей.
    """
    global tournament_ended
    if tournament_ended:
        return
    tournament_ended = True
    report = build_final_report()
    channel = bot.get_channel(SUBMISSION_CHANNEL_ID)
    if channel:
        await channel.send(report)
    # Если передан source (например, контекст команды !stop), можно также ответить туда
    if source is not None:
        try:
            await source.send("Итоговый список команд и сетка матчей отправлен в канал заявок.")
        except:
            pass
    # Remove tournament role from all users who have it
    await remove_tournament_role_from_all()

# ========== СОБЫТИЯ ==========
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    load_voice_data()
    # Start background role check task if not already running
    if not role_check_task.is_running():
        role_check_task.start()

@bot.event
async def on_voice_state_update(member, before, after):
    # Пользователь зашёл в любой войс
    if before.channel is None and after.channel is not None:
        join_time = datetime.now(timezone.utc)
        voice_sessions.append({
            'user_id': member.id,
            'join': join_time.isoformat(),
            'leave': None
        })
        save_voice_data()
    # Пользователь вышел из войса
    elif before.channel is not None and after.channel is None:
        # Находим последнюю открытую сессию этого пользователя
        for session in reversed(voice_sessions):
            if session['user_id'] == member.id and session['leave'] is None:
                leave_time = datetime.now(timezone.utc)
                session['leave'] = leave_time.isoformat()
                save_voice_data()
                break

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Обработка личных сообщений (заявки и инструкции)
    if isinstance(message.channel, discord.DMChannel):
        if message.author.id in banned_users:
            await message.author.send('🚫 Вы забанены и не можете подавать заявки боту.')
            return

        content = message.content.strip()

        if content.startswith('!game'):
            users_who_asked_instruction.add(message.author.id)
            if tournament['active'] and tournament['instruction']:
                await message.author.send(
                    f'📝 Инструкция для подачи заявки:\n{tournament["instruction"]}\n\n'
                    f'Теперь вы можете отправить свою заявку.'
                )
            else:
                await message.author.send(
                    'Сейчас нет активного турнира или инструкция не задана. '
                    'Пожалуйста, дождитесь объявления турнира администратором.'
                )
            return

        if content.startswith('!'):
            await bot.process_commands(message)
            return

        if not tournament['active']:
            await message.author.send('❌ Сейчас набор на турнир закрыт.')
            return

        now_utc = datetime.now(timezone.utc)
        if now_utc < tournament['start']:
            await message.author.send(
                f'⏳ Приём заявок ещё не начался.\n'
                f'Начнётся {tournament["start"].strftime("%d.%m.%Y %H:%M")} UTC'
            )
            return
        if now_utc > tournament['end']:
            await message.author.send(
                f'⏰ Время приёма заявок истекло.\n'
                f'Окончание было {tournament["end"].strftime("%d.%m.%Y %H:%M")} UTC'
            )
            if tournament['active']:
                tournament['active'] = False
                await finalize_tournament()
            return

        if message.author.id not in users_who_asked_instruction:
            await message.author.send(
                'Сначала получите инструкцию, отправив команду `!game`. '
                'После этого вы сможете подать заявку.'
            )
            return

        # Принимаем любое сообщение как заявку (без валидации)
        formatted = content  # raw text as is

        # Сохраняем сырую заявку (для резерва)
        submissions.append((message.author.id, message.author.display_name, formatted))

        # Пытаемся распарсить команду и игроков
        parsed = parse_submission(formatted)
        if parsed:
            team_name, players = parsed
            # Если команда уже существует, мы можем решить обновлять список игроков или игнорировать.
            # Для простоты, если команда уже есть — заменяем список игроков новым (последняя заявка перезаписывает).
            if team_name not in tournament_teams:
                team_order.append(team_name)
            tournament_teams[team_name] = players
            # Уведомим пользователя, что команда зарегистрирована/обновлена
            await message.author.send(f'✅ Команда "{team_name}" зарегистрирована/обновлена с {len(players)} игроком(ами).')
        else:
            # Если не удалось распарсить, всё равно сохраняем заявку как есть (без предупреждения)
            pass

        # Give tournament role to the submitter (if they are in the guild)
        try:
            guild = bot.get_guild(GUILD_ID)
            if guild is None:
                # Try to fetch guild if not cached
                guild = await bot.fetch_guild(GUILD_ID)
            if guild is not None:
                member_obj = guild.get_member(message.author.id)
                if member_obj is not None:
                    await give_tournament_role(member_obj)
                    await message.author.send('🎖️ Вам выдана роль «турнир».')
                else:
                    await message.author.send('⚠️ Не удалось найти вас на сервере для выдачи роли. Убедитесь, что вы находитесь на сервере и у бота есть права управлять ролями.')
            else:
                await message.author.send('⚠️ Не удалось получить информацию о сервере для выдачи роли.')
        except Exception as e:
            print(f"Error assigning tournament role: {e}")
            await message.author.send('⚠️ Произошла ошибка при выдаче роли. Обратитесь к администратору.')

        # Отправка заявки в канал (сырой текст)
        channel = bot.get_channel(SUBMISSION_CHANNEL_ID)
        if channel is None:
            await message.author.send('⚠️ Ошибка: канал для заявок не найден. Сообщите администратору.')
            return
        await channel.send(
            f'📬 Новая заявка от {message.author} ({message.author.id}):\n{formatted}'
        )
        await message.author.send('✅ Ваша заявка отправлена!')
        return

    # Текстовая команда !отчёт в определённом канале
    if message.content.strip() == '!отчёт':
        if message.channel.id != REPORT_TEXT_CHANNEL_ID:
            await message.channel.send("Эту команду можно использовать только в определённом текстовом канале.")
            return
        await handle_text_report(message)
        return

    await bot.process_commands(message)

async def handle_text_report(source):
    now = datetime.now(timezone.utc)
    today = now.date()
    start_of_week = today - timedelta(days=today.weekday())  # понедельник
    # Approximate month start: first day of current month
    start_of_month = today.replace(day=1)

    # Структуры для хранения времени per user
    user_week = {}
    user_month = {}
    user_all = {}
    # Также посчитаем уникальных пользователей
    users_week_set = set()
    users_month_set = set()
    users_all_set = set()

    for s in voice_sessions:
        uid = s['user_id']
        join = datetime.fromisoformat(s['join'])
        leave_str = s['leave']
        if leave_str is None:
            leave = now
        else:
            leave = datetime.fromisoformat(leave_str)
        if leave < join:
            continue  # странно, но защита
        duration = (leave - join).total_seconds()
        # Determine which periods this session contributes to
        # For simplicity, we assign the session to periods based on its leave time (or now for ongoing)
        # This approximates but acceptable.
        # We'll add the duration to each period if the session's leave (or now) is within that period.
        if leave.date() >= start_of_week:
            user_week[uid] = user_week.get(uid, 0) + duration
            users_week_set.add(uid)
        if leave.date() >= start_of_month:
            user_month[uid] = user_month.get(uid, 0) + duration
            users_month_set.add(uid)
        user_all[uid] = user_all.get(uid, 0) + duration
        users_all_set.add(uid)

    def fmt(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h}ч {m}м {s}с"

    lines = ["**Отчёт по голосовой активности**",
             f"📅 За неделю (с {start_of_week.strftime('%d.%m.%Y')}):",
             f"👥 Уникальных пользователей: {len(users_week_set)}"]
    for uid, secs in sorted(user_week.items(), key=lambda x: x[1], reverse=True):
        member = source.guild.get_member(uid) if source.guild else None
        name = member.display_name if member else f"ID:{uid}"
        lines.append(f"• {name}: {fmt(secs)}")
    lines.append("")
    lines.append(f"📅 За месяц (с {start_of_month.strftime('%d.%m.%Y')}):")
    lines.append(f"👥 Уникальных пользователей: {len(users_month_set)}")
    for uid, secs in sorted(user_month.items(), key=lambda x: x[1], reverse=True):
        member = source.guild.get_member(uid) if source.guild else None
        name = member.display_name if member else f"ID:{uid}"
        lines.append(f"• {name}: {fmt(secs)}")
    lines.append("")
    lines.append("📅 За всё время:")
    lines.append(f"👥 Уникальных пользователей: {len(users_all_set)}")
    for uid, secs in sorted(user_all.items(), key=lambda x: x[1], reverse=True):
        member = source.guild.get_member(uid) if source.guild else None
        name = member.display_name if member else f"ID:{uid}"
        lines.append(f"• {name}: {fmt(secs)}")
    report = "\n".join(lines)

    channel = bot.get_channel(REPORT_TEXT_CHANNEL_ID)
    if channel:
        await channel.send(report)
    else:
        # Optionally, you could send an error to the source channel if desired.
        pass

# ========== ТУРНИРНЫЕ КОМАНДЫ ==========
@bot.command(name='turnir')
@is_admin()
async def turnir(ctx, start_str: str, end_str: str, *, instruction: str):
    try:
        start_naive = datetime.strptime(start_str, '%d.%m.%Y %H:%M')
        end_naive = datetime.strptime(end_str, '%d.%m.%Y %H:%M')
    except ValueError:
        await ctx.send('❌ Неверный формат даты. Используйте `DD.MM.YYYY HH:MM` (например, 28.05.2026 18:00)')
        return

    if start_naive >= end_naive:
        await ctx.send('❌ Дата начала должна быть раньше даты окончания.')
        return

    start_local = start_naive.replace(tzinfo=LOCAL_TZ)
    end_local = end_naive.replace(tzinfo=LOCAL_TZ)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    tournament['active'] = True
    tournament['start'] = start_utc
    tournament['end'] = end_utc
    tournament['instruction'] = instruction
    users_who_asked_instruction.clear()
    submissions.clear()
    tournament_teams.clear()
    team_order.clear()
    tournament_role_users.clear()
    global submissions_sent, tournament_ended
    submissions_sent = False
    tournament_ended = False

    await ctx.send(
        f'✅ Турнир создан!\n'
        f'📅 Период: {start_str} – {end_str}\n'
        f'📝 Инструкция: {instruction}'
    )

@bot.command(name='check')
async def check(ctx):
    await ctx.send('✅ Бот работает и готов принимать заявки!')

@bot.command(name='stop')
@is_admin()
async def stop(ctx):
    if tournament['active']:
        tournament['active'] = False
        await finalize_tournament(ctx)
    tournament['start'] = None
    tournament['end'] = None
    tournament['instruction'] = None
    users_who_asked_instruction.clear()
    await ctx.send('⏹️ Набор на турнир остановлен.')

@bot.command(name='ban')
@is_admin()
async def ban(ctx, user_id: int):
    banned_users.add(user_id)
    await ctx.send(f'🚫 Пользователь с ID {user_id} забанен и не может подавать заявки.')

# ========== КОМАНДА !clean ==========
@bot.command(name='clean')
@is_admin()
async def clean(ctx, team_number: int):
    """
    Удалить команду по её номеру в списке (1-indexed).
    """
    if team_number < 1 or team_number > len(team_order):
        await ctx.send(f'❌ Неверный номер команды. Доступно команд: {len(team_order)}')
        return
    # Удаляем из списка_order и словаря
    idx = team_number - 1
    removed_name = team_order.pop(idx)
    del tournament_teams[removed_name]
    await ctx.send(f'🗑️ Команда "{removed_name}" (номер {team_number}) удалена из списка.')

# ========== КОМАНДА !bb (конец турнира и снятие ролей) ==========
@bot.command(name='bb')
@is_admin()
async def bb(ctx):
    """
    Завершить турнир, отправить итоговый список и снять роль турнира со всех.
    """
    if not tournament['active']:
        await ctx.send('Турнир уже не активен.')
        return
    tournament['active'] = False
    tournament['start'] = None
    tournament['end'] = None
    tournament['instruction'] = None
    users_who_asked_instruction.clear()
    await finalize_tournament(ctx)
    await ctx.send('🏁 Турнир завершён, роли сняты, итоговый список отправлен.')

# ========== КОМАНДА !delrol (снять роль с конкретного пользователя) ==========
@bot.command(name='delrol')
@is_admin()
async def delrol(ctx, user_id: int):
    """
    Снять роль турнира с указанного пользователя по его Discord ID.
    """
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        await ctx.send('Не удалось получить гильдию.')
        return
    member = guild.get_member(user_id)
    if member is None:
        await ctx.send(f'Пользователь с ID {user_id} не найден на сервере.')
        return
    await remove_tournament_role(member)
    await ctx.send(f'Роль турнира снята с пользователя {member} (ID: {user_id}).')

# ========== ОТПРАВКА СПИСКА ЗАЯВОК (для обратной совместимости, если нужно) ==========
async def send_submissions_if_needed(source):
    global submissions_sent
    if submissions_sent or not submissions:
        return
    channel = bot.get_channel(SUBMISSION_CHANNEL_ID)
    if channel is None:
        if hasattr(source, 'send'):
            await source.send('⚠️ Канал для заявок не найден. Не могу отправить список заявок.')
        return
    lines = [f'📋 Список участников турнира:']
    for idx, (uid, uname, msg) in enumerate(submissions, start=1):
        lines.append(f'{idx}. {uname} (ID: {uid}): {msg}')
    text = '\n'.join(lines)
    await channel.send(text)
    submissions_sent = True

# List of inspirational quotes
QUOTES = [
    "Побеждает тот, кто верит в победу. — Неизвестно",
    "В единстве сила. — Русская пословица",
    "Лучшая подготовка к завтрашнему дню — усердная работа сегодня. — Неизвестно",
    "Не бойся медленного движения, бойся только стояния. — Китайская поговорка",
    "Тот, кто не рискует, не пьёт шампанского. — Неизвестно"
]

@bot.command(name='quote')
async def quote(ctx):
    """Отправляет случайную мотивационную цитату."""
    import random
    quote = random.choice(QUOTES)
    await ctx.send(quote)

# ========== ВОЙС КОМАНДЫ ==========
@bot.command(name='jo')
@is_admin()
async def jo(ctx, channel_id: int):
    """Заставить бота войти в голосовой канал по ID."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        await ctx.send(f'❌ Канал с ID {channel_id} не найден.')
        return
    if not isinstance(channel, discord.VoiceChannel):
        await ctx.send(f'❌ Канал с ID {channel_id} не является голосовым каналом.')
        return
    # If already connected in this guild, move to new channel
    vc = ctx.guild.voice_client
    if vc and vc.is_connected():
        try:
            await vc.move_to(channel)
            await ctx.send(f'✅ Бот перемещён в голосовой канал: {channel.name}')
        except Exception as e:
            await ctx.send(f'❌ Не удалось переместиться: {e}')
    else:
        try:
            await channel.connect()
            await ctx.send(f'✅ Бот присоединился к голосовому каналу: {channel.name}')
        except Exception as e:
            await ctx.send(f'❌ Не удалось присоединиться: {e}')

@bot.command(name='leave')
@is_admin()
async def leave(ctx):
    """Отключить бота от текущего голосового канала."""
    vc = ctx.guild.voice_client
    if vc and vc.is_connected():
        await vc.disconnect()
        await ctx.send('✅ Бот отключён от голосового канала.')
    else:
        await ctx.send('⚠️ Бот не подключён к любому голосовому каналу.')

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    bot.run(TOKEN)
