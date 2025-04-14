import asyncio
from app import check_and_charge_tesla

if __name__ == "__main__":
    asyncio.run(check_and_charge_tesla())