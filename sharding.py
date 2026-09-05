import hashlib
import bisect
from db import engines

def stable_hash(key: str) -> int:
    digest = hashlib.shake_256(key.encode('utf-8')).hexdigest(8);
    return int(digest, 16)

ring = []
def add_shard(shard_id, num_virtual_nodes = 100):
    for i in range(num_virtual_nodes):
        position = stable_hash(f"{shard_id}-{i}")
        bisect.insort(ring, (position, shard_id))

for shard_id in engines.keys():
    add_shard(shard_id)

def get_shard(key: str):
    if not ring:
        raise Exception("No shards available")

    key_hash = stable_hash(key)
    positions = [pos for pos, shard_id in ring]
    index = bisect.bisect_left(positions, key_hash)

    if index == len(ring):
        index = 0
    
    return ring[index][1]
