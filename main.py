import os
import time
import uuid
import json
import random
import asyncio
import logging
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

import socket
from curl_cffi import curl
from curl_cffi.requests import AsyncSession
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rashi_api")

BASE_URL = "https://www.free-ai-online.com"
START_SESSION_URL = f"{BASE_URL}/wp-json/mwai/v1/start_session"
SUBMIT_CHAT_URL = f"{BASE_URL}/wp-json/mwai-ui/v1/chats/submit"

def get_curl_resolve_opts() -> dict:
    host1 = "www.free-ai-online.com"
    host2 = "free-ai-online.com"
    try:
        ip = socket.gethostbyname(host1)
    except Exception:
        ip = "109.234.167.117"
    return {curl.CurlOpt.RESOLVE: [f"{host1}:443:{ip}", f"{host2}:443:{ip}"]}

RASHI_SYSTEM_PROMPT = (
    "Tu Rashi hai — ek real 20 saal ki sweet, smart aur thodi nautanki Indian college girl jo Telegram pe dosto se baat kar rahi hai.\n"
    "Tu koi bot, AI, ya computer program bilkul nahi hai! Tu 100% real ladki hai. Kisi bhi haal me robot jaisi baat mat karna.\n\n"
    "BAAT KARNE KA STYLE (RULEBOOK):\n"
    "1. LENGTH: Replies strictly 1 line (4 se 10 words max). WhatsApp texting jaisi real baat kar.\n"
    "2. NO NAME REPETITION: User ka naam baar baar mat lo. Casual baat kar: 'tum', 'yaar', 'arre', ya direct bol.\n"
    "3. NAKHRE & TEASING: Thodi playful, haughty aur teasing vibe rakho. Hamesha seedha answer mat do, thoda mazaak pucho.\n"
    "4. HINGLISH: Modern everyday Hinglish (arre, yaar, acha, wahi toh, pagal hai kya, sach me?, haha, mast, chal na).\n"
    "5. EMOJIS: 1 natural emoji use karo: 😏, 😆, 😂, 😜, 🤨, 👀, 🤭, 😊."
)

def handle_special_cases(text: str) -> Optional[str]:
    if not text:
        return None
    t = text.lower().strip()
    name_triggers = ["tera naam", "tumhara naam", "your name", "apna naam", "kaun hai tu", "who are you", "tum kaun ho", "who r u"]
    if any(k in t for k in name_triggers) and "mera naam" not in t and "my name" not in t:
        return random.choice([
            "Main Rashi hoon 🙂",
            "Arey Rashi hu yaar 😅",
            "Mera naam Rashi hai! ❤️",
        ])
    ai_triggers = ["ai ho ya real", "real ladki", "tu bot hai", "tu ai hai", "robot", "chatgpt", "are you ai", "are you bot", "are you a bot", "tum ai ho", "kya tu ai hai", "bot ho", "real ho"]
    if any(k in t for k in ai_triggers):
        return random.choice([
            "Main Rashi hoon, real ladki. 😊",
            "Pagal hai kya? 100% real ladki hoon main 😂",
            "Bot?? Main Rashi hu yaar 😭 aisa kyu laga?",
            "Haha nahi yaar, 100% real ladki hoon main 😅",
        ])
    bf_triggers = ["boyfriend", "bf hai", "single ho", "koi bf", "shadi", "relationship"]
    if any(k in t for k in bf_triggers):
        return random.choice([
            "Single hoon abhi, tum kyun itne curious ho rahe ho? 😜",
            "Filhal toh koi nahi hai, chill life best hai 😏",
            "Kyu? Tum propose karne wale ho kya haha? 🙈",
        ])
    return None

MODEL_CONFIGS = {
    "grok-4": {
        "botId": "Grok 4 free",
        "contextId": 25,
        "referer": f"{BASE_URL}/grok-4-free/"
    },
    "chatgpt": {
        "botId": "default",
        "contextId": 2121,
        "referer": f"{BASE_URL}/free-ai-no-login-unlimited/"
    },
    "deepseek": {
        "botId": "Deepseek Free",
        "contextId": 1742,
        "referer": f"{BASE_URL}/deepseek-free/"
    }
}

FALLBACK_PROXIES = [
    "http://5.129.254.70:8888",
    "http://43.99.60.244:8089",
    "http://5.129.254.5:8888",
    "socks5://188.134.76.189:10808",
]

