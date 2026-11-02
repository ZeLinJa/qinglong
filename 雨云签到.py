#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# name: 雨云自动签到
# 环境变量名称：RAINYUN
# 单账户填写：账号#密码
# 多账户填写：账号1#密码1@账号2#密码2
# 依赖：requests, opencv-python-headless, numpy, pillow, ddddocr
# cron: 44 8,21 * * *

from __future__ import annotations
import os
import sys
import json
import time
import random
import base64
import hashlib
from datetime import datetime
from typing import List, Tuple, Optional, Dict, TYPE_CHECKING
from pathlib import Path
if TYPE_CHECKING:
    import numpy as np

# Windows 终端 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests
import urllib3
from PIL import Image
from io import BytesIO

try:
    import cv2
    import numpy as np
    CV2_OK = True
except ImportError:
    CV2_OK = False

# ddddocr 深度学习识别（核心）
try:
    import ddddocr
    DDDDOCR_OK = True
except ImportError:
    DDDDOCR_OK = False
    print("[rainyun] ddddocr 未安装，请执行 pip3 install ddddocr", flush=True)

try:
    from py_mini_racer import MiniRacer
    JS_OK = True
except ImportError:
    JS_OK = False
    print("[rainyun] py_mini_racer 未安装，将使用 Node.js 降级方案", flush=True)
except Exception:
    JS_OK = False

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# 配置常量
# ============================================================
API_BASE      = "https://api.v2.rainyun.com"
CAPTCHA_BASE  = "https://turing.captcha.qcloud.com"
APP_URL       = "https://app.rainyun.com"
CAPTCHA_AID   = "2039519451"

def parse_rainyun_accounts(raw: str) -> List[Tuple[str, str, str]]:
    """解析 RAINYUN=账号#密码@账号#密码；兼容单账户。"""
    accounts: List[Tuple[str, str, str]] = []
    for index, entry in enumerate(str(raw or "").strip().split("@"), 1):
        entry = entry.strip()
        if not entry:
            continue
        if "#" not in entry:
            raise ValueError(f"RAINYUN 第 {index} 个账户格式错误：缺少 # 分隔符")
        username, password = entry.split("#", 1)
        username = username.strip()
        password = password.strip()
        if not username or not password:
            raise ValueError(f"RAINYUN 第 {index} 个账户格式错误：账号或密码为空")
        accounts.append((f"账号{len(accounts) + 1}", username, password))
    return accounts

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR   = os.path.join(SCRIPT_DIR, "rainyun_templates")
COOKIES_FILE   = os.path.join(SCRIPT_DIR, "rainyun_cookies.json")
LOG_FILE       = os.path.join(SCRIPT_DIR, "rainyun_sign.log")
CAPTCHA_RETRIES = 20   # 验证码最多重试次数

# ====== 提现配置 ======
WITHDRAW_POINTS     = os.environ.get("WITHDRAW_POINTS", "60000")
WITHDRAW_MIN_POINTS = int(os.environ.get("WITHDRAW_MIN_POINTS", "60000"))
WITHDRAW_TARGET     = os.environ.get("WITHDRAW_TARGET", "alipay")

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line.encode('utf-8', errors='replace').decode('utf-8'), flush=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def send_qinglong_notification(title: str, content: str) -> bool:
    """调用青龙 notify.py"""
    search_dirs = [SCRIPT_DIR, "/ql/data/scripts", "/ql/scripts"]
    for directory in search_dirs:
        if directory and directory not in sys.path:
            sys.path.insert(0, directory)
    try:
        from notify import send
        send(title, content)
        print("青龙通知：发送请求成功", flush=True)
        return True
    except Exception as e:
        print(f"青龙通知：发送失败（{type(e).__name__}: {e}）", flush=True)
        return False

def ensure_dirs():
    os.makedirs(TEMPLATE_DIR, exist_ok=True)

