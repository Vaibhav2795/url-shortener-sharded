import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

load_dotenv()

SHARD_0_DATABASE_URL = os.getenv("SHARD_0_DATABASE_URL")
SHARD_1_DATABASE_URL = os.getenv("SHARD_1_DATABASE_URL")

print(SHARD_0_DATABASE_URL, SHARD_1_DATABASE_URL)
# 2. Instantiate your foundational SQLAlchemy engine configuration
engine_shard_0 = create_engine(SHARD_0_DATABASE_URL)
engine_shard_1 = create_engine(SHARD_1_DATABASE_URL)

engines = {0: engine_shard_0, 1: engine_shard_1}

# 3. Create isolated database session objects
session_local_0 = sessionmaker(autocommit=False, autoflush=False, bind=engine_shard_0)
session_local_1 = sessionmaker(autocommit=False, autoflush=False, bind=engine_shard_1)
SessionLocal = {0: session_local_0, 1: session_local_1}

# 4. Construct the standard declarative base for data mapping
Base = declarative_base()

# 5. Global dependency token to cleanly borrow database sessions inside FastAPI
def get_db(index: int):
    db = SessionLocal[index]()
    try:
        yield db
    finally:
        db.close()
