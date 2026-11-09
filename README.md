# MySQL email notifier

Paket ini mengirim email berisi `email` dan `email_activation_code` ke alamat `email` di baris yang sama saat baris dibuat atau kode berubah.

Alur: trigger MySQL menulis ke `email_notification_queue`; worker Python mengambil antrean dan mengirim via SMTP. Trigger tidak menghubungi SMTP secara langsung.

## 1. Prasyarat

- MySQL 8.0+ di VPS (worker memakai `SKIP LOCKED`).
- Python 3.10+ di VPS.
- Akun SMTP transactional yang diaktifkan. Contoh memakai Brevo SMTP; paket gratis Brevo menawarkan kuota harian dan akun harus disetujui sebelum mengirim.
- Tabel target memakai InnoDB (tabel yang diberikan sudah InnoDB).

## 2. Pasang trigger dan tabel antrean

Jalankan `setup.sql` pada database `db_gigel_prod`, dengan akun yang berhak membuat tabel dan trigger. Script membuat antrean dan dua trigger. Jika nama trigger sudah terpakai, SQL akan berhenti; tinjau trigger lama sebelum melakukan perubahan.

Trigger mengantrekan `INSERT`, dan `UPDATE` hanya jika `email_activation_code` berubah. Perubahan email saja tidak memicu email. Setiap perubahan kode menghasilkan notifikasi tersendiri.

## 3. Konfigurasi worker

```sh
cd mysql-email-notifier
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Isi `.env` di VPS. Gunakan akun MySQL khusus dengan hak minimum: `SELECT` pada tabel target dan `SELECT`/`UPDATE` pada antrean. Jangan gunakan `root`. Batasi akses MySQL jaringan ke alamat tepercaya atau gunakan SSH tunnel.

Contoh pemberian akses (jalankan sebagai administrator MySQL setelah tabel antrean dibuat; ganti password di VPS, jangan gunakan contoh ini sebagai password):

```sql
CREATE USER 'email_notifier'@'localhost' IDENTIFIED BY 'GANTI_DENGAN_PASSWORD_KUAT';
GRANT SELECT ON db_gigel_prod.users_admin_activation_code TO 'email_notifier'@'localhost';
GRANT SELECT, UPDATE ON db_gigel_prod.email_notification_queue TO 'email_notifier'@'localhost';
```

Ambil host, port, login, dan SMTP key dari pengaturan SMTP/API Brevo. Gunakan alamat pengirim yang sudah diverifikasi. Jangan taruh key di Git atau kirim lewat chat.

## 4. Jalankan

Uji worker manual di VPS:

```sh
set -a
. ./.env
set +a
python worker.py
```

Untuk menjalankannya terus-menerus, sesuaikan `mysql-email-notifier.service.example` sebagai unit systemd di VPS. Worker mencoba ulang kegagalan dengan jeda bertambah. Setelah batas percobaan, status antrean menjadi `failed` untuk ditinjau.

## Catatan operasional

- Penerimaan oleh SMTP tidak menjamin pesan masuk inbox.
- Pengiriman bersifat at-least-once: jika SMTP menerima pesan lalu koneksi terputus sebelum status tersimpan, email dapat terkirim ulang.
- Kode aktivasi sensitif. Batasi akses ke antrean dan log. Worker tidak menulis isi email atau kode ke log.
- Jika baris sumber dihapus sebelum worker memprosesnya, email/kode tetap ada di antrean.
- Backup database sebelum pemasangan; uji di staging jika ada.

## Menghapus instalasi

Hapus kedua trigger setelah meninjau dampaknya. Arsipkan atau hapus antrean setelah selesai diproses dan worker dihentikan.
