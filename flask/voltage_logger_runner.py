import asyncio
from app import voltage_logger_loop

if __name__ == "__main__":
    asyncio.run(voltage_logger_loop())