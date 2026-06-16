"""Start the game server: python run.py [port]"""
import asyncio
import sys

from server.net import GameServer

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"game server on ws://0.0.0.0:{port}")
    asyncio.run(GameServer().serve(port=port))