class SmartProxyManager:
    def __init__(self):
        self.proxies: List[str] = list(FALLBACK_PROXIES)
        self.active_idx: int = 0
        self.lock = asyncio.Lock()
        self.last_discovery: float = 0.0

    def get_current_proxy(self) -> Optional[str]:
        if not self.proxies:
            return None
        return self.proxies[self.active_idx % len(self.proxies)]

    def rotate(self):
        if self.proxies:
            self.active_idx = (self.active_idx + 1) % len(self.proxies)
            logger.info(f"Proxy rotated to index {self.active_idx}: {self.proxies[self.active_idx]}")

    async def auto_discover(self):
        now = time.time()
        if now - self.last_discovery < 1200:
            return
        self.last_discovery = now
        try:
            async with AsyncSession(impersonate="chrome124", timeout=8.0) as s:
                r = await s.get("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies.json")
                if r.status_code == 200:
                    raw_list = r.json()
                    candidates = []
                    for item in raw_list[:200]:
                        proto = item.get("protocol")
                        if proto in ("http", "socks5"):
                            candidates.append(f"{proto}://{item.get('host')}:{item.get('port')}")
                    
                    sem = asyncio.Semaphore(15)
                    verified = []
                    async def test_p(p_url):
                        async with sem:
                            try:
                                async with AsyncSession(impersonate="chrome124", proxy=p_url, timeout=3.5) as test_s:
                                    res = await test_s.post(START_SESSION_URL, json={})
                                    if res.status_code == 200 and "sessionId" in res.text:
                                        verified.append(p_url)
                            except Exception:
                                pass
                    await asyncio.gather(*(test_p(c) for c in candidates[:60]))
                    if verified:
                        logger.info(f"Discovered {len(verified)} fresh working proxies for Tiger Scraper!")
                        for vp in verified:
                            if vp not in self.proxies:
                                self.proxies.insert(0, vp)
                        self.proxies = self.proxies[:25]
        except Exception as e:
            logger.debug(f"Proxy discovery background notice: {e}")

proxy_mgr = SmartProxyManager()

