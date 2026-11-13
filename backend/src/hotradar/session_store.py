"""Encrypted platform sessions for the API and collection worker."""

import ctypes
import json
import math
import os
import re
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

from hotradar.config import PROJECT_ROOT

ROOT = PROJECT_ROOT / "data" / "sessions"
PLATFORMS = {"weibo": ("weibo.com", "weibo.cn"), "douyin": ("douyin.com",)}
SCOPES = {
    "douyin-hot": "douyin",
    "douyin-creator-hot": "douyin",
    "douyin-wuxi": "douyin",
    "douyin-content": "douyin",
    "weibo-wuxi": "weibo",
    "weibo-content": "weibo",
}


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


def portable_path(scope):
    if scope not in SCOPES:
        raise ValueError("Unregistered session scope.")
    load_dotenv(PROJECT_ROOT / ".env")
    directory = PROJECT_ROOT / Path(os.environ.get("HOTRADAR_SESSION_DIR") or ROOT)
    return directory / (scope + ".fernet")


def session_cipher():
    load_dotenv(PROJECT_ROOT / ".env")
    try:
        return Fernet(os.environ["HOTRADAR_SESSION_KEY"])
    except (KeyError, ValueError):
        raise RuntimeError("Platform session encryption key is unavailable.") from None


def allowed_domain(domain, platform):
    return (
        isinstance(domain, str)
        and re.fullmatch(r"\.?[a-z0-9-]+(?:\.[a-z0-9-]+)*", domain) is not None
        and any(
            domain.lstrip(".") == base or domain.endswith("." + base)
            for base in PLATFORMS[platform]
        )
    )


def validate_cookies(platform, cookies):
    if not isinstance(cookies, list) or not 1 <= len(cookies) <= 512:
        raise ValueError("Invalid platform cookie list.")
    cleaned = []
    for cookie in cookies:
        if (
            not isinstance(cookie, dict)
            or not allowed_domain(cookie.get("domain"), platform)
            or not isinstance(cookie.get("name"), str)
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", cookie["name"])
            or not isinstance(cookie.get("value"), str)
            or not re.fullmatch(r"[\x20-\x3a\x3c-\x7e]*", cookie["value"])
            or not isinstance(cookie.get("path"), str)
            or not re.fullmatch(r"/[\x21-\x3a\x3c-\x7e]*", cookie["path"])
        ):
            raise ValueError("Cookie scope is not the requested platform.")
        expiry = cookie.get("expires", -1)
        if type(expiry) not in (int, float) or abs(expiry) > 253402300799:
            raise ValueError("Invalid cookie expiry.")
        if not math.isfinite(expiry):
            raise ValueError("Invalid cookie expiry.")
        if "partitionKey" in cookie:
            continue
        cleaned.append(
            {key: cookie[key] for key in ("name", "value", "domain", "path")} | {"expires": expiry}
        )
    if not cleaned:
        raise ValueError("No unpartitioned platform cookies were supplied.")
    return cleaned


def atomic_write(path, encrypted):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            handle.write(encrypted)
        os.replace(temp, path)
    finally:
        if temp is not None:
            with suppress(FileNotFoundError):
                temp.unlink()


def save_portable(scope, cookies):
    path = portable_path(scope)
    payload = json.dumps(
        {
            "scope": scope,
            "saved_at": datetime.now(UTC).isoformat(),
            "cookies": validate_cookies(SCOPES[scope], cookies),
        }
    ).encode()
    atomic_write(path, session_cipher().encrypt(payload))


def private_directory(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        return
    sid = (
        subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
        .strip()
        .split(",")[-1]
        .strip('"')
    )
    subprocess.run(
        [
            "icacls",
            str(directory),
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def save(platform, cookies):
    path = cookie_path(platform)
    validate_cookies(platform, cookies)
    payload = json.dumps(
        {"platform": platform, "saved_at": datetime.now(UTC).isoformat(), "cookies": cookies},
        ensure_ascii=False,
    ).encode()
    encrypted = crypt(payload)
    private_directory(path.parent)
    atomic_write(path, encrypted)
    return {"platform": platform, "saved": True, "cookie_count": len(cookies), "encrypted": True}


def load(platform):
    file = cookie_path(platform)
    if os.name != "nt" or not file.exists():
        return None
    data = json.loads(crypt(file.read_bytes(), decrypt=True))
    if data.get("platform") != platform:
        raise RuntimeError("Credential platform mismatch.")
    if any(not allowed_domain(cookie.get("domain"), platform) for cookie in data["cookies"]):
        raise RuntimeError("Credential domain mismatch.")
    return data


def read_session(scope, *, allow_legacy=True):
    path = portable_path(scope)
    if path.exists():
        try:
            data = json.loads(session_cipher().decrypt(path.read_bytes()))
        except InvalidToken:
            raise RuntimeError("Platform session could not be decrypted.") from None
        if not isinstance(data, dict) or data.get("scope") != scope:
            raise RuntimeError("Platform session scope mismatch.")
        data["cookies"] = validate_cookies(SCOPES[scope], data.get("cookies"))
        return data
    return load(SCOPES[scope]) if allow_legacy else None


def session_status(scope):
    try:
        data = read_session(scope, allow_legacy=scope != "douyin-hot")
        if data is None:
            return {"state": "missing", "saved_at": None}
        saved_at = data.get("saved_at")
        return {
            "state": "saved" if cookie_jar(data["cookies"]).jar else "expired",
            "saved_at": saved_at,
        }
    except (RuntimeError, ValueError, OSError):
        return {"state": "unavailable", "saved_at": None}


def cookie_jar(cookies):
    jar = httpx.Cookies()
    now = datetime.now(UTC).timestamp()
    for cookie in cookies:
        expiry = cookie.get("expires", -1)
        if 0 <= expiry <= now:
            continue
        jar.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
    for cookie in jar.jar:
        cookie.secure = True
    return jar


def client_cookies(platform, *, scope=None):
    if scope is not None and SCOPES.get(scope) != platform:
        raise ValueError("Platform session scope mismatch.")
    data = read_session(scope) if scope else load(platform)
    if data is None:
        raise RuntimeError("Platform session is missing.")
    jar = cookie_jar(data["cookies"])
    if not jar.jar:
        raise RuntimeError("Platform session has expired.")
    return jar


def platform_url(url, platform):
    parts = urlsplit(url)
    return parts.scheme == "https" and allowed_domain(parts.hostname, platform)