# ============================================================
# HTTP 会话
# ============================================================
class Session:
    def __init__(self, username: str = ""):
        self.s = requests.Session()
        self.s.verify = False
        self._set_headers()
        self.dev_code      = ""
        self.rain_session  = ""
        self.csrf_token    = ""
        self.username      = username

    def _set_headers(self):
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": APP_URL,
            "Referer": APP_URL + "/",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        })

    def post(self, path, data=None, params=None, extra_headers=None):
        url = API_BASE + path
        h = dict(self.s.headers)
        if extra_headers:
            h.update(extra_headers)
        try:
            r = self.s.post(url, json=data, params=params, headers=h, timeout=20)
            result = r.json()
            if not r.ok and not isinstance(result, dict):
                r.raise_for_status()
            return result
        except Exception as e:
            log(f"  ❌ POST {path} 失败: {e}")
            return None

    def get(self, path, params=None, extra_headers=None):
        url = API_BASE + path
        h = dict(self.s.headers)
        if extra_headers:
            h.update(extra_headers)
        try:
            r = self.s.get(url, params=params, headers=h, timeout=20)
            result = r.json()
            if not r.ok and not isinstance(result, dict):
                r.raise_for_status()
            return result
        except Exception as e:
            log(f"  ❌ GET {path} 失败: {e}")
            return None

    def get_captcha(self, path, params=None):
        url = CAPTCHA_BASE + path
        h = dict(self.s.headers)
        h["Referer"] = APP_URL + "/"
        try:
            r = self.s.get(url, params=params, headers=h, timeout=20)
            r.raise_for_status()
            return r
        except Exception as e:
            log(f"  ❌ CAPTCHA GET {path} 失败: {e}")
            return None

    def download_img(self, url: str) -> Optional[bytes]:
        if url.startswith("/"):
            url = CAPTCHA_BASE + url
        try:
            r = self.s.get(url, timeout=15, stream=True, verify=False)
            r.raise_for_status()
            return r.content
        except Exception as e:
            log(f"  ❌ 下载图片失败: {e}")
            return None

    def bytes_to_cv(self, raw: bytes, keep_alpha: bool = False) -> Optional[np.ndarray]:
        if not raw:
            return None
        arr = np.frombuffer(raw, np.uint8)
        flag = cv2.IMREAD_UNCHANGED if keep_alpha else cv2.IMREAD_COLOR
        img = cv2.imdecode(arr, flag)
        if keep_alpha and img is not None and len(img.shape) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        return img

    def save_cookies(self):
        cookies = {}
        for c in self.s.cookies:
            if c.name in ("rain-session", "dev-code", "HMACCOUNT", "X-CSRF-Token"):
                cookies[c.name] = c.value
        cookies["csrf_token"] = self.csrf_token
        cookies["dev_code"]   = self.dev_code
        all_data = {}
        if os.path.exists(COOKIES_FILE):
            try:
                with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
            except Exception:
                all_data = {}
        all_data[self.username] = cookies
        try:
            with open(COOKIES_FILE, "w", encoding="utf-8") as f:
                json.dump(all_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        log(f"  💾 已保存 Cookies")

    def load_cookies(self) -> bool:
        if not os.path.exists(COOKIES_FILE):
            return False
        try:
            with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                all_data = json.load(f)
            cookies = all_data.get(self.username, {})
            if not cookies:
                return False
            for name, val in cookies.items():
                if name in ("rain-session", "dev-code", "HMACCOUNT", "X-CSRF-Token"):
                    self.s.cookies.set(name, val, domain=".rainyun.com")
            self.dev_code      = cookies.get("dev_code", cookies.get("dev-code", ""))
            self.rain_session  = cookies.get("rain-session", "")
            self.csrf_token    = cookies.get("csrf_token", "")
            log(f"  🔑 已加载 Cookies")
            return True
        except Exception as e:
            log(f"  ⚠️ 加载 Cookies 失败: {e}")
            return False

# ============================================================
# 基础验证码求解器（OpenCV 传统算法）
# ============================================================
class CaptchaSolver:
    def __init__(self, sess: Session):
        self.sess = sess
        self.ticket     = ""
        self.randstr    = ""
        self.coords: List[Tuple[int, int]] = []
        self.captcha_data: dict = {}
        self.captcha_aid: str = CAPTCHA_AID

    def get_prehandle(self) -> Optional[dict]:
        params = {
            "aid": self.captcha_aid,
            "protocol": "https",
            "accver": "1",
            "showtype": "popup",
            "ua": base64.b64encode(self.sess.s.headers["User-Agent"].encode()).decode(),
            "noheader": "1",
            "fb": "1",
            "aged": "0",
            "enableAged": "0",
            "enableDarkMode": "0",
            "grayscale": "1",
            "clientype": "2",
            "cap_cd": "",
            "uid": "",
            "lang": "zh-cn",
            "entry_url": "https://turing.captcha.gtimg.com/1/template/drag_ele.html",
            "elder_captcha": "0",
            "js": "/tcaptcha-frame.97a921e6.js",
            "login_appid": "",
            "wb": "1",
            "subsid": "9",
            "callback": "",
            "sess": "",
        }
        r = self.sess.get_captcha("/cap_union_prehandle", params=params)
        if not r:
            return None
        text = r.text.strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]
        try:
            data = json.loads(text)
            if data.get("state") != 1:
                log(f"  ⚠️ prehandle 返回 state={data.get('state')}")
                return None
            return data
        except json.JSONDecodeError as e:
            log(f"  ❌ prehandle 响应解析失败: {e}")
            return None

    def download_images(self, prehandle_data: dict) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        dyn = prehandle_data.get("data", {})
        info = dyn.get("dyn_show_info", {})
        bg_url   = info.get("bg_elem_cfg", {}).get("img_url", "")
        sprite_url = info.get("sprite_url", "")
        if not bg_url or not sprite_url:
            log("  ❌ 未找到图片 URL")
            return None, None
        bg_raw   = self.sess.download_img(bg_url)
        sprite_raw = self.sess.download_img(sprite_url)
        bg   = self.sess.bytes_to_cv(bg_raw, keep_alpha=False)   if bg_raw   else None
        sprite = self.sess.bytes_to_cv(sprite_raw, keep_alpha=True) if sprite_raw else None
        if bg is not None and sprite is not None:
            log(f"  ✅ 图片已下载  背景:{bg.shape[1]}x{bg.shape[0]}  碎片:{sprite.shape[1]}x{sprite.shape[0]}")
        return bg, sprite

    def split_sprite(self, sprite: np.ndarray, ins_elem_cfg: Optional[List[dict]] = None) -> List[np.ndarray]:
        h, w = sprite.shape[:2]
        if ins_elem_cfg and len(ins_elem_cfg) > 1:
            pieces = []
            for elem in ins_elem_cfg:
                pos = elem.get("sprite_pos", [0, 0])
                size = elem.get("size_2d", [w, h])
                x, y = pos[0], pos[1]
                pw, ph = size[0], size[1]
                pieces.append(sprite[y:y+ph, x:x+pw].copy())
            return pieces
        piece_w = w // 3
        pieces = []
        for i in range(3):
            x1 = i * piece_w
            x2 = w if i == 2 else (i + 1) * piece_w
            pieces.append(sprite[:, x1:x2].copy())
        return pieces

    def find_positions(self, bg: np.ndarray, pieces: List[np.ndarray]) -> List[Tuple[int, int]]:
        if not CV2_OK or not pieces:
            return []
        positions = []
        bg_h, bg_w = bg.shape[:2]
        used_regions: List[Tuple[int, int, int, int]] = []
        bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
        bg_edges = cv2.Canny(bg_gray, 50, 150)
        _, bg_thresh = cv2.threshold(bg_gray, 120, 255, cv2.THRESH_BINARY_INV)

        try:
            sift = cv2.SIFT_create()
            bg_kp, bg_des = sift.detectAndCompute(bg_gray, None)
        except Exception:
            sift = None
            bg_kp, bg_des = None, None

        for i, piece in enumerate(pieces):
            if piece is None or piece.size == 0:
                continue
            has_alpha = piece.shape[2] == 4 if len(piece.shape) == 3 else False
            piece_bgr = piece[:, :, :3] if has_alpha else piece
            piece_alpha = piece[:, :, 3] if has_alpha else None
            piece_gray = cv2.cvtColor(piece_bgr, cv2.COLOR_BGR2GRAY)
            ph, pw = piece_gray.shape[:2]
            piece_edges = cv2.Canny(piece_gray, 50, 150)

            best_pos, best_score = None, 0.0
            best_method = ""

            # 多尺度边缘模板匹配
            for scale in [0.8, 0.9, 1.0, 1.1, 1.2, 1.4, 1.6]:
                new_w = max(10, int(pw * scale))
                new_h = max(10, int(ph * scale))
                scaled = cv2.resize(piece_edges, (new_w, new_h))
                if new_h > bg_h or new_w > bg_w:
                    continue
                res = cv2.matchTemplate(bg_edges, scaled, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                cx, cy = max_loc[0] + new_w//2, max_loc[1] + new_h//2
                overlaps = any(cx < ux2 and cx > ux1 and cy < uy2 and cy > uy1
                               for ux1, uy1, ux2, uy2 in used_regions)
                if not overlaps and max_val > best_score:
                    best_score = max_val
                    best_pos = (cx, cy)
                    best_method = f"edge(scale={scale})"

            # SIFT 特征匹配
            if sift is not None and bg_des is not None and best_score < 0.6:
                try:
                    piece_kp, piece_des = sift.detectAndCompute(piece_gray, None)
                    if piece_des is not None and len(piece_des) > 2:
                        flann = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {"checks": 50})
                        matches = flann.knnMatch(piece_des, bg_des, k=2)
                        good = [m for m, n in matches if m.distance < 0.85 * n.distance]
                        if len(good) >= 8:
                            src_pts = np.float32([piece_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                            dst_pts = np.float32([bg_kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
                            matrix, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
                            if matrix is not None:
                                pts = np.float32([[0,0],[pw,0],[pw,ph],[0,ph]]).reshape(-1,1,2)
                                dst = cv2.perspectiveTransform(pts, matrix)
                                cx = int((dst[0][0][0] + dst[2][0][0]) / 2)
                                cy = int((dst[0][0][1] + dst[2][0][1]) / 2)
                                if 0 <= cx < bg_w and 0 <= cy < bg_h:
                                    overlaps = any(cx < ux2 and cx > ux1 and cy < uy2 and cy > uy1
                                                   for ux1, uy1, ux2, uy2 in used_regions)
                                    if not overlaps:
                                        sift_score = min(len(good) * 0.15 + 0.5, 2.0)
                                        if sift_score > best_score:
                                            best_score = sift_score
                                            best_pos = (cx, cy)
                                            best_method = f"sift({len(good)})"
                except Exception:
                    pass

            # 灰度阈值模板匹配（数字类碎片）
            if best_score < 0.55:
                res = cv2.matchTemplate(bg_thresh, piece_gray, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                cx, cy = max_loc[0] + pw//2, max_loc[1] + ph//2
                overlaps = any(cx < ux2 and cx > ux1 and cy < uy2 and cy > uy1
                               for ux1, uy1, ux2, uy2 in used_regions)
                if not overlaps and max_val > best_score and max_val >= 0.5:
                    best_score = max_val
                    best_pos = (cx, cy)
                    best_method = "gray_thresh"

            if best_pos:
                positions.append(best_pos)
                used_regions.append((best_pos[0]-pw//2, best_pos[1]-ph//2,
                                     best_pos[0]+pw//2, best_pos[1]+ph//2))
                log(f"  ✅ 碎片{i+1}: {best_pos}, 相似度={best_score:.3f} [{best_method}]")
            else:
                log(f"  ❌ 碎片{i+1} 未找到")
        return positions

    def save_debug_images(self, bg: np.ndarray, pieces: List[np.ndarray], positions: List[Tuple[int, int]], output_dir: str):
        import os
        os.makedirs(output_dir, exist_ok=True)
        debug_bg = bg.copy()
        for i, (x, y) in enumerate(positions, 1):
            cv2.circle(debug_bg, (x, y), 10, (0, 255, 0), -1)
            cv2.putText(debug_bg, f"P{i}", (x-20, y-15), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imwrite(os.path.join(output_dir, "debug_matched.png"), debug_bg)

    def _get_collect_and_eks(self, tdc_js: str, positions: List[Tuple[int, int]] = None) -> tuple:
        aid = self.captcha_data.get("aid", CAPTCHA_AID) if self.captcha_data else CAPTCHA_AID
        env_path = os.path.join(SCRIPT_DIR, "env.js")
        runner_path = os.path.join(SCRIPT_DIR, "tdc_runner.js")

        # 优先 Node.js
        if os.path.exists(runner_path) and os.path.exists(env_path):
            try:
                import subprocess
                positions_json = json.dumps(positions or [])
                result = subprocess.run(
                    ["node", runner_path, env_path, "-", str(aid), positions_json],
                    input=tdc_js, capture_output=True, text=True, timeout=15, encoding="utf-8"
                )
                if result.returncode == 0 and result.stdout.strip():
                    data = json.loads(result.stdout.strip())
                    return data.get("collect", ""), data.get("eks", "")
            except Exception:
                pass

        # 回退 py_mini_racer
        if JS_OK:
            try:
                ctx = MiniRacer()
                ctx.eval(tdc_js)
                ctx.eval('window.TDC && window.TDC.setData && window.TDC.setData("qf_7Pf__H")')
                if positions:
                    prev_x, prev_y = random.randint(100, 300), random.randint(100, 200)
                    for (x, y) in positions:
                        try:
                            ctx.eval(f'window.mouse_move && window.mouse_move({prev_x},{prev_y},{x},{y})')
                            ctx.eval(f'window.mouse_click && window.mouse_click({x},{y})')
                        except Exception:
                            pass
                        prev_x, prev_y = x, y
                        time.sleep(0.05)
                collect = ctx.eval('(window.TDC && window.TDC.getData) ? window.TDC.getData(true) : ""')
                eks = ctx.eval('(window.TDC && window.TDC.getInfo) ? window.TDC.getInfo().info : ""')
                return collect, eks
            except Exception:
                pass
        return "", ""

    def _find_md5_collision(self, target_md5: str, prefix: str) -> tuple:
        start = time.time()
        num = 0
        while num < 114514000:
            candidate = prefix + str(num)
            if hashlib.md5(candidate.encode()).hexdigest() == target_md5:
                return candidate, int((time.time()-start)*1000)
            num += 1
        return prefix, int((time.time()-start)*1000)

    def submit_captcha(self, positions, sess_id, tdc_js, pow_prefix, pow_md5) -> Optional[dict]:
        ans = []
        for idx, (x, y) in enumerate(positions, 1):
            ans.append({"elem_id": idx, "type": "DynAnswerType_POS", "data": f"{x},{y}"})
        ans_str = json.dumps(ans, separators=(",", ":"))

        collect, eks = self._get_collect_and_eks(tdc_js, positions)
        from urllib.parse import unquote, quote_plus
        tlg = len(unquote(collect or ""))
        pow_answer, pow_time = self._find_md5_collision(pow_md5, pow_prefix)

        form_data = (
            f"sess={quote_plus(sess_id)}"
            f"&ans={quote_plus(ans_str)}"
            f"&collect={collect or ''}"
            f"&tlg={tlg}"
            f"&eks={quote_plus(eks or '')}"
            f"&pow_answer={quote_plus(pow_answer)}"
            f"&pow_calc_time={pow_time}"
        )
        try:
            r = self.sess.s.post(
                CAPTCHA_BASE + "/cap_union_new_verify",
                data=form_data,
                headers={
                    "Referer": APP_URL + "/",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": self.sess.s.headers["User-Agent"],
                },
                timeout=20
            )
            result = r.json()
        except Exception as e:
            log(f"  ❌ 提交验证码失败: {e}")
            return None

        if int(result.get("errorCode", -1)) == 0:
            self.ticket = result.get("ticket", "")
            self.randstr = result.get("randstr", "")
            log(f"  ✅ 验证码验证通过")
            return result
        else:
            log(f"  ❌ 验证失败 errorCode={result.get('errorCode')} msg={result.get('errMessage', '')}")
            return result

    def solve(self) -> Optional[Dict]:
        log("\n🔍 开始本地求解验证码...")
        prehandle = self.get_prehandle()
        if not prehandle:
            return None
        sess_id = prehandle.get("sess", "")
        self.captcha_data = prehandle

        # 获取 TDC JS 和 pow 配置
        comm_cfg = prehandle.get("data", {}).get("comm_captcha_cfg", {})
        tdc_path = comm_cfg.get("tdc_path", "")
        pow_cfg = comm_cfg.get("pow_cfg", {})
        pow_prefix = pow_cfg.get("prefix", "")
        pow_md5 = pow_cfg.get("md5", "")
        tdc_js = self.sess.download_img(tdc_path).decode("utf-8", errors="replace") if tdc_path else ""

        bg, sprite = self.download_images(prehandle)
        if bg is None or sprite is None:
            return None
        ins_elem_cfg = prehandle.get("data", {}).get("dyn_show_info", {}).get("ins_elem_cfg")
        pieces = self.split_sprite(sprite, ins_elem_cfg)
        log(f"  🔪 已分割为 {len(pieces)} 个碎片")
        time.sleep(random.uniform(1.5, 3.0))

        for attempt in range(1, CAPTCHA_RETRIES + 1):
            positions = self.find_positions(bg, pieces)
            self.save_debug_images(bg, pieces, positions, os.path.join(SCRIPT_DIR, "debug_output"))
            log(f"  🎯 第{attempt}次匹配: 找到 {len(positions)}/{len(pieces)} 个")

            if len(positions) == len(pieces):
                self.coords = positions
                result = self.submit_captcha(positions, sess_id, tdc_js, pow_prefix, pow_md5)
                if result and int(result.get("errorCode", -1)) == 0:
                    return {
                        "clicks": "|".join(f"{x},{y}" for x, y in positions),
                        "ticket": self.ticket,
                        "randstr": self.randstr,
                    }
                if attempt < CAPTCHA_RETRIES:
                    log("  🔄 刷新验证码重试...")
                    time.sleep(random.uniform(2, 4))
                    prehandle = self.get_prehandle()
                    if prehandle:
                        sess_id = prehandle.get("sess", sess_id)
                        comm_cfg = prehandle.get("data", {}).get("comm_captcha_cfg", {})
                        pow_cfg = comm_cfg.get("pow_cfg", {})
                        pow_prefix = pow_cfg.get("prefix", pow_prefix)
                        pow_md5 = pow_cfg.get("md5", pow_md5)
                        bg, sprite = self.download_images(prehandle)
                        if bg is not None and sprite is not None:
                            pieces = self.split_sprite(sprite, prehandle.get("data", {}).get("dyn_show_info", {}).get("ins_elem_cfg"))
                    time.sleep(random.uniform(1, 2))
            else:
                if attempt < CAPTCHA_RETRIES:
                    time.sleep(random.uniform(0.5, 1.5))
                    continue
        return None

# ============================================================
# ddddocr 深度学习求解器（主用）
# ============================================================
class DdddOCRSolver(CaptchaSolver):
    def __init__(self, sess: Session):
        super().__init__(sess)
        self.ocr = None
        if DDDDOCR_OK:
            try:
                self.ocr = ddddocr.DdddOcr(det=False, show_ad=False)
                log("  🧠 ddddocr 深度学习模型加载成功")
            except Exception as e:
                log(f"  ⚠️ ddddocr 初始化失败: {e}，降级传统算法")

    def find_positions(self, bg: np.ndarray, pieces: List[np.ndarray]) -> List[Tuple[int, int]]:
        if not self.ocr:
            return super().find_positions(bg, pieces)

        positions = []
        bg_h, bg_w = bg.shape[:2]
        used_regions = []

        # 背景图转 bytes
        bg_rgb = cv2.cvtColor(bg, cv2.COLOR_BGR2RGB)
        bg_bytes = BytesIO()
        Image.fromarray(bg_rgb).save(bg_bytes, format='PNG')
        bg_bytes = bg_bytes.getvalue()

        remaining = []
        for i, piece in enumerate(pieces):
            if piece is None or piece.size == 0:
                remaining.append(i)
                continue

            # 碎片转 bytes
            has_alpha = len(piece.shape) == 3 and piece.shape[2] == 4
            if has_alpha:
                piece_img = Image.fromarray(cv2.cvtColor(piece, cv2.COLOR_BGRA2RGBA))
            else:
                piece_img = Image.fromarray(cv2.cvtColor(piece, cv2.COLOR_BGR2RGB))
            piece_buf = BytesIO()
            piece_img.save(piece_buf, format='PNG')
            piece_bytes = piece_buf.getvalue()

            try:
                res = self.ocr.detection(bg_bytes, piece_bytes)
                if res and 'target' in res:
                    x1, y1, x2, y2 = res['target']
                    cx, cy = (x1+x2)//2, (y1+y2)//2
                    score = res.get('rate', 0.0)

                    if 0 <= cx < bg_w and 0 <= cy < bg_h and score >= 0.3:
                        overlaps = any(cx < ux2 and cx > ux1 and cy < uy2 and cy > uy1
                                       for ux1, uy1, ux2, uy2 in used_regions)
                        if not overlaps:
                            positions.append((cx, cy))
                            used_regions.append((x1, y1, x2, y2))
                            log(f"  ✅ 碎片{i+1}: ({cx},{cy}) 置信度={score:.3f} [ddddocr]")
                            continue
                remaining.append(i)
            except Exception as e:
                log(f"  ⚠️ 碎片{i+1} ddddocr 异常: {e}")
                remaining.append(i)

        # ddddocr 没识别到的，用传统算法兜底
        if remaining:
            fallback_pieces = [pieces[i] for i in remaining]
            fallback_pos = super().find_positions(bg, fallback_pieces)
            for idx, pos in enumerate(fallback_pos):
                cx, cy = pos
                overlaps = any(cx < ux2 and cx > ux1 and cy < uy2 and cy > uy1
                               for ux1, uy1, ux2, uy2 in used_regions)
                if not overlaps:
                    orig_idx = remaining[idx]
                    positions.insert(orig_idx, pos)
                    used_regions.append((cx-40, cy-40, cx+40, cy+40))

        return positions

# ============================================================
# 签到主流程
# ============================================================
class RainyunSigner:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.sess     = Session(username=username)
        self.logged_in = False

    def login(self) -> bool:
        log("\n" + "=" * 50)
        log("🔐 登录雨云")
        log("=" * 50)
        if self.sess.load_cookies():
            info = self.sess.get("/user/")
            if info and info.get("code") == 200:
                log("  ✅ 缓存会话有效")
                self.logged_in = True
                return True
        payload = {"field": self.username, "password": self.password}
        result = self.sess.post("/user/login", data=payload)
        if not result:
            return False
        if result.get("code") == 200:
            log("  ✅ 登录成功")
            self.sess.save_cookies()
            self.logged_in = True
            return True
        log(f"  ❌ 登录失败: {result.get('message', '未知错误')}")
        return False

    def fetch_csrf(self) -> bool:
        result = self.sess.get("/user/csrf")
        if result and result.get("code") == 200:
            token = result.get("data", "")
            if token:
                self.sess.csrf_token = token
                self.sess.s.headers["X-CSRF-Token"] = token
                log("  🔑 CSRF Token 已获取")
                return True
        return False

    def check_sign_status(self) -> bool:
        log("\n📊 检查签到状态...")
        result = self.sess.get("/user/reward/tasks")
        if not result:
            return False
        for task in result.get("data", []):
            if task.get("Name") == "每日签到" and task.get("Status") == 2:
                log("  ✅ 今日已签到")
                return True
        log("  📌 今日未签到")
        return False

    def get_points(self) -> int:
        result = self.sess.get("/user/")
        if result and result.get("code") == 200:
            points = result.get("data", {}).get("Points", 0)
            log(f"  💰 当前积分: {points}（约 ¥{points/2000:.2f}）")
            return points
        return 0

    def withdraw(self) -> Tuple[bool, int]:
        log("\n" + "=" * 50)
        log("💸 检查提现条件")
        log("=" * 50)
        points = self.get_points()
        cfg = str(WITHDRAW_POINTS).strip().lower()
        if cfg == "all":
            if points < WITHDRAW_MIN_POINTS:
                log(f"  📌 积分不足最低提现门槛 {WITHDRAW_MIN_POINTS}，跳过")
                return False, 0
            amount = points
        else:
            try:
                amount = int(cfg)
            except ValueError:
                return False, 0
            if points < amount:
                log(f"  📌 当前积分 {points} 未达提现阈值 {amount}，跳过")
                return False, 0

        self.fetch_csrf()
        log(f"  💰 申请提现 {amount} 积分到 {WITHDRAW_TARGET}")
        result = self.sess.post(
            "/user/reward/withdraw",
            data={"points": amount, "target": WITHDRAW_TARGET},
            extra_headers={"X-CSRF-Token": self.sess.csrf_token},
        )
        if result and result.get("code") == 200:
            log("  ✅ 提现申请成功")
            self.get_points()
            return True, amount
        log(f"  ❌ 提现失败: {result.get('message', '') if result else '请求失败'}")
        return False, 0

    def do_checkin(self) -> Tuple[bool, str]:
        self.fetch_csrf()
        payload = {"task_name": "每日签到", "verifyCode": "", "vticket": "", "vrandstr": ""}
        try:
            resp = self.sess.s.post(API_BASE + "/user/reward/tasks", json=payload, timeout=20)
            result = resp.json()
        except Exception as e:
            return False, str(e)
        code = result.get("code")
        msg = result.get("message", "")
        if code == 200:
            return True, "签到成功"
        elif code == 10004:
            return False, "需要验证码"
        else:
            return False, msg or str(result)

    def sign_in_with_captcha(self) -> bool:
        """纯本地验证码识别，无任何远程打码服务"""
        log("\n" + "=" * 50)
        log("📅 执行签到（本地验证码识别）")
        log("=" * 50)
        self.fetch_csrf()

        # 优先 ddddocr，失败自动降级传统算法
        if DDDDOCR_OK:
            solver = DdddOCRSolver(self.sess)
        elif CV2_OK:
            solver = CaptchaSolver(self.sess)
            log("  ⚠️ ddddocr 不可用，使用 OpenCV 传统算法")
        else:
            log("  ❌ 未安装任何图像识别依赖，请安装 ddddocr 或 opencv-python")
            return False

        captcha_result = solver.solve()
        if not captcha_result:
            log("  ❌ 验证码识别失败")
            return False

        ticket = solver.ticket
        randstr = solver.randstr
        time.sleep(random.uniform(5, 8))

        # 提交签到
        payload = {
            "task_name": "每日签到",
            "verifyCode": "",
            "vticket": ticket,
            "vrandstr": randstr,
        }
        try:
            resp = self.sess.s.post(
                API_BASE + "/user/reward/tasks",
                json=payload,
                timeout=30,
                verify=False,
            )
            result = resp.json()
        except Exception as e:
            log(f"  ❌ 签到请求异常: {e}")
            return False

        if result.get("code") == 200:
            log("  ✅ 签到成功！")
            return True
        log(f"  ❌ 签到失败: {result.get('message', '')}")
        return False

    def run(self) -> Dict:
        result = self._run_sign_flow()
        if result["login_ok"]:
            ok, amount = self.withdraw()
            result["withdraw_ok"] = ok
            result["withdraw_amount"] = amount
            if ok:
                result["points"] = self.get_points()
        return result

    def _run_sign_flow(self) -> Dict:
        result = {
            "username": self.username,
            "login_ok": False,
            "sign_ok":  False,
            "points":   0,
            "withdraw_ok": False,
            "error":    "",
        }
        if not self.login():
            result["error"] = "登录失败"
            return result
        result["login_ok"] = True
        time.sleep(random.uniform(1, 3))

        points_before = self.get_points()
        if self.check_sign_status():
            result["sign_ok"] = True
            result["points"] = points_before
            return result

        log("\n" + "=" * 50)
        log("📅 执行签到")
        log("=" * 50)
        time.sleep(random.uniform(1, 3))
        ok, msg = self.do_checkin()
        if ok:
            result["sign_ok"] = True
            result["points"] = self.get_points()
            return result

        if "验证码" in msg or "captcha" in msg.lower():
            log("  🔄 触发验证码，启动本地识别...")
            if self.sign_in_with_captcha():
                result["sign_ok"] = True
                result["points"] = self.get_points()
                return result
            else:
                result["error"] = "验证码识别失败"
                result["points"] = self.get_points()
                return result

        result["error"] = f"签到失败: {msg}"
        result["points"] = self.get_points()
        return result

# ============================================================
# 入口
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="雨云自动签到 - 纯本地验证码版")
    parser.add_argument("-u", "--username")
    parser.add_argument("-p", "--password")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--cookies", action="store_true")
    args = parser.parse_args()
    ensure_dirs()

    account_list = []
    if args.username and args.password:
        # 命令行指定单账号
        account_list = [(args.username, args.username, args.password)]
    elif os.environ.get("RAINYUN"):
        # 环境变量配置（青龙推荐方式）
        try:
            account_list = parse_rainyun_accounts(os.environ["RAINYUN"])
        except ValueError as e:
            print(f"配置错误：{e}")
            sys.exit(1)
    else:
        print("未配置账号！请在青龙环境变量中设置 RAINYUN=账号#密码（多账号用 @ 分隔）")
        sys.exit(1)

    if not account_list:
        print("未配置有效账号，请检查环境变量 RAINYUN")
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"雨云签到 - 共 {len(account_list)} 个账号")
    print(f"{'='*50}")

    all_results = []
    for idx, (name, username, password) in enumerate(account_list, 1):
        print(f"\n[{idx}/{len(account_list)}] {name}")
        if idx > 1:
            delay = random.uniform(15, 25)
            print(f"账号间延迟 {delay:.1f}s...")
            time.sleep(delay)

        signer = RainyunSigner(username, password)
        if args.status:
            if signer.login():
                signer.check_sign_status()
                signer.get_points()
        elif args.cookies:
            if signer.sess.load_cookies():
                info = signer.sess.get("/user/")
                print("✅ Cookies 有效" if info and info.get("code")==200 else "❌ Cookies 失效")
            else:
                print("❌ 无 Cookies")
        else:
            result = signer.run()
            result["name"] = name
            all_results.append(result)
            print(f"  登录: {'✅' if result['login_ok'] else '❌'}")
            print(f"  签到: {'✅' if result['sign_ok']  else '❌'}")
            print(f"  积分: {result['points']}")
            if result.get("withdraw_ok"):
                print(f"  提现: ✅ {result.get('withdraw_amount',0)} 积分")
            if result["error"]:
                print(f"  错误: {result['error']}")

    if all_results:
        print(f"\n{'='*50}")
        print("签到汇总")
        print(f"{'='*50}")
        notify_lines = []
        for r in all_results:
            status = "✅" if r["sign_ok"] else "❌"
            print(f"  {status} {r['name']}  积分:{r['points']}  {r.get('error','')}")
            line = f"{r['name']}｜签到：{'成功' if r['sign_ok'] else '失败'}｜积分：{r['points']}"
            if r.get("error"):
                line += f"｜错误：{r['error']}"
            notify_lines.append(line)
        send_qinglong_notification("雨云自动签到", "\n".join(notify_lines))

    if any(not r["sign_ok"] for r in all_results):
        sys.exit(1)

if __name__ == "__main__":
    main()
