from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import Base, engines, get_db
from encoding import generate_short_code_shake, encode_base62
from redis_client import client
from sharding import get_shard
from models import URLMapping

for engine in engines.values():
    Base.metadata.create_all(bind=engine)

app = FastAPI()

class URLShortenRequest(BaseModel):
    url: str

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

    cached_url = client.get(redis_cache_string)

    if cached_url is not None:
        if cached_url != payload.url:
            raise HTTPException(status_code=409, detail="This code is take")
    else:
        shard_id = get_shard(unique_code)
        db_gen = get_db(shard_id)
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
    return {"id": unique_code, "long_url": payload.url, "link": full_short_url}


@app.get("/{code}")
def get_code(code: str):
    redis_cache_string = f"code:{code}"
    long_url = client.get(redis_cache_string)

    if long_url is None:
        shard_id = get_shard(code)

        db_gen = get_db(shard_id)
        db: Session = next(db_gen)
        db_mapping = db.query(URLMapping).filter(URLMapping.short_code == code).first()

        if not db_mapping:
            raise HTTPException(status_code=404, detail="Short URL not found")

        long_url = db_mapping.long_url
        client.setex(redis_cache_string, 3600, db_mapping.long_url)
        db_gen.close()
    
    
    return RedirectResponse(url=long_url)