import os
import re
import time
import mmap
import datetime
import aiohttp
import aiofiles
import asyncio
import logging
import requests
import tgcrypto
import subprocess
import shutil
import concurrent.futures
from typing import Optional, List
from math import ceil
from utils import progress_bar
from pyrogram import Client, filters
from pyrogram.types import Message
from io import BytesIO
from pathlib import Path  
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import json
from base64 import b64decode
import cp_helper

def find_binary(name: str) -> Optional[str]:
    w = shutil.which(name)
    if w:
        return w
    base_dirs = ['.', '..', 'modules', 'Banana_bot--main', 'Banana_bot--main/modules', 'C:/muftukmall', '/usr/local/bin']
    exts = ['', '.exe', '.EXE']
    for b in base_dirs:
        for ext in exts:
            p = os.path.join(b, f"{name}{ext}")
            if os.path.exists(p):
                return os.path.abspath(p)
    return None

def get_video_info(filename: str):
    """
    Extracts accurate duration, width, and height using ffprobe.
    Ensures video can be streamed and played directly inside Telegram.
    """
    info = {"duration": 0, "width": 1280, "height": 720}
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=width,height,duration",
            "-of", "json", filename
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            format_info = data.get("format", {})
            if "duration" in format_info:
                try:
                    info["duration"] = int(float(format_info["duration"]))
                except (ValueError, TypeError):
                    pass
            for stream in data.get("streams", []):
                if stream.get("width") and stream.get("height"):
                    info["width"] = int(stream["width"])
                    info["height"] = int(stream["height"])
                    if not info["duration"] and stream.get("duration"):
                        try:
                            info["duration"] = int(float(stream["duration"]))
                        except (ValueError, TypeError):
                            pass
                    break
    except Exception as e:
        print(f"ffprobe metadata extraction skipped: {e}")
    return info

def duration(filename):
    info = get_video_info(filename)
    return float(info.get("duration", 0))

# ============================================================
#  🔥 NEW: get_mps_and_keys with Widevine & Classplus API v56
# ============================================================
def get_mps_and_keys(api_url, lic_url=None):
    """
    Fetch MPD and keys from ClassPlus API / Widevine CDM.
    Supports both direct MPD and Classplus content/streaming URLs.
    """
    try:
        # If it's a direct MPD URL
        if api_url.endswith('.mpd') or '/manifest' in api_url or '.mpd?' in api_url:
            pssh = cp_helper.extract_pssh(api_url)
            if pssh and lic_url:
                keys = cp_helper.extract_widevine_keys(pssh, lic_url)
                return api_url, keys
            return api_url, []

        # If it's a Classplus / Testbook URL
        res = cp_helper.resolve_classplus(api_url)
        if res.get("status") == "drm":
            return res.get("url"), res.get("keys", [])
        elif res.get("status") == "signed":
            return res.get("url"), []
        return api_url, []
    except Exception as e:
        print(f"Error in get_mps_and_keys: {e}")
        return api_url, []

def extract_keys_from_mpd(mpd_content, lic_url=None):
    """Extract decryption keys from MPD content using Widevine CDM."""
    try:
        pssh_pattern = r'<pssh[^>]*>([^<]+)</pssh>|<cenc:pssh[^>]*>([^<]+)</cenc:pssh>'
        pssh_matches = re.findall(pssh_pattern, mpd_content, re.IGNORECASE)
        pssh_b64 = None
        for m in pssh_matches:
            cand = m[0] or m[1]
            if cand:
                pssh_b64 = cand.strip()
                break
        if pssh_b64 and lic_url:
            return cp_helper.extract_widevine_keys(pssh_b64, lic_url)
        return []
    except Exception as e:
        print(f"Error extracting keys: {e}")
        return []

