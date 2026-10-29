import os
import re
import logging
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import urlparse, urljoin
import requests

logger = logging.getLogger("classx")


def is_classx_url(url_or_str: str) -> bool:
    """Checks whether the given URL or identifier is a ClassX stream."""
    if not url_or_str:
        return False
    u = url_or_str.lower().strip()
    return any(k in u for k in [
        "classx.co.in",
        "transcoded-videos.classx",
        "classx"
    ])


def extract_classx_tenant(url: str) -> str:
    """
    Extracts the tenant / institution name from a ClassX video URL.
    Examples:
    - https://transcoded-videos.classx.co.in/videos/impetusgurukul-data/... -> impetusgurukul
    - https://impetusgurukul.classx.co.in/videos/impetusgurukul/... -> impetusgurukul
    """
    if not url:
        return "transcoded-videos"

    # 1. Path pattern: /videos/{tenant}-data/ or /videos/{tenant}/
    m = re.search(r'/videos/([a-zA-Z0-9_\-]+?)(?:-data)?/', url)
    if m:
        t = m.group(1).strip()
        if t and t.lower() != "transcoded-videos":
            return t

    # 2. Subdomain pattern: https://{tenant}.classx.co.in
    m_sub = re.search(r'https?://([a-zA-Z0-9_\-]+)\.classx\.co\.in', url)
    if m_sub:
        sub = m_sub.group(1).strip()
        if sub and sub.lower() not in ("transcoded-videos", "api", "www", "app"):
            return sub

    return "transcoded-videos"


def get_classx_headers(
    url: str,
    custom_referer: Optional[str] = None,
    custom_origin: Optional[str] = None
) -> Dict[str, str]:
    """Generates the required HTTP headers for ClassX Google Edge Cache stream requests."""
    tenant = extract_classx_tenant(url)
    
    auto_ref = f"https://{tenant}.classx.co.in/"
    auto_orig = f"https://{tenant}.classx.co.in"

    ref = (custom_referer or "").strip() or auto_ref
    orig = (custom_origin or "").strip() or auto_orig

    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Referer': ref,
        'Origin': orig,
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'cross-site',
        'Sec-Fetch-Dest': 'empty'
    }


def get_classx_ytdlp_args(url: str) -> str:
    """Returns --add-header command arguments for yt-dlp."""
    headers = get_classx_headers(url)
    args = []
    for k, v in headers.items():
        args.append(f'--add-header "{k}: {v}"')
    return " " + " ".join(args)


def get_classx_n_m3u8dl_args(url: str) -> List[str]:
    """Returns -H arguments for N_m3u8DL-RE."""
    headers = get_classx_headers(url)
    args = []
    for k, v in headers.items():
        args.extend(["-H", f"{k}: {v}"])
    return args


def parse_m3u8_variant(master_url: str, m3u8_text: str, target_res: Optional[str] = None) -> Optional[str]:
    """
    Parses a multi-variant HLS master playlist (master.m3u8) to select the best stream
    matching the target resolution (or highest quality), preserving edge-cache-token parameters.
    """
    try:
        lines = [line.strip() for line in m3u8_text.splitlines() if line.strip()]
        variants = []
        current_inf = None
        for line in lines:
            if line.startswith("#EXT-X-STREAM-INF:"):
                current_inf = line
            elif current_inf and not line.startswith("#"):
                m_res = re.search(r'RESOLUTION=\d+x(\d+)', current_inf)
                m_bw = re.search(r'BANDWIDTH=(\d+)', current_inf)
                height = int(m_res.group(1)) if m_res else 0
                bandwidth = int(m_bw.group(1)) if m_bw else 0

                full_uri = urljoin(master_url, line)
                parsed_master = urlparse(master_url)
                parsed_variant = urlparse(full_uri)

                # Ensure query string from master_url (edge-cache-token, etc.) is preserved
                if parsed_master.query and not parsed_variant.query:
                    full_uri = f"{full_uri}?{parsed_master.query}"
                elif parsed_master.query and parsed_variant.query and parsed_master.query not in parsed_variant.query:
                    full_uri = f"{full_uri}&{parsed_master.query}"

                variants.append({
                    "height": height,
                    "bandwidth": bandwidth,
                    "url": full_uri
                })
                current_inf = None

        if not variants:
            return None

        # Filter by requested resolution
        if target_res and str(target_res).lower() != "best" and str(target_res).lower() != "un":
            try:
                digits = re.findall(r'\d+', str(target_res))
                if digits:
                    target_h = int(digits[0])
                    matching = [v for v in variants if v["height"] <= target_h and v["height"] > 0]
                    if matching:
                        matching.sort(key=lambda x: (x["height"], x["bandwidth"]), reverse=True)
                        return matching[0]["url"]
            except (ValueError, TypeError):
                pass

        # Default to highest available resolution
        variants.sort(key=lambda x: (x["height"], x["bandwidth"]), reverse=True)
        return variants[0]["url"]
    except Exception as e:
        logger.debug(f"Error parsing m3u8 variants: {e}")
        return None


def resolve_classx_url(
    url: str,
    token: Optional[str] = None,
    resolution: Optional[str] = "720"
) -> Dict[str, Any]:
    """
    Resolves a ClassX URL by appending Edge-Cache tokens, determining correct headers,
    and resolving the optimal resolution variant if it is a master M3U8 stream.
    """
    resolved_url = url.strip()

    # 1. Attach edge-cache-token if provided and not already in URL
    if token and isinstance(token, str) and token.strip() and token.strip().lower() not in ("no", "classx_token", "/d"):
        tok = token.strip()
        if "edge-cache-token=" not in resolved_url:
            if not tok.startswith("edge-cache-token="):
                tok = f"edge-cache-token={tok}"
            sep = "&" if "?" in resolved_url else "?"
            resolved_url = f"{resolved_url}{sep}{tok}"

    # 2. Extract tenant and generate headers
    tenant_name = extract_classx_tenant(resolved_url)
    headers = get_classx_headers(resolved_url)

    # 3. Check for multi-variant resolution if URL contains .m3u8
    if ".m3u8" in resolved_url:
        try:
            resp = requests.get(resolved_url, headers=headers, timeout=6)
            if resp.status_code in [403, 404]:
                # Fallback to transcoded-videos origin/referer
                fallback_headers = headers.copy()
                fallback_headers['Referer'] = "https://transcoded-videos.classx.co.in/"
                fallback_headers['Origin'] = "https://transcoded-videos.classx.co.in"
                fb_resp = requests.get(resolved_url, headers=fallback_headers, timeout=6)
                if fb_resp.status_code == 200:
                    headers = fallback_headers
                    resp = fb_resp

            if resp.status_code == 200 and "#EXT-X-STREAM-INF" in resp.text:
                variant_url = parse_m3u8_variant(resolved_url, resp.text, resolution or "720")
                if variant_url:
                    resolved_url = variant_url
                    logger.info(f"✅ Selected ClassX variant: {resolved_url[:80]}...")
        except Exception as e:
            logger.debug(f"ClassX multi-variant check skipped: {e}")

    return {
        "success": True,
        "url": resolved_url,
        "headers": headers,
        "tenant": tenant_name,
        "media_type": f"ClassX Stream ({tenant_name})"
    }
