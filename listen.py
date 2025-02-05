import asyncio
import json
import websockets
import base58
import base64
import struct
import sys
import os
import argparse
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.transaction import Transaction

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import (
    WSS_ENDPOINT, 
    PUMP_PROGRAM,
    SYSTEM_TOKEN_PROGRAM as TOKEN_PROGRAM_ID,
    SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM as ATA_PROGRAM_ID
)

def find_associated_bonding_curve(mint: Pubkey, bonding_curve: Pubkey) -> Pubkey:
    """
    Find the associated bonding curve for a given mint and bonding curve.
    This uses the standard ATA derivation.
    """
    derived_address, _ = Pubkey.find_program_address(
        [
            bytes(bonding_curve),
            bytes(TOKEN_PROGRAM_ID),
            bytes(mint),
        ],
        ATA_PROGRAM_ID
    )
    return derived_address

# Load the IDL JSON file
with open('idl/pump_fun_idl.json', 'r') as f:
    idl = json.load(f)

# Extract the "create" instruction definition
create_instruction = next(instr for instr in idl['instructions'] if instr['name'] == 'create')

def parse_create_instruction(data):
    if len(data) < 8:
        return None
    offset = 8
    parsed_data = {}

    # Parse fields based on CreateEvent structure
    fields = [
        ('name', 'string'),
        ('symbol', 'string'),
        ('uri', 'string'),
        ('mint', 'publicKey'),
        ('bondingCurve', 'publicKey'),
        ('associatedBondingCurve ', 'publicKey'),
        ('associatedUser ', 'publicKey'),       
        ('user', 'publicKey'),
        ('source', 'publicKey'),
    ]

    try:
        for field_name, field_type in fields:
            if field_type == 'string':
                length = struct.unpack('<I', data[offset:offset+4])[0]
                offset += 4
                value = data[offset:offset+length].decode('utf-8')
                offset += length
            elif field_type == 'publicKey':
                value = base58.b58encode(data[offset:offset+32]).decode('utf-8')
                offset += 32

            parsed_data[field_name] = value

        return parsed_data
    except:
        return None

def print_transaction_details(log_data):
    print(f"Signature: {log_data.get('signature')}")
    
    for log in log_data.get('logs', []):
        if log.startswith("Program data:"):
            try:
                data = base58.b58decode(log.split(": ")[1]).decode('utf-8')
                print(f"Data: {data}")
            except:
                pass

async def listen_for_copy(websocket, copy_address):
    while True:
        try:
            async with websockets.connect(WSS_ENDPOINT) as websocket:
                subscription_message = json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [str(PUMP_PROGRAM)]},
                        {"commitment": "processed"}
                    ]
                })
                await websocket.send(subscription_message)
                print(f"Listening for new token creations from program: {PUMP_PROGRAM}")

                # Wait for subscription confirmation
                response = await websocket.recv()
                print(f"Subscription response: {response}")

                while True:
                    try:
                        response = await websocket.recv()
                        data = json.loads(response)
                        print(f"DATA data: {data}")
                        if 'method' in data and data['method'] == 'logsNotification':
                            log_data = data['params']['result']['value']
                            logs = log_data.get('logs', [])
                            
                            if any("Program log: Instruction: Buy" in log for log in logs):
                                for log in logs:
                                    if "Program data:" in log:
                                        try:
                                            encoded_data = log.split(": ")[1]
                                            decoded_data = base64.b64decode(encoded_data)
                                            print(f"Listen decoded_data: {decoded_data}")
                                            
                                            parsed_data = parse_create_instruction(decoded_data)
                                            print(f"Listen parsed_data: {parsed_data}")
                                            
                                            temp_address = Pubkey.from_string(str(copy_address))
                                            print(f"Listen temp_address: {temp_address}")
                                            if parsed_data and 'name' in parsed_data:
                                                if (parsed_data['associatedBondingCurve'] != temp_address or
                                                    parsed_data['user'] != temp_address or
                                                    parsed_data['mint'] != temp_address):
                                                    print(f"Different copy_address: {copy_address}")
                                                    continue  # Skip if it doesn't match the copy address

                                                print(f"Yesssssssssssssssssss copy_address found: {copy_address}")
                                                
                                                # Calculate associated bonding curve
                                                mint = Pubkey.from_string(parsed_data['mint'])
                                                bonding_curve = Pubkey.from_string(parsed_data['bondingCurve'])
                                                associated_curve = find_associated_bonding_curve(mint, bonding_curve)
                                                print(f"Associated Bonding Curve: {associated_curve}")
                                                print("##########################################################################################")
                                        except Exception as e:
                                            print(f"Failed to decode: {log}")
                                            print(f"Error: {str(e)}")

                    except Exception as e:
                        print(f"An error occurred while processing message: {e}")
                        break

        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 5 seconds...")
            await asyncio.sleep(5)
            
def get_transaction_details(signature):
    url = "https://api.mainnet-beta.solana.com"  # Solana mainnet endpoint
    headers = {"Content-Type": "application/json"}
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [signature, "json"]
    })

    response = requests.post(url, headers=headers, data=payload)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to fetch transaction details: {response.status_code}")
        return None
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Listen for new tokens and filter by copy address")
    parser.add_argument("--copy", type=str, help="Copy interactions from specified wallet address")
    args = parser.parse_args()

    asyncio.run(listen_for_new_tokens(copy_address=args.copy))