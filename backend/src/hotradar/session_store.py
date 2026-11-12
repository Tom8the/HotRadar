"""Platform-scoped cookies encrypted by Windows DPAPI for the current local user."""

import ctypes
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[3] / "data" / "sessions"
PLATFORMS = {"weibo": ("weibo.com", "weibo.cn"), "douyin": ("douyin.com",)}


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Local platform credentials require Windows DPAPI.")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    dll = ctypes.WinDLL("crypt32", use_last_error=True)
    fn = dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    fn.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Blob),
    ]
    fn.restype = ctypes.c_int
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("Local credential encryption/decryption failed.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(target.data)


def cookie_path(platform):
    if platform not in PLATFORMS:
        raise ValueError("Unregistered content platform.")
    return ROOT / (platform + ".dpapi")


def allowed_domain(domain, platform):
    return isinstance(domain, str) and any(
        domain.lstrip(".") == base or domain.endswith("." + base) for base in PLATFORMS[platform]
    )


def save(platform, cookies):
    cookie_path(platform)
    if not isinstance(cookies, list) or not cookies:
        raise ValueError("No platform cookies were supplied.")
    for cookie in cookies:
        if (
            not isinstance(cookie, dict)
            or not allowed_domain(cookie.get("domain"), platform)
            or not isinstance(cookie.get("name"), str)
            or not isinstance(cookie.get("value"), str)
            or not str(cookie.get("path", "")).startswith("/")
        ):
            raise ValueError("Cookie scope is not the requested platform.")
    payload = json.dumps(
        {"platform": platform, "saved_at": datetime.now(UTC).isoformat(), "cookies": cookies},
        ensure_ascii=False,
    ).encode()
    encrypted = crypt(payload)
    ROOT.mkdir(parents=True, exist_ok=True)
    # Windows chmod does not provide a private ACL; restrict the directory before writing.
    sid = (
        subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
        .strip()
        .split(",")[-1]
        .strip('"')
    )
    subprocess.run(
        [
            "icacls",
            str(ROOT),
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    temp = cookie_path(platform).with_suffix(".tmp")
    temp.write_bytes(encrypted)
    os.replace(temp, cookie_path(platform))
    return {"platform": platform, "saved": True, "cookie_count": len(cookies), "encrypted": True}


def load(platform):
    file = cookie_path(platform)
    if not file.exists():
        return None
    data = json.loads(crypt(file.read_bytes(), decrypt=True))
    if data.get("platform") != platform:
        raise RuntimeError("Credential platform mismatch.")
    if any(not allowed_domain(cookie.get("domain"), platform) for cookie in data["cookies"]):
        raise RuntimeError("Credential domain mismatch.")
    return data


def client_cookies(platform):
    data = load(platform)
    if data is None:
        raise RuntimeError("Local platform session is missing.")
    jar = httpx.Cookies()
    now = datetime.now(UTC).timestamp()
    for cookie in data["cookies"]:
        expiry = cookie.get("expires", -1)
        if expiry > 0 and expiry <= now:
            continue
        jar.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
    # The collector is HTTPS-only; preserve an even stricter secure transport policy.
    for cookie in jar.jar:
        cookie.secure = True
    return jar


def platform_url(url, platform):
    parts = urlsplit(url)
    return parts.scheme == "https" and allowed_domain(parts.hostname, platform)
