#!/usr/bin/env python3
"""
Tambah akun ke data/accounts.json secara interaktif.

Cara pakai:
  python3 add_account.py

Kamu tinggal paste private key (format ed25519:...), script akan:
  1. Validasi format
  2. Cek akunnya ada di NEAR mainnet
  3. Cek akunnya sudah mining di game.hot.tg
  4. Simpan ke accounts.json

Tekan Enter kosong untuk selesai.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot import (ACCOUNTS_FILE, DATA_DIR, C, auto_resolve_account_ids,
                 get_game_state, parse_near_key, resolve_account_id)


def load_data():
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE) as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {"accounts": []}
    return {"accounts": []}


def save_data(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    # chmod 600 biar cuma owner yang bisa baca
    fd = os.open(ACCOUNTS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(ACCOUNTS_FILE, 0o600)


def main():
    data = load_data()
    accounts = data.get("accounts", [])
    existing_pks = {a.get("private_key", "").strip() for a in accounts}

    print(f"\n{C.BOLD}HOT Wallet - Tambah Akun{C.R}")
    print(f"{C.GRY}Format: ed25519:xxxxx...{C.R}")
    print(f"{C.GRY}Enter kosong = selesai{C.R}")
    print(C.GRY + "─" * 50 + C.R)
    print(f"Terdaftar: {len(accounts)} akun\n")

    while True:
        print(f"{C.CYN}Private Key:{C.R} ", end="", flush=True)
        try:
            pk = input().strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not pk:
            break

        # validasi format
        try:
            _, pub = parse_near_key(pk)
        except Exception as e:
            print(f"  {C.RED}❌ Format salah: {e}{C.R}")
            continue

        if pk in existing_pks:
            print(f"  {C.YLW}⚠️  Udah ada di daftar, skip{C.R}")
            continue

        # resolve account_id
        print(f"  {C.GRY}cari account_id...{C.R}", end=" ", flush=True)
        try:
            aid = resolve_account_id(pub)
        except Exception as e:
            print(f"{C.RED}error: {e}{C.R}")
            continue

        if not aid:
            print(f"{C.RED}❌ Key ini tidak punya akun di NEAR mainnet{C.R}")
            continue
        print(f"{C.GRN}{aid}{C.R}")

        # cek game state
        try:
            gs = get_game_state(aid)
            if gs is None:
                print(f"  {C.YLW}⚠️  Akun ada, tapi belum mining di game.hot.tg{C.R}")
            else:
                bal = gs.get("balance", 0) / 1e18
                print(f"  {C.GRN}✓ HOT: {bal:.4f} · storage L{gs.get('storage')}{C.R}")
        except Exception as e:
            print(f"  {C.YLW}⚠️  gak bisa cek game state: {e}{C.R}")

        accounts.append({"private_key": pk})
        existing_pks.add(pk)
        data["accounts"] = accounts
        try:
            save_data(data)
            print(f"  {C.GRN}✓ tersimpan{C.R}\n")
        except Exception as e:
            print(f"  {C.RED}❌ gagal simpan: {e}{C.R}\n")

    print(f"\n{C.GRY}─" + "─" * 49 + C.R)
    print(f"{C.BOLD}Total: {len(accounts)} akun{C.R}")
    print(f"{C.GRY}file: {ACCOUNTS_FILE}{C.R}")
    if accounts:
        print(f"\nLanjut: {C.CYN}python3 check.py{C.R} untuk cek semua akun")


if __name__ == "__main__":
    main()