def exec(cmd):
        process = subprocess.run(cmd, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        output = process.stdout.decode()
        print(output)
        return output

def pull_run(work, cmds):
    with concurrent.futures.ThreadPoolExecutor(max_workers=work) as executor:
        print("Waiting for tasks to complete")
        fut = executor.map(exec,cmds)
        
async def aio(url,name):
    k = f'{name}.pdf'
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                f = await aiofiles.open(k, mode='wb')
                await f.write(await resp.read())
                await f.close()
    return k


async def download(url,name):
    ka = f'{name}.pdf'
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                f = await aiofiles.open(ka, mode='wb')
                await f.write(await resp.read())
                await f.close()
    return ka


def parse_vid_info(info):
    info = info.strip()
    info = info.split("\n")
    new_info = []
    temp = []
    for i in info:
        i = str(i)
        if "[" not in i and '---' not in i:
            while "  " in i:
                i = i.replace("  ", " ")
            i.strip()
            i = i.split("|")[0].split(" ",2)
            try:
                if "RESOLUTION" not in i[2] and i[2] not in temp and "audio" not in i[2]:
                    temp.append(i[2])
                    new_info.append((i[0], i[2]))
            except:
                pass
    return new_info

def vid_info(info):
    info = info.strip()
    info = info.split("\n")
    new_info = dict()
    temp = []
    for i in info:
        i = str(i)
        if "[" not in i and '---' not in i:
            while "  " in i:
                i = i.replace("  ", " ")
            i.strip()
            i = i.split("|")[0].split(" ",3)
            try:
                if "RESOLUTION" not in i[2] and i[2] not in temp and "audio" not in i[2]:
                    temp.append(i[2])
                    new_info.update({f'{i[2]}':f'{i[0]}'})
            except:
                pass
    return new_info

# ============================================================
#  🔥 FIXED: decrypt_and_merge_video (N_m3u8DL-RE & mp4decrypt)
# ============================================================
async def decrypt_and_merge_video(mpd_url, keys_string, output_path, output_name, quality="720", keys_list=None):
    try:
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)

        # Parse keys_list if not passed explicitly
        if not keys_list and keys_string:
            keys_list = re.findall(r'([0-9a-fA-F]{32}:[0-9a-fA-F]{32})', keys_string)
            if not keys_list:
                keys_list = [k.strip() for k in keys_string.replace('--key', '').split() if ':' in k]

        clean_keys = [k.strip() for k in (keys_list or []) if k and k.strip()]
        keys_arg_str = " ".join([f"--key {k}" for k in clean_keys]) if clean_keys else keys_string

        safe_name = "".join(c for c in output_name if c.isalnum() or c in (" ", "_", "-")).strip() or f"Video_{int(time.time())}"
        safe_name = re.sub(r"\s+", "_", safe_name)

        n_m3u8dl = find_binary("N_m3u8DL-RE")
        ff_bin = find_binary("ffmpeg")

        # -------------------------------------------------------------
        # 1. Prefer N_m3u8DL-RE engine if available
        # -------------------------------------------------------------
        if n_m3u8dl:
            print(f"🚀 Using N_m3u8DL-RE engine ({n_m3u8dl}) for download & decrypt...")
            n_cmd = [
                n_m3u8dl,
                mpd_url,
                "--auto-select",
                "--save-dir", str(output_path.resolve()),
                "--save-name", safe_name,
                "-M", "format=mp4",
                "--del-after-done"
            ]
            if ff_bin:
                n_cmd.extend(["--ffmpeg-binary-path", ff_bin])
            for k in clean_keys:
                n_cmd.extend(["--key", k])

            print(f"Running N_m3u8DL-RE: {' '.join(n_cmd)}")
            proc = subprocess.run(n_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            print(f"N_m3u8DL-RE exit code: {proc.returncode}")

            candidate_files = list(output_path.glob(f"{safe_name}*.mp4")) or list(output_path.glob("*.mp4"))
            if candidate_files:
                target_file = output_path / f"{safe_name}.mp4"
                if not target_file.exists():
                    target_file = candidate_files[0]
                print(f"✅ N_m3u8DL-RE successfully completed: {target_file}")
                return str(target_file)

            print("⚠️ N_m3u8DL-RE finished but output file not found, falling back to yt-dlp + mp4decrypt...")

        # -------------------------------------------------------------
        # 2. Fallback: yt-dlp + mp4decrypt + ffmpeg
        # -------------------------------------------------------------
        print("🔄 Running yt-dlp + mp4decrypt + ffmpeg pipeline...")
        cmd1 = f'yt-dlp -f "bv[height<={quality}]+ba/b" -o "{output_path}/file.%(ext)s" --allow-unplayable-format --no-check-certificate "{mpd_url}"'
        print(f"Running command: {cmd1}")
        os.system(cmd1)

        avDir = list(output_path.iterdir())
        print(f"Downloaded files: {avDir}")
        print("Decrypting")

        video_decrypted = False
        audio_decrypted = False

        if keys_arg_str and '--key' in keys_arg_str:
            mp4dec_bin = find_binary("mp4decrypt") or "mp4decrypt"
            for data in avDir:
                if data.suffix == ".mp4" and not video_decrypted:
                    cmd2 = f'{mp4dec_bin} {keys_arg_str} --show-progress "{data}" "{output_path}/video.mp4"'
                    print(f"Running command: {cmd2}")
                    os.system(cmd2)
                    if (output_path / "video.mp4").exists():
                        video_decrypted = True
                    data.unlink()
                elif data.suffix == ".m4a" and not audio_decrypted:
                    cmd3 = f'{mp4dec_bin} {keys_arg_str} --show-progress "{data}" "{output_path}/audio.m4a"'
                    print(f"Running command: {cmd3}")
                    os.system(cmd3)
                    if (output_path / "audio.m4a").exists():
                        audio_decrypted = True
                    data.unlink()

        if not video_decrypted:
            for data in avDir:
                if data.suffix in ['.mp4', '.mkv', '.webm', '.ts']:
                    data.rename(output_path / "video.mp4")
                    video_decrypted = True
                    break

        if not audio_decrypted:
            for data in avDir:
                if data.suffix in ['.m4a', '.mp3', '.aac', '.mka']:
                    data.rename(output_path / "audio.m4a")
                    audio_decrypted = True
                    break

        final_path = output_path / f"{safe_name}.mp4"
        if video_decrypted and audio_decrypted:
            cmd4 = f'ffmpeg -y -i "{output_path}/video.mp4" -i "{output_path}/audio.m4a" -c:v copy -c:a aac -movflags +faststart "{final_path}"'
            print(f"Running command: {cmd4}")
            os.system(cmd4)
            if (output_path / "video.mp4").exists():
                (output_path / "video.mp4").unlink()
            if (output_path / "audio.m4a").exists():
                (output_path / "audio.m4a").unlink()
        elif video_decrypted and not audio_decrypted:
            if (output_path / "video.mp4").exists():
                # Remux with faststart
                cmd_fast = f'ffmpeg -y -i "{output_path}/video.mp4" -c copy -movflags +faststart "{final_path}"'
                os.system(cmd_fast)
                if not final_path.exists():
                    (output_path / "video.mp4").rename(final_path)
                else:
                    (output_path / "video.mp4").unlink()
        else:
            for data in output_path.iterdir():
                if data.suffix in ['.mp4', '.mkv', '.webm']:
                    cmd_fast = f'ffmpeg -y -i "{data}" -c:v copy -c:a aac -movflags +faststart "{final_path}"'
                    os.system(cmd_fast)
                    if not final_path.exists():
                        data.rename(final_path)
                    else:
                        data.unlink()
                    break

        if not final_path.exists():
            raise FileNotFoundError("Merged video file not found.")

        try:
            probe_res = subprocess.run(["ffmpeg", "-i", str(final_path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            m_dur = re.search(r'Duration:\s*(\d+:\d+:\d+)', probe_res.stdout)
            if m_dur:
                print(f"Duration info: {m_dur.group(1)}")
        except Exception as err:
            print(f"Duration info check skipped: {err}")

        return str(final_path)

    except Exception as e:
        print(f"Error during decryption and merging: {str(e)}")
        raise


async def run(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()

    print(f'[{cmd!r} exited with {proc.returncode}]')
    if proc.returncode == 1:
        return False
    if stdout:
        return f'[stdout]\n{stdout.decode()}'
    if stderr:
        return f'[stderr]\n{stderr.decode()}'


def old_download(url, file_name, chunk_size = 1024 * 10):
    if os.path.exists(file_name):
        os.remove(file_name)
    r = requests.get(url, allow_redirects=True, stream=True)
    with open(file_name, 'wb') as fd:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                fd.write(chunk)
    return file_name


def human_readable_size(size, decimal_places=2):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if size < 1024.0 or unit == 'PB':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"


def time_name():
    date = datetime.date.today()
    now = datetime.datetime.now()
    current_time = now.strftime("%H%M%S")
    return f"{date} {current_time}.mp4"


# ============================================================
#  🔥 FIXED: download_video with URL parameter preservation
# ============================================================
async def download_video(url, cmd, name):
    # For ClassX, Akamai, Classplus, and HLS m3u8 streams with custom headers, do not force aria2c as it drops custom HTTP headers
    if any(k in url.lower() for k in ["classx", "akamai", "classplus", ".m3u8"]) or "--concurrent-fragments" in cmd:
        download_cmd = f'{cmd} -R 25 --fragment-retries 25'
    else:
        download_cmd = f'{cmd} -R 25 --fragment-retries 25 --external-downloader aria2c --downloader-args "aria2c: -x 16 -j 32"'

    global failed_counter
    print(f"⬇️ Running: {download_cmd}")
    logging.info(download_cmd)
    k = subprocess.run(download_cmd, shell=True)
    if "visionias" in cmd and k.returncode != 0 and failed_counter <= 10:
        failed_counter += 1
        await asyncio.sleep(5)
        return await download_video(url, cmd, name)
    failed_counter = 0

    # Locate the actual downloaded file on disk
    base_stem = Path(name).stem
    found_file = None
    candidates = [
        name,
        f"{name}.mp4",
        f"{name}.mkv",
        f"{name}.webm",
        f"{base_stem}.mp4",
        f"{base_stem}.mkv",
        f"{base_stem}.webm",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 0:
            found_file = c
            break

    if not found_file:
        for f in os.listdir("."):
            if f.startswith(base_stem) and f.endswith((".mp4", ".mkv", ".webm")):
                if os.path.getsize(f) > 0:
                    found_file = f
                    break

    if not found_file:
        raise FileNotFoundError(f"Video download failed or file '{name}' not found on disk. Please verify the URL or token.")

    # Convert/Remux to Faststart MP4 for instant Telegram streaming
    # This places the moov atom at the front so users can stream immediately without downloading full file!
    faststart_mp4 = f"{base_stem}_faststart.mp4"
    print(f"🎬 Preparing faststart MP4 for Telegram streaming: {found_file} -> {faststart_mp4}")
    remux_cmd = f'ffmpeg -y -i "{found_file}" -c:v copy -c:a aac -movflags +faststart "{faststart_mp4}"'
    subprocess.run(remux_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    if os.path.isfile(faststart_mp4) and os.path.getsize(faststart_mp4) > 0:
        try:
            if os.path.abspath(found_file) != os.path.abspath(faststart_mp4):
                os.remove(found_file)
        except Exception:
            pass
        return faststart_mp4
    else:
        return found_file


async def send_doc(bot: Client, m: Message, cc, ka, cc1, prog, count, name, channel_id):
    reply = await bot.send_message(channel_id, f"Downloading pdf:\n<pre><code>{name}</code></pre>")
    time.sleep(1)
    start_time = time.time()
    await bot.send_document(ka, caption=cc1)
    count+=1
    await reply.delete (True)
    time.sleep(1)
    os.remove(ka)
    time.sleep(3) 


def decrypt_file(file_path, key):  
    if not os.path.exists(file_path): 
        return False  

    with open(file_path, "r+b") as f:  
        num_bytes = min(28, os.path.getsize(file_path))  
        with mmap.mmap(f.fileno(), length=num_bytes, access=mmap.ACCESS_WRITE) as mmapped_file:  
            for i in range(num_bytes):  
                mmapped_file[i] ^= ord(key[i]) if i < len(key) else i 
    return True  

async def download_and_decrypt_video(url, cmd, name, key):  
    video_path = await download_video(url, cmd, name)  
    
    if video_path:  
        decrypted = decrypt_file(video_path, key)  
        if decrypted:  
            print(f"File {video_path} decrypted successfully.")  
            return video_path  
        else:  
            print(f"Failed to decrypt {video_path}.")  
            return None  

# ============================================================
#  🔥 FIXED: send_vid with accurate duration & Telegram streaming
# ============================================================
async def send_vid(bot: Client, m: Message, cc, filename, vidwatermark, thumb, name, prog, channel_id):
    if not filename or not os.path.isfile(filename) or os.path.getsize(filename) == 0:
        raise FileNotFoundError(f"Video file '{filename}' does not exist on disk to upload.")

    info = get_video_info(filename)
    dur = info.get("duration", 0)
    width = info.get("width", 1280)
    height = info.get("height", 720)

    thumb_time = "00:00:02" if dur >= 3 else "00:00:00"
    subprocess.run(f'ffmpeg -y -i "{filename}" -ss {thumb_time} -vframes 1 "{filename}.jpg"', shell=True)
    if prog:
        try:
            await prog.delete(True)
        except Exception:
            pass
    reply1 = await bot.send_message(channel_id, f"**📩 Uploading Video 📩:-**\n<blockquote>**{name}**</blockquote>")
    reply = await m.reply_text(f"**Generate Thumbnail:**\n<blockquote>**{name}**</blockquote>")
    try:
        if thumb == "/d":
            thumbnail = f"{filename}.jpg"
        else:
            thumbnail = thumb  
        
        if vidwatermark == "/d":
            w_filename = f"{filename}"
        else:
            w_filename = f"w_{filename}"
            font_path = "vidwater.ttf"
            subprocess.run(
                f'ffmpeg -y -i "{filename}" -vf "drawtext=fontfile={font_path}:text=\'{vidwatermark}\':fontcolor=white@0.3:fontsize=h/6:x=(w-text_w)/2:y=(h-text_h)/2" -c:a aac -movflags +faststart "{w_filename}"',
                shell=True
            )
            info = get_video_info(w_filename)
            dur = info.get("duration", dur)
            width = info.get("width", width)
            height = info.get("height", height)
            
    except Exception as e:
        await m.reply_text(str(e))
        w_filename = f"{filename}"

    start_time = time.time()
    thumb_to_send = thumbnail if (thumbnail and os.path.exists(thumbnail) and os.path.getsize(thumbnail) > 0) else None

    try:
        await bot.send_video(
            channel_id,
            w_filename,
            caption=cc,
            supports_streaming=True,
            height=height,
            width=width,
            thumb=thumb_to_send,
            duration=dur,
            progress=progress_bar,
            progress_args=(reply, start_time)
        )
    except Exception as send_err:
        print(f"send_video error: {send_err}, falling back to send_document")
        await bot.send_document(channel_id, w_filename, caption=cc, progress=progress_bar, progress_args=(reply, start_time))

    try:
        if os.path.exists(w_filename):
            os.remove(w_filename)
    except Exception:
        pass
    await reply.delete(True)
    await reply1.delete(True)
    try:
        if os.path.exists(f"{filename}.jpg"):
            os.remove(f"{filename}.jpg")
    except Exception:
        pass
