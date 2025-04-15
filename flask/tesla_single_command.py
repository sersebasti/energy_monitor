import asyncio
from app import run_remote_command  # oppure il nome del tuo file se diverso

async def test_comandi():
    print("\n--- TEST 1: Comando wake_up ---")
    await run_remote_command("wake_up")

if __name__ == "__main__":
    asyncio.run(test_comandi())