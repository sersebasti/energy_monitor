import asyncio
from app import run_tesla_command  # oppure il nome del tuo file se diverso

async def test_comand():
    command = "charge_start"
    #command = "set_charging_amps"
    value = 8
    print("TEST  Comand " + command)
    await run_tesla_command(command, value)


if __name__ == "__main__":
    asyncio.run(test_comand())