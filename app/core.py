# app/core.py

import json
import asyncio
import random
import hashlib
import hmac
from urllib.parse import urlencode
from typing import Tuple, Dict, Any

import httpx
import requests
from Crypto.Cipher import AES
from google.protobuf import json_format, message

from app.settings import settings
from ff_proto import freefire_pb2 

# ---------------------------------------------------------
# THE VAULT: In-memory storage for active guest accounts
# ---------------------------------------------------------
ACTIVE_ACCOUNTS = {}

def pkcs7_pad(b: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(b) % block_size)
    return b + bytes([pad_len]) * pad_len

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pkcs7_pad(plaintext, 16))

def json_to_proto(json_data: Dict[str, Any], proto_message: message.Message) -> bytes:
    json_format.ParseDict(json_data, proto_message)
    return proto_message.SerializeToString()

# =========================================================
# THE FORGE: Auto-Generates New Free Fire Accounts
# =========================================================
def forge_new_account(region: str):
    """Synchronously registers a brand new Guest Account with Garena."""
    s = b'2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3'
    cid = "100067"
    ua = "GarenaMSDK/4.0.19P9(SM-S908E; Android 11; en; IN)"
    session = requests.Session()
    
    def e(x):
        k = [0,0,0,2,0,1,7,0,0,0,0,0,2,0,1,7,0,0,0,0,0,2,0,1,7,0,0,0,0,0,2,0]
        return bytes(b ^ k[i % len(k)] ^ 48 for i, b in enumerate(x.encode()))
    
    def aes(h):
        c = AES.new(b"Yg&tc%DEuh6%Zc^8", AES.MODE_CBC, b"6oyZDr22E3ychjM%")
        return c.encrypt(pkcs7_pad(bytes.fromhex(h), 16)).hex()
    
    def ev(n):
        r = bytearray()
        while n:
            b = n & 0x7F
            n >>= 7
            r.append(b | (0x80 if n else 0))
        return bytes(r)
    
    def ef(f,v):
        if type(v) == int: return ev((f<<3)|0)+ev(v)
        b = v.encode() if type(v)==str else v
        return ev((f<<3)|2)+ev(len(b))+b
    
    def ep(d):
        p = bytearray()
        for k in sorted(d): p.extend(ef(k,d[k]))
        return p
    
    pwd = str(random.randint(1000000000,9999999999))
    ph = hashlib.sha256(pwd.encode()).hexdigest().upper()
    
    bd = urlencode({'password':ph,'client_type':'2','source':'2','app_id':cid})
    hd = {'User-Agent':ua,'Authorization':f"Signature {hmac.new(s,bd.encode(),hashlib.sha256).hexdigest()}",'Content-Type':'application/x-www-form-urlencoded'}
    
    # 1. Guest Register
    r1 = session.post('https://ffmconnect.live.gop.garenanow.com/oauth/guest/register', data=bd, headers=hd, timeout=10)
    if r1.status_code != 200: return None, None
    
    uid = r1.json().get("uid")
    if not uid: return None, None
    
    # 2. Token Grant
    td = {'uid':str(uid),'password':ph,'response_type':"token",'client_type':"2",'client_secret':s.decode(),'client_id':cid}
    r2 = session.post("https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant", data=td, headers={'User-Agent':ua}, timeout=10)
    if r2.status_code != 200: return None, None
    
    j = r2.json()
    at = j.get("access_token")
    oid = j.get("open_id") or j.get("openId") or j.get("openid")
    if not at or not oid: return None, None
    
    # 3. Major Register
    pf = {1:f"0xMe{''.join('⁰¹²³⁴⁵⁶⁷⁸⁹'[int(d)] for d in str(random.randint(1,9999)))}",2:at,3:oid,5:102000007,6:4,7:1,13:1,14:e(oid),15:region,16:1}
    ed = bytes.fromhex(aes(ep(pf).hex()))
    
    hs = {
        "Authorization":f"Bearer {at}",
        "X-Unity-Version": settings.X_UNITY_VERSION,
        "X-GA":"v1 1",
        "ReleaseVersion": settings.RELEASE_VERSION, 
        "Content-Type":"application/octet-stream",
        "Content-Length":str(len(ed)),
        "User-Agent":ua,
        "Host":"loginbp.ggblueshark.com",
        "Connection":"Keep-Alive",
        "Accept-Encoding":"gzip"
    }
    r3 = session.post('https://loginbp.ggblueshark.com/MajorRegister', data=ed, headers=hs, timeout=10)
    session.close()
    
    if r3.status_code == 200:
        return str(uid), ph
    return None, None

