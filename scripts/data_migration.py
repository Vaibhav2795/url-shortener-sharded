from db import Base, engines, get_db

from models import URLMapping
from sharding import get_shard

for engine in engines.values():
    Base.metadata.create_all(bind=engine)

failures =[]

def main():
    for source_shard in engines:
        print(source_shard)
        source_gen = get_db(source_shard)
        source_db = next(source_gen)
        db_mappings = source_db.query(URLMapping).all()

        for db_mapping in db_mappings:
            correct_shard = get_shard(db_mapping.short_code)

            if correct_shard != source_shard:
                print(f"Moving {db_mapping.short_code}: shard {source_shard} -> {correct_shard}")

                
                target_gen = get_db(correct_shard)
                target_db = next(target_gen)

                try:
                    new_row = URLMapping(
                        short_code=db_mapping.short_code,
                        long_url=db_mapping.long_url,
                    )
                    target_db.add(new_row)
                    target_db.commit()
                except Exception as e:
                    failures.append({
                        "short_code": db_mapping.short_code,
                        "stage": "insert",
                        "from_shard": source_shard,
                        "to_shard": correct_shard,
                        "error": str(e),
                    })
                    target_db.close()
                finally:
                    target_db.close()

                try:
                    source_db.delete(db_mapping)
                    source_db.commit()
                except Exception as e:
                    failures.append({
                        "short_code": db_mapping.short_code,
                        "stage": "delete",
                        "from_shard": source_shard,
                        "to_shard": correct_shard,
                        "error": str(e),
                    })
        source_db.close()
        
    if failures:
        import json
        with open("migration_failures.json", "w") as f:
            json.dump(failures, f, indent=2)
        print(f"\n{len(failures)} failures logged to migration_failures.json")
    else:
        print("\nMigration completed with no failures.")


if __name__ == "__main__":
    main()