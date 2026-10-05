#!/usr/bin/env python3
"""
HOT Wallet Auto Claim Bot
Claim harian $HOT otomatis untuk akun NEAR (game.hot.tg).

Cara kerja (sesuai reverse-engineering web app app.hot-labs.org):
  1. get_user(account_id) via RPC view -> game_state
  2. POST /api/v1/user/hot/claim/signature  (JWT auth) -> {signature, mining_time, max_ts}
  3. JWT didapat dari POST /api/v1/user/auth dengan auth_intent (NEAR intents signature)
  4. functionCall game.hot.tg l2_claim {charge_gas_fee, signature, mining_time, max_ts}

Semua private key dibaca dari accounts.json (lokal, tidak pernah dikirim ke mana pun).
"""

import asyncio
import base64
import hashlib
import json
import os
import random
import secrets
import time
from datetime import datetime, timedelta, timezone

import base58
import requests
from nacl.signing import SigningKey
from py_near_primitives import Transaction, FunctionCallAction

# ── Konstanta ───────────────────────────────────────────────────────────────
RPC_URL = "https://rpc.mainnet.near.org"
API_BASE = "https://api0.herewallet.app"
CONTRACT = "game.hot.tg"
INTENTS_CONTRACT = "intents.near"
CHAIN_ID = "mainnet"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

# Delay claim: tunggu storage FULL baru claim (bukan delay fix).
# Kapasitas storage dihitung dari level (tabel dari web app HOT).
# Rumus: capacity_hours = value / 36e11
STORAGE_LEVELS = {
    20: 72e11,   # 2 jam
    21: 108e11,  # 3 jam
    22: 144e11,  # 4 jam
    23: 216e11,  # 6 jam
    24: 432e11,  # 12 jam
    25: 864e11,  # 24 jam
}

# Anti-bot: akun di-sebar saat init (1x), lalu tiap cycle jeda-nya
# berubah sendiri karena tiap akun nunggu storage full + jitter random.
# Jitter setelah full: 5-90 menit (re-roll tiap cycle)
JITTER_AFTER_FULL_MIN_SEC = 5 * 60
JITTER_AFTER_FULL_MAX_SEC = 90 * 60
# Jeda minimum antar akun biar gak berdesakan
MIN_GAP_SEC = 20 * 60


# ── Logging (warna ANSI) ─────────────────────────────────────────────────────
class C:
    R = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GRN = "\033[92m"
    YLW = "\033[93m"
    BLU = "\033[94m"
    CYN = "\033[96m"
    GRY = "\033[90m"


def _color_for(level):
    return {
        "ERROR": C.RED,
        "WARN": C.YLW,
        "OK": C.GRN,
        "INFO": C.CYN,
    }.get(level, C.CYN)


def log(msg, level="INFO", plain=False):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if plain:
        print(msg, flush=True)
    else:
        col = _color_for(level)
        print(f"{C.GRY}[{ts}]{C.R} {col}{level:<5}{C.R} {msg}", flush=True)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        # tulis versi plain (tanpa ANSI) ke file log
        import re as _re
        clean = _re.sub(r"\033\[[0-9;]*m", "", msg)
        with open(LOG_FILE, "a") as f:
            f.write(f"[{ts}] [{level}] {clean}\n")
    except Exception:
        pass


def banner():
    print(f"{C.YLW}{C.BOLD}", flush=True)
    print("    ██╗  ██╗   ██╗   ███████╗", flush=True)
    print("    ██║ ██╔╝   ██║   ╚════██║", flush=True)
    print("    █████╔╝    ██║       ██║", flush=True)
    print("    ██╔═██╗    ██║       ██║", flush=True)
    print("    ██║  ██╗   ██║   ██╗ ██║", flush=True)
    print("    ╚═╝  ╚═╝   ╚═╝   ╚═╝ ╚═╝", flush=True)
    print(f"{C.R}{C.BOLD}  HOT WALLET AUTO CLAIM{C.R} {C.GRY}· tunggu storage full{C.R}", flush=True)
    print(f"{C.GRY}  contract: game.hot.tg · backend: api0.herewallet.app{C.R}", flush=True)
    print(C.GRY + "  " + "─" * 48 + C.R, flush=True)


