#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
QQ音乐签到 code 版 (缓存优先 + YYB-GO 自动刷新)

环境变量：
  YYB_GO_URL        必填，YYB Go 地址（如 http://115.190.216.15:8000）
  YYB_GO_ID         必填，YYB Go 账号引用（ID/UIN/openid），多账号用 & 或换行分隔（可加 #备注）
  PUSHPLUS_TOKEN    PushPlus token，可选
  PROXY_API         品赞代理提取 API，可选
  PROXY_TYPE        http / socks5，默认 http
  QQ_ENABLE_ACTIVITY  抽奖/红包雨/活动任务开关，默认 1
  QQ_ENABLE_FAVORITE  临时收藏/关注任务开关，默认 1
  QQ_DEBUG          调试日志开关，默认 0
"""

import json
import os
import random
import re
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote
from pathlib import Path

import requests
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ==================== 常量与配置 ====================
APP_NAME = "QQ音乐签到小程序"
APPID = "wxada7aab80ba27074"
CACHE_FILE = Path(__file__).parent / "yyb_go_token_cache.json"

YYB_GO_URL = os.getenv("YYB_GO_URL", "").strip().rstrip("/")
YYB_GO_ID_RAW = os.getenv("YYB_GO_ID", "")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")
PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()
ENABLE_ACTIVITY = os.getenv("QQ_ENABLE_ACTIVITY", "1") not in ("0", "false", "False")
ENABLE_FAVORITE = os.getenv("QQ_ENABLE_FAVORITE", "1") not in ("0", "false", "False")
IS_DEBUG = os.getenv("QQ_DEBUG", "0") in ("1", "true", "True")

PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

MUSIC_API_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
APP_API_URL = "https://u6.y.qq.com/cgi-bin/musics.fcg"

COIN_SIGN_ACT_ID = "Z25hHGi"
COIN_SIGN_SCENE_ID = "2"
DAILY_TASK_ACT_ID = "Z1NRf2o"
LOTTERY_SIGN_ACT_ID = "Z156KEu"
COIN_LOTTERY_PLAY_ID = "PR-Lottery-20240408-33489273491"
RED_PACKET_RAIN_KEY = "1joIuy"
TIMER_TASK_MODULE_ID = "ZGp4ja"
AUDIOBOOK_CATEGORY_ID = "42800344"
AUDIOBOOK_CANDIDATES = [93654004]
PLAYLIST_CANDIDATES = [9611383852]
SINGER_CANDIDATES = ["0039zms40xSD5K"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat"
)

CURRENT_ACCOUNTS: List["AccountTarget"] = []


# ==================== 数据模型 ====================
@dataclass
class AccountTarget:
    server: str
    ref: str = ""
    remark: str = ""
    nickname: str = ""
    index: int = 0

    @property
    def label(self) -> str:
        display = self.remark or self.nickname
        if display and self.ref:
            base = self.ref if self.ref.isdigit() else str(self.index or "?")
            return f"{display}（账号 {base}）"
        if self.ref:
            return f"账号 {self.ref if self.ref.isdigit() else (self.index or '?')}"
        return display or self.server


# ==================== 缓存管理 (核心新增) ====================
def read_cache() -> Dict[str, Any]:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def write_cache(data: Dict[str, Any]) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"⚠️ [缓存] 写入失败: {e}")

def get_cached_auth(ref: str) -> Dict[str, str] | None:
    """读取指定账号的缓存凭证"""
    cache = read_cache()
    entry = cache.get(ref)
    if entry and entry.get("authst") and entry.get("uin"):
        return {"authst": entry["authst"], "uin": entry["uin"]}
    return None

def save_cached_auth(ref: str, auth: Dict[str, str]) -> None:
    """保存/更新指定账号的凭证到缓存"""
    cache = read_cache()
    cache[ref] = {
        "authst": auth["authst"],
        "uin": auth["uin"],
        "updated_at": now_text()
    }
    write_cache(cache)


# ==================== 工具函数 ====================
def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def sleep(seconds: float) -> None:
    time.sleep(seconds)

def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"

def json_preview(data: Any, limit: int = 800) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)[:limit]
    except Exception:
        return str(data)[:limit]

def to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


# ==================== 账号加载 ====================
def load_account_targets() -> List[AccountTarget]:
    if not YYB_GO_URL:
        raise RuntimeError("❌ 未配置环境变量 YYB_GO_URL")
    raw_ids = YYB_GO_ID_RAW.strip()
    if not raw_ids:
        raise RuntimeError("❌ 未配置环境变量 YYB_GO_ID")

    entries = re.split(r'[&\n]+', raw_ids)
    accounts: List[AccountTarget] = []
    for idx, entry in enumerate(entries, 1):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("#", 1)
        ref = parts[0].strip()
        remark = parts[1].strip() if len(parts) > 1 else ""
        if not ref:
            continue
        accounts.append(AccountTarget(server=YYB_GO_URL, ref=ref, remark=remark, index=idx))

    if not accounts:
        raise RuntimeError("YYB_GO_ID 未解析到有效账号")
    load_account_labels(accounts)
    return accounts


def load_account_labels(accounts: List[AccountTarget]) -> None:
    grouped: Dict[str, List[AccountTarget]] = {}
    for account in accounts:
        grouped.setdefault(account.server, []).append(account)
    for server, rows in grouped.items():
        try:
            response = direct_session().get(f"{server}/accounts", timeout=10)
            payload = response.json()
            items = payload.get("data") if isinstance(payload, dict) else payload
            if isinstance(items, dict):
                items = items.get("accounts") or items.get("items") or []
            if not response.ok or not isinstance(items, list):
                continue
        except (requests.RequestException, ValueError):
            continue
        for account in rows:
            for item in items:
                if not isinstance(item, dict):
                    continue
                identifiers = {str(item.get("id") or ""), str(item.get("openid") or ""), str(item.get("uin") or "")}
                if account.ref not in identifiers:
                    continue
                if not account.remark:
                    account.remark = str(item.get("remark") or item.get("alias") or "").strip()
                if not account.nickname:
                    account.nickname = str(item.get("nickname") or "").strip()
                break


def apply_account_label(account: AccountTarget, item: Any) -> None:
    if not isinstance(item, dict):
        return
    if not account.remark:
        account.remark = str(item.get("remark") or item.get("alias") or "").strip()
    if not account.nickname:
        account.nickname = str(item.get("nickname") or "").strip()


# ==================== YYB-GO Code 获取 ====================
def get_code(account: AccountTarget) -> str | None:
    server = account.server
    ref = account.ref
    url = f"{server}/wxapp/getCode"
    print(f"🔐 [授权] 请求 YYB-GO: {url} (ref={mask(ref)})")

    for attempt in range(1, 4):
        try:
            response = direct_session().post(
                url, json={"app_id": APPID, "ref": ref},
                headers={"Content-Type": "application/json"}, timeout=20,
            )
            data = response.json()
            nested = data.get("data") if isinstance(data, dict) else None
            if isinstance(nested, dict):
                apply_account_label(account, nested.get("account"))

            top_code = data.get("code") if isinstance(data, dict) else None
            if top_code is not None and str(top_code) not in ("0", "None", ""):
                msg = data.get("msg") or data.get("message") or ""
                if str(top_code) == "502":
                    print(f"❌ [授权] 账号凭证已失效(502)，请在 YYB-GO 中重新授权: {msg}")
                    return None
                print(f"❌ [授权] 第 {attempt} 次 YYB-GO 返回错误 (code={top_code}): {msg}")
                if attempt < 3: sleep(3)
                continue

            code = ""
            if isinstance(nested, dict):
                result = nested.get("result")
                if isinstance(result, dict):
                    code = str(result.get("code") or "").strip()
                elif isinstance(result, str):
                    code = result.strip()
                if not code:
                    code = str(nested.get("code") or "").strip()
            if not code and isinstance(data, dict):
                code = str(data.get("code") or "").strip()

            if bool(code) and code.lower() != "null":
                print(f"✅ [授权] code 获取成功 (长度={len(code)})")
                return code

            print(f"❌ [授权] 第 {attempt} 次未获取到有效 code: {json_preview(data)}")
            if attempt < 3: sleep(3)
        except requests.Timeout:
            print(f"⏰ [授权] 第 {attempt} 次请求超时(20s)")
        except requests.ConnectionError as exc:
            print(f"🔌 [授权] 第 {attempt} 次连接失败: {exc}")
        except Exception as exc:
            print(f"❌ [授权] 第 {attempt} 次异常: {exc}")
        if attempt < 3: sleep(3)

    print("❌ [授权] 3 次尝试均失败")
    return None


# ==================== QQ音乐登录与验证 ====================
def common_headers(auth: Dict[str, Any] | None = None) -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT, "Content-Type": "application/json",
        "Accept": "*/*", "xweb_xhr": "1",
        "Referer": f"https://servicewechat.com/{APPID}/175/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if auth:
        headers["Cookie"] = f"uin=o{auth['uin']}; qm_keyst={auth['authst']}"
    return headers


def extract_musickey(data: Any) -> Tuple[str | None, str | None]:
    if not isinstance(data, dict): return None, None
    login = data.get("login")
    if not isinstance(login, dict): return None, None
    inner = login.get("data")
    if not isinstance(inner, dict): return None, None
    musickey = inner.get("musickey")
    musicid = inner.get("musicid") or inner.get("str_musicid")
    if musickey and musicid: return str(musickey), str(musicid)
    return None, None


def login_by_code(server: str, code: str, proxies: Dict[str, str] | None) -> Tuple[Dict[str, Any] | None, Dict[str, Any] | None]:
    try:
        print("🔐 [登录] 使用 code 换 token")
        payload = {
            "comm": {"uin": "0", "authst": "", "mina": 1, "appid": APPID, "ct": 25, "tmeAppID": "qqmusic", "tmeLoginType": "1"},
            "login": {"module": "music.login.LoginServer", "method": "Login", "param": {"code": code, "strAppid": APPID}},
        }
        response = request_with_proxy("POST", MUSIC_API_URL, headers=common_headers(), json=payload, proxies=proxies, server=server)
        try: data = response.json()
        except Exception: data = {"raw": response.text[:800]}
        musickey, musicid = extract_musickey(data)
        if musickey:
            print(f"✅ [登录] token 获取成功: {mask(musickey)}")
            return {"uin": musicid, "authst": musickey}, data
        print(f"❌ [登录] 未识别 token 字段: {json_preview(data)}")
        return None, data
    except Exception as exc:
        print(f"❌ [登录] 请求异常: {exc}")
        return None, None


def verify_auth_valid(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> bool:
    """
    通过轻量级业务接口验证 CK 是否仍然有效
    使用绿钻签到查询接口，不会触发实际签到
    """
    try:
        payload = {
            "comm": {"uin": int(auth["uin"]), "authst": auth["authst"], "mina": 1, "appid": APPID, "ct": 29, "cv": 0, "format": "json"},
            "req_0": {"module": "music.lvz.MuFest13TaskSvr", "method": "EveryDaySignLvzScore", "param": {"Uin": auth["uin"], "Cmd": "get"}},
        }
        response = request_with_proxy("POST", MUSIC_API_URL, headers=common_headers(auth), json=payload, proxies=proxies, server=server)
        data = response.json()
        # 如果返回了有效的业务数据（即使code非0但不是鉴权错误），说明CK有效
        r0 = data.get("req_0") or {}
        r0_code = r0.get("code")
        # 明确的鉴权失败码
        if r0_code in (-1, 1000, 1001, 10001, 10003, 40001):
            return False
        # 顶层鉴权失败
        top_code = data.get("code")
        if top_code in (1000, 1001, 10001, 10003, 40001):
            return False
        # 能拿到 req_0 且不是鉴权错误，视为有效
        if r0_code is not None:
            return True
        return False
    except Exception as exc:
        print(f"⚠️ [验证] CK 有效性检查异常: {exc}")
        return False


def ensure_auth(account: AccountTarget, proxies: Dict[str, str] | None) -> Dict[str, Any] | None:
    """
    核心登录入口：缓存优先 -> 验证 -> 失效刷新
    返回有效的 auth dict 或 None
    """
    ref = account.ref

    # Step 1: 尝试读取缓存
    cached_auth = get_cached_auth(ref)
    if cached_auth:
        print(f"📦 [缓存] 发现缓存 CK: {mask(cached_auth['authst'])}")
        if verify_auth_valid(account.server, cached_auth, proxies):
            print("✅ [缓存] CK 验证有效，直接使用")
            return cached_auth
        print("⚠️ [缓存] CK 已失效，准备刷新...")
    else:
        print("📦 [缓存] 无缓存记录，需要全新登录")

    # Step 2: 通过 YYB-GO 获取新 CK
    code = get_code(account)
    if not code:
        print("❌ [登录] 无法获取 code，跳过该账号")
        return None

    new_auth, _ = login_by_code(account.server, code, proxies)
    if not new_auth:
        print("❌ [登录] code 换取 token 失败")
        return None

    # Step 3: 写入缓存
    save_cached_auth(ref, new_auth)
    print(f"💾 [缓存] 新 CK 已保存到缓存文件")
    return new_auth


# ==================== 代理相关 ====================
def parse_proxy_response(text: Any) -> Dict[str, Any] | None:
    if not isinstance(text, str): text = json.dumps(text, ensure_ascii=False)
    text = text.strip()
    if not text: return None
    try:
        data = json.loads(text)
        proxy_obj = None
        if isinstance(data.get("data"), list) and data["data"]: proxy_obj = data["data"][0]
        elif isinstance(data.get("data"), dict): proxy_obj = data["data"]
        elif data.get("ip") and data.get("port"): proxy_obj = data
        elif isinstance(data.get("result"), dict): proxy_obj = data["result"]
        if proxy_obj:
            host = proxy_obj.get("ip") or proxy_obj.get("host")
            port = proxy_obj.get("port")
            if host and port:
                return {"host": str(host), "port": int(port),
                        "username": proxy_obj.get("user") or proxy_obj.get("username") or "",
                        "password": proxy_obj.get("pass") or proxy_obj.get("password") or ""}
    except Exception: pass
    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            return {"host": parts[0], "port": int(parts[1]),
                    "username": parts[2] if len(parts) > 2 else "",
                    "password": parts[3] if len(parts) > 3 else ""}
    return None

def build_proxy_dict(proxy_info: Dict[str, Any] | None) -> Dict[str, str] | None:
    if not proxy_info: return None
    host, port = proxy_info["host"], proxy_info["port"]
    username, password = proxy_info.get("username", ""), proxy_info.get("password", "")
    auth = f"{quote(username)}:{quote(password)}@" if username and password else ""
    scheme = "socks5" if PROXY_TYPE == "socks5" else "http"
    proxy_url = f"{scheme}://{auth}{host}:{port}"
    print(f"🛠️ [代理] 生成 {scheme.upper()} 代理 {host}:{port}")
    return {"http": proxy_url, "https": proxy_url}

def validate_proxy(proxies: Dict[str, str] | None) -> Tuple[bool, str]:
    if not proxies: return False, ""
    try:
        response = requests.get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15)
        if response.status_code == 200:
            try: ip = response.json().get("origin", "未知")
            except Exception: ip = "未知"
            print(f"✅ [代理] 验证通过，出口 IP: {ip}")
            return True, ip
    except Exception as exc: print(f"⚠️ [代理] 验证失败: {exc}")
    return False, ""

def get_valid_proxy(account_name: str) -> Tuple[Dict[str, str] | None, str]:
    if not PROXY_API:
        print(f"⚠️ [代理] {account_name} 未配置 PROXY_API，使用直连")
        return None, ""
    print(f"🌐 [代理] {account_name} 正在获取品赞代理...")
    for index in range(1, PROXY_RETRY_TIMES + 1):
        try:
            response = direct_session().get(PROXY_API, timeout=15)
            proxy_info = parse_proxy_response(response.text)
            if not proxy_info:
                print(f"⚠️ [代理] 第 {index} 次代理解析失败"); continue
            print(f"✅ [代理] 提取到 {proxy_info['host']}:{proxy_info['port']}")
            proxies = build_proxy_dict(proxy_info)
            ok, ip = validate_proxy(proxies)
            if ok: return proxies, ip
            print(f"⚠️ [代理] 第 {index} 次代理不可用")
        except Exception as exc: print(f"⚠️ [代理] 第 {index} 次获取代理异常: {exc}")
        if index < PROXY_RETRY_TIMES: sleep(2)
    print("⚠️ [代理] 获取失败，使用直连")
    return None, ""

def request_with_proxy(method: str, url: str, *, proxies: Dict[str, str] | None = None, server: str = "", **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    if proxies:
        try: return requests.request(method, url, proxies=proxies, **kwargs)
        except Exception as exc:
            print(f"⚠️ [代理] {server} 代理请求失败: {exc}")
            if not ENABLE_DIRECT_FALLBACK: raise
            print("🔁 [兜底] 切换直连重试")
    return direct_session().request(method, url, **kwargs)


# ==================== PushPlus ====================
def send_pushplus(title: str, content: str) -> None:
    if not PUSHPLUS_TOKEN:
        print("⚠️ [PushPlus] 未配置 PUSHPLUS_TOKEN，跳过推送"); return
    try:
        requests.post("https://www.pushplus.plus/send",
                      json={"token": PUSHPLUS_TOKEN, "title": title, "content": content, "template": "txt"}, timeout=10)
        print("✅ [PushPlus] 推送成功")
    except Exception as exc: print(f"❌ [PushPlus] 推送失败: {exc}")


# ==================== QQ音乐 请求层 ====================
def hash33(text: str) -> int:
    h = 5381.0
    for ch in text:
        i32 = int(h) & 0xFFFFFFFF
        shifted = i32 << 5
        if shifted >= 0x80000000: shifted -= 0x100000000
        h = h + shifted + ord(ch)
    return int(h) & 0x7FFFFFFF

def sha1_hex_utf8(text: str) -> str:
    import hashlib; return hashlib.sha1(text.encode("utf-8")).hexdigest()

def zzc_sign(payload: str) -> str:
    import base64
    h = sha1_hex_utf8(payload).upper()
    part1_indexes = [23, 14, 6, 36, 16, 7, 19]
    part2_indexes = [16, 1, 32, 12, 19, 27, 8, 5]
    scramble = [89, 39, 179, 150, 218, 82, 58, 252, 177, 52, 186, 123, 120, 64, 242, 133, 143, 161, 121, 179]
    part1 = "".join(h[i] for i in part1_indexes)
    part2 = "".join(h[i] for i in part2_indexes)
    raw = bytes(scramble[i] ^ int(h[i * 2:i * 2 + 2], 16) for i in range(20))
    middle = base64.b64encode(raw).decode().replace("\\", "").replace("/", "").replace("+", "").replace("=", "")
    return ("zzc" + part1 + middle + part2).lower()

def make_comm(auth: Dict[str, Any]) -> Dict[str, Any]:
    return {"uin": int(auth["uin"]), "authst": auth["authst"], "mina": 1, "appid": APPID, "ct": 29, "cv": 0, "format": "json"}

def make_app_comm(auth: Dict[str, Any], ct: int, cv: int, mesh: str) -> Dict[str, Any]:
    return {"g_tk": hash33(auth["authst"]), "uin": int(auth["uin"]), "format": "json",
            "inCharset": "utf-8", "outCharset": "utf-8", "notice": 0, "platform": "h5",
            "needNewCode": 1, "ct": ct, "cv": cv, "mesh_devops": mesh}

def redact_sensitive(text: str) -> str:
    text = re.sub(r'("(?:authst|musickey|refresh_key|session_key|uin|musicid|str_musicid|cookie|openid|unionid|encryptUin|userip|phoneNo|encryptedPhoneNo)"\s*:\s*")[^"]*', r'\1<redacted>', text, flags=re.I)
    text = re.sub(r'("(?:uin|musicid)"\s*:\s*)\d+', r'\1<redacted>', text, flags=re.I)
    text = re.sub(r'(qm_keyst=)[^;\s]+', r'\1<redacted>', text, flags=re.I)
    text = re.sub(r'(refresh_key=)[^;\s]+', r'\1<redacted>', text, flags=re.I)
    text = re.sub(r'(sign=)[^&\s]+', r'\1<redacted>', text, flags=re.I)
    return text

def debug_log(content: Any, title: str = "debug") -> None:
    if not IS_DEBUG: return
    print(f"\n----- {title} -----")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    print(redact_sensitive(text)); print("----- end -----\n")

def post_musicu(server: str, auth: Dict[str, Any], payload: Dict[str, Any], proxies: Dict[str, str] | None) -> Dict[str, Any] | None:
    try:
        response = request_with_proxy("POST", MUSIC_API_URL, headers=common_headers(auth), json=payload, proxies=proxies, server=server)
        return response.json()
    except Exception as exc: print(f"❌ [请求] musicu.fcg 异常: {exc}"); return None

def app_post(server: str, auth: Dict[str, Any], cgi_key: str, payload: Dict[str, Any], proxies: Dict[str, str] | None) -> Dict[str, Any] | None:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    sign = zzc_sign(body)
    url = f"{APP_API_URL}?_webcgikey={quote(cgi_key)}&_={int(time.time() * 1000)}&sign={sign}"
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded",
               "Accept": "*/*", "xweb_xhr": "1",
               "Referer": f"https://servicewechat.com/{APPID}/175/page-frame.html",
               "Cookie": f"uin=o{auth['uin']}; qm_keyst={auth['authst']}"}
    try:
        response = request_with_proxy("POST", url, headers=headers, data=body, proxies=proxies, server=server)
        return response.json()
    except Exception as exc: print(f"❌ [请求] musics.fcg({cgi_key}) 异常: {exc}"); return None


# ==================== QQ音乐 通用工具 ====================
def app_request_succeeded(res: Dict[str, Any] | None, req_key: str = "req_0") -> bool:
    if not res or res.get("code") != 0: return False
    req = res.get(req_key)
    if not req or req.get("code") != 0: return False
    data = req.get("data")
    if not isinstance(data, dict): return True
    for key in ("retCode", "RetCode", "ret", "Ret", "code"):
        if key in data and to_float(data[key]) != 0: return False
    return True

def music_request_succeeded(res: Dict[str, Any] | None, req_key: str = "req_0") -> bool:
    if not res or res.get("code") != 0: return False
    req = res.get(req_key)
    data = req.get("data") if req else None
    return bool(req and req.get("code") == 0 and data and to_float(data.get("retCode", 0)) == 0)

def red_packet_request_succeeded(res: Dict[str, Any] | None) -> bool:
    if not app_request_succeeded(res): return False
    data = (res.get("req_0") or {}).get("data")
    if not data or "Code" not in data: return True
    return to_float(data["Code"]) in (0, 10000)

def read_red_packet_rest_chance(data: Any, now: int | None = None) -> int:
    if now is None: now = int(time.time())
    if not isinstance(data, dict): return 0
    config = data.get("BaseConfig")
    if isinstance(config, str):
        try: config = json.loads(config)
        except Exception: return 0
    if not isinstance(config, dict): return 0
    session = config.get("session")
    segments = session.get("timeSegment") if isinstance(session, dict) else None
    if not isinstance(segments, list): return 0
    active = None
    for item in segments:
        if isinstance(item, dict) and to_float(item.get("status")) == 2: active = item; break
    if active is None:
        for item in segments:
            if not isinstance(item, dict): continue
            rng = item.get("timeRangeTs")
            if isinstance(rng, list) and len(rng) >= 2 and to_float(rng[0]) <= now <= to_float(rng[1]): active = item; break
    if not active: return 0
    return max(0, int(to_float(active.get("restChance"))))

def read_song_add_status(res: Dict[str, Any] | None, song_id: int) -> bool | None:
    if not music_request_succeeded(res): return None
    data = (res.get("req_0") or {}).get("data") or {}
    result = data.get("result")
    entries = result.get("songlist") if isinstance(result, dict) else None
    if not isinstance(entries, list): return None
    entry = None
    for item in entries:
        if isinstance(item, dict) and int(to_float(item.get("songId") or item.get("backendSongId"))) == int(song_id): entry = item; break
    if not entry or "existed" not in entry: return None
    existed = to_float(entry["existed"])
    if existed == 0: return True
    if existed == 1: return False
    return None

def collect_values_by_keys(root: Any, keys: List[str], predicate: Any = None, limit: int = 50) -> List[Any]:
    wanted = set(str(k) for k in keys); result: List[Any] = []
    def walk(value: Any) -> None:
        if len(result) >= limit or value is None: return
        if isinstance(value, list):
            for item in value: walk(item)
            return
        if not isinstance(value, dict): return
        for key, item in value.items():
            if key in wanted and (predicate is None or predicate(item)): result.append(item)
            walk(item)
            if len(result) >= limit: return
    walk(root); return unique_values(result)

def collect_singer_mids(root: Any, limit: int = 20) -> List[str]:
    body = root.get("body") if isinstance(root, dict) else root
    if not isinstance(body, dict): return []
    singers: List[str] = []
    def add_singer(singer: Any) -> None:
        if not isinstance(singer, dict): return
        mid = singer.get("mid") or singer.get("singerMID") or singer.get("singer_mid")
        if mid and re.match(r"^[A-Za-z0-9]{10,20}$", str(mid)): singers.append(str(mid))
    def add_song(song: Any) -> None:
        if not isinstance(song, dict) or not isinstance(song.get("singer"), list): return
        for singer in song["singer"]: add_singer(singer)
    for song in body.get("item_song") or []: add_song(song)
    for singer in body.get("singer") or []: add_singer(singer)
    for singer in body.get("item_singer") or []: add_singer(singer)
    return unique_values(singers)[:limit]

def unique_values(values: Any) -> List[Any]:
    seen = set(); result = []
    for value in values or []:
        key = str(value)
        if not key or key in seen: continue
        seen.add(key); result.append(value)
    return result

def find_first_value(root: Any, keys: List[str]) -> Any:
    wanted = set(str(k) for k in keys); found = [None]
    def walk(value: Any) -> None:
        if found[0] is not None or value is None: return
        if isinstance(value, list):
            for item in value: walk(item)
            return
        if not isinstance(value, dict): return
        for key, item in value.items():
            if key in wanted: found[0] = item; return
            walk(item)
            if found[0] is not None: return
    walk(root); return found[0]

def normalize_status(value: Any) -> bool | None:
    if value is True or value == 1 or value == "1" or value == "true": return True
    if value is False or value == 0 or value == "0" or value == "false": return False
    return None

def read_single_mapped_status(root: Any, map_keys: List[str]) -> bool | None:
    if not isinstance(root, dict): return None
    statuses = []
    for key in map_keys:
        mapping = root.get(key)
        if not isinstance(mapping, dict): continue
        for value in mapping.values():
            status = normalize_status(value)
            if status is not None: statuses.append(status)
    return statuses[0] if len(statuses) == 1 else None

def read_target_status(root: Any, target: str) -> bool | None:
    found = [None]
    def walk(value: Any) -> None:
        if found[0] is not None or value is None: return
        if isinstance(value, list):
            for item in value: walk(item)
            return
        if not isinstance(value, dict): return
        if target in value:
            direct = normalize_status(value[target])
            if direct is not None: found[0] = direct; return
        object_id = value.get("id") or value.get("userid") or value.get("userId") or value.get("mid") or value.get("singerMID")
        if str(object_id or "") == target:
            for key in ("status", "follow", "followed", "isFollow", "fav", "isFav", "operation", "oper"):
                if key not in value: continue
                status = normalize_status(value[key])
                if status is not None: found[0] = status; return
        for item in value.values():
            walk(item)
            if found[0] is not None: return
    walk(root); return found[0]

def format_lottery_gift(data: Any) -> str:
    gift = find_first_value(data, ["lotteryGift"])
    if isinstance(gift, str):
        try: gift = json.loads(gift)
        except Exception:
            if gift.strip(): return gift.strip()
    source = gift if isinstance(gift, dict) else data
    name = find_first_value(source, ["giftName", "prizeName", "PrizeName", "name"])
    value = to_float(find_first_value(source, ["awardValue", "RewardGold", "rewardGold", "coinNum", "coin"]))
    if name and value: return f"{name} +{int(value)}"
    if name: return str(name)
    if value: return f"金币 +{int(value)}"
    return "已领取"

def format_coin_reward(task: Dict[str, Any] | None) -> str:
    prize_list = task.get("PrizeList") if task else None
    prize = prize_list[0] if isinstance(prize_list, list) and prize_list else None
    if not prize: return ""
    if prize.get("Name"): return f" ({prize['Name']})"
    if prize.get("Value"): return f" (+{prize['Value']} 金币)"
    return ""


# ==================== QQ音乐 签到流程 ====================
def check_lvz_score(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> str:
    payload = {"comm": make_comm(auth), "req_0": {"module": "music.lvz.MuFest13TaskSvr", "method": "EveryDaySignLvzScore", "param": {"Uin": auth["uin"], "Cmd": "get"}}}
    res = post_musicu(server, auth, payload, proxies); debug_log(res, "EveryDaySignLvzScore")
    if not res: return "❌ 绿钻成长值签到无响应"
    r0 = res.get("req_0") or {}; data = r0.get("data") or {}
    if res.get("code") != 0 or (r0.get("code") not in (0, None) and "Ret" not in data):
        return f"❌ 绿钻成长值签到失败 (code={res.get('code')}, req_0.code={r0.get('code')})"
    ret = data.get("Ret"); msg = data.get("Msg") or ""
    if ret == 0:
        info = data.get("Info") or {}; score = info.get("Score") or 0
        return f"✅ 绿钻成长值签到成功{': 今日 +' + str(score) if score else ''}"
    if ret == 20019 or re.search(r"已.*领取|已签|重复", msg):
        return f"✨ 绿钻成长值今日已签到{('(' + msg + ')') if msg else ''}"
    return f"⚠️ 绿钻成长值已处理 (Ret={ret}){': ' + msg if msg else ''}"

def get_coin_sign_state(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, act_id: str, scene_id: str) -> Dict[str, Any] | None:
    payload = {"comm": make_comm(auth),
               "req_0": {"module": "music.actCenter.ActCenterSignNewSvr", "method": "GetSignInSummary", "param": {"ActID": act_id}},
               "req_1": {"module": "music.actCenter.ActCenterSignNewSvr", "method": "GetSignInTaskList", "param": {"ActID": act_id, "ScenesID": scene_id}}}
    res = post_musicu(server, auth, payload, proxies); debug_log(res, "Coin Sign State")
    summary = res.get("req_0") if res else None; tasks = res.get("req_1") if res else None
    summary_data = summary.get("data") if summary else None; task_data = tasks.get("data") if tasks else None
    if (not res or res.get("code") != 0 or not summary or summary.get("code") != 0
            or not tasks or tasks.get("code") != 0 or not summary_data or summary_data.get("retCode") != 0
            or not task_data or task_data.get("retCode") != 0):
        print(f"❌ [金币] 状态查询失败"); return None
    task_list_info = task_data.get("TaskListInfo") or {}
    task_list = ((task_list_info.get("TaskList") or {}).get("ContinueTaskList")) or {}
    return {"info": task_data.get("Info") or summary_data.get("Info") or {}, "taskList": task_list}

def check_coin_sign_in(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> str:
    act_id, scene_id = COIN_SIGN_ACT_ID, COIN_SIGN_SCENE_ID
    state = get_coin_sign_state(server, auth, proxies, act_id, scene_id)
    if not state: return "❌ 金币中心签到失败(状态查询失败)"
    signed_now = False
    if not state["info"].get("IsSignIn"):
        payload = {"comm": make_comm(auth), "req_0": {"module": "music.actCenter.ActCenterSignNewSvr", "method": "SignIn", "param": {"ActID": act_id, "ScenesID": scene_id}}}
        res = post_musicu(server, auth, payload, proxies); debug_log(res, "Coin SignIn")
        sign_req = res.get("req_0") if res else None; sign_data = sign_req.get("data") if sign_req else None
        if (not res or res.get("code") != 0 or not sign_req or sign_req.get("code") != 0
                or not sign_data or sign_data.get("retCode") != 0 or not (sign_data.get("Info") or {}).get("IsSignIn")):
            return f"❌ 金币中心签到失败"
        signed_now = True
        state = get_coin_sign_state(server, auth, proxies, act_id, scene_id)
        if not state: return "⚠️ 金币中心已签到,状态刷新失败"
    day = int(to_float(state["info"].get("ContinueSignInCount")))
    task_map = state["taskList"] or {}
    task = next((item for item in task_map.values() if isinstance(item, dict) and item.get("State") == 2), None)
    if task is None: task = task_map.get(str(day))
    reward = format_coin_reward(task)
    if isinstance(task, dict) and task.get("State") == 2:
        payload = {"comm": make_comm(auth), "req_0": {"module": "music.actCenter.ActCenterSignNewSvr", "method": "AwardPrize", "param": {"ActID": act_id, "TaskID": task.get("ID")}}}
        res = post_musicu(server, auth, payload, proxies); debug_log(res, "Coin AwardPrize")
        award_req = res.get("req_0") if res else None; award_data = award_req.get("data") if award_req else None
        if (res and res.get("code") == 0 and award_req and award_req.get("code") == 0
                and award_data and award_data.get("retCode") in (0, 100004)):
            return f"{'✅ 金币中心签到成功' if signed_now else '✅ 金币中心奖励已领取'}{reward}"
        return f"⚠️ 金币中心已签到,领奖失败"
    if state["info"].get("IsSignIn"): return f"✨ 金币中心今日已签到{reward}"
    return "⚠️ 金币中心签到状态未确认"

def get_lottery_sign_state(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> Dict[str, Any] | None:
    payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsCoinCenter3"),
               "req_0": {"module": "music.actCenter.ActCenterSignNewSvr", "method": "GetSignInSummary", "param": {"ActID": LOTTERY_SIGN_ACT_ID}},
               "req_1": {"module": "music.actCenter.ActCenterSignNewSvr", "method": "GetSignInTaskList", "param": {"ActID": LOTTERY_SIGN_ACT_ID}}}
    res = app_post(server, auth, "GetSignInSummary", payload, proxies); debug_log(res, "Lottery Sign State")
    if not app_request_succeeded(res, "req_0") or not app_request_succeeded(res, "req_1"): return None
    summary_data = (res.get("req_0") or {}).get("data") or {}; task_data = (res.get("req_1") or {}).get("data") or {}
    task_list_info = task_data.get("TaskListInfo") or {}
    task_list = ((task_list_info.get("TaskList") or {}).get("ContinueTaskList")) or {}
    return {"info": task_data.get("Info") or summary_data.get("Info") or {}, "taskList": task_list}

def check_lottery_sign_in(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> str:
    state = get_lottery_sign_state(server, auth, proxies)
    if not state: return "⚠️ 金币抽奖签到失败(状态查询失败)"
    signed_now = False
    if not state["info"].get("IsSignIn"):
        payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsCoinCenter3"), "req_0": {"module": "music.actCenter.ActCenterSignNewSvr", "method": "SignIn", "param": {"ActID": LOTTERY_SIGN_ACT_ID}}}
        res = app_post(server, auth, "SignIn", payload, proxies); debug_log(res, "Lottery SignIn")
        if not app_request_succeeded(res): return "⚠️ 金币抽奖签到失败"
        signed_now = True
        state = get_lottery_sign_state(server, auth, proxies)
        if not state: return "⚠️ 金币抽奖已签到,状态刷新失败"
    task = next((item for item in (state["taskList"] or {}).values() if isinstance(item, dict) and item.get("State") == 2), None)
    if isinstance(task, dict):
        payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsCoinCenter3"), "req_0": {"module": "music.actCenter.ActCenterSignNewSvr", "method": "AwardPrize", "param": {"ActID": LOTTERY_SIGN_ACT_ID, "TaskID": task.get("ID")}}}
        res = app_post(server, auth, "AwardPrize", payload, proxies); debug_log(res, "Lottery Sign Award")
        if app_request_succeeded(res): return f"✅ 金币抽奖签到{format_coin_reward(task)}"
        return "⚠️ 金币抽奖签到已完成,领奖失败"
    if signed_now: return "✅ 金币抽奖签到已完成"
    return "✨ 金币抽奖今日已签到"

def draw_coin_lottery(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> str:
    def query() -> Dict[str, Any] | None:
        payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsCoinCenter3"), "req_0": {"module": "music.actCenter.CoinLotterySvr", "method": "GetCoinUserInfo", "param": {"Param": 1, "Playid": COIN_LOTTERY_PLAY_ID}}}
        return app_post(server, auth, "GetCoinUserInfo", payload, proxies)
    info_res = query(); debug_log(info_res, "Coin Lottery Info")
    if not app_request_succeeded(info_res): return "⚠️ 金币抽奖状态查询失败"
    remain = int(to_float(find_first_value((info_res.get("req_0") or {}).get("data"), ["lotteryRemain"])))
    if remain <= 0: return "✨ 金币抽奖机会已用完"
    gifts = []
    for i in range(min(remain, 10)):
        payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsCoinCenter3"), "req_0": {"module": "music.actCenter.CoinLotterySvr", "method": "UserCoinLottery", "param": {"Param": 1, "Playid": COIN_LOTTERY_PLAY_ID}}}
        res = app_post(server, auth, "UserCoinLottery", payload, proxies); debug_log(res, f"Coin Lottery {i + 1}")
        if not app_request_succeeded(res): break
        gifts.append(format_lottery_gift((res.get("req_0") or {}).get("data"))); sleep(0.35)
    if gifts: return f"✅ 金币抽奖 {len(gifts)} 次: {'、'.join(gifts)}"
    return "⚠️ 金币抽奖未完成"

def read_prize_coins(data: Any) -> int:
    prize_infos = find_first_value(data, ["PrizeInfos", "prizeInfos"])
    if isinstance(prize_infos, str):
        try: prize_infos = json.loads(prize_infos)
        except Exception: prize_infos = None
    if isinstance(prize_infos, dict):
        total = 0
        for item in prize_infos.get("results") or []:
            if not isinstance(item, dict): continue
            info = item.get("info")
            if not isinstance(info, dict): continue
            for prize in info.get("thePrize") or []:
                if isinstance(prize, dict) and re.search(r"金币", str(prize.get("prizeName") or "")):
                    total += int(to_float(prize.get("sendPrizeNum") or prize.get("prizeNum")))
        if total: return total
    return int(to_float(find_first_value(data, ["awardValue", "RewardGold", "rewardGold", "coinNum", "coin"])))

def run_red_packet_rain(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> str:
    payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsCoinCenter3"), "req_0": {"module": "music.actCenter.RedPacketRainSvr", "method": "Raining", "param": {"RainKey": RED_PACKET_RAIN_KEY}}}
    res = app_post(server, auth, "Raining", payload, proxies); debug_log(res, "Red Packet Rain State")
    if not red_packet_request_succeeded(res):
        data = ((res or {}).get("req_0") or {}).get("data")
        if isinstance(data, dict) and to_float(data.get("Code")) == 20001: return "✨ 红包雨当前无可领次数"
        return "⚠️ 红包雨状态查询失败"
    rest_chance = read_red_packet_rest_chance((res.get("req_0") or {}).get("data"))
    if rest_chance <= 0: return "✨ 红包雨当前无可领次数"
    completed, coins = 0, 0
    for i in range(min(rest_chance, 6)):
        chance_payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsCoinCenter3"), "req_0": {"module": "music.actCenter.RedPacketRainSvr", "method": "IncrChance", "param": {"RainKey": RED_PACKET_RAIN_KEY, "IncrType": 2}}}
        chance_res = app_post(server, auth, "IncrChance", chance_payload, proxies); debug_log(chance_res, f"Red Packet Chance {i + 1}")
        if not red_packet_request_succeeded(chance_res): break
        sleep(0.8)
        draw_payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsCoinCenter3"), "req_0": {"module": "music.actCenter.RedPacketRainSvr", "method": "DrawPrizes", "param": {"RainKey": RED_PACKET_RAIN_KEY, "HitNum": 10, "HitStreakNum": 0}}}
        draw_res = app_post(server, auth, "DrawPrizes", draw_payload, proxies); debug_log(draw_res, f"Red Packet Draw {i + 1}")
        if not red_packet_request_succeeded(draw_res): break
        completed += 1; coins += read_prize_coins((draw_res.get("req_0") or {}).get("data"))
    if completed: return f"✅ 红包雨 {completed} 次{': +' + str(coins) + ' 金币' if coins else ''}"
    return "⚠️ 红包雨未完成"

def query_coin_balance(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> str:
    url = "https://i2.y.qq.com/n3/coin_center/pages/client_v1/sign.html?_hidehd=1&_hdct=1&_miniplayer=1"
    try:
        response = request_with_proxy("GET", url, headers={"User-Agent": USER_AGENT, "Cookie": f"uin=o{auth['uin']}; qm_keyst={auth['authst']}", "Referer": "https://i2.y.qq.com/n3/coin_center/pages/client_v1/index.html"}, proxies=proxies, server=server)
        match = re.search(r'__ssrFirstPageData__="((?:[^"\\]|\\.)*)"', response.text)
        if not match: return "-"
        try:
            raw = match.group(1).replace('\\"', '"').replace('\\\\', '\\'); data = json.loads(raw)
        except Exception as exc: print(f"⚠️ [余额] 解析失败: {exc}"); return "-"
        coin = data.get("coin")
        if coin is None: return "-"
        return str(int(to_float(coin)))
    except Exception as exc: print(f"⚠️ [余额] 查询失败: {exc}"); return "-"


# ==================== 每日任务 ====================
def query_daily_tasks(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, page_id: str, floor_ids: List[int]) -> List[Dict[str, Any]] | None:
    payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsCoinCenter3"), "req_0": {"module": "music.activeCenter.FloorManagerSvr", "method": "GetFloors", "param": {"Release": 1, "PageID": page_id, "PersonalityMode": 1, "FloorIDs": floor_ids}}}
    res = app_post(server, auth, "GetFloors", payload, proxies); debug_log(res, f"Daily Tasks {page_id}")
    req = res.get("req_0") if res else None; data = req.get("data") if req else None
    if not res or res.get("code") != 0 or not req or req.get("code") != 0 or not data or data.get("RetCode") != 0: return None
    tasks: List[Dict[str, Any]] = []
    for floor in data.get("Floors") or []:
        if not isinstance(floor, dict): continue
        for item in floor.get("ItemList") or []:
            if not isinstance(item, dict): continue
            try:
                conf = item.get("ResourceConf")
                if isinstance(conf, str): conf = json.loads(conf)
                if not isinstance(conf, dict): continue
                task_list = ((conf.get("ActTaskModule") or {}).get("TaskList")) or []
                for task in task_list:
                    if not isinstance(task, dict): continue
                    task = dict(task); task["_actID"] = conf.get("ActID") or DAILY_TASK_ACT_ID; tasks.append(task)
            except Exception as exc: print(f"⚠️ [任务] 配置解析失败: {exc}")
    return tasks

def get_daily_tasks(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> List[Dict[str, Any]] | None:
    tasks = query_daily_tasks(server, auth, proxies, "18NtBy", [193])
    if not tasks: tasks = query_daily_tasks(server, auth, proxies, "songpopup", [85])
    return tasks

def find_daily_task(tasks: List[Dict[str, Any]], predicate: Any) -> Dict[str, Any] | None:
    for task in tasks:
        if predicate(task): return task
    return None

def task_reached_ready(tasks: List[Dict[str, Any]], target: Dict[str, Any]) -> bool:
    for task in tasks:
        if task.get("ID") == target.get("ID") and task.get("State") == 2: return True
    return False

def is_timed_floor_task(task: Dict[str, Any]) -> bool:
    return bool(task.get("ID") == "26EIHk" or int(to_float(task.get("Type"))) == 600 or re.search(r"定时领金币", task.get("Name") or ""))

def unique_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set(); result = []
    for task in tasks:
        if not isinstance(task, dict) or not task.get("ID"): continue
        key = f"{task.get('_actID') or ''}:{task.get('ID')}"
        if key in seen: continue
        seen.add(key); result.append(task)
    return result

def claim_ready_task_rewards(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, tasks: List[Dict[str, Any]]) -> str:
    ready = [t for t in tasks if isinstance(t, dict) and t.get("State") == 2
             and isinstance(t.get("PrizeList"), list)
             and any(isinstance(p, dict) and int(to_float(p.get("Type"))) == 12 and to_float(p.get("Value")) > 0 for p in t["PrizeList"])]
    claimed = []
    for task in ready:
        payload = {"comm": make_app_comm(auth, 23, 0, "DevopsBase"), "req_0": {"module": "music.activeCenter.ActTaskNewSvr", "method": "AwardTaskPrize", "param": {"actID": task.get("_actID"), "TaskID": task.get("ID")}}}
        res = app_post(server, auth, "AwardTaskPrize", payload, proxies); debug_log(res, f"Award {task.get('ID')}")
        award_req = res.get("req_0") if res else None; award_data = award_req.get("data") if award_req else None
        if res and res.get("code") == 0 and award_req and award_req.get("code") == 0 and award_data and award_data.get("retCode") == 0:
            value = int(to_float(award_data.get("awardValue")))
            claimed.append(f"{task.get('Name') or task.get('ID')}{' +' + str(value) if value else ''}")
            if isinstance(award_data.get("taskStatusInfo"), dict): task.update(award_data["taskStatusInfo"])
        else:
            print(f"⚠️ [任务] 领奖失败 ({task.get('Name') or task.get('ID')})")
    return f"✅ 每日任务领奖: {'、'.join(claimed)}" if claimed else ""

def report_daily_task_action(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, task: Dict[str, Any]) -> bool:
    payload = {"comm": make_app_comm(auth, 23, 0, "DevopsBase"), "req_0": {"module": "music.activeCenter.ActTaskNewSvr", "method": "TaskActDataReport", "param": {"actID": task.get("_actID") or DAILY_TASK_ACT_ID, "taskID": task.get("ID"), "actData": 1}}}
    res = app_post(server, auth, "TaskActDataReport", payload, proxies); debug_log(res, f"Task Action {task.get('ID')}")
    return app_request_succeeded(res)

def refresh_tasks_after_action(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, target: Dict[str, Any]) -> List[Dict[str, Any]]:
    refreshed: List[Dict[str, Any]] = []
    for _ in range(4):
        sleep(1.5); refreshed = get_daily_tasks(server, auth, proxies) or refreshed
        if task_reached_ready(refreshed, target): break
    return refreshed


# ==================== 临时收藏/关注 ====================
def add_temporary_song_favorite(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> Dict[str, Any] | None:
    payload = {"comm": make_comm(auth), "req_0": {"module": "music.musicToplist.Toplist", "method": "GetDetail", "param": {"topId": 26, "offset": 0, "num": 10, "withTags": True}}}
    res = post_musicu(server, auth, payload, proxies); debug_log(res, "Favorite Candidates")
    data = ((res or {}).get("req_0") or {}).get("data") or {}
    songs = (data.get("data") or {}).get("song") or []; random.shuffle(songs)
    for song in songs:
        if not isinstance(song, dict): continue
        candidate = {"songId": int(to_float(song.get("songId"))), "songType": int(to_float(song.get("songType")))}
        if not candidate["songId"]: continue
        if add_favorite_song_if_new(server, auth, proxies, candidate): return candidate
    print("⚠️ [任务] 未找到可临时收藏的榜单歌曲"); return None

def add_favorite_song_if_new(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, song: Dict[str, Any]) -> bool:
    payload = {"comm": make_comm(auth), "req_0": {"module": "music.musicasset.PlaylistDetailWrite", "method": "AddSonglist", "param": {"dirId": 201, "tid": 0, "bFmtUtf8": True, "v_songInfo": [{"songId": song["songId"], "songType": song["songType"]}]}}}
    res = post_musicu(server, auth, payload, proxies); debug_log(res, "AddSonglist")
    added = read_song_add_status(res, song["songId"])
    if added is None: return False
    if not added: return False
    return True

def update_favorite_song(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, method: str, song: Dict[str, Any]) -> bool:
    payload = {"comm": make_comm(auth), "req_0": {"module": "music.musicasset.PlaylistDetailWrite", "method": method, "param": {"dirId": 201, "tid": 0, "bFmtUtf8": True, "v_songInfo": [{"songId": song["songId"], "songType": song["songType"]}]}}}
    res = post_musicu(server, auth, payload, proxies); debug_log(res, method)
    return music_request_succeeded(res)

def search_content(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, search_type: int) -> Dict[str, Any] | None:
    payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsBase"), "req_0": {"module": "music.search.SearchCgiService", "method": "DoSearchForQQMusicMobile", "param": {"query": "音乐", "highlight": 1, "searchid": "", "sub_searchid": 0, "search_type": search_type, "sin": 0, "ein": 29, "page_num": 1, "num_per_page": 15, "cat": 2, "grp": 1, "remoteplace": "txt.mqq.all", "multi_zhida": 1}}}
    res = app_post(server, auth, "DoSearchForQQMusicMobile", payload, proxies); debug_log(res, f"Search Candidates {search_type}")
    if not app_request_succeeded(res): return None
    return (res.get("req_0") or {}).get("data")

def search_content_candidates(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, search_type: int, keys: List[str]) -> List[Any]:
    data = search_content(server, auth, proxies, search_type)
    return collect_values_by_keys(data, keys, lambda value: value != "" and value != 0, 20)

def search_singer_candidates(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> List[str]:
    data = search_content(server, auth, proxies, 7); return collect_singer_mids(data, 20)

def query_follow_status(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, follow_type: int, target_id: str) -> bool | None:
    payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsBase"), "req_0": {"module": "music.follow.FollowStatus", "method": "QueryFollowStatus", "param": {"type": follow_type, "id": str(target_id)}}}
    res = app_post(server, auth, "QueryFollowStatus", payload, proxies); debug_log(res, f"Follow Status {follow_type}")
    if not app_request_succeeded(res): return None
    data = (res.get("req_0") or {}).get("data")
    direct = read_target_status(data, str(target_id))
    if direct is not None: return direct
    return read_single_mapped_status(data, ["m_user_status", "m_singer_status"])

def update_favorite_playlist(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, add: bool, playlist: Dict[str, Any]) -> bool:
    method = "FavPlaylist" if add else "CancelFavPlaylist"
    payload = {"comm": make_comm(auth), "req_0": {"module": "music.musicasset.PlaylistFavWrite", "method": method, "param": {"v_playlistId": [int(playlist["playlistID"])]}}}
    res = post_musicu(server, auth, payload, proxies); debug_log(res, "Playlist Favorite")
    return music_request_succeeded(res)

def add_temporary_playlist_favorite(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> Dict[str, Any] | None:
    dynamic_ids = search_content_candidates(server, auth, proxies, 4, ["dissid"])
    candidates = unique_values([*PLAYLIST_CANDIDATES, *dynamic_ids])
    for item in candidates:
        candidate = {"playlistID": int(to_float(item))}
        if candidate["playlistID"] <= 0: continue
        status = query_follow_status(server, auth, proxies, 500, str(candidate["playlistID"]))
        if status is True: continue
        if status is not False: continue
        if update_favorite_playlist(server, auth, proxies, True, candidate): return candidate
    print("⚠️ [任务] 未找到可临时收藏的歌单"); return None

def update_favorite_audiobook(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, add: bool, audiobook: Dict[str, Any]) -> bool:
    payload = {"comm": make_comm(auth), "req_0": {"module": "music.favorSystemWrite.FavorSystem", "method": "do_favor", "param": {"reqtype": 1 if add else 2, "fav_type": 1, "vec_id": [str(audiobook["bookID"])]}}}
    res = post_musicu(server, auth, payload, proxies); debug_log(res, "Audiobook Favorite")
    return music_request_succeeded(res)

def add_temporary_audiobook_favorite(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> Dict[str, Any] | None:
    payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsBase"), "req_0": {"module": "music.longRadio.LongRadioContent", "method": "GetChannelPageV2", "param": {"tabIndex": -1, "abt": "39445_39445003", "splashEndInterval": -1, "categoryId": AUDIOBOOK_CATEGORY_ID, "page": 1}}}
    res = app_post(server, auth, "GetChannelPageV2", payload, proxies); debug_log(res, "Audiobook Candidates")
    dynamic_ids = collect_values_by_keys(res, ["albumID", "albumId", "album_id", "radioID", "radioId", "radio_id"], lambda value: re.match(r"^\d{6,12}$", str(value)), 20)
    candidates = unique_values([*AUDIOBOOK_CANDIDATES, *dynamic_ids])
    for item in candidates:
        candidate = {"bookID": str(item)}
        status = query_follow_status(server, auth, proxies, 400, candidate["bookID"])
        if status is True: continue
        if status is not False: continue
        if update_favorite_audiobook(server, auth, proxies, True, candidate): return candidate
    print("⚠️ [任务] 未找到可临时收藏的有声书"); return None

def query_singer_follow_status(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, mid: str) -> bool | None:
    payload = {"comm": make_app_comm(auth, 1, 200605, "DevopsBase"), "req_0": {"module": "music.concern.ConcernSystem", "method": "cgi_qry_concern_status", "param": {"vec_userinfo": [{"usertype": 1, "userid": mid}]}}}
    res = app_post(server, auth, "cgi_qry_concern_status", payload, proxies); debug_log(res, "Singer Follow Status")
    if not app_request_succeeded(res): return None
    data = (res.get("req_0") or {}).get("data")
    mapping = data.get("map_singer_status") if isinstance(data, dict) else None
    if isinstance(mapping, dict) and mid in mapping: return normalize_status(mapping[mid])
    direct = read_target_status(data, mid)
    if direct is not None: return direct
    return read_single_mapped_status(data, ["map_singer_status"])

def update_singer_follow(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None, add: bool, singer: Dict[str, Any]) -> bool:
    payload = {"comm": make_app_comm(auth, 23, 0, "DevopsBase"), "req_0": {"module": "music.concern.ConcernSystem", "method": "cgi_concern_user_v2", "param": {"bussinesstype": "", "source": 137, "opertype": 0 if add else 1, "bussinessid": "", "userinfo": {"userid": singer["mid"], "usertype": 1}}}}
    res = app_post(server, auth, "cgi_concern_user_v2", payload, proxies); debug_log(res, "Follow Singer")
    return app_request_succeeded(res)

def add_temporary_singer_follow(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> Dict[str, Any] | None:
    dynamic_mids = search_singer_candidates(server, auth, proxies)
    candidates = unique_values([*SINGER_CANDIDATES, *dynamic_mids])
    for item in candidates:
        mid = str(item)
        if not re.match(r"^[A-Za-z0-9]{10,20}$", mid): continue
        candidate = {"mid": mid}
        status = query_singer_follow_status(server, auth, proxies, mid)
        if status is True: continue
        if status is not False: continue
        if update_singer_follow(server, auth, proxies, True, candidate): return candidate
    print("⚠️ [任务] 未找到可临时关注的歌手"); return None


# ==================== 每日任务主流程 ====================
def claim_daily_task_rewards(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> str:
    tasks = get_daily_tasks(server, auth, proxies)
    if not tasks: return "❌ 每日任务查询失败"
    messages: List[str] = []; cleanups: List[Dict[str, Any]] = []
    try:
        quiz_task = find_daily_task(tasks, lambda t: t.get("ID") == "Zff1WO" or re.search(r"皇宫身份", t.get("Name") or ""))
        if quiz_task and quiz_task.get("State") == 1 and ENABLE_ACTIVITY:
            if report_daily_task_action(server, auth, proxies, quiz_task):
                tasks = refresh_tasks_after_action(server, auth, proxies, quiz_task)
        money_tree_task = find_daily_task(tasks, lambda t: t.get("ID") == "5E9TC" or re.search(r"摇钱树", t.get("Name") or ""))
        if money_tree_task and money_tree_task.get("State") == 1 and ENABLE_ACTIVITY:
            if report_daily_task_action(server, auth, proxies, money_tree_task):
                tasks = refresh_tasks_after_action(server, auth, proxies, money_tree_task)
        if ENABLE_FAVORITE:
            favorite_song_task = find_daily_task(tasks, lambda t: t.get("ID") == "Z1mKlEI" or t.get("TaskActTypID") == "2tuNRp" or int(to_float(t.get("Type"))) == 8)
            if favorite_song_task and favorite_song_task.get("State") == 1:
                song = add_temporary_song_favorite(server, auth, proxies)
                if song:
                    cleanups.append({"name": "临时收藏歌曲", "run": lambda: update_favorite_song(server, auth, proxies, "DelSonglist", song), "warning": "请到“我喜欢”检查最新一首"})
                    tasks = refresh_tasks_after_action(server, auth, proxies, favorite_song_task)
                    if not task_reached_ready(tasks, favorite_song_task): messages.append("⚠️ 收藏歌曲任务状态未更新")
            favorite_playlist_task = find_daily_task(tasks, lambda t: t.get("ID") == "ZBiJs9" or int(to_float(t.get("Type"))) == 9)
            if favorite_playlist_task and favorite_playlist_task.get("State") == 1:
                playlist = add_temporary_playlist_favorite(server, auth, proxies)
                if playlist:
                    cleanups.append({"name": "临时收藏歌单", "run": lambda: update_favorite_playlist(server, auth, proxies, False, playlist), "warning": "请到收藏歌单中检查"})
                    tasks = refresh_tasks_after_action(server, auth, proxies, favorite_playlist_task)
                    if not task_reached_ready(tasks, favorite_playlist_task) and report_daily_task_action(server, auth, proxies, favorite_playlist_task):
                        tasks = refresh_tasks_after_action(server, auth, proxies, favorite_playlist_task)
                    if not task_reached_ready(tasks, favorite_playlist_task): messages.append("⚠️ 收藏歌单任务状态未更新")
            favorite_audiobook_task = find_daily_task(tasks, lambda t: t.get("ID") == "Z5bnvq" or t.get("TaskActTypID") == "CeYSX" or int(to_float(t.get("Type"))) == 36)
            if favorite_audiobook_task and favorite_audiobook_task.get("State") == 1:
                audiobook = add_temporary_audiobook_favorite(server, auth, proxies)
                if audiobook:
                    cleanups.append({"name": "临时收藏有声书", "run": lambda: update_favorite_audiobook(server, auth, proxies, False, audiobook), "warning": "请到收藏的有声书中检查"})
                    tasks = refresh_tasks_after_action(server, auth, proxies, favorite_audiobook_task)
            follow_singer_task = find_daily_task(tasks, lambda t: t.get("ID") == "2wgcMV" or int(to_float(t.get("Type"))) == 13)
            if follow_singer_task and follow_singer_task.get("State") == 1:
                singer = add_temporary_singer_follow(server, auth, proxies)
                if singer:
                    cleanups.append({"name": "临时关注歌手", "run": lambda: update_singer_follow(server, auth, proxies, False, singer), "warning": "请到关注歌手中检查"})
                    tasks = refresh_tasks_after_action(server, auth, proxies, follow_singer_task)
        reward_msg = claim_ready_task_rewards(server, auth, proxies, [t for t in tasks if not is_timed_floor_task(t)])
        if reward_msg: messages.append(reward_msg)
    finally:
        for cleanup in cleanups:
            try:
                if cleanup["run"](): print(f"🧹 [清理] {cleanup['name']}已恢复")
                else: print(f"⚠️ [清理] {cleanup['name']}恢复失败,{cleanup['warning']}")
            except Exception as exc: print(f"⚠️ [清理] {cleanup['name']}恢复异常: {exc}")
    return "\n".join(messages) if messages else "✅ 每日任务处理完成"

def get_timer_tasks(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> List[Dict[str, Any]] | None:
    payload = {"comm": make_app_comm(auth, 23, 0, "DevopsBase"), "req_0": {"module": "music.activeCenter.ActTaskNewSvr", "method": "GetTaskModules", "param": {"actID": DAILY_TASK_ACT_ID, "taskModuleIDs": [TIMER_TASK_MODULE_ID]}}}
    res = app_post(server, auth, "GetTaskModules", payload, proxies); debug_log(res, "Timer Treasure Task")
    if not app_request_succeeded(res): return None
    modules = ((res.get("req_0") or {}).get("data") or {}).get("taskModules") or {}
    tasks: List[Dict[str, Any]] = []
    for module in modules.values():
        if not isinstance(module, dict): continue
        for task in module.get("TaskList") or []:
            if not isinstance(task, dict): continue
            task = dict(task); task["_actID"] = DAILY_TASK_ACT_ID; tasks.append(task)
    return tasks

def claim_timed_task_rewards(server: str, auth: Dict[str, Any], proxies: Dict[str, str] | None) -> str:
    messages: List[str] = []
    for source in ("floor", "treasure"):
        queried = query_daily_tasks(server, auth, proxies, "18NtBy", [193]) if source == "floor" else get_timer_tasks(server, auth, proxies)
        timed_tasks = unique_tasks([t for t in (queried or []) if source != "floor" or is_timed_floor_task(t)])
        if not timed_tasks: continue
        reward_msg = claim_ready_task_rewards(server, auth, proxies, timed_tasks)
        if reward_msg: messages.append(reward_msg)
    return "\n".join(messages) if messages else ""


# ==================== 时段判断 ====================
def is_daily_flow_due(now: datetime | None = None) -> bool:
    now = now or datetime.now(); return now.hour > 7 or (now.hour == 7 and now.minute >= 30)

def is_timer_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(); return now.hour in (9, 10)

def get_red_packet_slot(now: datetime | None = None) -> str:
    now = now or datetime.now(); return str(now.hour) if now.hour in (0, 8, 12, 16, 20, 22) else ""


# ==================== 单账号流程 (使用缓存登录) ====================
def run_account(index: int, total: int, account: AccountTarget) -> Dict[str, Any]:
    server = account.server
    result = {
        "server": server, "accountLabel": account.label, "remark": account.remark,
        "nickname": account.nickname, "success": False, "proxyStatus": "未使用代理",
        "proxyIp": "-", "token": "-", "lvzMsg": "-", "coinMsg": "-", "taskMsg": "-",
        "lotteryMsg": "-", "redPacketMsg": "-", "balance": "-", "error": "",
    }
    log_account_header(index, total, account)
    proxies, proxy_ip = get_valid_proxy(server)
    result["proxyStatus"] = "使用专属代理" if proxies else "使用直连"
    result["proxyIp"] = proxy_ip or "-"
    sleep(PROXY_FETCH_INTERVAL)
    delay = random.randint(2, 6)
    print(f"⏳ [延迟] 启动延迟 {delay}s"); sleep(delay)

    # ★ 核心改动：使用缓存优先登录
    auth = ensure_auth(account, proxies)
    if not auth:
        result["error"] = "登录失败（缓存无效且无法获取新CK）"
        return result

    result["accountLabel"] = account.label
    result["remark"] = account.remark
    result["nickname"] = account.nickname
    if account.remark or account.nickname:
        print(f"👤 [YYB] {account.label}")
    result["token"] = mask(auth["authst"])
    print(f"👤 [账号] uin={mask(auth['uin'])}")

    try:
        now = datetime.now()
        if not is_daily_flow_due(now):
            result["lvzMsg"] = "⏰ 未到每日签到时段(7:30 后执行)"
            result["coinMsg"] = "⏰ 未到每日签到时段(7:30 后执行)"
            result["taskMsg"] = "⏰ 未到每日签到时段(7:30 后执行)"
        else:
            print("💎 [签到] 绿钻成长值签到")
            result["lvzMsg"] = check_lvz_score(server, auth, proxies)
            print(f"💎 [绿钻] {result['lvzMsg']}")
            print("🪙 [签到] 金币中心签到")
            result["coinMsg"] = check_coin_sign_in(server, auth, proxies)
            print(f"🪙 [金币] {result['coinMsg']}")
            print("📋 [任务] 每日任务")
            result["taskMsg"] = claim_daily_task_rewards(server, auth, proxies)
            print(f"📋 [任务] {result['taskMsg']}")
            if ENABLE_ACTIVITY:
                print("🎰 [抽奖] 金币抽奖签到")
                lottery_msgs = [check_lottery_sign_in(server, auth, proxies)]
                print(f"🎰 [抽奖] {lottery_msgs[-1]}")
                lottery_msgs.append(draw_coin_lottery(server, auth, proxies))
                print(f"🎰 [抽奖] {lottery_msgs[-1]}")
                result["lotteryMsg"] = "\n".join(msg for msg in lottery_msgs if msg)
            else:
                result["lotteryMsg"] = "⏭️ 活动功能已关闭"
        if ENABLE_ACTIVITY and is_timer_window(now):
            print("⏰ [任务] 定时金币任务")
            timed_msg = claim_timed_task_rewards(server, auth, proxies)
            if timed_msg:
                parts = [x for x in (result["taskMsg"], timed_msg) if x and x != "-"]
                result["taskMsg"] = "\n".join(parts)
            print(f"⏰ [定时] {timed_msg or '无待领奖励'}")
        if ENABLE_ACTIVITY and get_red_packet_slot(now):
            print("🧧 [红包] 红包雨")
            result["redPacketMsg"] = run_red_packet_rain(server, auth, proxies)
            print(f"🧧 [红包] {result['redPacketMsg']}")
        else:
            result["redPacketMsg"] = "⏰ 当前时段无红包雨"
        print("💰 [余额] 查询金币余额")
        result["balance"] = query_coin_balance(server, auth, proxies)
        print(f"💰 [余额] {result['balance']}")
        result["success"] = True
        return result
    except Exception as exc:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行失败: {exc}")
        return result


# ==================== 日志与通知 ====================
def log_title() -> None:
    print()
    print("╔" + "═" * 54 + "╗")
    print("║ ♻️ QQ音乐签到 (缓存优先 + YYB-GO 自动刷新)       ║")
    print(f"║ 🕒 启动时间: {now_text():<36}║")
    print(f"║ 🔢 账号数量: {len(CURRENT_ACCOUNTS):<38}║")
    print(f"║ 🌍 YYB-GO: {(YYB_GO_URL or '未配置'):<40}║")
    print(f"║ 💾 缓存文件: {str(CACHE_FILE.name):<34}║")
    print("╚" + "═" * 54 + "╝")

def log_account_header(index: int, total: int, account: AccountTarget) -> None:
    print()
    print("┌" + "─" * 54 + "┐")
    print(f"│ 🧩 账号 {index} / {total:<41}│")
    print(f"│ 👤 账号 {account.label:<42}│")
    print(f"│ 🌍 来源 {account.server:<44}│")
    print("└" + "─" * 54 + "┘")

def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count
    content = f"""♻️ QQ音乐多账号任务结果

