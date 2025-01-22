import asyncio
import json
import base64
import struct
import base58
import hashlib
import websockets
import os
import argparse
from datetime import datetime
from time import time

# Đảm bảo import đúng cách
from solders.pubkey import Pubkey
# from solders.keypair import Keypair
# from solders.instruction import Instruction, AccountMeta
# from solders.system_program import transfer, TransferParams
# from solders.transaction import VersionedTransaction
# from solders.transaction import Transaction
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
# from solana.rpc.types import TxOpts
# from solana.rpc.api import Client

# from solana.transaction import Transaction  # Dùng Transaction từ thư viện mới

# from spl.token.instructions import get_associated_token_address
# import spl.token.instructions as spl_token

from config import *

# Import functions from buy.py
from buy import get_pump_curve_state, calculate_pump_curve_price, buy_token, listen_for_interaction

# Import functions from sell.py
# from sell import sell_token


def log_trade(action, token_data, price, tx_hash):
    os.makedirs("trades", exist_ok=True)
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "token_address": token_data['mint'],
        "price": price,
        "tx_hash": tx_hash
    }
    with open("trades/trades.log", 'a') as log_file:
        json.dump(log_entry, log_file)
        log_file.write("\n")


async def trade(websocket=None, copy_address=None):
    if websocket is None:
        async with websockets.connect(WSS_ENDPOINT, timeout=20) as websocket:
            await _trade(websocket, copy_address)
    else:
        print("Wss Connected ==> Trade Start")
        await _trade(websocket, copy_address)

async def _trade(websocket, copy_address=None):
    while True:
        print(f"Listening for interactions from {copy_address} with pump.fun...")
        
        # Lắng nghe sự kiện từ ví copy_address
        token_data = await listen_for_interaction(websocket, copy_address)
        print(f"_trade token_data: {token_data}")
        if token_data:
            print("Interaction detected:")
            print(json.dumps(token_data, indent=2))

            # Lấy tên và địa chỉ token
            mint_address = token_data['mint']
            token_name = token_data['name']

            print(f"Token name: {token_name}")
            print(f"Token mint address: {mint_address}")

            mint = Pubkey.from_string(mint_address)
            bonding_curve = Pubkey.from_string(token_data['bondingCurve'])
            associated_bonding_curve = Pubkey.from_string(token_data['associatedBondingCurve'])

            # Fetch the token price
            async with AsyncClient(RPC_ENDPOINT) as client:
                curve_state = await get_pump_curve_state(client, bonding_curve)
                token_price_sol = calculate_pump_curve_price(curve_state)

            print(f"Bonding curve address: {bonding_curve}")
            print(f"Token price: {token_price_sol:.10f} SOL")
            print(f"Buying {BUY_AMOUNT:.6f} SOL worth of the new token with {BUY_SLIPPAGE*100:.1f}% slippage tolerance...")
            buy_tx_hash = await buy_token(mint, bonding_curve, associated_bonding_curve, BUY_AMOUNT, BUY_SLIPPAGE)
            if buy_tx_hash:
                log_trade("buy", token_data, token_price_sol, str(buy_tx_hash))
            else:
                print("Buy transaction failed.")
        else:
            print("No interaction detected from the specified address.")

async def main(copy_address=None):
    while True:
        try:
            print(f"Attempting to connect to WebSocket at {WSS_ENDPOINT}...")
            async with websockets.connect(WSS_ENDPOINT , timeout=20) as websocket:
                print("Connected to WebSocket.")
                while True:
                    try:
                        await trade(websocket, copy_address)
                    except websockets.exceptions.ConnectionClosed as e:
                        print(f"WebSocket closed with code {e.code} and reason: {e.reason}")
                        break
                    except Exception as e:
                        print(f"An error occurred during trade: {e}")
                    print("Waiting for 5 seconds before looking for the next token...")
                    await asyncio.sleep(5)        
        except Exception as e:
            print(f"Unexpected error: {e.__class__.__name__}: {e}")
            print("Reconnecting in 6 seconds...")
            await asyncio.sleep(6)

async def ping_websocket(websocket):
    while True:
        try:
            await websocket.ping()
            await asyncio.sleep(20) # Send a ping every 20 seconds
        except:
            break

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trade tokens on Solana.")
    parser.add_argument("--copy", type=str, help="Copy minting from specified wallet address")
    args = parser.parse_args()
    asyncio.run(main(copy_address=args.copy))