def table_init(rows):
    """rows: list of (account, level, full_h, slot_time, wait_h)"""
    print(f"\n{C.BOLD}  AKUN{C.R}      {C.BOLD}LVL{C.R} {C.BOLD}FULL{C.R}    {C.BOLD}SLOT{C.R}      {C.BOLD}TUNGGU{C.R}", flush=True)
    print(C.GRY + "  " + "─" * 46 + C.R, flush=True)
    for aid, lvl, full_h, slot, wait_h in rows:
        print(f"  {C.CYN}{aid:<10}{C.R} {C.YLW}L{lvl:<2}{C.R} "
              f"{full_h:>6.1f}h  {C.GRN}{slot}{C.R}  {C.GRY}{wait_h:>5.1f}h{C.R}", flush=True)
    print(C.GRY + "  " + "─" * 46 + C.R, flush=True)
    print(f"  {C.DIM}total {len(rows)} akun · tunggu storage full, lalu claim{C.R}\n", flush=True)


def claim_box(aid, tx, mined, before, after, next_slot):
    b = C.GRY + "  ┌" + "─" * 48 + "┐" + C.R
    print(f"\n{b}", flush=True)
    print(f"  {C.BOLD}⚡ CLAIM BERHASIL · {C.GRN}{aid}{C.R}", flush=True)
    print(f"  {C.GRY}├────────────────────────────────────────────────┤{C.R}", flush=True)
    print(f"  {C.BOLD}HOT sebelum{C.R} : {before:.4f}", flush=True)
    print(f"  {C.BOLD}HOT sesudah{C.R}: {C.GRN}{after:.4f}{C.R}", flush=True)
    print(f"  {C.BOLD}Masuk{C.R}      : {C.GRN}+{mined:.6f} HOT{C.R} 🔥", flush=True)
    print(f"  {C.BOLD}TX{C.R}         : {C.CYN}{tx[:20]}...{C.R}", flush=True)
    print(f"  {C.GRY}├────────────────────────────────────────────────┤{C.R}", flush=True)
    print(f"  {C.BOLD}Next claim {C.R}: {C.YLW}{next_slot}{C.R}", flush=True)
    print(C.GRY + "  └" + "─" * 48 + "┘" + C.R, flush=True)


# ── NEAR helpers ────────────────────────────────────────────────────────────
def base58_encode(data: bytes) -> str:
    return base58.b58encode(data).decode("ascii")


def base58_decode(s: str) -> bytes:
    return base58.b58decode(s)


def near_sha256_bytes(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def parse_near_key(key_str: str):
    """Parse private key format NEAR. Return (signing_key, public_key_str)."""
    key_str = key_str.strip()
    if key_str.startswith("ed25519:"):
        raw = base58_decode(key_str.split(":", 1)[1])
    else:
        raw = base58_decode(key_str)
    sk = SigningKey(raw[:32])
    pk = base58_encode(bytes(sk.verify_key))
    return sk, f"ed25519:{pk}"


# ── RPC ─────────────────────────────────────────────────────────────────────
# Multiple RPC - rpc.mainnet.near.org limit request ketat (429 kalau 14 akun
# beruntun). Rotate ke RPC lain kalau ada yang kena rate limit.
RPC_URLS = [
    "https://rpc.mainnet.near.org",
    "https://rpc.mainnet.fastnear.com",
    "https://near.drpc.org",
    "https://1rpc.io/near",
]
_rpc_idx = 0


def rpc_call(method, params):
    """Call NEAR JSON-RPC. Return result or raise."""
    global _rpc_idx
    payload = {"jsonrpc": "2.0", "id": int(time.time()), "method": method, "params": params}
    last_err = None
    for attempt in range(6):
        url = RPC_URLS[_rpc_idx % len(RPC_URLS)]
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 429:
                # rate limit: ganti RPC
                _rpc_idx += 1
                raise RuntimeError("429 rate limit, ganti RPC")
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"RPC error: {data['error']}")
            return data.get("result")
        except Exception as e:
            last_err = e
            # kalau RPC ini bermasalah, coba yg lain
            _rpc_idx += 1
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"semua RPC gagal: {last_err}")


