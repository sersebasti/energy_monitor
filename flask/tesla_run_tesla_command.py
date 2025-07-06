import asyncio
from app import run_tesla_command  # oppure il nome del tuo file se diverso

async def test_comandi():
    print("\n--- TEST 1: Comando wake_up ---")
    #await run_tesla_command("wake_up")
    #await run_tesla_command("flash_lights")
    #await run_tesla_command("charge_stop")
    await run_tesla_command("charge_start")

if __name__ == "__main__":
    asyncio.run(test_comandi())