async def trigger_the_forge(region: str):
    """Async wrapper to prevent FastAPI from freezing during generation."""
    print(f"[HEALER] Forging new identity for {region}...")
    new_uid, new_password = await asyncio.to_thread(forge_new_account, region)
    if not new_uid:
        raise RuntimeError(f"Failed to forge new account for {region}")
    ACTIVE_ACCOUNTS[region] = {"uid": new_uid, "password": new_password}
    print(f"[HEALER] Success! New {region} ID minted: {new_uid}")

# =========================================================
# GARENA COMMUNICATION LAYER (Your updated code)
# =========================================================
async def get_access_token(client: httpx.AsyncClient, uid: str, password: str) -> Tuple[str, str]:
    parts = settings.CLIENT_SECRET_PAYLOAD.split('&client_id=')
    client_secret = parts[0]
    client_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 100067

    payload = {
        "client_id": client_id, 
        "client_secret": client_secret,
        "client_type": 2,
        "password": password,
        "response_type": "token",
        "uid": int(uid)
    }
    
    headers = {
        "User-Agent": settings.USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip"
    }
    
    r = await client.post(settings.OAUTH_URL, json=payload, headers=headers, timeout=settings.TIMEOUT)
    r.raise_for_status()
    
    response_json = r.json()
    data = response_json.get("data", {})
    
    if 'error' in data:
        raise RuntimeError(f"Garena API Error: {data.get('error_description', data['error'])}")

    return data.get("access_token", "0"), data.get("open_id", "0")

async def create_jwt(uid: str, password: str) -> Dict[str, str]:
    async with httpx.AsyncClient(http2=False) as client:
        access_token, open_id = await get_access_token(client, uid, password)
        if access_token == "0":
            raise RuntimeError("Failed to obtain access token.")

        login_req = {
            "open_id": open_id,
            "open_id_type": "4",
            "login_token": access_token,
            "orign_platform_type": "4",
        }

        req_msg = freefire_pb2.LoginReq()
        encoded = json_to_proto(login_req, req_msg)
        encrypted_payload = aes_cbc_encrypt(settings.MAIN_KEY, settings.MAIN_IV, encoded)

        major_login_ua = "Dalvik/2.1.0 (Linux; U; Android 15; I2404 Build/AP3A.240905.015.A2_V000L1)"
        
        headers = {
            "User-Agent": major_login_ua,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/octet-stream",
            "Expect": "100-continue",
            "X-Unity-Version": settings.X_UNITY_VERSION,
            "X-GA": "v1 1",
            "ReleaseVersion": settings.RELEASE_VERSION,
        }

        r = await client.post(
            settings.MAJOR_LOGIN_URL,
            content=encrypted_payload,
            headers=headers,
            timeout=settings.TIMEOUT,
        )
        r.raise_for_status()

        res_msg = freefire_pb2.LoginRes()
        res_msg.ParseFromString(r.content)

        token = res_msg.token if res_msg.token else "0"
        lock_region = res_msg.lock_region if res_msg.lock_region else ""
        server_url = res_msg.server_url if res_msg.server_url else ""

        if token == "0" or len(token) == 0:
            res_dict = json.loads(json_format.MessageToJson(res_msg))
            raise RuntimeError(f"Failed to obtain JWT. Response details: {res_dict}")

        return {
            "token": token,
            "lockRegion": lock_region,
            "serverUrl": server_url,
        }

# =========================================================
# THE MASTER HEALING LOOP (Called by your API routes)
# =========================================================
async def get_secure_jwt_with_healing(region: str) -> Dict[str, str]:
    """Wraps create_jwt in a self-healing retry loop."""
    region = region.upper()
    
    if region not in ACTIVE_ACCOUNTS:
        await trigger_the_forge(region)
        
    max_retries = 2
    for attempt in range(max_retries):
        creds = ACTIVE_ACCOUNTS[region]
        try:
            return await create_jwt(creds["uid"], creds["password"])
        except Exception as e:
            # If Garena throws a 400/401 Bad Request, the account is burned
            print(f"[!] JWT Fetch failed for {region} (Attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await trigger_the_forge(region)
            else:
                raise RuntimeError("Self-healing loop failed after maximum retries.")