def view_function(contract, method, args_dict):
    args_b64 = base64.b64encode(json.dumps(args_dict).encode()).decode()
    res = rpc_call("query", {
        "request_type": "call_function",
        "finality": "final",
        "account_id": contract,
        "method_name": method,
        "args_base64": args_b64,
    })
    if res.get("error"):
        raise RuntimeError(f"view error {contract}:{method}: {res['error']}")
    inner = res.get("result", res)
    # RPC lama: {"result": {"result": [..bytes..]}}  |  RPC baru: {"result": {"result": {"result": [...]}}}
    while isinstance(inner, dict) and isinstance(inner.get("result"), dict):
        inner = inner["result"]
    arr = inner["result"] if isinstance(inner, dict) else inner
    # arr bisa list of byte-values atau bytes langsung
    if isinstance(arr, list):
        raw = bytes(arr)
    else:
        raw = bytes(arr)
    return json.loads(raw.decode()) if raw else None


def resolve_account_id(public_key):
    """Cari account_id dari public key via nearblocks."""
    url = f"https://api.nearblocks.io/v1/keys/{public_key}"
    # nearblocks membatasi ~10 req/menit -> retry kalau kena 429
    # juga handle DNS/connection error (jaringan HP sering flaky)
    backoff = 10
    for attempt in range(6):
        try:
            r = requests.get(url, timeout=15)
        except requests.exceptions.RequestException:
            time.sleep(backoff)
            backoff += 15
            continue
        if r.status_code == 429:
            time.sleep(20)
            continue
        if r.status_code != 200:
            return None
        data = r.json()
        keys = [k for k in (data.get("keys") or []) if k.get("account_id")]
        if not keys:
            return None
        return keys[0].get("account_id")
    return None


def auto_resolve_account_ids(accounts):
    """Isi account_id otomatis untuk akun yang cuma punya private_key."""
    changed = False
    for acc in accounts:
        if acc.get("account_id"):
            continue
        if not acc.get("private_key"):
            continue
        try:
            _, pk = parse_near_key(acc["private_key"])
            aid = resolve_account_id(pk)
            if aid:
                acc["account_id"] = aid
                changed = True
                log(f"  resolve: {pk[:25]}... -> {aid}", "OK")
            else:
                log(f"  resolve: {pk[:25]}... -> TIDAK KETEMU akun", "ERROR")
        except Exception as e:
            log(f"  resolve error: {e}", "ERROR")
        # nearblocks membatasi ~10 req/menit -> jeda 7s biar gak kena 429
        time.sleep(7)
    return changed


def get_account_state(account_id):
    res = rpc_call("query", {
        "request_type": "view_account",
        "finality": "final",
        "account_id": account_id,
    })
    if "error" in res:
        return None
    return res


def get_access_key(account_id, public_key):
    res = rpc_call("query", {
        "request_type": "view_access_key",
        "finality": "final",
        "account_id": account_id,
        "public_key": public_key,
    })
    if "error" in res:
        return None
    return res


def get_recent_block_hash():
    res = rpc_call("block", {"finality": "final"})
    return res["header"]["hash"]


def get_account_nonce(account_id, public_key):
    key = get_access_key(account_id, public_key)
    if key is None:
        return None
    return key["nonce"]


def is_pk_registered_intents(account_id, public_key):
    """Cek apakah public key udah terdaftar di intents.near."""
    try:
        res = view_function(INTENTS_CONTRACT, "public_keys_of",
                            {"account_id": account_id})
        return res is not None and public_key in res
    except Exception:
        return False


