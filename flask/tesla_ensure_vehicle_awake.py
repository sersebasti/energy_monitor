import asyncio
from app import ensure_vehicle_awake

if __name__ == "__main__":
    asyncio.run(ensure_vehicle_awake())