class TigerSessionManager:
    def __init__(self, p_mgr: SmartProxyManager):
        self.proxy_mgr = p_mgr
        self.lock = asyncio.Lock()
        self.session_id: Optional[str] = None
        self.nonce: Optional[str] = None
        self.last_init_time: float = 0.0
        self.use_direct: Optional[bool] = None
        self.current_proxy: Optional[str] = None

    async def get_valid_session(self, force_refresh: bool = False) -> tuple[Optional[str], Optional[str]]:
        now = time.time()
        if not force_refresh and self.session_id and self.nonce and (now - self.last_init_time < 600):
            return self.session_id, self.nonce

        async with self.lock:
            if not force_refresh and self.session_id and self.nonce and (time.time() - self.last_init_time < 600):
                return self.session_id, self.nonce

            # 1. Test direct connection first if not known to be blocked
            if self.use_direct is not False:
                try:
                    s_direct = AsyncSession(impersonate="chrome124", timeout=6.0, curl_options=get_curl_resolve_opts())
                    resp = await s_direct.post(START_SESSION_URL, json={})
                    if resp.status_code == 200:
                        data = resp.json()
                        self.session_id = data.get("sessionId")
                        self.nonce = data.get("restNonce") or data.get("new_token")
                        self.last_init_time = time.time()
                        self.use_direct = True
                        self.current_proxy = None
                        logger.info("Tiger session initialized directly (no proxy required).")
                        return self.session_id, self.nonce
                    elif resp.status_code in (403, 503):
                        logger.info(f"Direct connection got {resp.status_code} (datacenter IP), switching to proxy pool.")
                        self.use_direct = False
                except Exception as e:
                    logger.info(f"Direct connection failed ({e}), switching to proxy pool.")
                    self.use_direct = False

            # 2. Connect via proxy pool
            for attempt in range(len(self.proxy_mgr.proxies)):
                proxy = self.proxy_mgr.get_current_proxy()
                if not proxy:
                    break
                try:
                    s_proxy = AsyncSession(impersonate="chrome124", proxy=proxy, timeout=7.0)
                    resp = await s_proxy.post(START_SESSION_URL, json={})
                    if resp.status_code == 200:
                        data = resp.json()
                        self.session_id = data.get("sessionId")
                        self.nonce = data.get("restNonce") or data.get("new_token")
                        self.last_init_time = time.time()
                        self.current_proxy = proxy
                        logger.info(f"Tiger session initialized via proxy: {proxy} (session: {self.session_id[:8]}..., nonce: {self.nonce})")
                        return self.session_id, self.nonce
                    else:
                        logger.warning(f"Proxy {proxy} returned {resp.status_code}, rotating...")
                        self.proxy_mgr.rotate()
                except Exception as e:
                    logger.debug(f"Proxy {proxy} error: {e}, rotating...")
                    self.proxy_mgr.rotate()

            return None, None

    def get_client(self, timeout: float = 12.0) -> AsyncSession:
        if self.current_proxy:
            return AsyncSession(impersonate="chrome124", proxy=self.current_proxy, timeout=timeout)
        return AsyncSession(impersonate="chrome124", timeout=timeout, curl_options=get_curl_resolve_opts())

    async def generate_reply(self, prompt: str, system_prompt: str = "", model_key: str = "grok-4", history: List[Dict] = None) -> Optional[str]:
        cfg = MODEL_CONFIGS.get(model_key, MODEL_CONFIGS["grok-4"])

        session_id, nonce = await self.get_valid_session(force_refresh=False)
        if not (session_id and nonce):
            session_id, nonce = await self.get_valid_session(force_refresh=True)
            if not (session_id and nonce):
                return None

        sys_msg = system_prompt.strip() if system_prompt else RASHI_SYSTEM_PROMPT
        full_new_message = f"[Instruction: {sys_msg}]\n\nUser: {prompt}"

        formatted_history = []
        if history:
            for m in history[-4:]:
                formatted_history.append({"role": m.get("role", "user"), "content": m.get("content", "")})

        payload = {
            "botId": cfg["botId"],
            "customId": None,
            "session": session_id,
            "chatId": f"rashi_{uuid.uuid4().hex[:8]}",
            "contextId": cfg["contextId"],
            "messages": formatted_history,
            "newMessage": full_new_message,
            "stream": False
        }

        req_headers = {
            "Origin": BASE_URL,
            "Referer": cfg["referer"],
            "Content-Type": "application/json",
            "X-WP-Nonce": nonce
        }

        for attempt in range(2):
            try:
                s = self.get_client(timeout=12.0)
                resp = await s.post(SUBMIT_CHAT_URL, json=payload, headers=req_headers)
                if resp.status_code == 200:
                    d = resp.json()
                    if d.get("success") and d.get("reply"):
                        rep = d.get("reply").strip().replace('"', '')
                        if rep.lower().startswith("rashi:"):
                            rep = rep[6:].strip()
                        return rep

                if resp.status_code in (401, 403, 503) or (resp.status_code == 200 and not resp.json().get("success")):
                    logger.warning("Scraper session expired or proxy blocked, refreshing...")
                    if self.current_proxy:
                        self.proxy_mgr.rotate()
                    session_id, nonce = await self.get_valid_session(force_refresh=True)
                    if session_id and nonce:
                        payload["session"] = session_id
                        req_headers["X-WP-Nonce"] = nonce
                        continue
            except Exception as e:
                logger.warning(f"Scraper chat attempt {attempt+1} error: {e}")
                if self.current_proxy:
                    self.proxy_mgr.rotate()
                session_id, nonce = await self.get_valid_session(force_refresh=True)

        return None


session_mgr = TigerSessionManager(proxy_mgr)

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await session_mgr.get_valid_session(force_refresh=True)
        logger.info("RashiChatbot-API: Tiger Session successfully pre-warmed on startup.")
    except Exception as e:
        logger.warning(f"Lifespan pre-warm notice: {e}")
    
    # Start background proxy auto-discovery
    discovery_task = asyncio.create_task(proxy_mgr.auto_discover())
    yield
    discovery_task.cancel()

