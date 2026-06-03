import os
from dotenv import load_dotenv
from datetime import timezone, timedelta

load_dotenv()

# Токен берётся из переменной окружения (файл .env)
TOKEN = os.getenv('DISCORD_TOKEN')

# Role IDs that are allowed to use admin commands (!settings, etc.)
ADMIN_ROLE_IDS = [
    123456789012345678,  # Замените на ваш реальный ID роли администратора
    987654321098765432,  # Замените на ваш реальный ID роли администратора
]

# Default channel IDs (will be saved/updated via admin panel)
DEFAULT_SUBMISSION_CHANNEL_ID = None  # Where applications will be posted
DEFAULT_LOG_CHANNEL_ID = None         # Where logs/updates will be sent

# Local timezone for any date/time displays (example: Moscow UTC+3)
LOCAL_TZ = timezone(timedelta(hours=3))