import os
import requests
import sys

# --- 1. ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ---
print("--- Starting bot script ---")

VK_TOKEN = os.environ.get('VK_API_TOKEN')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')
SOURCE_GROUP = os.environ.get('SOURCE_GROUP')

if not VK_TOKEN:
    print("❌ ERROR: VK_API_TOKEN secret is not set!")
    sys.exit(1)
else:
    print("✅ VK_API_TOKEN found.")

if not TG_BOT_TOKEN:
    print("❌ ERROR: TG_BOT_TOKEN secret is not set!")
    sys.exit(1)
else:
    print("✅ TG_BOT_TOKEN found.")

if not CHANNEL_ID:
    print("❌ ERROR: CHANNEL_ID secret is not set!")
    sys.exit(1)
else:
    print(f"✅ CHANNEL_ID found: {CHANNEL_ID}")

if not SOURCE_GROUP:
    print("❌ ERROR: SOURCE_GROUP secret is not set!")
    sys.exit(1)
else:
    print(f"✅ SOURCE_GROUP found: {SOURCE_GROUP}")

# --- 2. ФАЙЛ ДЛЯ ХРАНЕНИЯ ИСТОРИИ ОТПРАВЛЕННЫХ ПОСТОВ ---
HISTORY_FILE = "sent_posts.txt"

def load_sent_posts():
    """Загружает ID уже отправленных постов из файла"""
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_sent_post(post_id):
    """Сохраняет ID отправленного поста в файл"""
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{post_id}\n")

# --- 3. ПОЛУЧАЕМ ПОСТЫ ИЗ VK (до 5 последних) ---
print(f"\n--- Getting recent posts from VK group '{SOURCE_GROUP}' ---")
url = 'https://api.vk.com/method/wall.get'
params = {
    'access_token': VK_TOKEN,
    'domain': SOURCE_GROUP,
    'count': 5,  # Проверяем последние 5 постов
    'v': '5.131'
}

try:
    response = requests.get(url, params=params)
    response.raise_for_status()
    vk_data = response.json()
    print("VK API response received.")

    if 'error' in vk_data:
        print(f"❌ VK API Error: {vk_data['error'].get('error_msg', 'Unknown error')}")
        sys.exit(1)

    items = vk_data.get('response', {}).get('items', [])
    if not items:
        print(f"ℹ️ No posts found in the group '{SOURCE_GROUP}'.")
        sys.exit(0)

    # Загружаем историю уже отправленных постов
    sent_posts = load_sent_posts()
    print(f"📋 Already sent posts: {len(sent_posts)}")

    # Ищем новые посты (игнорируем закреплённые)
    new_posts = []
    for post in items:
        if post.get('is_pinned', False):
            print(f"⏸️ Skipping pinned post ID: {post['id']}")
            continue
        if str(post['id']) not in sent_posts:
            new_posts.append(post)
        else:
            print(f"⏸️ Post ID {post['id']} already sent, skipping.")

    if not new_posts:
        print("ℹ️ No new posts found.")
        sys.exit(0)

    print(f"🆕 Found {len(new_posts)} new post(s)!")

    # --- 4. ОТПРАВЛЯЕМ НОВЫЕ ПОСТЫ В TELEGRAM ---
    for post in reversed(new_posts):  # Отправляем в хронологическом порядке (старые первыми)
        post_id = post['id']
        post_text = post.get('text', '')
        if not post_text:
            post_text = "[Пост без текста]"
        else:
            if len(post_text) > 500:
                post_text = post_text[:500] + "..."

        print(f"\n--- Sending post ID {post_id} to Telegram ---")
        
        send_url = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'
        message_text = f"<b>🆕 Новый пост!</b>\n\n{post_text}"
        
        post_link = f"https://vk.com/{SOURCE_GROUP}?w=wall{post['owner_id']}_{post_id}"
        message_text += f"\n\n<a href='{post_link}'>Читать на сайте VK</a>"

        send_data = {
            'chat_id': CHANNEL_ID,
            'text': message_text,
            'parse_mode': 'HTML'
        }

        tg_response = requests.post(send_url, data=send_data)
        tg_response.raise_for_status()
        print(f"✅ Post ID {post_id} sent successfully!")

        # Сохраняем ID отправленного поста
        save_sent_post(post_id)

except requests.exceptions.RequestException as e:
    print(f"❌ Network or request error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ An unexpected error occurred: {e}")
    sys.exit(1)

print("\n--- Bot script finished successfully ---")