def register_pk_intents(sk, account_id, pk_str):
    """Daftarin public key ke intents.near (sama kaya web app HOT).
    Diperlukan sekali per akun sebelum auth backend bisa verifikasi."""
    nonce = (get_account_nonce(account_id, pk_str) or 0) + 1
    block_hash = get_recent_block_hash()
    actions = [{
        "functionCall": {
            "methodName": "add_public_key",
            "args": {"public_key": pk_str},
            "gas": 80 * 10**12,
            "deposit": 1,  # 1 yocto
        },
    }]
    signed = build_signed_tx(sk, account_id, INTENTS_CONTRACT, actions,
                             nonce, block_hash, pk_str)
    tx_res = send_tx(signed)
    if "error" in tx_res:
        raise RuntimeError(f"add_public_key failed: {tx_res['error']}")
    time.sleep(3)
    return True


# ── Signature NEAR (transaksi) ──────────────────────────────────────────────
def sign_transaction(signing_key, receiver_id, actions, nonce, block_hash, account_id):
    """Buat & sign transaksi NEAR manual (borsh-like serialization)."""
    # SERDE/JSON format yang diterima RPC send_transaction
    public_key = f"ed25519:{base58_encode(bytes(signing_key.verify_key))}"
    signed_actions = []
    for a in actions:
        signed_actions.append({
            "enum": "functionCall",
            "functionCall": {
                "methodName": a["functionCall"]["methodName"],
                "args": base64.b64encode(
                    json.dumps(a["functionCall"]["args"]).encode()
                ).decode(),
                "gas": str(a["functionCall"]["gas"]),
                "deposit": str(a["functionCall"]["deposit"]),
            },
        })
    tx = {
        "signerId": account_id,
        "publicKey": public_key,
        "nonce": nonce,
        "receiverId": receiver_id,
        "actions": signed_actions,
        "blockHash": block_hash,
    }
    # Serialize & sign (kompilasi pesan)
    msg = serialize_transaction(tx)
    sig = signing_key.sign(msg).signature
    return {
        "transaction": tx,
        "signature": {
            "keyType": 0,
            "data": base64.b64encode(sig).decode(),
        },
    }


def serialize_transaction(tx):
    """Serialisasi transaksi NEAR ke bytes untuk di-sign.
    Format borsh manual: lihat nearcore/protocol/transaction.rs
    """
    buf = bytearray()
    # signerId (string)
    _write_string(buf, tx["signerId"])
    # publicKey (ed25519: enum 0 + 32 bytes)
    buf.append(0)
    pk_raw = base58_decode(tx["publicKey"].split(":", 1)[1])
    buf.extend(pk_raw)
    # nonce (u64 LE)
    buf.extend((tx["nonce"]).to_bytes(8, "little"))
    # receiverId (string)
    _write_string(buf, tx["receiverId"])
    # blockHash (32 bytes)
    buf.extend(base58_decode(tx["blockHash"]))
    # actions (vec)
    _write_len_prefix(buf, len(tx["actions"]))
    for a in tx["actions"]:
        _write_action(buf, a)
    return bytes(buf)


def _write_string(buf, s):
    data = s.encode("utf-8")
    _write_len_prefix(buf, len(data))
    buf.extend(data)


def _write_len_prefix(buf, n):
    # borsh u128 little endian 4 bytes untuk length
    buf.extend(n.to_bytes(4, "little"))


def _write_action(buf, a):
    fc = a["functionCall"]
    buf.append(2)  # ActionVariant::FunctionCall
    _write_string(buf, fc["methodName"])
    args = base64.b64decode(fc["args"])
    _write_len_prefix(buf, len(args))
    buf.extend(args)
    # gas u64 LE
    buf.extend(int(fc["gas"]).to_bytes(8, "little"))
    # deposit u128 LE 16 bytes
    buf.extend(int(fc["deposit"]).to_bytes(16, "little"))


def send_tx(signed_tx):
    res = rpc_call("broadcast_tx_commit", [signed_tx])
    return res