━━━━━━━━━━━━━━━━━━━━
🏁 总结：{success_count} 成功 / {fail_count} 失败
🕒 时间：{now_text()}
━━━━━━━━━━━━━━━━━━━━
"""
    for idx, res in enumerate(results, 1):
        icon = "✅" if res["success"] else "❌"
        content += f"""
🧩 账号 {idx}：{res.get('accountLabel') or res['server']}
🌍 来源：{res["server"]}
🌐 代理：{res["proxyStatus"]}
📡 出口IP：{res["proxyIp"]}
🔐 Token：{res["token"]}
💎 绿钻：{res["lvzMsg"]}
🪙 金币：{res["coinMsg"]}
📋 任务：{res["taskMsg"]}
🎰 抽奖：{res["lotteryMsg"]}
🧧 红包：{res["redPacketMsg"]}
💰 余额：{res["balance"]} 金币
{icon} 结果：{"成功" if res["success"] else "失败"}
"""
        if not res["success"]:
            content += f"❌ 原因：{res['error']}\n"
        content += "━━━━━━━━━━━━━━━━━━━━\n"
    return content


# ==================== 主入口 ====================
def main() -> None:
    global CURRENT_ACCOUNTS
    try:
        CURRENT_ACCOUNTS = load_account_targets()
    except Exception as exc:
        print(f"❌ [配置] {exc}"); return
    log_title()
    results: List[Dict[str, Any]] = []
    for index, account in enumerate(CURRENT_ACCOUNTS, 1):
        try:
            result = run_account(index, len(CURRENT_ACCOUNTS), account)
            results.append(result)
        except Exception as exc:
            print(f"❌ [主程序] {account.label} 执行异常: {exc}")
            results.append({
                "server": account.server, "accountLabel": account.label,
                "remark": account.remark, "nickname": account.nickname,
                "success": False, "proxyStatus": "-", "proxyIp": "-",
                "token": "-", "lvzMsg": "-", "coinMsg": "-", "taskMsg": "-",
                "lotteryMsg": "-", "redPacketMsg": "-", "balance": "-",
                "error": traceback.format_exc().strip(),
            })
        if index < len(CURRENT_ACCOUNTS):
            print("⏳ [间隔] 等待 2s 后处理下一个账号"); sleep(2)
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count
    print()
    print("╔" + "═" * 54 + "╗")
    print("║ 🏁 QQ音乐任务执行完成                          ║")
    print(f"║ ✅ 成功: {success_count:<43}║")
    print(f"║ ❌ 失败: {fail_count:<43}║")
    print(f"║ 🕒 结束时间: {now_text():<36}║")
    print("╚" + "═" * 54 + "╝")
    send_pushplus("♻️ QQ音乐多账号任务完成", build_notify(results))


if __name__ == "__main__":
    main()