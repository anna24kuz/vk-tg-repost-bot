import os
import requests
import time
from datetime import datetime

VK_TOKEN = os.environ.get('VK_API_TOKEN')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')
SOURCE_GROUP = os.environ.get('SOURCE_GROUP')

def get_last_post():
    url = 'https://api.vk.com/method/wall.get'
    params = {
        'access_token': VK_TOKEN,
        'domain': SOURCE_GROUP,
        'count': 1,
        'v': '5.131'
    }
    response = requests.get(url, params=params).json()
    if 'response' in response:
        items = response['response']['items']
        if items:
            return items[0]
    return None

def send_to_telegram(text):
    url = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'
    data = {
        'chat_id': CHANNEL_ID,
        'text': text,
        'parse_mode': 'HTML'
    }
    requests.post(url, data=data)

def main():
    post = get_last_post()
    if post:
        text = f"<b>Новый пост</b>\n\n{post['text'][:500]}"
        send_to_telegram(text)

if __name__ == '__main__':
    main()
