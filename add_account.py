#!/usr/bin/env python3
"""
Tambah akun ke data/accounts.json.

Cara pakai:
  python3 add_account.py            # paste PK (boleh banyak sekaligus)
  python3 add_account.py keys.txt   # baca PK dari file

Format PK (2-duanya diterima):
  ed25519:3S4QZ...   (dengan prefix)
  3S4QZ...           (tanpa prefix)
  1 PK per baris

Perintah saat input:
  (enter kosong)     selesai
  selesai            selesai
  hapus <no>         hapus akun no X
  list               lihat daftar
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot import (ACCOUNTS_FILE, DATA_DIR, C, get_game_state,
                 parse_near_key, resolve_account_id)


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
    fd = os.open(ACCOUNTS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(ACCOUNTS_FILE, 0o600)


def process_pk(pk, accounts, existing_pks):
    """Proses 1 PK. Return (True, msg) / (False, msg) / (None, None)"""
    pk = pk.strip()
    if not pk:
        return None, None
    try:
        _, pub = parse_near_key(pk)
    except Exception as e:
        return False, f"format salah: {e}"

    if pk in existing_pks:
        return False, "sudah ada di daftar"

    try:
        aid = resolve_account_id(pub)
    except Exception as e:
        return False, f"error: {e}"

    if not aid:
        return False, "tidak punya akun di NEAR mainnet"

    extra = ""
    try:
        gs = get_game_state(aid)
        if gs is None:
            extra = " (belum mining)"
        else:
            bal = gs.get("balance", 0) / 1e18
            extra = f" HOT {bal:.4f} · L{gs.get('storage')}"
    except Exception:
        pass

    accounts.append({"private_key": pk})
    existing_pks.add(pk)
    return True, f"{aid}{extra}"


def show_list(accounts):
    if not accounts:
        print(f"  {C.GRY}(kosong){C.R}")
        return
    for i, acc in enumerate(accounts, 1):
        pk = acc.get("private_key", "")
        print(f"  {C.YLW}{i:2d}.{C.R} {acc.get('account_id', '?')}  "
              f"{C.GRY}{pk[:10]}...{C.R}")


def main():
    data = load_data()
    accounts = data.get("accounts", [])
    existing_pks = {a.get("private_key", "").strip() for a in accounts}

    # ── mode: baca dari file ──
    args = sys.argv[1:]
    if args and os.path.exists(args[0]):
        path = args[0]
        print(f"\n{C.CYN}Baca PK dari {path}{C.R}")
        with open(path) as f:
            lines = [l.strip() for l in f if l.strip()]
        print(f"{C.GRY}{len(lines)} baris ditemukan{C.R}\n")
        ok = fail = 0
        for i, line in enumerate(lines, 1):
            status, msg = process_pk(line, accounts, existing_pks)
            if status is None:
                continue
            if status:
                print(f"  {C.GRN}✓{C.R} [{i}/{len(lines)}] {msg}")
                ok += 1
            else:
                print(f"  {C.RED}✗{C.R} [{i}/{len(lines)}] {msg}")
                fail += 1
        data["accounts"] = accounts
        save_data(data)
        print(f"\n{C.BOLD}Selesai: {C.GRN}{ok} sukses{C.R}, {C.RED}{fail} gagal{C.R}")
        print(f"{C.GRY}Total tersimpan: {len(accounts)} akun{C.R}\n")
        return

    # ── mode interaktif ──
    print(f"\n{C.BOLD}HOT Wallet - Tambah Akun{C.R}")
    print(f"{C.GRY}Paste PK, 1 per baris (boleh banyak sekaligus){C.R}")
    print(f"{C.GRY}Perintah: selesai · list · hapus <no>{C.R}")
    print(C.GRY + "─" * 50 + C.R)
    print(f"Terdaftar: {C.BOLD}{len(accounts)}{C.R} akun\n")

    while True:
        try:
            raw = input(f"{C.CYN}PK>{C.R} ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not raw or raw.lower() in ("selesai", "done", "exit", "q"):
            break

        if raw.lower() == "list":
            show_list(accounts)
            continue

        if raw.lower().startswith("hapus "):
            try:
                n = int(raw.split()[1])
                acc = accounts[n - 1]
                existing_pks.discard(acc.get("private_key", "").strip())
                accounts.pop(n - 1)
                data["accounts"] = accounts
                save_data(data)
                print(f"  {C.YLW}dihapus: {acc.get('account_id', '?')}{C.R}")
            except (ValueError, IndexError):
                print(f"  {C.RED}nomor salah{C.R}")
            continue

        status, msg = process_pk(raw, accounts, existing_pks)
        if status:
            print(f"  {C.GRN}✓ {msg}{C.R}")
            data["accounts"] = accounts
            save_data(data)
        elif status is False:
            print(f"  {C.RED}✗ {msg}{C.R}")

    print(f"\n{C.GRY}─" + "─" * 49 + C.R)
    print(f"{C.BOLD}Total tersimpan: {len(accounts)} akun{C.R}")
    if accounts:
        print(f"\n{C.GRY}Daftar:{C.R}")
        show_list(accounts)
        print(f"\nLanjut: {C.CYN}python3 check.py{C.R} untuk cek semua akun")


if __name__ == "__main__":
    main()
