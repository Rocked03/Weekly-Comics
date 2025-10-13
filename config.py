import os
from dotenv import load_dotenv
from pathlib import Path

# Load .env file
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

TOKEN = os.getenv('TOKEN')
BOT_PREFIX = os.getenv('BOT_PREFIX')
API_URL = os.getenv('API_URL')

postgres_credentials = {
    "user": os.getenv('POSTGRES_USER'),
    "password": os.getenv('POSTGRES_PASSWORD'),
    "database": os.getenv('POSTGRES_DATABASE'),
    "host": os.getenv('POSTGRES_HOST')
}

ADMIN_GUILD_IDS = [int(x) for x in os.getenv('ADMIN_GUILD_IDS', '').split(',') if x]
ADMIN_USER_IDS = [int(x) for x in os.getenv('ADMIN_USER_IDS', '').split(',') if x]
