#!/usr/bin/env python3
"""
Cek akun HOT Wallet tanpa claim.
Output: alamat, balance NEAR, balance HOT, storage level, last claim.

PRIVATE KEY TIDAK PERNAH DITRANSMISIKAN - hanya dipake buat
nandatangani pesan auth ke backend HOT (sama kayak web app resmi).

Cara pakai:
  python3 check.py                    # cek semua akun di data/accounts.json
  python3 check.py <account_id>       # cek 1 akun
"""

import base64
import hashlib
import json
import os
import sys
import time
from datetime import datetime

import base58
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot import (API_BASE, CONTRACT, HotApi, get_account_state, get_game_state,
                 parse_near_key, rpc_call, sign_auth_intent)


def check_one(account_id, private_key, do_auth=False):
    print(f"\n{'='*60}")
    print(f"Akun: {account_id}")
    print(f"{'='*60}")

    sk, pk_str = parse_near_key(private_key)
    print(f"Public key : {pk_str}")

    # 1) Akun NEAR ada?
    st = get_account_state(account_id)
    if st is None:
        print(f"❌ Akun NEAR {account_id} TIDAK ADA di mainnet")
        return False
    near_bal = int(st["amount"]) / 1e24
    print(f"NEAR       : {near_bal:.5f} NEAR")
    if near_bal < 0.001:
        print("⚠️  NEAR hampir habis - tidak bisa bayar gas fee untuk claim")

    # 2) Access key (PK benar untuk akun ini?)
    res = rpc_call("query", {
        "request_type": "view_access_key",
        "finality": "final",
        "account_id": account_id,
        "public_key": pk_str,
    })
    if "error" in res:
        print(f"❌ Private key TIDAK MATCH dengan akun {account_id}")
        return False
    print(f"Access key : OK (nonce={res['nonce']}) ✓")

    # 3) Game state
    gs = get_game_state(account_id)
    if gs is None:
        print("❌ Akun belum terdaftar di game.hot.tg")
        return False
    hot_bal = gs.get("balance", 0) / 1e6  # HOT: 6 desimal (ft_metadata)
    last = datetime.fromtimestamp(gs.get("last_claim", 0) / 1e9)
    elapsed_h = (time.time() - last.timestamp()) / 3600
    print(f"HOT        : {hot_bal:.4f} HOT")
    print(f"Storage    : level {gs.get('storage')}")
    print(f"Boost      : {gs.get('boost')}")
    print(f"Firespace  : {gs.get('firespace')}")
    print(f"Village    : {gs.get('village')}")
    print(f"Referrals  : {gs.get('refferals')}")
    print(f"Last claim : {last.strftime('%Y-%m-%d %H:%M:%S')} ({elapsed_h:.1f} jam lalu)")

    # 4) Test auth backend (opsional - ini yang menandatangani pesan, bukan TX)
    if do_auth:
        try:
            api = HotApi(device_id=f"check-{account_id}")
            api.auth(sk, account_id, pk_str)
            # test get user endpoint
            r = requests.get(f"{API_BASE}/api/v1/user/hot",
                             headers={"Authorization": f"Bearer {api.jwt}",
                                      "Device-Id": api.device_id},
                             timeout=15)
            print(f"Backend    : auth OK, /user/hot -> HTTP {r.status_code} ✓")
        except Exception as e:
            print(f"⚠️  Backend auth gagal: {e}")

    return True


def main():
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    acc_file = os.path.join(data_dir, "accounts.json")
    do_auth = "--auth" in sys.argv
    target = next((a for a in sys.argv[1:] if not a.startswith("--")), None)

    if not os.path.exists(acc_file):
        print(f"File {acc_file} belum ada.")
        print("Isi format: {\"accounts\": [{\"account_id\": \"xxx.tg\", \"private_key\": \"ed25519:...\"}]}")
        return

    with open(acc_file) as f:
        accounts = json.load(f).get("accounts", [])

    # resolve account_id jika kosong
    from bot import auto_resolve_account_ids
    auto_resolve_account_ids(accounts)

    ok, fail = 0, 0
    for acc in accounts:
        if target and acc["account_id"] != target:
            continue
        try:
            if check_one(acc["account_id"], acc["private_key"], do_auth):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            print(f"❌ Error: {e}")
            fail += 1

    print(f"\n{'='*60}")
    print(f"Total: {ok} OK, {fail} gagal")
    if not do_auth:
        print("\nJalankan dengan --auth untuk test login ke backend HOT juga")


if __name__ == "__main__":
    main()
