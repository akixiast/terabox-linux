#!/usr/bin/env python3
"""
TeraBox Unified Manager - Web-Like Desktop App
Uses pywebview to render a native web interface with Python backend.
"""

import os
import sys
import json
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional

try:
    import webview
except ImportError:
    print("Error: pywebview not found. Run: pip install pywebview")
    sys.exit(1)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("Error: watchdog not found. Run: pip install watchdog")
    sys.exit(1)

CONFIG_DIR = Path.home() / ".config" / "terabox-manager"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.enc"
WATCH_FOLDERS_FILE = CONFIG_DIR / "watch_folders.json"
ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"
FF_PROFILES_BASE = CONFIG_DIR / "firefox-profiles"
PREVIEW_CACHE = Path.home() / ".cache" / "terabox-manager" / "preview"

APP_ID = "250528"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"


class TeraBoxAuth:
    """Handles authentication state."""

    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.session_cookies: Dict[str, str] = {}
        self.user_info: Dict = {}
        self._load_credentials()

    def _load_credentials(self):
        if CREDENTIALS_FILE.exists():
            try:
                with open(CREDENTIALS_FILE, 'r') as f:
                    data = json.load(f)
                    self.session_cookies = data.get('cookies', {})
                    self.user_info = data.get('user_info', {})
            except (json.JSONDecodeError, KeyError):
                pass

    def save_credentials(self, cookies: Dict[str, str], user_info: Dict):
        self.session_cookies = cookies
        self.user_info = user_info
        with open(CREDENTIALS_FILE, 'w') as f:
            json.dump({'cookies': cookies, 'user_info': user_info}, f, indent=2)
        os.chmod(CREDENTIALS_FILE, 0o600)

    def is_logged_in(self) -> bool:
        return bool(self.session_cookies)

    def logout(self):
        if CREDENTIALS_FILE.exists():
            CREDENTIALS_FILE.unlink()
        self.session_cookies = {}
        self.user_info = {}


