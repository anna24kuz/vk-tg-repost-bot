import os
import requests
import sys

# --- 1. ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ---
print("--- Starting bot script ---")

VK_TOKEN = os.environ.get('VK_API_TOKEN')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')
SOURCE_GROUP = os.environ.get('SOURCE_GROUP')

# Проверяем, найдены ли все переменные
if not VK_TOKEN:
    print("❌ ERROR: VK_API_TOKEN secret is not set!")
    sys.exit(1)
else:
    print(f"✅ VK_API_TOKEN found (first 10 chars: {VK_TOKEN[:10]}...)")

if not TG_BOT_TOKEN:
    print("❌ ERROR: TG_BOT_TOKEN secret is not set!")
    sys.exit(1)
else:
    print(f"✅ TG_BOT_TOKEN found (first 10 chars: {TG_BOT_TOKEN[:10]}...)")

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

# --- 2. ПОЛУЧАЕМ ПОСТ ИЗ VK ---
print(f"\n--- Trying to get last post from VK group '{SOURCE_GROUP}' ---")
url = 'https://api.vk.com/method/wall.get'
params = {
    'access_token': VK_TOKEN,
    'domain': SOURCE_GROUP,
    'count': 1,
    'v': '5.131'
}

try:
    response = requests.get(url, params=params)
    response.raise_for_status()
    vk_data = response.json()
    print(f"VK API response received.")

    if 'error' in vk_data:
        print(f"❌ VK API Error: {vk_data['error'].get('error_msg', 'Unknown error')}")
        sys.exit(1)

    items = vk_data.get('response', {}).get('items', [])
    if not items:
        print(f"ℹ️ No posts found in the group '{SOURCE_GROUP}'. Nothing to send.")
        sys.exit(0)

    post = items[0]
    print(f"✅ Post found! Post ID: {post['id']}")

    post_text = post.get('text', '')
    if not post_text:
        post_text = "[Этот пост не содержит текста]"
    else:
        if len(post_text) > 500:
            post_text = post_text[:500] + "..."

    # --- 3. ОТПРАВЛЯЕМ ПОСТ В TELEGRAM ---
    print(f"\n--- Trying to send post to Telegram channel {CHANNEL_ID} ---")
    send_url = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'
    message_text = f"<b>Новый пост из VK!</b>\n\n{post_text}"
    
    post_link = f"https://vk.com/{SOURCE_GROUP}?w=wall{post['owner_id']}_{post['id']}"
    message_text += f"\n\n<a href='{post_link}'>Читать на сайте VK</a>"

    send_data = {
        'chat_id': CHANNEL_ID,
        'text': message_text,
        'parse_mode': 'HTML'
    }

    tg_response = requests.post(send_url, data=send_data)
    tg_response.raise_for_status()
    print("✅ Message sent to Telegram successfully!")

except requests.exceptions.RequestException as e:
    print(f"❌ Network or request error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ An unexpected error occurred: {e}")
    sys.exit(1)

print("\n--- Bot script finished successfully ---")
