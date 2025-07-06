async def hello():
    print("Ciao2")
    return "Ciao"

if __name__ == '__main__':
    import asyncio
    asyncio.run(hello())
    print("Hello World")