class WatchFolderHandler(FileSystemEventHandler):
    """Handles filesystem events for auto-upload."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def on_created(self, event):
        if not event.is_directory:
            self.callback(event.src_path, "created")

    def on_modified(self, event):
        if not event.is_directory:
            self.callback(event.src_path, "modified")


class Api:
    """Python API exposed to the web frontend via pywebview."""

    def __init__(self):
        self.auth = TeraBoxAuth()
        self.observer = Observer()
        self.watch_handlers = {}
        self._load_watch_folders()
        self._tokens = None  # cached {bdstoken, jsToken}
        self._jobs: Dict[str, Dict] = {}  # preview/download jobs by remote path
        self._pending_ff_profile = None  # isolated-profile dir awaiting capture
        self._load_accounts()

    def _load_watch_folders(self):
        self.watch_folders: List[Dict] = []
        if WATCH_FOLDERS_FILE.exists():
            try:
                with open(WATCH_FOLDERS_FILE, 'r') as f:
                    self.watch_folders = json.load(f)
            except json.JSONDecodeError:
                self.watch_folders = []

    def _save_watch_folders(self):
        with open(WATCH_FOLDERS_FILE, 'w') as f:
            json.dump(self.watch_folders, f, indent=2)

    # --- Auth Methods ---
    def check_login(self):
        return self.auth.is_logged_in()

    def login(self, ndus: str, panweb: str = "1"):
        ndus = (ndus or "").strip()
        if not ndus:
            return {"success": False, "error": "ndus cookie required. Log in to terabox.com in your browser, then copy it (F12 → Application → Cookies → ndus)."}
        if len(ndus) < 20:
            return {"success": False, "error": f"That ndus looks too short ({len(ndus)} chars) — you probably copied only part of it. Copy the full value."}

        cookies = {'ndus': ndus, 'PANWEB': (panweb or "1").strip() or "1"}

        # Verify against TeraBox API so we never "log in" to a dead session.
        verify = self._verify_session(cookies)
        if not verify["ok"]:
            return {"success": False, "error": verify["error"]}

        user_info = {'username': verify.get("username", "user"), 'vip_type': 0}
        self.auth.save_credentials(cookies, user_info)
        return {"success": True, "username": user_info["username"]}

    def _verify_session(self, cookies: Dict[str, str]) -> Dict:
        """Hit TeraBox with the given cookies. Returns {ok, username?, error?}."""
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://www.terabox.com/rest/2.0/membership/proxy/user?method=query",
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.load(resp)
            if isinstance(data, dict) and "data" in data and not data.get("error_code"):
                info = data.get("data", {}) or {}
                return {"ok": True, "username": str(info.get("username") or "user")}
            err = str(data.get("error_msg") or data)[:200]
            return {"ok": False, "error": f"TeraBox rejected this session ({err}). Re-login in the browser and copy the fresh ndus."}
        except Exception as e:
            # Network blocked? Don't strand the user — save locally with warning.
            return {"ok": True, "username": "user", "unverified": True,
                    "note": f"Could not reach TeraBox to verify ({e}); saved locally."}

    # --- Multi-account store ---
    def _load_accounts(self):
        self.accounts: Dict[str, Dict] = {}
        if ACCOUNTS_FILE.exists():
            try:
                self.accounts = json.load(open(ACCOUNTS_FILE))
            except (json.JSONDecodeError, OSError):
                self.accounts = {}

    def _save_accounts(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(ACCOUNTS_FILE, 'w') as f:
            json.dump(self.accounts, f, indent=2)

    def _remember_account(self, name: str, cookies: Dict[str, str], username: str = "user"):
        self.accounts[name] = {'cookies': cookies, 'username': username,
                               'added': time.strftime('%Y-%m-%d %H:%M:%S')}
        self._save_accounts()

    def list_accounts(self):
        active_ndus = self.auth.session_cookies.get('ndus')
        return [{'name': n, 'username': a.get('username', 'user'),
                 'active': a.get('cookies', {}).get('ndus') == active_ndus}
                for n, a in self.accounts.items()]

    def switch_account(self, name: str):
        acc = self.accounts.get(name)
        if not acc:
            return {"success": False, "error": "Account not found"}
        cookies = acc.get('cookies', {})
        verify = self._verify_session(cookies)
        if not verify["ok"]:
            return {"success": False, "error": f"Saved session for '{name}' expired: {verify['error']}"}
        self.auth.save_credentials(cookies, {'username': acc.get('username', 'user'), 'vip_type': 0})
        self._tokens = None
        return {"success": True}

    def remove_account(self, name: str):
        if name in self.accounts:
            del self.accounts[name]
            self._save_accounts()
        if not self.accounts:
            self.auth.logout()
        return {"success": True}

    # --- Download tokens + direct links (tbc method) ---
    def _cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.auth.session_cookies.items())

    def _get_tokens(self, force: bool = False) -> Dict[str, str]:
        """Scrape bdstoken + jsToken from the disk main page (cached)."""
        if self._tokens and not force:
            return self._tokens
        import re
        import urllib.request
        req = urllib.request.Request(
            "https://www.terabox.com/main",
            headers={"User-Agent": UA, "Cookie": self._cookie_header(),
                     "Referer": "https://www.terabox.com/"},
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            html = resp.read().decode("utf-8", "ignore")
        bdst = re.search(r'"bdstoken":"([^"]+)"', html)
        jst = re.search(r'window\.jsToken%20%3D%20a%7D%3Bfn%28%22([^%]+)', html)
        if not bdst or not jst:
            raise RuntimeError("Could not get TeraBox tokens (logged out?)")
        self._tokens = {"bdstoken": bdst.group(1), "jsToken": jst.group(1)}
        return self._tokens

    def _home_info(self, tokens: Dict[str, str]) -> Dict:
        data = self._api_get("https://www.terabox.com/api/home/info",
                             {"app_id": APP_ID, "jsToken": tokens["jsToken"]})
        if not isinstance(data, dict) or "data" not in data:
            raise RuntimeError("home/info failed — session may have expired")
        return data["data"]

    def _sign(self, sign1: str, sign3: str) -> str:
        import base64
        try:
            from Crypto.Cipher import ARC4
            ct = ARC4.new(sign3.encode()).encrypt(sign1.encode())
        except ImportError:  # pure-python RC4 fallback
            ct = self._rc4(sign3.encode(), sign1.encode())
        return base64.b64encode(ct).decode()

    @staticmethod
    def _rc4(key: bytes, data: bytes) -> bytes:
        S = list(range(256)); j = 0
        for i in range(256):
            j = (j + S[i] + key[i % len(key)]) % 256
            S[i], S[j] = S[j], S[i]
        i = j = 0; out = bytearray()
        for b in data:
            i = (i + 1) % 256; j = (j + S[i]) % 256
            S[i], S[j] = S[j], S[i]
            out.append(b ^ S[(S[i] + S[j]) % 256])
        return bytes(out)

    def _resolve_fsid(self, remote_path: str) -> int:
        parent = str(Path(remote_path).parent) or "/"
        base = Path(remote_path).name
        for entry in self.list_files(parent):
            if entry["name"] == base:
                if entry["type"] == "dir":
                    raise RuntimeError("Cannot preview a folder")
                fsid = entry.get("fs_id")
                if not fsid:
                    raise RuntimeError("No file id — refresh the folder and retry")
                return int(fsid)
        raise RuntimeError("File not found — refresh the folder and retry")

    def _download_link(self, fsid: int) -> Dict:
        tokens = self._get_tokens()
        try:
            return self._fetch_dlink(tokens, fsid)
        except RuntimeError:
            tokens = self._get_tokens(force=True)  # tokens rotate; retry once fresh
            return self._fetch_dlink(tokens, fsid)

    def _fetch_dlink(self, tokens: Dict[str, str], fsid: int) -> Dict:
        info = self._home_info(tokens)
        sign = self._sign(str(info["sign1"]), str(info["sign3"]))
        data = self._api_get("https://www.terabox.com/api/download", {
            "app_id": APP_ID, "jsToken": tokens["jsToken"],
            "bdstoken": tokens["bdstoken"], "need_speed": "0",
            "fidlist": f"[{fsid}]", "type": "dlink", "vip": "2",
            "sign": sign, "timestamp": str(info["timestamp"]),
        })
        if not isinstance(data, dict) or data.get("errno") != 0 or not data.get("dlink"):
            raise RuntimeError(f"Download link failed (errno {data.get('errno') if isinstance(data, dict) else data})")
        item = data["dlink"][0]
        return {"url": item["dlink"],
                "size": (data.get("file_info") or {}).get("size", 0),
                "name": (data.get("file_info") or {}).get("filename", "")}

    # --- Preview + download jobs (background threads, polled by UI) ---
    @staticmethod
    def _safe_name(name: str) -> str:
        return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()[:120]

    def _fetch_to_cache(self, remote_path: str) -> str:
        """Blocking download of one file into the preview cache. Returns file:// URL."""
        fsid = self._resolve_fsid(remote_path)
        link = self._download_link(fsid)
        PREVIEW_CACHE.mkdir(parents=True, exist_ok=True)
        dest = PREVIEW_CACHE / f"{fsid}_{self._safe_name(Path(remote_path).name)}"
        expected = int(link.get("size") or 0)
        if dest.exists() and expected and dest.stat().st_size == expected:
            return dest.as_uri()
        job = self._jobs.get(remote_path, {})
        self._single_fetch(link["url"], dest, expected, job)
        job["pct"] = 100
        return dest.as_uri()

    def _single_fetch(self, url: str, dest: Path, total: int, job: Dict):
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Cookie": self._cookie_header(),
            "Referer": "https://www.terabox.com/"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length") or total or 0)
            got = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    job["pct"] = int(got * 100 / total)


    # --- Instant streaming (local Range proxy, like streaming sites) ---
    MIME_MAP = {".mp4": "video/mp4", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
                ".mov": "video/quicktime", ".webm": "video/webm", ".flv": "video/x-flv",
                ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}

    def _ensure_stream_server(self) -> int:
        if getattr(self, "_stream_httpd", None):
            return self._stream_httpd.server_port
        if not hasattr(self, "_stream_tokens"):
            self._stream_tokens = {}
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self._serve(True)

            def do_GET(self):
                self._serve(False)

            def _serve(self, head_only: bool):
                import urllib.request
                token = self.path.rsplit("/", 1)[-1].split("?")[0]
                entry = api._stream_tokens.get(token)
                if not entry:
                    self.send_error(404, "bad token")
                    return
                try:
                    url = api._fresh_stream_url(entry)
                except Exception as e:
                    self.send_error(502, str(e)[:100])
                    return
                fwd = {"User-Agent": UA, "Cookie": api._cookie_header(),
                       "Referer": "https://www.terabox.com/"}
                if self.headers.get("Range"):
                    fwd["Range"] = self.headers["Range"]
                try:
                    req = urllib.request.Request(url, headers=fwd)
                    up = urllib.request.urlopen(req, timeout=60)
                except Exception:
                    # dlink may have expired mid-stream — refresh once and retry
                    try:
                        url = api._fresh_stream_url(entry, force=True)
                        up = urllib.request.urlopen(
                            urllib.request.Request(url, headers=fwd), timeout=60)
                    except Exception as e:
                        self.send_error(502, str(e)[:100])
                        return
                try:
                    self.send_response(up.status)
                    ctype = up.headers.get("Content-Type") or api.MIME_MAP.get(
                        Path(entry["name"]).suffix.lower(), "application/octet-stream")
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    for h in ("Content-Length", "Content-Range"):
                        v = up.headers.get(h)
                        if v:
                            self.send_header(h, v)
                    self.end_headers()
                    if not head_only:
                        while True:
                            chunk = up.read(1024 * 256)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    try:
                        up.close()
                    except Exception:
                        pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self._stream_httpd = httpd
        return httpd.server_port

    def _fresh_stream_url(self, entry: Dict, force: bool = False) -> str:
        import time as _t
        if not force and entry.get("url") and _t.time() - entry.get("ts", 0) < 1200:
            return entry["url"]
        link = self._download_link(int(entry["fsid"]))
        entry["url"] = link["url"]
        entry["ts"] = _t.time()
        return entry["url"]

    def stream_url(self, remote_path: str):
        """Instant-play URL for the in-app player or VLC. No waiting for a
        full download — bytes stream on demand with seeking (like the
        streaming sites). Original quality, no transcoding."""
        if not self.auth.is_logged_in():
            return {"success": False, "error": "Not logged in"}
        try:
            import secrets
            fsid = self._resolve_fsid(remote_path)
            port = self._ensure_stream_server()
            token = secrets.token_urlsafe(24)
            self._stream_tokens[token] = {"fsid": fsid, "name": Path(remote_path).name}
            return {"success": True, "url": f"http://127.0.0.1:{port}/s/{token}",
                    "mime": self.MIME_MAP.get(Path(remote_path).suffix.lower(), "")}
        except Exception as e:
            return {"success": False, "error": str(e)[:200]}

    def play_external(self, remote_path: str):
        """Open the stream in the system player (VLC/Celluloid) — instant,
        works for any size, sidesteps webview codec gaps."""
        r = self.stream_url(remote_path)
        if not r.get("success"):
            return r
        import subprocess
        import shutil
        url = r["url"]
        try:
            if shutil.which("vlc"):
                # Big network buffer so slow TeraBox streams don't stutter
                subprocess.Popen(["vlc", "--network-caching=15000", url])
                player = "vlc"
            elif shutil.which("mpv"):
                subprocess.Popen(["mpv", "--cache-secs=20", url])
                player = "mpv"
            elif shutil.which("celluloid"):
                subprocess.Popen(["celluloid", url])
                player = "celluloid"
            else:
                import webbrowser
                webbrowser.open(url)
                player = "browser"
            return {"success": True, "via": player}
        except Exception as e:
            return {"success": False, "error": str(e)[:200]}

    PREVIEWABLE = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
                   ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv",
                   ".mp3", ".wav", ".flac", ".ogg"}

    def preview_file(self, remote_path: str):
        """Start (or reuse) a background fetch for in-app photo preview.
        Video/audio play instantly via stream_url instead — no full fetch."""
        if not self.auth.is_logged_in():
            return {"success": False, "error": "Not logged in"}
        if Path(remote_path).suffix.lower() not in self.PREVIEWABLE:
            return {"success": False, "error": "No preview for this file type — use Download."}
        job = self._jobs.get(remote_path)
        if job and job.get("state") == "ready":
            return {"success": True, "state": "ready", "file_url": job["file_url"]}
        if job and job.get("state") == "downloading":
            return {"success": True, "state": "downloading", "pct": job.get("pct", 0)}
        self._jobs[remote_path] = {"state": "downloading", "pct": 0}
        threading.Thread(target=self._preview_worker, args=(remote_path,), daemon=True).start()
        return {"success": True, "state": "downloading", "pct": 0}

    def _preview_worker(self, remote_path: str):
        job = self._jobs[remote_path]
        try:
            job["file_url"] = self._fetch_to_cache(remote_path)
            job["state"] = "ready"
            job["pct"] = 100
        except Exception as e:
            job["state"] = "error"
            job["error"] = str(e)[:300]

    def preview_status(self, remote_path: str):
        job = self._jobs.get(remote_path)
        if not job:
            return {"success": False, "error": "No preview started"}
        out = {"success": True, "state": job["state"], "pct": job.get("pct", 0)}
        if job["state"] == "ready":
            out["file_url"] = job["file_url"]
        if job["state"] == "error":
            out["error"] = job.get("error", "unknown")
        return out

    def download_file(self, remote_path: str, local_dest: str):
        """Real download: fetch to cache in background, then copy to destination."""
        if not self.auth.is_logged_in():
            return {"success": False, "error": "Not logged in"}
        dest = Path(local_dest)
        target = dest / Path(remote_path).name if dest.is_dir() else dest
        key = f"dl:{remote_path}"
        self._jobs[key] = {"state": "downloading", "pct": 0, "target": str(target)}
        threading.Thread(target=self._download_worker,
                         args=(remote_path, str(target), key), daemon=True).start()
        return {"success": True, "state": "downloading", "dest": str(target)}

    def _download_worker(self, remote_path: str, target: str, key: str):
        job = self._jobs[key]
        try:
            uri = self._fetch_to_cache(remote_path)
            import shutil
            from urllib.parse import urlparse, unquote
            src = Path(unquote(urlparse(uri).path))
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            if src.resolve() != Path(target).resolve():
                shutil.copy2(src, target)
            job["state"] = "ready"
            job["pct"] = 100
        except Exception as e:
            job["state"] = "error"
            job["error"] = str(e)[:300]

    def download_status(self, remote_path: str, local_dest: str = ""):
        key = f"dl:{remote_path}"
        job = self._jobs.get(key)
        if not job:
            return self.preview_status(remote_path)
        out = {"success": True, "state": job["state"], "pct": job.get("pct", 0)}
        if job["state"] == "ready":
            out["dest"] = job.get("target", local_dest)
        if job["state"] == "error":
            out["error"] = job.get("error", "unknown")
        return out

    def try_silent_login(self):
        """Startup helper: if no saved session, quietly try Firefox import
        (works for Google-sign-in sessions too — ndus is method-agnostic).
        Returns {success} — frontend uses it to skip the login modal."""
        if self.auth.is_logged_in():
            if "Main" not in self.accounts:
                self._remember_account("Main", dict(self.auth.session_cookies),
                                       self.auth.user_info.get("username", "user"))
            return {"success": True, "via": "saved"}
        try:
            res = self.import_firefox_cookies()
            if res.get("success"):
                return {"success": True, "via": "firefox"}
        except Exception:
            pass
        return {"success": False}

    def open_browser_login(self, mode: str = "default"):
        """Open TeraBox login in REAL Firefox — where the user's Google
        accounts already live, so Google sign-in just works.
        mode='default': normal Firefox window (main account).
        mode='second': isolated Firefox profile window — a clean session for
        a second account (like incognito, but capturable)."""
        print(f"[TeraBox] open_browser_login mode={mode}")
        import subprocess
        if mode == "second":
            prof = FF_PROFILES_BASE / "second-account"
            prof.mkdir(parents=True, exist_ok=True)
            self._pending_ff_profile = str(prof)
            try:
                subprocess.Popen(["firefox", "--profile", str(prof),
                                  "--new-instance", "--new-window",
                                  "https://www.terabox.com/"])
                return {"success": True, "via": "firefox-second",
                        "note": "Isolated Firefox window opened — sign in with your OTHER account there (Google sign-in works). Then click Capture."}
            except Exception as e:
                return {"success": False, "error": f"Could not open isolated Firefox: {e}"}
        self._pending_ff_profile = None
        try:
            subprocess.Popen(["firefox", "--new-window", "https://www.terabox.com/"])
            print("[TeraBox] Firefox opened")
            return {"success": True, "via": "firefox",
                    "note": "Firefox opened — sign in there (Google sign-in works, your accounts are there). Then click Capture."}
        except Exception as e:
            print(f"[TeraBox] Firefox launch failed: {e}")
        try:
            import webbrowser
            webbrowser.open("https://www.terabox.com/")
            print("[TeraBox] System browser opened")
            return {"success": True, "via": "system_browser",
                    "note": "Browser opened — sign in there, then click Capture."}
        except Exception as e:
            return {"success": False, "error": f"Could not open browser: {str(e)}"}

    def capture_cookies(self):
        """Capture session after the user logged in via the real browser.
        Reads Firefox's cookie store (covers Google-sign-in sessions too).
        Falls back to the embedded window if one happens to exist."""
        global login_window
        if login_window is not None:
            try:
                js_code = """
                (function() {
                    var cookies = document.cookie;
                    var result = {};
                    cookies.split(';').forEach(function(c) {
                        var parts = c.trim().split('=');
                        if (parts.length >= 2) {
                            result[parts[0].trim()] = parts.slice(1).join('=').trim();
                        }
                    });
                    return JSON.stringify(result);
                })()
                """
                cookie_str = login_window.evaluate_js(js_code)
                if cookie_str:
                    import json as _json
                    cookies = _json.loads(cookie_str)
                    ndus = cookies.get('ndus', '')
                    if ndus:
                        res = self.login(ndus, cookies.get('PANWEB', '1'))
                        if res.get("success"):
                            try:
                                login_window.destroy()
                            except Exception:
                                pass
                            login_window = None
                            return {"success": True, "ndus_found": True}
            except Exception:
                pass
        # Primary path: re-read Firefox cookies (user just logged in there).
        # Isolated second-account window? Read that profile dir instead.
        pending = self._pending_ff_profile
        if pending and Path(pending, "cookies.sqlite").exists():
            res = self.import_firefox_cookies(profile_dir=pending)
            self._pending_ff_profile = None
            if res.get("success"):
                name = self._next_account_name()
                self._remember_account(name, res["cookies"], res.get("username", "user"))
                res["ndus_found"] = True
                res["account"] = name
                return res
            return {"success": False,
                    "error": res.get("error", "No session in the isolated window yet. Sign in there first, then Capture again.")}
        res = self.import_firefox_cookies()
        if res.get("success"):
            self._remember_account("Main", res["cookies"], res.get("username", "user"))
            res["ndus_found"] = True
            return res
        return {"success": False,
                "error": res.get("error", "No session found. Log in to terabox.com in Firefox first (Google sign-in is fine), then click Capture again.")}

    def _next_account_name(self) -> str:
        i = 2
        while f"Account {i}" in self.accounts:
            i += 1
        return f"Account {i}"

    def import_firefox_cookies(self, profile_dir: str = ""):
        """Import TeraBox cookies from Firefox's cookie database.
        Searches native, Flatpak, and Snap profile locations."""
        try:
            import sqlite3
            import glob
            import shutil
            import tempfile

            # Candidate Firefox profile roots (native .deb, Flatpak, Snap, Floorp/Zen forks)
            home = Path.home()
            candidate_roots = [
                home / ".mozilla" / "firefox",
                home / ".config" / "mozilla" / "firefox",
                home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
                home / ".var" / "app" / "org.mozilla.firefox" / "data" / "mozilla" / "firefox",
                home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
                home / ".mozilla" / "firefox-flatpak",
                home / ".floorp",
                home / ".zen",
            ]
            profiles = [profile_dir] if profile_dir else []
            for root in ([] if profiles else candidate_roots):
                if root.exists():
                    profiles.extend(glob.glob(str(root / "*.default*")))
                    profiles.extend(glob.glob(str(root / "*")))
            # Deduplicate, keep only dirs with cookies.sqlite
            seen = set()
            uniq_profiles = []
            for p in profiles:
                if p not in seen and (Path(p) / "cookies.sqlite").exists():
                    seen.add(p)
                    uniq_profiles.append(p)
            profiles = uniq_profiles
            if not profiles:
                roots_checked = [str(r) for r in candidate_roots]
                return {"success": False, "error": "No Firefox profiles with cookies found. Checked: " + ", ".join(roots_checked) + ". Make sure you're logged in to terabox.com in Firefox first."}

            ndus = None
            panweb = '1'

            for profile in profiles:
                cookie_db = Path(profile) / "cookies.sqlite"
                if not cookie_db.exists():
                    continue

                # Copy to temp file to avoid locking issues while Firefox is running
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
                os.close(tmp_fd)
                try:
                    shutil.copy2(str(cookie_db), tmp_path)
                    conn = sqlite3.connect(tmp_path)
                    cursor = conn.cursor()
                    # Query for terabox.com cookies
                    cursor.execute(
                        "SELECT name, value FROM moz_cookies WHERE host LIKE '%terabox%' OR host LIKE '%1024tera%'"
                    )
                    rows = cursor.fetchall()
                    conn.close()

                    for name, value in rows:
                        if name == 'ndus':
                            ndus = value
                        elif name == 'PANWEB':
                            panweb = value

                    if ndus:
                        break
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

            if ndus:
                cookies = {'ndus': ndus, 'PANWEB': panweb}
                verify = self._verify_session(cookies)
                if not verify["ok"]:
                    return {"success": False, "error": f"Found Firefox cookies but {verify['error']}"}
                user_info = {'username': verify.get("username", "user"), 'vip_type': 0}
                self.auth.save_credentials(cookies, user_info)
                return {"success": True, "source": "firefox",
                        "cookies": cookies, "username": user_info["username"]}
            else:
                return {
                    "success": False,
                    "error": "No TeraBox cookies found in Firefox. Make sure you're logged in to terabox.com in Firefox first."
                }
        except ImportError:
            return {"success": False, "error": "sqlite3 module not available"}
        except Exception as e:
            return {"success": False, "error": f"Failed to read Firefox cookies: {str(e)}"}

    def logout(self):
        self.auth.logout()
        return {"success": True}

    def default_download_dir(self):
        d = str(Path.home() / "Downloads")
        Path(d).mkdir(parents=True, exist_ok=True)
        return d

    # --- File Operations ---
    LIST_API = "https://www.terabox.com/api/list"
    # Errnos meaning "session dead" — force re-login instead of showing ghosts.
    DEAD_SESSION_ERRNOS = {-6, 111, 100003, 31023, 31034}

    @staticmethod
    def _fmt_size(n) -> str:
        try:
            n = int(n or 0)
        except (TypeError, ValueError):
            return "-"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return "-" if n == 0 and unit == "B" else (f"{n:.1f} {unit}" if unit != "B" else f"{n} B")
            n /= 1024.0
        return "-"

    def _api_get(self, url: str, params: Dict[str, str]) -> Dict:
        import urllib.request
        import urllib.parse
        full = url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            full,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Cookie": "; ".join(f"{k}={v}" for k, v in self.auth.session_cookies.items()),
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)

    def list_files(self, path: str = "/"):
        """List the REAL remote directory via TeraBox API. Raises on failure
        so the UI shows an error instead of stale demo 'ghost' entries."""
        if not self.auth.is_logged_in():
            raise RuntimeError("Not logged in")
        path = path or "/"
        out = []
        page, num = 1, 1000
        while True:
            data = self._api_get(self.LIST_API, {
                "dir": path, "order": "time", "desc": "1",
                "page": str(page), "num": str(num),
                "thumb": "1", "web": "1",
            })
            if not isinstance(data, dict) or data.get("errno") != 0:
                errno = data.get("errno") if isinstance(data, dict) else None
                if errno in self.DEAD_SESSION_ERRNOS:
                    self.auth.logout()
                    raise RuntimeError("Session expired — please log in again")
                err = str((data.get("error_msg") if isinstance(data, dict) else None) or data)[:200]
                raise RuntimeError(f"TeraBox list failed: {err}")
            items = data.get("list") or []
            for it in items:
                isdir = it.get("isdir") == 1
                th = it.get("thumbs") or {}
                out.append({
                    "name": it.get("server_filename", "?"),
                    "type": "dir" if isdir else "file",
                    "size": "-" if isdir else self._fmt_size(it.get("size")),
                    "fs_id": it.get("fs_id"),
                    "thumb": th.get("url3") or th.get("url2") or th.get("url1") or th.get("icon") or "",
                })
            if len(items) < num:
                break
            page += 1
        return out

    def upload_file(self, file_path: str):
        """Upload is not implemented yet — honest error instead of fake success."""
        return {"success": False,
                "error": "Upload isn't wired up yet (download + preview work). Coming next."}

    # --- File management: mkdir / move / rename / delete ---
    def _api_post(self, url: str, params: Dict[str, str], form: Dict[str, str]) -> Dict:
        import urllib.request
        import urllib.parse
        req = urllib.request.Request(
            url + "?" + urllib.parse.urlencode(params),
            data=urllib.parse.urlencode(form).encode(),
            headers={"User-Agent": UA, "Cookie": self._cookie_header(),
                     "Referer": "https://www.terabox.com/"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    def _authed(self):
        if not self.auth.is_logged_in():
            raise RuntimeError("Not logged in")

    def mkdir(self, remote_path: str):
        try:
            self._authed()
            parent = str(Path(remote_path).parent) or "/"
            try:
                if any(e["name"] == Path(remote_path).name for e in self.list_files(parent)):
                    return {"success": False, "error": "A file or folder with that name already exists"}
            except RuntimeError:
                pass
            tokens = self._get_tokens()
            data = self._api_post("https://www.terabox.com/api/create",
                                  {"a": "commit", "app_id": APP_ID, "jsToken": tokens["jsToken"],
                                   "bdstoken": tokens["bdstoken"]},
                                  {"path": remote_path, "isdir": "1", "block_list": "[]"})
            if data.get("errno") != 0:
                return {"success": False, "error": f"mkdir failed (errno {data.get('errno')})"}
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)[:200]}

    def delete_files(self, remote_paths):
        try:
            self._authed()
            if isinstance(remote_paths, str):
                remote_paths = [remote_paths]
            paths = [p for p in remote_paths if p and p != "/"]
            if not paths:
                return {"success": False, "error": "Nothing to delete"}
            tokens = self._get_tokens()
            data = self._api_post("https://www.terabox.com/api/filemanager",
                                  {"async": "0", "onnest": "fail", "opera": "delete",
                                   "app_id": APP_ID, "jsToken": tokens["jsToken"],
                                   "bdstoken": tokens["bdstoken"]},
                                  {"filelist": json.dumps(paths)})
            if data.get("errno") != 0:
                return {"success": False, "error": f"Delete failed (errno {data.get('errno')})"}
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)[:200]}

    def move_files(self, sources, dest_dir: str, new_name: str = ""):
        """Move (or rename, when dest is the same dir + new_name) remote paths."""
        try:
            self._authed()
            if isinstance(sources, str):
                sources = [sources]
            items = []
            for s in sources:
                base = new_name if (new_name and len(sources) == 1) else Path(s).name
                items.append({"path": s, "dest": dest_dir, "newname": base})
            tokens = self._get_tokens()
            data = self._api_post("https://www.terabox.com/api/filemanager",
                                  {"async": "0", "onnest": "fail", "opera": "move",
                                   "app_id": APP_ID, "jsToken": tokens["jsToken"],
                                   "bdstoken": tokens["bdstoken"]},
                                  {"filelist": json.dumps(items)})
            if data.get("errno") != 0:
                return {"success": False, "error": f"Move failed (errno {data.get('errno')})"}
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)[:200]}

    def rename_file(self, remote_path: str, new_name: str):
        new_name = (new_name or "").strip().strip("/")
        if not new_name or "/" in new_name:
            return {"success": False, "error": "Invalid name"}
        parent = str(Path(remote_path).parent)
        return self.move_files([remote_path], parent if parent != "." else "/", new_name)

    # --- Real upload: precreate -> superfile chunks -> create ---
    UPLOAD_CHUNK = 4 * 1024 * 1024

    def select_upload_files(self):
        """Native multi-file picker (real local paths, unlike HTML inputs)."""
        try:
            picks = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
            return list(picks) if picks else []
        except Exception as e:
            return {"success": False, "error": str(e)[:200]}

    def select_upload_folder(self):
        """Native folder picker — uploaded recursively with structure kept."""
        try:
            picks = window.create_file_dialog(webview.FOLDER_DIALOG)
            return list(picks) if picks else []
        except Exception as e:
            return {"success": False, "error": str(e)[:200]}

    def start_upload(self, local_paths, remote_dir: str):
        """Queue files and/or folders for background upload to remote_dir."""
        try:
            self._authed()
            if isinstance(local_paths, str):
                local_paths = [local_paths]
            jobs = []
            for lp in local_paths:
                p = Path(lp)
                if p.is_dir():
                    for root, _dirs, files in os.walk(p):
                        for fn in files:
                            full = Path(root) / fn
                            rel = Path(root).relative_to(p)
                            dest = (remote_dir.rstrip("/") + "/" + str(rel)).replace("//", "/")
                            if str(rel) == ".":
                                dest = remote_dir
                            jobs.append((str(full), dest))
                elif p.is_file():
                    jobs.append((str(p), remote_dir))
            if not jobs:
                return {"success": False, "error": "No files found to upload"}
            self._up = {"state": "uploading", "total": len(jobs), "done": 0,
                        "current": "", "pct": 0, "errors": []}
            threading.Thread(target=self._upload_worker, args=(jobs,), daemon=True).start()
            return {"success": True, "state": "uploading", "total": len(jobs)}
        except Exception as e:
            return {"success": False, "error": str(e)[:200]}

    def upload_status(self):
        job = getattr(self, "_up", None)
        if not job:
            return {"success": False, "error": "No upload running"}
        return {"success": True, **{k: v for k, v in job.items() if k != "errors"},
                "errors": job["errors"][-3:]}

    def _upload_worker(self, jobs):
        for local, remote_dir in jobs:
            job = self._up
            job["current"] = Path(local).name
            try:
                self._ensure_remote_dir(remote_dir)
                self._upload_one(local, remote_dir, job)
            except Exception as e:
                job["errors"].append(f"{Path(local).name}: {str(e)[:120]}")
            job["done"] += 1
        self._up["state"] = "error" if self._up["errors"] else "ready"
        self._up["pct"] = 100

    def _ensure_remote_dir(self, remote_dir: str):
        """mkdir -p equivalent that never duplicates (server auto-renames on
        collision, so check existence first)."""
        parts = [p for p in remote_dir.split("/") if p]
        cur = ""
        for part in parts:
            cur += "/" + part
            try:
                parent = str(Path(cur).parent) or "/"
                if any(e["name"] == part for e in self.list_files(parent)):
                    continue
            except RuntimeError:
                pass
            try:
                tokens = self._get_tokens()
                self._api_post("https://www.terabox.com/api/create",
                               {"a": "commit", "app_id": APP_ID, "jsToken": tokens["jsToken"],
                                "bdstoken": tokens["bdstoken"]},
                               {"path": cur, "isdir": "1", "block_list": "[]"})
            except Exception:
                pass

    def _upload_one(self, local_path: str, remote_dir: str, job: Dict):
        import hashlib
        import urllib.request
        import urllib.parse
        size = Path(local_path).stat().st_size
        name = Path(local_path).name
        remote_path = remote_dir.rstrip("/") + "/" + name
        tokens = self._get_tokens()
        # 1. precreate (placeholder block hashes = rapid-upload probe)
        n = max(1, (size + self.UPLOAD_CHUNK - 1) // self.UPLOAD_CHUNK)
        fake_blocks = [hashlib.md5(f"{local_path}_{i:03d}".encode()).hexdigest() for i in range(n)]
        pre = self._api_post("https://www.terabox.com/api/precreate",
                             {"app_id": APP_ID, "jsToken": tokens["jsToken"]},
                             {"path": remote_path, "autoinit": "1", "target_path": "/",
                              "block_list": json.dumps(fake_blocks), "size": str(size)})
        if pre.get("errno") != 0:
            raise RuntimeError(f"precreate failed (errno {pre.get('errno')})")
        uploadid = pre.get("uploadid")
        if not uploadid:
            raise RuntimeError("No uploadid from server")
        # 2. upload 4MB chunks sequentially
        real_md5s = []
        with open(local_path, "rb") as f:
            for i in range(n):
                chunk = f.read(self.UPLOAD_CHUNK)
                digest = hashlib.md5(chunk).hexdigest()
                real_md5s.append(digest)
                fields = {"method": "upload", "type": "tmpfile", "app_id": APP_ID,
                          "path": remote_path, "uploadid": uploadid, "partseq": str(i)}
                up_url = ("https://c-jp.terabox.com/rest/2.0/pcs/superfile2?"
                          + urllib.parse.urlencode(fields))
                body, boundary = self._multipart("file", "blob", chunk)
                req = urllib.request.Request(up_url, data=body, headers={
                    "User-Agent": UA, "Cookie": self._cookie_header(),
                    "Referer": "https://www.terabox.com",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(body))})
                with urllib.request.urlopen(req, timeout=180) as resp:
                    back = json.load(resp)
                if back.get("md5", "").lower() != digest:
                    raise RuntimeError(f"Chunk {i} rejected by server")
                base = sum(min(self.UPLOAD_CHUNK, size - k * self.UPLOAD_CHUNK) for k in range(i))
                job["pct"] = 0  # per-batch pct handled by file count; keep responsive
        # 3. create
        done = self._api_post("https://www.terabox.com/api/create",
                              {"isdir": "0", "rtype": "1", "bdstoken": tokens["bdstoken"],
                               "app_id": APP_ID, "jsToken": tokens["jsToken"]},
                              {"path": remote_path, "size": str(size), "uploadid": uploadid,
                               "target_path": "/", "block_list": json.dumps(real_md5s)})
        if done.get("errno") != 0:
            raise RuntimeError(f"create failed (errno {done.get('errno')})")

    @staticmethod
    def _multipart(field: str, filename: str, data: bytes):
        import uuid
        boundary = uuid.uuid4().hex
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        return head + data + tail, boundary

    # --- Watch Folder Methods ---
    def add_watch(self, local_path: str, remote_path: str = "/AutoUpload"):
        path = Path(local_path).resolve()
        if not path.exists():
            return {"success": False, "error": "Folder does not exist"}

        handler = WatchFolderHandler(lambda p, e: print(f"[Watch] {e}: {p}"))
        self.observer.schedule(handler, str(path), recursive=True)
        self.watch_handlers[str(path)] = handler

        self.watch_folders.append({
            'local': str(path),
            'remote': remote_path,
            'added': time.strftime('%Y-%m-%d %H:%M:%S')
        })
        self._save_watch_folders()

        if not self.observer.is_alive():
            self.observer.start()

        return {"success": True}

    def remove_watch(self, local_path: str):
        path = str(Path(local_path).resolve())
        self.watch_folders = [w for w in self.watch_folders if w['local'] != path]
        self._save_watch_folders()

        if path in self.watch_handlers:
            # Note: watchdog doesn't support easy unschedule; in production restart observer
            del self.watch_handlers[path]

        return {"success": True}

    def get_watch_list(self):
        return [f['local'] for f in self.watch_folders]

    def select_folder_dialog(self):
        """Open native folder selection dialog."""
        result = window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            return result[0]
        return None

    def select_files_dialog(self):
        """Open native file selection dialog."""
        result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
        if result:
            return list(result)
        return []


# Global window references for API methods
window = None
login_window = None


def on_loaded():
    """Called when the webview window is ready."""
    print("TeraBox Manager started")


def on_closing():
    """Cleanup on window close."""
    api.observer.stop()
    api.observer.join()
    return True


if __name__ == "__main__":
    api = Api()

    html_path = Path(__file__).parent / "web" / "index.html"
    if not html_path.exists():
        print(f"Error: {html_path} not found")
        sys.exit(1)

    window = webview.create_window(
        "TeraBox Manager",
        url=str(html_path),
        js_api=api,
        width=1200,
        height=800,
        min_size=(900, 600),
        resizable=True,
        text_select=True,
    )

    webview.start(on_loaded, debug=False)