def build_signed_tx(sk, account_id, receiver_id, actions, nonce, block_hash, pk_str):
    """Buat transaksi borsh siap kirim (base64) pakai py_near_primitives."""
    pk_bytes = bytes(sk.verify_key)
    fc_actions = []
    for a in actions:
        fc = a["functionCall"]
        fc_actions.append(FunctionCallAction(
            fc["methodName"],
            json.dumps(fc["args"]).encode(),
            int(fc["gas"]),
            int(fc["deposit"]),
        ))
    tx = Transaction(
        account_id,
        pk_bytes,
        nonce,
        receiver_id,
        base58_decode(block_hash),
        fc_actions,
    )
    # serialize transaksi (tanpa signature), sign hash-nya, gabung borsh
    tx_bytes = tx.serialize()
    sig = sk.sign(tx.get_hash()).signature
    signed_borsh = tx_bytes + b"\x00" + sig  # enum 0 = ed25519 + 64 bytes
    return base64.b64encode(signed_borsh).decode()


# ── Intents signature (untuk auth backend) ──────────────────────────────────
def sign_auth_intent(signing_key, account_id):
    """Sign NEAR intent seperti signAuthIntents() di web app HOT.

    Format (dari reverse-engineering backend api0.herewallet.app):
      standard: raw_ed25519
      payload: {signer_id, deadline, intents: [], nonce, verifying_contract}
      nonce = base64(sha256("hot_auth_intent_" + auth_seed))
      signature = ed25519.sign(payload_json_bytes)
    """
    seed = secrets.token_hex(32)
    # nonce: sha256("hot_auth_intent_<seed>") -> base64
    nonce_tag = f"hot_auth_intent_{seed}"
    nonce_b64 = base64.b64encode(
        hashlib.sha256(nonce_tag.encode("utf-8")).digest()
    ).decode()

    # deadline: 24 jam ke depan (sama kaya web app)
    deadline = (datetime.now(timezone.utc) + timedelta(hours=24)) \
        .replace(microsecond=0).isoformat().replace("+00:00", ".000Z")

    payload_str = json.dumps({
        "signer_id": account_id.lower(),
        "deadline": deadline,
        "intents": [],
        "nonce": nonce_b64,
        "verifying_contract": INTENTS_CONTRACT,
    }, separators=(",", ":"))

    sig = signing_key.sign(payload_str.encode("utf-8")).signature
    return {
        "signature": f"ed25519:{base58_encode(sig)}",
        "public_key": f"ed25519:{base58_encode(bytes(signing_key.verify_key))}",
        "standard": "raw_ed25519",
        "payload": payload_str,
    }, seed


# ── Backend API ─────────────────────────────────────────────────────────────
class HotApi:
    def __init__(self, device_id="hot-claimer-bot"):
        self.device_id = device_id
        self.jwt = None

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "Device-Id": self.device_id,
            "DeviceId": self.device_id,
            "Platform": "web",
            "Version": "1.0",
            # backend api0.herewallet.app 500 kalau gak ada header web browser
            "Origin": "https://app.hot-labs.org",
            "Referer": "https://app.hot-labs.org/",
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/129.0.0.0 Safari/537.36"),
        }
        if self.jwt:
            # backend HOT: Authorization tanpa prefix "Bearer"
            h["Authorization"] = self.jwt
        return h

    def post(self, path, body):
        url = f"{API_BASE}{path}"
        for attempt in range(3):
            try:
                r = requests.post(url, json=body, headers=self._headers(), timeout=30)
                if r.status_code == 401:
                    raise PermissionError("JWT invalid/expired - need re-auth")
                if r.status_code == 403:
                    raise PermissionError(f"Forbidden: {r.text[:200]}")
                if r.status_code >= 500:
                    raise RuntimeError(f"HTTP {r.status_code}")
                return r.json()
            except PermissionError:
                raise
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))

    def auth(self, signing_key, account_id, public_key):
        intent, seed = sign_auth_intent(signing_key, account_id)
        body = {
            "imported": True,
            "near_account_id": account_id,
            "device_id": self.device_id,
            "device_name": "HOTClaimBot/1.0",
            "ref_code": None,
            "auth_intent": intent,
            "auth_seed": seed,
        }
        log(f"  auth: POST /api/v1/user/auth (account={account_id})")
        res = self.post("/api/v1/user/auth", body)
        if "token" not in res:
            raise RuntimeError(f"auth failed: {json.dumps(res)[:300]}")
        self.jwt = res["token"]
        log(f"  auth: JWT acquired ✓")
        return self.jwt

    def get_claim_signature(self, game_state, charge_gas_fee=False):
        body = {"game_state": game_state}
        res = self.post("/api/v1/user/hot/claim/signature", body)
        if "signature" not in res:
            raise RuntimeError(f"signature endpoint: {json.dumps(res)[:300]}")
        return res


