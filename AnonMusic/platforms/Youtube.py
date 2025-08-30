import asyncio
import httpx
import yt_dlp
import os
import re
import random
from typing import Union
from urllib.parse import unquote
from pyrogram.types import Message
from pyrogram.enums import MessageEntityType
from youtubesearchpython.__future__ import VideosSearch

from AnonMusic import app
from AnonMusic.utils.database import is_on_off
from AnonMusic.utils.formatters import time_to_seconds

class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.api_url = "https://op.spotifytech.shop/youtube"
        self.api_key = "SANATANIxTECH"
        self.stream_base = "https://op.spotifytech.shop/stream/"
        self.download_folder = "downloads"
        
        os.makedirs(self.download_folder, exist_ok=True)

    async def exists(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        
        for message in messages:
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        return text[entity.offset:entity.offset + entity.length]
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return None

    async def _get_video_info(self, link: str):
        results = VideosSearch(link, limit=1)
        result = (await results.next())["result"][0]
        return {
            "title": result["title"],
            "duration": result["duration"],
            "thumbnail": result["thumbnails"][0]["url"].split("?")[0],
            "vidid": result["id"],
            "link": result["link"],
            "duration_sec": time_to_seconds(result["duration"]) if result["duration"] else 0
        }

    async def _get_cached_stream(self, vidid: str, is_video: bool):
        file_ext = ".mp4" if is_video else ".mp3"
        filename = f"{vidid}_{'video' if is_video else 'audio'}{file_ext}"
        filepath = os.path.join(self.download_folder, filename)
        
        if os.path.exists(filepath):
            return filepath
        return None

    async def _save_stream(self, stream_url: str, vidid: str, is_video: bool):
        file_ext = ".mp4" if is_video else ".mp3"
        filename = f"{vidid}_{'video' if is_video else 'audio'}{file_ext}"
        filepath = os.path.join(self.download_folder, filename)
        
        if os.path.exists(filepath):
            return filepath
            
        loop = asyncio.get_running_loop()
        
        def _download():
            ydl_opts = {
                "format": "best" if is_video else "bestaudio",
                "outtmpl": filepath,
                "quiet": True,
                "no_warnings": True,
                "http_headers": {"User-Agent": random.choice(USER_AGENTS)},
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([stream_url])
            return filepath
        
        return await loop.run_in_executor(None, _download)

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        info = await self._get_video_info(link.split("&")[0])
        return (info["title"], info["duration"], info["duration_sec"], 
                info["thumbnail"], info["vidid"])

    async def title(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        info = await self._get_video_info(link.split("&")[0])
        return info["title"]

    async def duration(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        info = await self._get_video_info(link.split("&")[0])
        return info["duration"]

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        info = await self._get_video_info(link.split("&")[0])
        return info["thumbnail"]

    async def _get_stream_url(self, link: str, video: bool = False):
        params = {
            "query": unquote(link),
            "video": str(video).lower(),
            "api_key": self.api_key
        }
        
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                response = await client.get(self.api_url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    return data.get("stream_url", "")
            except Exception:
                pass
        return ""

    async def video(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        
        vidid = extract_video_id(link)
        if vidid:
            cached = await self._get_cached_stream(vidid, True)
            if cached:
                return cached
        
        stream_url = await self._get_stream_url(link, True)
        
        if stream_url and vidid:
            await self._save_stream(stream_url, vidid, True)
            
        return stream_url

    async def playlist(self, link: str, limit: int, user_id: int, videoid: Union[bool, str] = None):
        if videoid:
            link = self.listbase + link
        
        api_url = f"{self.api_url}?query={unquote(link)}&playlist=true&api_key={self.api_key}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    return [item["id"] for item in data.get("entries", [])[:limit]]
            except Exception:
                pass
        
        ydl_opts = {
            "extract_flat": True,
            "playlistend": limit,
            "quiet": True
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link, download=False)
            return [entry["id"] for entry in info.get("entries", [])]

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        info = await self._get_video_info(link.split("&")[0])
        return {
            "title": info["title"],
            "link": info["link"],
            "vidid": info["vidid"],
            "duration_min": info["duration"],
            "thumb": info["thumbnail"]
        }, info["vidid"]

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        
        ydl_opts = {"quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link, download=False)
            formats = []
            for fmt in info.get("formats", []):
                if not fmt.get("format_note", "").lower() == "dash":
                    formats.append({
                        "format": fmt.get("format", ""),
                        "filesize": fmt.get("filesize", 0),
                        "format_id": fmt.get("format_id", ""),
                        "ext": fmt.get("ext", ""),
                        "format_note": fmt.get("format_note", ""),
                        "yturl": link
                    })
            return formats, link

    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        
        results = VideosSearch(link, limit=10)
        result = (await results.next())["result"][query_type]
        return (
            result["title"],
            result["duration"],
            result["thumbnails"][0]["url"].split("?")[0],
            result["id"]
        )

    async def download(
        self,
        link: str,
        mystic,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title: Union[bool, str] = None,
    ) -> str:
        if videoid:
            link = self.base + link
        
        if songvideo:
            return await self._download_song_video(link, format_id, title)
        elif songaudio:
            return await self._download_song_audio(link, format_id, title)
        elif video:
            stream_url = await self._get_stream_url(link, True)
            vidid = extract_video_id(link)
            if vidid:
                await self._save_stream(stream_url, vidid, True)
            return stream_url, None
        else:
            stream_url = await self._get_stream_url(link, False)
            vidid = extract_video_id(link)
            if vidid:
                await self._save_stream(stream_url, vidid, False)
            return stream_url, None

    async def _download_song_video(self, link: str, format_id: str, title: str):
        loop = asyncio.get_running_loop()
        
        def _dl():
            ydl_opts = {
                "format": f"{format_id}+140",
                "outtmpl": f"{self.download_folder}/{title}",
                "merge_output_format": "mp4",
                "quiet": True,
                "no_warnings": True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([link])
            return f"{self.download_folder}/{title}.mp4"
        
        return await loop.run_in_executor(None, _dl)

    async def _download_song_audio(self, link: str, format_id: str, title: str):
        loop = asyncio.get_running_loop()
        
        def _dl():
            ydl_opts = {
                "format": format_id,
                "outtmpl": f"{self.download_folder}/{title}.%(ext)s",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "quiet": True,
                "no_warnings": True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([link])
            return f"{self.download_folder}/{title}.mp3"
        
        return await loop.run_in_executor(None, _dl)

def extract_video_id(url: str):
    patterns = [
        r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|v/|shorts/))([^\?&]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.5672.63 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.121 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.5563.64 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.5481.178 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.5672.127 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.138 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:114.0) Gecko/20100101 Firefox/114.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:113.0) Gecko/20100101 Firefox/113.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:112.0) Gecko/20100101 Firefox/112.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13.5; rv:115.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13.4; rv:114.0) Gecko/20100101 Firefox/114.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.67",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.5672.127 Safari/537.36 Edg/113.0.1774.50",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.5672.127 Safari/537.36 OPR/98.0.4758.80",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.121 Safari/537.36 OPR/97.0.4719.63",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.5735.198 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.136 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:115.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.5414.120 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; Bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 (Linux; U; Android 9; en-US; SM-J260F Build/PPR1.180610.011) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.136 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; Pixel 4 XL Build/RQ3A.210705.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.136 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.5359.124 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.5304.122 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.5249.103 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1 Safari/605.1.15",
]
