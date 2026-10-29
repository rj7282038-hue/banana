import glob
import os
import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple, Dict, Any
import requests

import shutil

try:
    from pywidevine.cdm import Cdm
    from pywidevine.device import Device, DeviceTypes
    from pywidevine.pssh import PSSH
    WIDEVINE_ENABLED = True
except ImportError:
    WIDEVINE_ENABLED = False


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

DEFAULT_CP_HEADERS = {
    'host': 'api.classplusapp.com',
    'accept-language': 'en',
    'api-version': '56',
    'app-version': '1.12.1.1',
    'build-number': '56',
    'connection': 'Keep-Alive',
    'content-type': 'application/json',
    'device-details': 'motorola_Moto G4_SDK-32',
    'device-id': 'c28d3cb16bbdac01',
    'region': 'IN',
    'user-agent': 'Mobile-Android',
    'accept-encoding': 'gzip'
}


def get_widevine_device() -> Optional[Any]:
    """
    Load a Widevine Device from:
    1. Any .wvd file in WVDs/ or current/parent directories
    2. device_client_id_blob + device_private_key.txt
    """
    if not WIDEVINE_ENABLED:
        return None

    # Search paths for .wvd files
    wvd_patterns = [
        'WVDs/*.wvd',
        'modules/WVDs/*.wvd',
        '../WVDs/*.wvd',
        './*.wvd',
        'modules/*.wvd',
        '../*.wvd'
    ]
    for pattern in wvd_patterns:
        matches = glob.glob(pattern)
        if matches:
            for wvd_path in matches:
                try:
                    dev = Device.load(wvd_path)
                    return dev
                except Exception as e:
                    print(f"[CP] Error loading WVD '{wvd_path}': {e}")

    # Fallback to device_client_id_blob + device_private_key.txt
    blob_candidates = [
        'device_client_id_blob',
        'modules/device_client_id_blob',
        '../device_client_id_blob'
    ]
    key_candidates = [
        'device_private_key.txt',
        'modules/device_private_key.txt',
        '../device_private_key.txt'
    ]

    blob_file = next((f for f in blob_candidates if os.path.exists(f)), None)
    key_file = next((f for f in key_candidates if os.path.exists(f)), None)

    if blob_file and key_file:
        try:
            with open(blob_file, 'rb') as f:
                client_id = f.read()
            with open(key_file, 'rb') as f:
                private_key = f.read()
            return Device(
                type_=DeviceTypes.ANDROID,
                security_level=3,
                flags={},
                private_key=private_key,
                client_id=client_id
            )
        except Exception as e:
            print(f"[CP] Error initializing Device from blob/key: {e}")

    return None