# ── Game state ──────────────────────────────────────────────────────────────
def get_game_state(account_id):
    return view_function(CONTRACT, "get_user", {"account_id": account_id})


# ── Claim satu akun ─────────────────────────────────────────────────────────
def claim_account(account_id, private_key, charge_gas_fee=False):
    sk, pk_str = parse_near_key(private_key)

    # 1) cek akun & access key
    st = get_account_state(account_id)
    if st is None:
        return {"ok": False, "error": f"Akun NEAR {account_id} tidak ditemukan"}
    log(f"  balance: {int(st['amount'])/1e24:.4f} NEAR")

    # 2) game state
    gs = get_game_state(account_id)
    if gs is None:
        return {"ok": False, "error": "Belum terdaftar di game.hot.tg (get_user null)"}
    last_claim_ns = gs.get("last_claim", 0)
    last_claim_s = last_claim_ns / 1e9
    balance_hot = gs.get("balance", 0) / 1e18
    log(f"  game: balance={balance_hot:.4f} HOT, storage={gs.get('storage')}, "
        f"last_claim={datetime.fromtimestamp(last_claim_s).strftime('%H:%M:%S')}")

    # 3) daftarin pk ke intents.near kalau belum (wajib sebelum auth)
    if not is_pk_registered_intents(account_id, pk_str):
        log(f"  daftar pk ke intents.near (1x)...", "INFO")
        register_pk_intents(sk, account_id, pk_str)
        log(f"  pk terdaftar ✓", "OK")

    # 4) auth ke backend
    api = HotApi(device_id=f"hotbot-{account_id.split('.')[0]}")
    api.auth(sk, account_id, pk_str)

    # 4) minta signature
    sig_res = api.get_claim_signature(gs, charge_gas_fee)
    log(f"  signature acquired: mining_time={sig_res.get('mining_time')}, "
        f"max_ts={sig_res.get('max_ts')}")

    # 5) kirim transaksi l2_claim
    nonce = (get_account_nonce(account_id, pk_str) or 0) + 1
    block_hash = get_recent_block_hash()
    actions = [{
        "functionCall": {
            "methodName": "l2_claim",
            "args": {
                "charge_gas_fee": bool(charge_gas_fee),
                "signature": str(sig_res["signature"]),
                "mining_time": str(sig_res["mining_time"]),
                "max_ts": str(sig_res["max_ts"]),
            },
            "gas": 30 * 10**12,  # 30 TGas (web app: 20 TGas + margin)
            "deposit": 0,
        },
    }]
    signed = build_signed_tx(sk, account_id, CONTRACT, actions, nonce, block_hash, pk_str)
    tx_res = send_tx(signed)

    if "error" in tx_res:
        return {"ok": False, "error": f"TX failed: {tx_res['error']}"}

    outcomes = tx_res.get("receipts_outcome", [])
    success = all(o.get("outcome", {}).get("status", {}).get("SuccessValue") is not None or
                  "SuccessValue" in o.get("outcome", {}).get("status", {}) or
                  o.get("outcome", {}).get("status", {}).get("SuccessValue", "") == ""
                  for o in outcomes) if outcomes else True
    tx_hash = tx_res.get("transaction", {}).get("hash", "?")
    new_gs = get_game_state(account_id)
    new_bal = (new_gs or {}).get("balance", 0) / 1e18
    return {
        "ok": True,
        "tx_hash": tx_hash,
        "balance_before": balance_hot,
        "balance_after": new_bal,
        "mined": new_bal - balance_hot,
    }


