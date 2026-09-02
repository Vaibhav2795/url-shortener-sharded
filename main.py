from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import Column, BigInteger, String, DateTime
from sqlalchemy.orm import Session
import datetime
import hashlib

from db import Base, engines, get_db
from redis_client import client

class URLMapping(Base):
    __tablename__ = "url_mappings"

    # BigInteger maps to your native Postgres 'bigint' type
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    short_code = Column(String, unique=True, index=True, nullable=True)
    long_url = Column(String, nullable=True)


# Instruct SQLAlchemy to automatically ensure your table structure is present on startup
for engine in engines.values():
    Base.metadata.create_all(bind=engine)

app = FastAPI()


class URLShortenRequest(BaseModel):
    url: str

def encode_base62(num: int) -> str:
    """Converts an integer to a Base62 string."""
    BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return BASE62_ALPHABET[0]
    
    arr = []
    while num > 0:
        num, rem = divmod(num, 62)
        arr.append(BASE62_ALPHABET[rem])
    
    # Reverse array because the remainders are calculated from least to most significant
    return "".join(reversed(arr))

def decode_base62(code: str) -> int:
    BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    total = 0
    for char in code:
        total = total * 62 + BASE62_ALPHABET.index(char)
    return total

def generate_short_code_shake(text: str, length: int=8) -> str:
    """
        shake_256 requires the digest length in bytes
        1 byte = 2 hex characters, so divide desired string length by 2
    """
    byte_length = max(1, length // 2)
    return hashlib.shake_256(text.encode('utf-8')).hexdigest(byte_length)

def get_shard_index(hash_int: int, num_shards: int = 2) -> int:
    return hash_int % num_shards

@app.get("/")
def read_root():
    return RedirectResponse(url="/docs")

@app.get("/ping")
def root():
    return {"health": "Healthy"}

@app.post("/shorten")
def shorten(payload: URLShortenRequest, request: Request):
    hash = generate_short_code_shake(payload.url, 8)
    hash_int = int(hash, 16)
    unique_code = encode_base62(hash_int)
    redis_cache_string = f"code:{unique_code}"

    shard_index = get_shard_index(hash_int)

    if client.get(redis_cache_string) is None:
        db_gen = get_db(shard_index)
        db: Session = next(db_gen)
        db_mapping = db.query(URLMapping).filter(URLMapping.short_code == unique_code).first()

        if db_mapping is None:
            db_mapping = URLMapping(short_code=unique_code, long_url=payload.url)
            db.add(db_mapping)
            db.commit()
        elif db_mapping.long_url != payload.url:
            raise HTTPException(status_code=409, detail="This code is taken")

        client.setex(redis_cache_string, 3600, db_mapping.long_url)
        db_gen.close()

    full_short_url = f"{str(request.base_url)}{unique_code}"

    # Return the short code to the client
    return {"id": unique_code, "long_url": payload.url, "link": full_short_url}


@app.get("/{code}")
def get_code(code: str):
    redis_cache_string = f"code:{code}"
    long_url = client.get(redis_cache_string)

    if long_url is None:
        hash_int = decode_base62(code)
        shard_index = get_shard_index(hash_int)

        db_gen = get_db(shard_index)
        db: Session = next(db_gen)
        db_mapping = db.query(URLMapping).filter(URLMapping.short_code == code).first()

        if not db_mapping:
            raise HTTPException(status_code=404, detail="Short URL not found")

        long_url = db_mapping.long_url
        client.setex(redis_cache_string, 3600, db_mapping.long_url)
        db_gen.close()
    
    
    return RedirectResponse(url=long_url)