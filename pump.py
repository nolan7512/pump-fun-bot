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

from solana.rpc.async_api import AsyncClient
from solana.transaction import Transaction
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts

from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.instruction import Instruction, AccountMeta
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from spl.token.instructions import get_associated_token_address
import spl.token.instructions as spl_token

from config import *

# Import functions from buy.py
from buy import get_pump_curve_state, calculate_pump_curve_price, buy_token

# Import functions from sell.py
from sell import sell_token

async def listen_for_interaction(websocket, copy_address):
    idl = load_idl('idl/pump_fun_idl.json')
    buy_discriminator = 16927863322537952870
    mint_discriminator = 8576854823835016728
    
    subscription_message = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "blockSubscribe",
        "params": [
            {"mentionsAccountOrProgram": str(PUMP_PROGRAM)},
            {
                "commitment": "processed",
                "encoding": "base64",
                "showRewards": False,
                "transactionDetails": "full",
                "maxSupportedTransactionVersion": 0
            }
        ]
    })
    await websocket.send(subscription_message)
    print(f"Subscribed to blocks mentioning program: {PUMP_PROGRAM}")

    ping_interval = 20
    last_ping_time = time()

    while True:
        try:
            current_time = time()
            if current_time - last_ping_time > ping_interval:
                await websocket.ping()
                last_ping_time = current_time

            response = await asyncio.wait_for(websocket.recv(), timeout=30)
            data = json.loads(response)
            
            if 'method' in data and data['method'] == 'blockNotification':
                if 'params' in data and 'result' in data['params']:
                    block_data = data['params']['result']
                    if 'value' in block_data and 'block' in block_data['value']:
                        block = block_data['value']['block']
                        if 'transactions' in block:
                            for tx in block['transactions']:
                                if isinstance(tx, dict) and 'transaction' in tx:
                                    tx_data_decoded = base64.b64decode(tx['transaction'][0])
                                    transaction = VersionedTransaction.from_bytes(tx_data_decoded)
                                    
                                    for ix in transaction.message.instructions:
                                        if str(transaction.message.account_keys[ix.program_id_index]) == str(PUMP_PROGRAM):
                                            ix_data = bytes(ix.data)
                                            discriminator = struct.unpack('<Q', ix_data[:8])[0]
                                            
                                            if discriminator in [buy_discriminator, mint_discriminator]:
                                                account_keys = [str(transaction.message.account_keys[index]) for index in ix.accounts]
                                                if copy_address in account_keys:
                                                    create_ix = next(instr for instr in idl['instructions'] if instr['name'] == 'create')
                                                    decoded_args = decode_create_instruction(ix_data, create_ix, account_keys)
                                                    return decoded_args
        except asyncio.TimeoutError:
            print("No data received for 30 seconds, sending ping...")
            await websocket.ping()
            last_ping_time = time()
        except websockets.exceptions.ConnectionClosed:
            print("WebSocket connection closed. Reconnecting...")
            raise

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
        async with websockets.connect(WSS_ENDPOINT) as websocket:
            await _trade(websocket, copy_address)
    else:
        await _trade(websocket, copy_address)

async def _trade(websocket, copy_address=None):
    while True:
        print(f"Listening for interactions from {copy_address} with pump.fun...")
        
        # Lắng nghe sự kiện từ ví copy_address
        token_data = await listen_for_interaction(websocket, copy_address)
        
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
            async with websockets.connect(WSS_ENDPOINT) as websocket:
                while True:
                    try:
                        await trade(websocket, copy_address)
                    except websockets.exceptions.ConnectionClosed:
                        print("WebSocket connection closed. Reconnecting...")
                        break
                    except Exception as e:
                        print(f"An error occurred: {e}")
                    print("Waiting for 5 seconds before looking for the next token...")
                    await asyncio.sleep(5)
        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

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
