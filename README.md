# HOT Wallet Auto Claim Bot

Claim harian $HOT otomatis untuk akun NEAR (game.hot.tg).

## Cara setup

### 1. Isi private key

## Format private key

Hanya butuh **private key** — account_id-nya di-resolve otomatis:

```json
{
  "accounts": [
    { "private_key": "ed25519:4zvwRjXUKG..." },
    { "private_key": "ed25519:8kF2jR..." },
    { "private_key": "ed25519:9kF2jR..." }
  ]
}
```

Bot akan cari account_id dari setiap private key (via nearblocks), lalu
menyimpannya kembali ke file. Jadi run berikutnya tidak perlu resolve lagi.

Opsional: bisa juga pakai keduanya:
```json
{ "account_id": "dimar0987.tg", "private_key": "ed25519:..." }
```

Ambil private key dari HOT Wallet: Settings → Private Key.

File ini hanya bisa dibaca owner (`chmod 600`).

### 2. Cek akun (tanpa claim)

```bash
python3 check.py
```

Akan menampilkan: alamat, balance NEAR, balance HOT, storage level, last claim.
Tambah `--auth` untuk test login backend HOT juga.

### 3. Jalankan bot

```bash
python3 bot.py
```

Bot akan:
- Baca storage level tiap akun (dari game.hot.tg)
- Tunggu sampai storage **FULL**, baru claim
- Tambah jitter 1-30 menit supaya 20 akun gak barengan
- Log ke `data/bot.log`
- State di `data/state.json`

Kapasitas storage per level (dari web app HOT):

| Level | Jam |
|-------|-----|
| 20 | 2 |
| 21 | 3 |
| 22 | 4 |
| 23 | 6 |
| 24 | 12 |
| 25 | 24 |

### 4. Jalankan di background (24 jam)

```bash
nohup python3 bot.py > /dev/null 2>&1 &
```

Atau pakai systemd (lihat bawah).

## Cara kerja

Dari reverse-engineering web app HOT (app.hot-labs.org):

1. `get_user(account_id)` via RPC → game_state
2. Auth: sign NEAR intents message → POST `/api/v1/user/auth` → JWT
3. POST `/api/v1/user/hot/claim/signature` (JWT) → {signature, mining_time, max_ts}
4. `l2_claim` transaction ke game.hot.tg dengan signature dari backend

Backend: `https://api0.herewallet.app`

## File

- `bot.py` - bot utama (claim + scheduler)
- `check.py` - cek akun tanpa claim
- `data/accounts.json` - daftar akun + PK (RAHASIA)
- `data/state.json` - state scheduler
- `data/bot.log` - log

## Catatan

- Gas fee: ~0.0005 NEAR per claim (auto dari balance NEAR)
- Storage penuh ~8 jam (level 25). Delay 1-4 jam aman.
- `charge_gas_fee: false` = HOT dipotong 30% untuk gas (gratis NEAR)
- `charge_gas_fee: true` = bayar gas pakai NEAR (dapat 100% HOT)
