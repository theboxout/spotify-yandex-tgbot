#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import logging
import difflib
from urllib.parse import quote
import requests
import telebot
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from yandex_music import Client as YandexClient
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')

if not all([TELEGRAM_TOKEN, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET]):
    print("❌ Заполни .env файл!")
    exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode='HTML')

sp = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET))

yandex = YandexClient()
yandex.init()
logger.info("✅ Бот запущен")


def clean_for_search(text: str) -> str:
    text = re.sub(r'\s*\(.*?\)\s*', '', text)
    text = re.sub(r'\s*(feat\.|ft\.|featuring)\s*.*$', '', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


def extract_spotify_track_id(url: str) -> str | None:
    if 'spotify.link' in url.lower():
        try:
            url = requests.head(url, allow_redirects=True, timeout=5).url
        except:
            pass
    match = re.search(r'open\.spotify\.com/(?:[^/]+/)?track/([a-zA-Z0-9]{22})', url)
    return match.group(1) if match else None

def spotify_to_yandex(spotify_url: str) -> tuple[str | None, str | None]:
    try:
        track_id = extract_spotify_track_id(spotify_url)
        if not track_id:
            return None, "❌ Это не ссылка на трек Spotify"

        track = sp.track(track_id)
        original_title = track['name']
        original_artist = track['artists'][0]['name'] if track.get('artists') else ''

        clean_title = clean_for_search(original_title)
        clean_artist = clean_for_search(original_artist)

        logger.info(f"Spotify → {original_artist} — {original_title}")

        queries = [
            f"{clean_artist} {clean_title}",
            clean_title,
            f"{clean_artist.split()[0]} {clean_title}" if ' ' in clean_artist else clean_title,
        ]

        best_track = None
        best_score = 0

        for q in queries:
            search_results = yandex.search(q.strip(), type_='track')

            tracks_data = getattr(search_results, 'tracks', None)
            if isinstance(tracks_data, dict):
                tracks = tracks_data.get('results', [])
            elif isinstance(tracks_data, list):
                tracks = tracks_data
            else:
                tracks = []

            print(f"DEBUG: Запрос='{q}' | Найдено: {len(tracks)}")

            for t in tracks[:15]:
                if isinstance(t, dict):
                    t_title = t.get('title', '')
                    t_artist = t.get('artists', [{}])[0].get('name', '') if t.get('artists') else ''
                else:
                    t_title = getattr(t, 'title', '')
                    t_artist = t.artists[0].name if getattr(t, 'artists', None) else ''

                title_sim = difflib.SequenceMatcher(None, clean_title.lower(), t_title.lower()).ratio()
                artist_sim = difflib.SequenceMatcher(None, clean_artist.lower(), t_artist.lower()).ratio()
                score = (title_sim * 0.65) + (artist_sim * 0.35)

                if score > best_score:
                    best_score = score
                    best_track = t

        if best_track and best_score > 0.45:
            track_id = best_track['id'] if isinstance(best_track, dict) else best_track.id
            return f"https://music.yandex.ru/track/{track_id}", None

        # Fallback — как ты просил
        query = f"{clean_artist} {clean_title}"
        yandex_link = f"https://music.yandex.ru/search?text={quote(query)}"
        youtube_link = f"https://music.youtube.com/search?q={quote(query)}"

        return None, (
            f"🔍 Яндекс Музыка:\n{yandex_link}\n\n"
            f"▶️ YouTube Music:\n{youtube_link}\n\n"
            "⚠️ Прямая ссылка на Яндекс Музыку из Spotify часто невозможна "
            "из-за ограничений API Яндекса."
        )

    except Exception as e:
        return None, f"❌ Ошибка: {str(e)[:80]}"


def yandex_to_spotify(yandex_url: str) -> tuple[str | None, str | None]:
    try:
        track_match = re.search(r'/track/(\d+)', yandex_url)
        album_match = re.search(r'/album/(\d+)', yandex_url)

        if not track_match:
            return None, "❌ Это не ссылка на трек Яндекс Музыки"

        track_id = track_match.group(1)
        album_id = album_match.group(1) if album_match else None

        if album_id:
            track_ids = [f"{track_id}:{album_id}"]
        else:
            track_ids = [track_id]

        tracks = yandex.tracks(track_ids)
        if not tracks or not tracks[0]:
            return None, "❌ Не удалось загрузить трек из Яндекс Музыки"

        t = tracks[0]
        artist = t.artists[0].name if t.artists else ""
        title = t.title

        query = f"{artist} {title}".strip()
        results = sp.search(query, type='track', limit=8)

        if results['tracks']['items']:
            return results['tracks']['items'][0]['external_urls']['spotify'], None

        return None, "😕 Не найдено в Spotify"

    except Exception as e:
        return None, f"❌ Ошибка: {str(e)[:80]}"


@bot.message_handler(commands=['start', 'help'])
def start(message):
    text = (
        "🎵 <b>Бот конвертер ссылок</b>\n\n"
        "<b>Важно:</b>\n"
        "• <b>Яндекс → Spotify</b> — работает хорошо\n"
        "• <b>Spotify → Яндекс</b> — часто отдаёт ссылку на поиск\n\n"
        "Кидай ссылку на трек:"
    )
    bot.reply_to(message, text)


@bot.message_handler(func=lambda m: True)
def convert(message):
    text = message.text.strip()

    if 'spotify' in text.lower():
        url, err = spotify_to_yandex(text)
        if url:
            bot.reply_to(message, f"✅ {url}")
        else:
            bot.reply_to(message, err or "❌ Не удалось конвертировать")

    elif 'yandex' in text.lower() or 'music.yandex' in text.lower():
        url, err = yandex_to_spotify(text)
        if url:
            bot.reply_to(message, f"✅ {url}")
        else:
            bot.reply_to(message, err or "❌ Не удалось конвертировать")
    else:
        bot.reply_to(message, "Кидай ссылку на трек из Spotify или Яндекс Музыки")


if __name__ == '__main__':
    bot.infinity_polling(skip_pending=True)