def extract_pssh(mpd_url: str) -> Optional[str]:
    """
    Fetch MPD manifest and extract Widevine PSSH box.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/dash+xml,application/xml,text/xml,*/*'
        }
        resp = requests.get(mpd_url, headers=headers, timeout=15)
        resp.raise_for_status()

        # Parse XML
        try:
            root = ET.fromstring(resp.content)
            for elem in root.iter():
                if 'ContentProtection' in elem.tag:
                    scheme = elem.get('schemeIdUri', '').lower()
                    if 'edef8ba9-79d6-4ace-a3c8-27dcd51d21ed' in scheme:
                        for child in elem:
                            if 'pssh' in child.tag.lower() and child.text:
                                return child.text.strip()
        except Exception as parse_err:
            print(f"[CP] XML parsing error: {parse_err}")

        # Fallback to regex
        pssh_match = re.search(r'<cenc:pssh[^>]*>([^<]+)</cenc:pssh>', resp.text, re.IGNORECASE)
        if not pssh_match:
            pssh_match = re.search(r'<pssh[^>]*>([^<]+)</pssh>', resp.text, re.IGNORECASE)
        if pssh_match:
            return pssh_match.group(1).strip()

    except Exception as err:
        print(f"[CP] Error fetching/parsing MPD for PSSH: {err}")
    return None


def extract_widevine_keys(pssh_b64: str, lic_url: str) -> List[str]:
    """
    Perform Widevine CDM license exchange and return list of 'kid:key' decryption keys.
    """
    if not WIDEVINE_ENABLED:
        print("[CP] pywidevine is not installed!")
        return []

    device = get_widevine_device()
    if not device:
        print("[CP] No Widevine device available for key extraction.")
        return []

    try:
        cdm = Cdm.from_device(device)
        session_id = cdm.open()

        challenge = cdm.get_license_challenge(session_id, PSSH(pssh_b64))
        lic_resp = requests.post(
            lic_url,
            data=challenge,
            headers={
                'user-agent': 'okhttp/4.9.3',
                'content-type': 'application/octet-stream'
            },
            timeout=20
        )
        lic_resp.raise_for_status()

        cdm.parse_license(session_id, lic_resp.content)
        keys = [f"{k.kid.hex}:{k.key.hex()}" for k in cdm.get_keys(session_id) if k.type == 'CONTENT']
        cdm.close(session_id)
        return keys
    except Exception as err:
        print(f"[CP] License acquisition failed: {err}")
        return []


def is_classplus_url(url: str) -> bool:
    """Check if URL or identifier belongs to Classplus / testbook ecosystem."""
    lower_url = url.lower()
    return any(k in lower_url for k in [
        "classplusapp", "cpvod.testbook.com", "media-cdn.classplusapp.com",
        "tencdn.classplusapp.com", "akamai-cdn.classplusapp.com", "webvideos.classplusapp",
        "/lc/", "contenthashid", "vidkey"
    ])


def resolve_classplus(url: str, token: Optional[str] = None, org_code: Optional[str] = None) -> Dict[str, Any]:
    """
    Resolve Classplus video using the new API logic:
    - URL cleanup / CDN replacement
    - JW signed URL API with api-version 56
    - MPD & Widevine keys extraction
    """
    clean_url = url.strip()
    clean_url = clean_url.replace("https://cpvod.testbook.com/", "https://media-cdn.classplusapp.com/drm/")
    clean_url = clean_url.replace("https://tencdn.classplusapp.com/", "https://media-cdn.classplusapp.com/tencent/")
    clean_url = clean_url.replace("http://cpvod.testbook.com/", "https://media-cdn.classplusapp.com/drm/")
    clean_url = clean_url.replace("http://tencdn.classplusapp.com/", "https://media-cdn.classplusapp.com/tencent/")

    headers = DEFAULT_CP_HEADERS.copy()
    if token and token != "/d" and token != "cptoken":
        headers["x-access-token"] = token
    if org_code and org_code != "/d":
        headers["orgCode"] = org_code

    sign_api = "https://api.classplusapp.com/cams/uploader/video/jw-signed-url"
    
    # Extract candidate contentId if available
    content_id = None
    for pattern in [r'contentHashId=([^&]+)', r'contentHashIdl=([^&]+)', r'[?&]contentHashId=([^&]+)', r'/lc/([^/]+)/', r'vidKey=([^&]+)']:
        m = re.search(pattern, url)
        if m:
            content_id = m.group(1).split('&')[0]
            break

    # 1. Try with URL or contentId parameter
    params = {"contentId": clean_url, "offlineDownload": "false"} if not clean_url.startswith("http") else {"url": clean_url}

    data = None
    try:
        resp = requests.get(sign_api, params=params, headers=headers, timeout=20)
        data = resp.json()
    except Exception as e:
        print(f"[CP] API error on first attempt: {e}")

    signed_url = None
    drm_urls = None
    if isinstance(data, dict):
        signed_url = data.get("url") or (data.get("data") or {}).get("url")
        drm_urls = data.get("drmUrls") or (data.get("data") or {}).get("drmUrls")

    # 2. If first attempt didn't yield signed_url or drm_urls, and content_id exists, try contentId param
    if not signed_url and not drm_urls and content_id:
        try:
            params_alt = {"contentId": content_id, "offlineDownload": "false"}
            resp_alt = requests.get(sign_api, params=params_alt, headers=headers, timeout=20)
            data_alt = resp_alt.json()
            if isinstance(data_alt, dict):
                signed_url = data_alt.get("url") or (data_alt.get("data") or {}).get("url")
                drm_urls = data_alt.get("drmUrls") or (data_alt.get("data") or {}).get("drmUrls")
                if signed_url or drm_urls:
                    data = data_alt
        except Exception as e:
            print(f"[CP] API error on contentId fallback attempt: {e}")

    # Check for direct signed URL (HLS/MP4)
    if signed_url:
        print(f"[CP] Signed direct URL obtained: {signed_url[:80]}...")
        return {
            "status": "signed",
            "url": signed_url,
            "keys": [],
            "raw": data
        }

    # Check for DRM protected stream
    if drm_urls:
        mpd_url = drm_urls.get("manifestUrl") or drm_urls.get("mpdUrl")
        lic_url = drm_urls.get("licenseUrl") or drm_urls.get("licUrl")
        print(f"[CP] DRM stream obtained - MPD: {mpd_url[:80]}...")
        print(f"[CP] License URL: {lic_url[:80]}...")

        keys = []
        if mpd_url and lic_url:
            pssh = extract_pssh(mpd_url)
            if pssh:
                print(f"[CP] PSSH extracted: {pssh[:40]}...")
                keys = extract_widevine_keys(pssh, lic_url)
                print(f"[CP] Keys acquired: {keys}")
            else:
                print("[CP] Warning: Could not find PSSH in MPD.")

        return {
            "status": "drm",
            "url": mpd_url,
            "keys": keys,
            "lic_url": lic_url,
            "raw": data
        }

    print(f"[CP] No signed/DRM URLs from API, falling back to original URL. Response: {data}")
    return {
        "status": "fallback",
        "url": url,
        "keys": [],
        "raw": data
    }
