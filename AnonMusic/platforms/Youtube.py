import asyncio
import os
import random
import re
import time
import uuid
import aiofiles
import httpx
import yt_dlp
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import unquote, quote, urlparse

from pyrogram import errors
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from youtubesearchpython.__future__ import VideosSearch

from AnonMusic import app
from AnonMusic.logging import LOGGER
from AnonMusic.utils.database import is_on_off
from AnonMusic.utils.formatters import time_to_seconds
from config import API_URL, API_KEY, DOWNLOADS_DIR

@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[Path] = None
    error: Optional[str] = None

# Add parse_tg_link function here
def parse_tg_link(link: str):
    """Parse Telegram link and return chat username and message ID"""
    try:
        parsed = urlparse(link)
        path = parsed.path.strip('/')
        parts = path.split('/')
        
        if len(parts) >= 2:
            # Check if it's from ApixFile channel
            if parts[0] == "ApixFile" and parts[1].isdigit():
                return str(parts[0]), int(parts[1])
        
        return None, None
    except Exception as e:
        LOGGER(__name__).error(f"Error parsing Telegram link: {e}")
        return None, None

class YouTubeAPI:
    DEFAULT_TIMEOUT = 120
    DEFAULT_DOWNLOAD_TIMEOUT = 120
    CHUNK_SIZE = 8192
    MAX_RETRIES = 2
    BACKOFF_FACTOR = 1.0
    BASE_URL = "https://www.youtube.com/watch?v="
    PLAYLIST_BASE = "https://youtube.com/playlist?list="
    REGEX = r"(?:youtube\.com|youtu\.be)"
    STATUS_URL = "https://www.youtube.com/oembed?url="

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, download_timeout: int = DEFAULT_DOWNLOAD_TIMEOUT, max_redirects: int = 0):
        self._timeout = timeout
        self._download_timeout = download_timeout
        self._max_redirects = max_redirects
        self._session = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self._timeout,
                read=self._timeout,
                write=self._timeout,
                pool=self._timeout
            ),
            follow_redirects=max_redirects > 0,
            max_redirects=max_redirects,
        )
        self._regex = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    async def close(self) -> None:
        try:
            await self._session.aclose()
        except Exception as e:
            LOGGER(__name__).error("Error closing HTTP session: %s", repr(e))

    @staticmethod
    def get_cookie_file() -> Optional[str]:
        """Get a random cookie file from the 'cookies' directory."""
        cookie_dir = "cookies"
        try:
            if not os.path.exists(cookie_dir):
                LOGGER(__name__).warning("Cookie directory '%s' does not exist.", cookie_dir)
                return None
            files = os.listdir(cookie_dir)
            cookies_files = [f for f in files if f.endswith(".txt")]
            if not cookies_files:
                LOGGER(__name__).warning("No cookie files found in '%s'.", cookie_dir)
                return None
            return os.path.join(cookie_dir, random.choice(cookies_files))
        except Exception as e:
            LOGGER(__name__).warning("Error accessing cookie directory: %s", e)
            return None

    def _get_headers(self, url: str, base_headers: dict[str, str]) -> dict[str, str]:
        headers = base_headers.copy()
        if API_URL and url.startswith(API_URL):
            headers["X-API-Key"] = API_KEY
        return headers

    async def download_file(self, url: str, file_path: Optional[Union[str, Path]] = None, overwrite: bool = False, **kwargs: Any) -> DownloadResult:
        if not url:
            return DownloadResult(success=False, error="Empty URL provided")
        headers = self._get_headers(url, kwargs.pop("headers", {}))
        try:
            async with self._session.stream("GET", url, timeout=self._download_timeout, headers=headers) as response:
                response.raise_for_status()
                if file_path is None:
                    cd = response.headers.get("Content-Disposition", "")
                    match = re.search(r'filename="?([^"]+)"?', cd)
                    filename = unquote(match[1]) if match else (Path(url).name or uuid.uuid4().hex)
                    path = Path(DOWNLOADS_DIR) / filename
                else:
                    path = Path(file_path) if isinstance(file_path, str) else file_path
                if path.exists() and not overwrite:
                    return DownloadResult(success=True, file_path=path)
                path.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(path, "wb") as f:
                    async for chunk in response.aiter_bytes(self.CHUNK_SIZE):
                        await f.write(chunk)
                LOGGER(__name__).debug("Successfully downloaded file to %s", path)
                return DownloadResult(success=True, file_path=path)
        except Exception as e:
            error_msg = self._handle_http_error(e, url)
            LOGGER(__name__).error(error_msg)
            return DownloadResult(success=False, error=error_msg)

    async def make_request(self, url: str, max_retries: int = MAX_RETRIES, backoff_factor: float = BACKOFF_FACTOR, **kwargs: Any) -> Optional[dict[str, Any]]:
        if not url:
            LOGGER(__name__).warning("Empty URL provided")
            return None
        headers = self._get_headers(url, kwargs.pop("headers", {}))
        for attempt in range(max_retries):
            try:
                start = time.monotonic()
                response = await self._session.get(url, headers=headers, **kwargs)
                response.raise_for_status()
                duration = time.monotonic() - start
                LOGGER(__name__).debug("Request to %s succeeded in %.2fs", url, duration)
                return response.json()
            except httpx.HTTPStatusError as e:
                try:
                    error_response = e.response.json()
                    error_msg = f"HTTP error {e.response.status_code} for {url}: {error_response.get('error', e.response.text)}"
                except ValueError:
                    error_msg = f"HTTP error {e.response.status_code} for {url}. Body: {e.response.text}"
                LOGGER(__name__).warning(error_msg)
                if attempt == max_retries - 1:
                    LOGGER(__name__).error(error_msg)
                    return None
            except httpx.TooManyRedirects as e:
                error_msg = f"Redirect loop for {url}: {repr(e)}"
                LOGGER(__name__).warning(error_msg)
                if attempt == max_retries - 1:
                    LOGGER(__name__).error(error_msg)
                    return None
            except httpx.RequestError as e:
                error_msg = f"Request failed for {url}: {repr(e)}"
                LOGGER(__name__).warning(error_msg)
                if attempt == max_retries - 1:
                    LOGGER(__name__).error(error_msg)
                    return None
            except ValueError as e:
                error_msg = f"Invalid JSON response from {url}: {repr(e)}"
                LOGGER(__name__).error(error_msg)
                return None
            except Exception as e:
                error_msg = f"Unexpected error for {url}: {repr(e)}"
                LOGGER(__name__).error(error_msg)
                return None
            await asyncio.sleep(backoff_factor * (2 ** attempt))
        LOGGER(__name__).error("All retries failed for URL: %s", url)
        return None

    @staticmethod
    def _handle_http_error(e: Exception, url: str) -> str:
        if isinstance(e, httpx.TooManyRedirects):
            return f"Too many redirects for {url}: {repr(e)}"
        elif isinstance(e, httpx.HTTPStatusError):
            try:
                error_response = e.response.json()
                return f"HTTP error {e.response.status_code} for {url}: {error_response.get('error', e.response.text)}"
            except ValueError:
                return f"HTTP error {e.response.status_code} for {url}. Body: {e.response.text}"
        elif isinstance(e, httpx.ReadTimeout):
            return f"Read timeout for {url}: {repr(e)}"
        elif isinstance(e, httpx.RequestError):
            return f"Request failed for {url}: {repr(e)}"
        return f"Unexpected error for {url}: {repr(e)}"

    async def download_with_api(self, video_id: str, is_video: bool = False) -> Optional[Path]:
        if not API_URL or not API_KEY:
            LOGGER(__name__).warning("API_URL or API_KEY is not set")
            return None
        if not video_id:
            LOGGER(__name__).warning("Video ID is None")
            return None
            
        # Build the API URL according to the new API structure
        api_url = f"{API_URL}/youtube?query={video_id}&video={'true' if is_video else 'false'}&api_key={API_KEY}"
        
        # Make request to the API
        api_response = await self.make_request(api_url)
        if not api_response:
            LOGGER(__name__).error("No response from API")
            return None
            
        # Extract the download URL from the API response
        cdn_url = api_response.get("cdnurl")
        if not cdn_url:
            LOGGER(__name__).error("API response is missing cdnurl")
            return None
            
        # Check if it's a Telegram URL from ApixFile
        if cdn_url and "t.me/ApixFile/" in cdn_url:
            try:
                # Use our parse_tg_link function for ApixFile links
                chat_username, message_id = parse_tg_link(cdn_url)
                
                if chat_username and message_id:
                    msg = await app.get_messages(chat_username, message_id)
                    if msg and (msg.document or msg.video or msg.audio):
                        path = await msg.download()
                        return Path(path) if path else None
            except Exception as e:
                LOGGER(__name__).error(f"Error downloading from ApixFile Telegram: {e}")
                return None
        # For other Telegram URLs or direct URLs
        elif "t.me" in cdn_url or "telegram.org" in cdn_url:
            try:
                # Handle other Telegram file downloads
                if "t.me" in cdn_url:
                    # Extract chat ID and message ID from Telegram URL
                    match = re.match(r"https:\/\/t\.me\/([^\/]+)\/(\d+)", cdn_url)
                    if match:
                        chat_username, message_id = match.groups()
                        msg = await app.get_messages(chat_username, int(message_id))
                        if msg and msg.document:
                            path = await msg.download()
                            return Path(path) if path else None
                
                # Handle direct Telegram file URLs
                elif "telegram.org" in cdn_url:
                    # Download directly from Telegram file URL
                    dl_result = await self.download_file(cdn_url)
                    return dl_result.file_path if dl_result.success else None
                    
            except Exception as e:
                LOGGER(__name__).error(f"Error downloading from Telegram: {e}")
                return None
        else:
            # Handle direct HTTP downloads
            dl_result = await self.download_file(cdn_url)
            return dl_result.file_path if dl_result.success else None
            
        return None

    async def get_direct_download_url(self, video_id: str, is_video: bool = False) -> Optional[str]:
        """Get direct download URL from API without downloading the file"""
        if not API_URL or not API_KEY:
            LOGGER(__name__).warning("API_URL or API_KEY is not set")
            return None
        if not video_id:
            LOGGER(__name__).warning("Video ID is None")
            return None
            
        # Build the API URL according to the new API structure
        api_url = f"{API_URL}/youtube?query={video_id}&video={'true' if is_video else 'false'}&api_key={API_KEY}"
        
        # Make request to the API
        api_response = await self.make_request(api_url)
        if not api_response:
            LOGGER(__name__).error("No response from API")
            return None
            
        # Return the direct download URL
        return api_response.get("cdnurl")

    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        if videoid:
            link = self.BASE_URL + link
        return bool(re.search(self.REGEX, link))

    async def url(self, message_1: Message) -> Optional[str]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        text = ""
        offset = None
        length = None
        for message in messages:
            if offset:
                break
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        offset, length = entity.offset, entity.length
                        break
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return text[offset:offset + length] if offset is not None else None

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.BASE_URL + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            vidid = result["id"]
            duration_sec = 0 if str(duration_min) == "None" else int(time_to_seconds(duration_min))
        return title, duration_min, duration_sec, thumbnail, vidid

    async def title(self, link: str, videoid: Union[bool, str] = None) -> str:
        if videoid:
            link = self.BASE_URL + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        return (await results.next())["result"][0]["title"]

    async def duration(self, link: str, videoid: Union[bool, str] = None) -> str:
        if videoid:
            link = self.BASE_URL + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        return (await results.next())["result"][0]["duration"]

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None) -> str:
        if videoid:
            link = self.BASE_URL + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        return (await results.next())["result"][0]["thumbnails"][0]["url"].split("?")[0]

    async def video(self, link: str, videoid: Union[bool, str] = None) -> tuple[int, str]:
        # First try to get direct download URL from API
        direct_url = await self.get_direct_download_url(link, True)
        if direct_url:
            return 1, direct_url
            
        # Fallback to yt-dlp if API fails
        if videoid:
            link = self.BASE_URL + link
        if "&" in link:
            link = link.split("&")[0]
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--cookies", self.get_cookie_file() or "",
            "-g",
            "-f",
            "best[height<=?720][width<=?1280]",
            link,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (1, stdout.decode().split("\n")[0]) if stdout else (0, stderr.decode())

    async def playlist(self, link: str, limit: int, user_id: int, videoid: Union[bool, str] = None) -> list[str]:
        if videoid:
            link = self.PLAYLIST_BASE + link
        if "&" in link:
            link = link.split("&")[0]
        proc = await asyncio.create_subprocess_shell(
            f"yt-dlp -i --get-id --flat-playlist --playlist-end {limit} --skip-download {link}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, errorz = await proc.communicate()
        result = out.decode("utf-8").split("\n") if not errorz or "unavailable videos are hidden" in errorz.decode("utf-8").lower() else []
        return [key for key in result if key]

    async def track(self, link: str, videoid: Union[bool, str] = None) -> tuple[dict, str]:
        if videoid:
            link = self.BASE_URL + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        result = (await results.next())["result"][0]
        track_details = {
            "title": result["title"],
            "link": result["link"],
            "vidid": result["id"],
            "duration_min": result["duration"],
            "thumb": result["thumbnails"][0]["url"].split("?")[0],
        }
        return track_details, result["id"]

    async def formats(self, link: str, videoid: Union[bool, str] = None) -> tuple[list[dict], str]:
        if videoid:
            link = self.BASE_URL + link
        if "&" in link:
            link = link.split("&")[0]
        ytdl_opts = {"quiet": True, "cookiefile": self.get_cookie_file()}
        with yt_dlp.YoutubeDL(ytdl_opts) as ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for format in r["formats"]:
                if "dash" in str(format.get("format", "")).lower():
                    continue
                if all(key in format for key in ["format", "filesize", "format_id", "ext", "format_note"]):
                    formats_available.append({
                        "format": format["format"],
                        "filesize": format["filesize"],
                        "format_id": format["format_id"],
                        "ext": format["ext"],
                        "format_note": format["format_note"],
                        "yturl": link,
                    })
        return formats_available, link

    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None) -> tuple[str, str, str, str]:
        if videoid:
            link = self.BASE_URL + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=10)
        result = (await results.next())["result"][query_type]
        return result["title"], result["duration"], result["thumbnails"][0]["url"].split("?")[0], result["id"]

    async def download(self, link: str, mystic, video: Union[bool, str] = None, videoid: Union[bool, str] = None, songaudio: Union[bool, str] = None, songvideo: Union[bool, str] = None, format_id: Union[bool, str] = None, title: Union[bool, str] = None) -> Union[str, tuple[str, bool]]:
        # First check if it's a direct ApixFile Telegram link
        if link and "t.me/ApixFile/" in link:
            try:
                chat_username, message_id = parse_tg_link(link)
                if chat_username and message_id:
                    msg = await app.get_messages(chat_username, message_id)
                    if msg and (msg.document or msg.video or msg.audio):
                        path = await msg.download()
                        return path, True
            except Exception as e:
                LOGGER(__name__).error(f"Error downloading from ApixFile link: {e}")
                # Fall through to normal processing if Telegram download fails
        
        if videoid:
            link = self.BASE_URL + link
        loop = asyncio.get_running_loop()

        def audio_dl():
            ydl_optssx = {
                "format": "bestaudio/best",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "cookiefile": self.get_cookie_file(),
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_optssx) as x:
                info = x.extract_info(link, download=False)
                xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
                if os.path.exists(xyz):
                    return xyz
                x.download([link])
                return xyz

        def video_dl():
            ydl_optssx = {
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "cookiefile": self.get_cookie_file(),
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_optssx) as x:
                info = x.extract_info(link, download=False)
                xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
                if os.path.exists(xyz):
                    return xyz
                x.download([link])
                return xyz

        def song_video_dl():
            formats = f"{format_id}+140"
            fpath = f"downloads/{title}"
            ydl_optssx = {
                "format": formats,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "cookiefile": self.get_cookie_file(),
                "quiet": True,
                "no_warnings": True,
                "prefer_ffmpeg": True,
                "merge_output_format": "mp4",
            }
            with yt_dlp.YoutubeDL(ydl_optssx) as x:
                x.download([link])
            return f"downloads/{title}.mp4"

        def song_audio_dl():
            fpath = f"downloads/{title}.%(ext)s"
            ydl_optssx = {
                "format": format_id,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "cookiefile": self.get_cookie_file(),
                "prefer_ffmpeg": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
            with yt_dlp.YoutubeDL(ydl_optssx) as x:
                x.download([link])
            return f"downloads/{title}.mp3"

        if songvideo:
            # Try to get direct URL from API first
            direct_url = await self.get_direct_download_url(link, True)
            if direct_url:
                return direct_url
            return await loop.run_in_executor(None, song_video_dl)
        elif songaudio:
            # Try to get direct URL from API first
            direct_url = await self.get_direct_download_url(link, False)
            if direct_url:
                return direct_url
            return await loop.run_in_executor(None, song_audio_dl)
        elif video:
            # Try to get direct URL from API first
            direct_url = await self.get_direct_download_url(link, True)
            if direct_url:
                return direct_url, True
                
            # Fallback to yt-dlp if API fails
            direct = True
            if await is_on_off(1):
                downloaded_file = await loop.run_in_executor(None, video_dl)
            else:
                proc = await asyncio.create_subprocess_exec(
                    "yt-dlp",
                    "--cookies", self.get_cookie_file() or "",
                    "-g",
                    "-f",
                    "best[height<=?720][width<=?1280]",
                    link,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if stdout:
                    downloaded_file = stdout.decode().split("\n")[0]
                    direct = None
                else:
                    return "", direct
        else:
            # Try to get direct URL from API first
            direct_url = await self.get_direct_download_url(link, False)
            if direct_url:
                return direct_url, True
                
            # Fallback to yt-dlp if API fails
            direct = True
            downloaded_file = await loop.run_in_executor(None, audio_dl)
        return downloaded_file, direct