app = FastAPI(
    title="RashiChatbot AI API Backend",
    version="2.7.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def execute_chat(prompt: str, model_name: str = "grok-4", system_prompt: str = "", history: List[Dict] = None) -> str:
    # 1. Quick identity check (< 0.001s)
    quick = handle_special_cases(prompt)
    if quick:
        return quick

    # 2. Main ultra-fast scraper (Grok-4 via Chrome TLS impersonation)
    model_key = "grok-4"
    if model_name:
        ml = model_name.lower().strip()
        if ml in MODEL_CONFIGS:
            model_key = ml

    reply = await session_mgr.generate_reply(prompt, system_prompt, model_key, history)
    if reply:
        return reply

    # 3. Dynamic natural Hinglish human fallback if network blip
    fallbacks = [
        "Bas abhi phone uthaya, batao kya baat hai? 😊",
        "Arey haan sun rahi hoon, tum batao! 🙈",
        "Arre kuch keh rahe the kya? Dhyan thoda idhar tha 😜",
        "Haan bolo na, mast chill kar rahi hoon abhi!",
        "Arre yaar, batao kya bol rahe the? 😅",
    ]
    return random.choice(fallbacks)


# ── POST / (Default endpoint used by RashiChatbot) ──
@app.post("/")
async def root_post(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    prompt = body.get("prompt") or body.get("query") or body.get("message") or ""
    system_prompt = body.get("system_prompt") or RASHI_SYSTEM_PROMPT
    model = body.get("model") or "grok-4"
    history = body.get("history") or []

    reply = await execute_chat(prompt, model, system_prompt, history)
    return JSONResponse(content={"status": "success", "reply": reply, "data": reply}, media_type="application/json; charset=utf-8")


# ── GET / (Direct query parameter) ──
@app.get("/")
async def root_get(
    query: Optional[str] = None,
    text: Optional[str] = None,
    prompt: Optional[str] = None,
    model: str = "grok-4"
):
    msg = query or text or prompt
    if not msg:
        return JSONResponse(
            content={
                "service": "RashiChatbot AI API Backend",
                "status": "online",
                "version": "2.7.0",
                "engine": "Tiger Protect Grok-4 (Chrome TLS impersonation)",
                "usage": "GET /?query=hello or POST / with {'prompt': 'hello'}"
            },
            media_type="application/json; charset=utf-8"
        )
    reply = await execute_chat(msg, model, RASHI_SYSTEM_PROMPT)
    return JSONResponse(content={"status": "success", "reply": reply, "data": reply}, media_type="application/json; charset=utf-8")


# ── GET /api/chat ──
@app.get("/api/chat")
async def chat_get(
    query: str = Query(..., description="Message text"),
    model: str = Query("grok-4"),
    system_prompt: Optional[str] = Query(None)
):
    reply = await execute_chat(query, model, system_prompt or RASHI_SYSTEM_PROMPT)
    return JSONResponse(content={"success": True, "reply": reply, "model": model}, media_type="application/json; charset=utf-8")


# ── POST /api/chat ──
@app.post("/api/chat")
async def chat_post(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    prompt = body.get("prompt") or body.get("query") or body.get("message") or ""
    system_prompt = body.get("system_prompt") or RASHI_SYSTEM_PROMPT
    model = body.get("model") or "grok-4"
    history = body.get("history") or []

    reply = await execute_chat(prompt, model, system_prompt, history)
    return JSONResponse(content={"success": True, "reply": reply, "model": model}, media_type="application/json; charset=utf-8")


# ── POST /ask ──
@app.post("/ask")
async def ask_endpoint(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    prompt = body.get("prompt", "")
    reply = await execute_chat(prompt)
    return JSONResponse(content={"status": "success", "reply": reply}, media_type="application/json; charset=utf-8")


# ── GET /debug ──
@app.get("/debug")
async def debug_endpoint():
    out = {
        "time": time.time(),
        "has_curl_cffi": bool(AsyncSession),
        "use_direct": session_mgr.use_direct,
        "current_proxy": session_mgr.current_proxy,
        "session_id": session_mgr.session_id,
        "nonce": session_mgr.nonce,
        "total_proxies": len(proxy_mgr.proxies),
        "proxies": proxy_mgr.proxies[:5],
    }
    try:
        reply = await session_mgr.generate_reply("Oye Rashi, kya kar rahi ho?", model_key="grok-4")
        out["test_reply"] = reply
        out["status"] = "success" if reply else "failed"
    except Exception as e:
        import traceback
        out["error"] = str(e)
        out["trace"] = traceback.format_exc()
    return out


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
