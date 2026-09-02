import os
import redis
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv(
    "REDIS_URL", 
    "redis://token@endpoint:6379"
)

# Connect using a standard TCP URL
client = redis.from_url(REDIS_URL, decode_responses=True)

# Test the connection
print(client.ping())  