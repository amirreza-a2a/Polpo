# راهنمای راه‌اندازی روی cPanel

## ۱. ساخت دیتابیس MySQL
1. وارد cPanel شوید
2. MySQL Databases → ساخت دیتابیس جدید
3. یک کاربر MySQL بسازید و به دیتابیس دسترسی کامل بدهید
4. اطلاعات را در `config.py` وارد کنید

## ۲. آپلود فایل‌ها
```
آپلود پوشه pdf_bot به خارج از public_html
مثلاً: /home/username/pdf_bot/
```

## ۳. نصب Python App در cPanel
1. Software → Setup Python App
2. Python Version: 3.10+
3. Application Root: pdf_bot
4. Application Startup File: main.py

## ۴. نصب کتابخانه‌ها
```bash
pip install -r requirements.txt
```

## ۵. راه‌اندازی دیتابیس
```bash
python database/connection.py
```

## ۶. تنظیم Cron Job برای Worker
در cPanel → Cron Jobs:
```
* * * * * /usr/bin/python3 /home/username/pdf_bot/services/worker.py >> /home/username/pdf_bot/worker.log 2>&1
```

## ۷. اجرای بات
```bash
python main.py
```

## ساختار فایل‌ها
```
pdf_bot/
├── main.py                 ← اجرای اصلی بات
├── config.py               ← تنظیمات (پر کنید)
├── requirements.txt
├── worker.lock             ← خودکار ساخته می‌شود
├── rate_limits.json        ← خودکار ساخته می‌شود
├── database/
│   ├── connection.py
│   └── models.py
├── handlers/
│   ├── common.py
│   ├── pdf.py
│   ├── user.py
│   └── admin.py
├── services/
│   ├── worker.py           ← اجرا توسط Cron
│   ├── api_manager.py
│   ├── pdf_processor.py
│   └── backup.py
├── utils/
│   ├── rate_limiter.py
│   └── file_manager.py
├── temp_files/             ← خودکار ساخته می‌شود
└── output_files/           ← خودکار ساخته می‌شود
```

## تنظیمات config.py
```python
BOT_TOKEN          = "توکن بات از BotFather"
ADMIN_IDS          = [آیدی عددی شما]
BACKUP_CHANNEL_ID  = آیدی چنل بکاپ (عدد منفی)
DB_HOST            = "localhost"
DB_NAME            = "نام دیتابیس"
DB_USER            = "کاربر دیتابیس"
DB_PASS            = "رمز دیتابیس"
```
