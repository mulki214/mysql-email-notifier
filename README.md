# MySQL/MariaDB email notifier

Paket ini mengirim kode aktivasi ke alamat `email` pada baris yang sama saat baris baru ditambahkan atau `email_activation_code` berubah.

Trigger menulis ke `email_notification_queue`. Worker Python menggunakan pustaka bawaan Python dan klien `mysql`; pengiriman dilakukan melalui Brevo Transactional Email API. Tidak perlu Docker, virtualenv, Composer, atau paket Python tambahan. Worker ini ditujukan agar bisa berjalan pada Python 3.6 dan MariaDB 10.1.

## Pasang trigger

Backup database, lalu jalankan `setup.sql` terhadap database `db_gigel_prod` sebagai administrator MySQL. SQL membuat tabel antrean dan dua trigger.

- `INSERT` menghasilkan antrean email.
- `UPDATE` hanya menghasilkan email bila kode berubah. Perubahan alamat email saja tidak memicu notifikasi.
- Beberapa perubahan kode akan menghasilkan beberapa email.

## Siapkan Brevo

Buat akun Brevo dan aktifkan pengiriman transactional. Free plan yang tersedia menawarkan kuota harian setelah akun disetujui. Verifikasi alamat pengirim, lalu buat/ambil API key v3. API key disimpan di `.env` pada VPS dan jangan di-commit.

## Konfigurasi VPS

Pastikan `python3`, klien `mysql`, dan `systemd` sudah tersedia. Paket ini tidak memerlukan `python3-venv` atau instalasi Python tambahan.

```sh
cp .env.example .env
cp .my.cnf.example .my.cnf
chmod 600 .env .my.cnf
```

Isi `.env` dan `.my.cnf` di VPS, bukan di GitHub. `.my.cnf` menyimpan password worker untuk koneksi lokal ke MariaDB. Buat user database terbatas:

```sql
CREATE USER 'email_notifier'@'localhost' IDENTIFIED BY 'GANTI_DENGAN_PASSWORD_KUAT';
GRANT SELECT ON db_gigel_prod.users_admin_activation_code TO 'email_notifier'@'localhost';
GRANT SELECT, UPDATE ON db_gigel_prod.email_notification_queue TO 'email_notifier'@'localhost';
```

Sesuaikan kredensial `.my.cnf` dan `DB_USER` agar cocok. Pastikan user dapat membaca antrean, mengubah status antrean, dan membaca sumber. Jangan gunakan root untuk worker.

Jika systemd unit memakai `User=email-notifier`, pastikan akun tersebut dapat membaca repo dan kedua file konfigurasi serta menulis file lock:

```sh
chown -R email-notifier:email-notifier /home/workspace/mysql-email-notifier
chmod 600 /home/workspace/mysql-email-notifier/.env /home/workspace/mysql-email-notifier/.my.cnf
```

Samakan `DB_DEFAULTS_FILE` di `.env` dan `WorkingDirectory`/`ExecStart` unit systemd dengan lokasi repo Anda.

## Jalankan

Jalankan worker secara manual untuk melihat apakah konfigurasi sudah lengkap:

```sh
python3 worker.py
```

Untuk systemd, salin `mysql-email-notifier.service.example` ke `/etc/systemd/system/mysql-email-notifier.service`, ubah `WorkingDirectory` ke lokasi repo di VPS, lalu jalankan:

```sh
systemctl daemon-reload
systemctl enable --now mysql-email-notifier
systemctl status mysql-email-notifier
journalctl -u mysql-email-notifier -f
```

Worker memakai `flock` agar satu proses berjalan pada satu waktu. Kegagalan pengiriman dicoba ulang dengan jeda bertambah; setelah batas percobaan antrean ditandai `failed`. Worker tidak mencatat isi email atau kode ke log.

## Keamanan dan operasi

- Kode aktivasi dikirim ke alamat email pada baris yang sama, sesuai konfigurasi ini. Batasi akses tabel antrean dan file `.env`/`.my.cnf`.
- Notifikasi antrean memiliki data salinan, sehingga tetap bisa terkirim jika baris sumber dihapus sebelum diproses.
- Jika API menerima email tetapi koneksi terputus sebelum status antrean tersimpan, email dapat terkirim ulang.
- Periksa error antrean dengan `SELECT id, status, attempts, last_error FROM email_notification_queue WHERE status='failed';`.
- VPS Ubuntu 17.10 sudah tidak didukung; konfigurasi ini menghindari perubahan paket OS, tetapi OS lama tetap memiliki risiko keamanan.