# ── Scheduler ───────────────────────────────────────────────────────────────
def load_accounts():
    """Load accounts.json. account_id opsional - di-resolve dari PK otomatis."""
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE) as f:
        data = json.load(f)
    accounts = data.get("accounts", [])
    # resolve account_id yang kosong
    if any(a.get("private_key") and not a.get("account_id") for a in accounts):
        log("Resolve account_id dari private key...", "INFO")
        if auto_resolve_account_ids(accounts):
            # simpan balik biar next run gak resolve lagi
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(ACCOUNTS_FILE, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass
    return accounts


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def storage_capacity_ms(storage_level):
    """Kapasitas storage dalam ms, dari tabel level web app HOT."""
    value = STORAGE_LEVELS.get(storage_level, 864e11)  # default 24 jam
    return value / 1e6  # value -> ms


def seconds_until_full(game_state):
    """Hitung berapa detik lagi storage full berdasarkan last_claim & level."""
    level = game_state.get("storage", 25)
    capacity_ms = storage_capacity_ms(level)
    last_claim_ms = game_state.get("last_claim", 0) / 1e6  # ns -> ms
    now_ms = time.time() * 1000
    remaining_ms = capacity_ms - (now_ms - last_claim_ms)
    return max(0, remaining_ms / 1000)


def account_slot_offset(account_id, all_ids):
    """Sebaran awal (1x) saat bot mulai. Tiap akun dapat porsi sendiri
    di 24 jam + jitter. Abis ini tiap cycle jeda-nya berubah sendiri
    karena tiap akun nunggu storage full + re-roll jitter."""
    n = len(all_ids)
    if n == 1:
        return 0.5
    ranked = sorted(all_ids, key=lambda a: hashlib.sha256(a.encode()).hexdigest())
    rank = ranked.index(account_id)
    h = int(hashlib.sha256(account_id.encode()).hexdigest(), 16)
    # jitter awal lebih lebar biar sebaran awal kelihatan natural
    jitter = ((h >> 40) % 1000) / 1000.0  # 0..1
    jitter = (jitter - 0.5) * (1.0 / n) * 0.9
    return (rank / n + jitter) % 1.0


def jitter_after_full(account_id, cycle):
    """Jitter SETELAH storage full. Re-roll tiap cycle (jam berubah tiap hari).
    5-90 menit, jadi jeda antar akun gak pernah sama tiap hari."""
    seed_str = f"{account_id}:{cycle}"
    h = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    rng = random.Random(h)
    return rng.uniform(JITTER_AFTER_FULL_MIN_SEC, JITTER_AFTER_FULL_MAX_SEC)


def aligned_next_claim(account_id, all_ids, capacity_sec, last_claim_sec, cycle=0):
    """Jadwalkan claim:
    1. Sebar awal (1x) supaya 20 akun gak barengan
    2. Setelah itu: tunggu storage full + jitter 5-90 menit (re-roll tiap cycle)
    """
    n = len(all_ids)
    if cycle == 0:
        # sebar awal: slot di hari ini, pasti setelah full
        slot = account_slot_offset(account_id, all_ids)
        T_full = last_claim_sec + capacity_sec
        day_start = (T_full // 86400) * 86400
        slot_sec = day_start + slot * 86400
        while slot_sec < T_full:
            slot_sec += 86400
        return slot_sec
    # cycle berikutnya: full + jitter random
    T_full = last_claim_sec + capacity_sec
    jitter = jitter_after_full(account_id, cycle)
    return T_full + jitter


def main_loop():
    banner()
    accounts = load_accounts()
    if not accounts:
        log(f"Tidak ada akun di {ACCOUNTS_FILE}", "ERROR")
        log("Buat data/accounts.json dulu (lihat README).", "ERROR")
        return
    log(f"Loaded {len(accounts)} akun", "OK")

    # Buang akun yang account_id-nya masih kosong (gagal resolve / key salah)
    unresolved = [a for a in accounts if not a.get("account_id")]
    if unresolved:
        for a in unresolved:
            pk = (a.get("private_key") or "")[:25]
            log(f"Akun {pk}... di-skip: account_id tidak diketahui", "WARN")
        accounts = [a for a in accounts if a.get("account_id")]
        log(f"{len(unresolved)} akun di-skip, lanjut dengan {len(accounts)} akun", "WARN")
    if not accounts:
        log("Tidak ada akun yang bisa diproses", "ERROR")
        return

    state = load_state()

    # Inisialisasi: hitung kapan storage full untuk tiap akun
    all_ids = [a["account_id"] for a in accounts]
    rows = []
    for acc in accounts:
        aid = acc["account_id"]
        try:
            gs = get_game_state(aid)
            if gs is None:
                log(f"{aid}: belum terdaftar di game.hot.tg", "WARN")
                state[aid] = {"status": "unregistered", "next_claim_at": None}
                continue
            rem = seconds_until_full(gs)
            capacity_sec = storage_capacity_ms(gs.get("storage", 25)) / 1000
            last_claim_sec = gs.get("last_claim", 0) / 1e9
            slot_ts = aligned_next_claim(aid, all_ids, capacity_sec, last_claim_sec, cycle=0)
            wait = slot_ts - time.time()
            state[aid] = {
                "status": "waiting",
                "next_claim_at": time.time() + wait,
                "storage_level": gs.get("storage"),
                "hot_balance": gs.get("balance", 0) / 1e18,
                "cycle": 0,
            }
            slot_time = datetime.fromtimestamp(slot_ts).strftime("%m-%d %H:%M")
            rows.append((aid, gs.get("storage"), rem / 3600, slot_time, wait / 3600))
        except Exception as e:
            log(f"{aid}: {e}", "ERROR")
            state[aid] = {"status": "error", "error": str(e)}
    save_state(state)
    if rows:
        table_init(sorted(rows, key=lambda r: r[3]))

    while True:
        now = time.time()

        for acc in accounts:
            aid = acc["account_id"]
            pk = acc["private_key"]
            st = state.get(aid)
            if st is None:
                st = state[aid] = {"status": "waiting", "next_claim_at": now + 300}
                save_state(state)
            if st["status"] in ("done_cycle", "unregistered"):
                continue
            if st.get("next_claim_at") is None:
                continue
            if now < st["next_claim_at"]:
                continue

            log(f"--- CLAIM {aid} ---", "INFO")
            try:
                res = claim_account(aid, pk)
                if res.get("ok"):
                    # jadwalkan ulang dulu biar next slot ada di box
                    next_slot_str = ""
                    try:
                        gs2 = get_game_state(aid)
                        if gs2:
                            cap2 = storage_capacity_ms(gs2.get("storage", 25)) / 1000
                            last2 = gs2.get("last_claim", 0) / 1e9
                            cyc = st.get("cycle", 0) + 1
                            nts = aligned_next_claim(aid, all_ids, cap2, last2, cycle=cyc)
                            st["next_claim_at"] = nts
                            st["cycle"] = cyc
                            st["storage_level"] = gs2.get("storage")
                            st["hot_balance"] = gs2.get("balance", 0) / 1e18
                            next_slot_str = datetime.fromtimestamp(nts).strftime("%m-%d %H:%M")
                    except Exception as e:
                        log(f"{aid}: gagal baca state setelah claim: {e}", "WARN")
                        st["next_claim_at"] = now + 3600
                    st["status"] = "waiting"
                    st["last_tx"] = res["tx_hash"]
                    st["last_mined"] = res["mined"]
                    save_state(state)
                    claim_box(aid, res["tx_hash"], res["mined"],
                              res["balance_before"], res["balance_after"],
                              next_slot_str or "n/a")
                else:
                    log(f"❌ {aid} gagal: {res['error']}", "ERROR")
                    st["status"] = "error"
                    st["error"] = res["error"]
                    st["next_claim_at"] = now + 600
                    st["status"] = "waiting"
            except Exception as e:
                log(f"❌ {aid} exception: {e}", "ERROR")
                st["status"] = "error"
                st["error"] = str(e)
                st["next_claim_at"] = now + 600
                st["status"] = "waiting"
            save_state(state)
            # jeda antar akun biar gak kena rate limit RPC/backend
            time.sleep(3)

        # sleep 60 detik sebelum cek lagi
        time.sleep(60)


if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        log("Bot dihentikan manual")
