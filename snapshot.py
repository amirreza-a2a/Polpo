import os

# فایل‌های سورس کد که باید گرفته بشن
SOURCE_EXTENSIONS = {'.py', '.txt', '.md', '.json', '.cfg', '.ini', '.toml', '.yaml', '.yml', '.env', '.sh'}

# پوشه‌ها و فایل‌هایی که باید نادیده گرفته بشن
SKIP_DIRS = {'__pycache__', '.git', '.venv', 'venv', 'env', 'node_modules',
             'cropped_charts', 'temp_files', 'tmp', 'public'}
SKIP_FILES = {'rate_limits.json'}  # فایل‌های runtime که نیاز نیست

OUTPUT_FILE = 'project_snapshot.txt'

def snapshot_project(root_dir='.'):
    lines = []
    lines.append('=' * 70)
    lines.append('PROJECT SNAPSHOT')
    lines.append(f'Root: {os.path.abspath(root_dir)}')
    lines.append('=' * 70)
    lines.append('')

    file_count = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # حذف پوشه‌های بلک‌لیست
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for filename in sorted(filenames):
            # فقط فایل‌های سورس کد
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SOURCE_EXTENSIONS:
                continue
            if filename in SKIP_FILES:
                continue

            filepath = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(filepath, root_dir)

            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception as e:
                content = f'[خطا در خواندن فایل: {e}]'

            lines.append('─' * 70)
            lines.append(f'FILE: {rel_path}')
            lines.append('─' * 70)
            lines.append(content)
            lines.append('')
            file_count += 1

    lines.append('=' * 70)
    lines.append(f'TOTAL FILES: {file_count}')
    lines.append('=' * 70)

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f'✅ اسنپ‌شات ذخیره شد: {OUTPUT_FILE}')
    print(f'📁 تعداد فایل‌های پردازش‌شده: {file_count}')

if __name__ == '__main__':
    snapshot_project()
