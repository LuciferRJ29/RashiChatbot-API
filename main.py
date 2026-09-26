import os
import time
import uuid
import json
import random
import asyncio
import re
import logging
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rashi_api")

BASE_URL = "https://www.free-ai-online.com"
START_SESSION_URL = f"{BASE_URL}/wp-json/mwai/v1/start_session"
SUBMIT_CHAT_URL = f"{BASE_URL}/wp-json/mwai-ui/v1/chats/submit"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/grok-4-free/",
    "Accept": "application/json, text/plain, */*",
}

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

class TigerSessionManager:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.session_id: Optional[str] = None
        self.nonce: Optional[str] = None
        self.last_init_time: float = 0.0
        self.client: Optional[httpx.AsyncClient] = None

    def get_client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return self.client

    async def get_valid_session(self, force_refresh: bool = False) -> tuple[Optional[str], Optional[str]]:
        now = time.time()
        if not force_refresh and self.session_id and self.nonce and (now - self.last_init_time < 600):
            return self.session_id, self.nonce

        async with self.lock:
            if not force_refresh and self.session_id and self.nonce and (time.time() - self.last_init_time < 600):
                return self.session_id, self.nonce

            client = self.get_client()
            headers = DEFAULT_HEADERS.copy()
            headers["Content-Type"] = "application/json"

            try:
                logger.info("Initializing fresh Tiger session from free-ai-online.com...")
                resp = await client.post(START_SESSION_URL, json={}, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success") or "sessionId" in data:
                        self.session_id = data.get("sessionId")
                        self.nonce = data.get("restNonce") or data.get("new_token")
                        self.last_init_time = time.time()
                        logger.info(f"Tiger session ready: {self.session_id[:8]}..., nonce: {self.nonce}")
                        return self.session_id, self.nonce
                logger.warning(f"Start session failed with code: {resp.status_code}")
            except Exception as e:
                logger.error(f"Error initializing Tiger session: {e}")

            return None, None

    async def generate_reply(self, prompt: str, system_prompt: str = "", model_key: str = "grok-4", history: List[Dict] = None) -> Optional[str]:
        cfg = MODEL_CONFIGS.get(model_key, MODEL_CONFIGS["grok-4"])
        client = self.get_client()

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

        req_headers = DEFAULT_HEADERS.copy()
        req_headers["Referer"] = cfg["referer"]
        req_headers["Content-Type"] = "application/json"
        req_headers["X-WP-Nonce"] = nonce

        for attempt in range(2):
            try:
                resp = await client.post(SUBMIT_CHAT_URL, json=payload, headers=req_headers)
                if resp.status_code == 200:
                    d = resp.json()
                    if d.get("success") and d.get("reply"):
                        rep = d.get("reply").strip().replace('"', '')
                        if rep.lower().startswith("rashi:"):
                            rep = rep[6:].strip()
                        return rep

                if resp.status_code in (401, 403) or (resp.status_code == 200 and not resp.json().get("success")):
                    logger.warning("Session expired or invalid nonce, refreshing session...")
                    session_id, nonce = await self.get_valid_session(force_refresh=True)
                    if session_id and nonce:
                        payload["session"] = session_id
                        req_headers["X-WP-Nonce"] = nonce
                        continue
            except Exception as e:
                logger.warning(f"Scraper chat attempt {attempt+1} error: {e}")

        return None


session_mgr = TigerSessionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await session_mgr.get_valid_session(force_refresh=True)
        logger.info("RashiChatbot-API: Tiger Session successfully pre-warmed on startup.")
    except Exception as e:
        logger.warning(f"Lifespan pre-warm notice: {e}")
    yield
    client = session_mgr.client
    if client and not client.is_closed:
        await client.aclose()

app = FastAPI(
    title="RashiChatbot AI API Backend",
    version="2.6.0",
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

    # 2. Main ultra-fast scraper (1.5s - Grok-4)
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
                "version": "2.6.0",
                "engine": "Tiger Protect Grok-4 (sub-2s latency)",
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


@app.get("/debug")
async def debug_endpoint():
    out = {"time": time.time()}
    client = httpx.AsyncClient(follow_redirects=True, timeout=12.0)
    
    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        r_get = await client.get("https://www.free-ai-online.com/grok-4-free/", headers=browser_headers)
        out["get_page"] = {"code": r_get.status_code, "server": r_get.headers.get("server"), "title": re.search(r"<title>(.*?)</title>", r_get.text).group(1) if re.search(r"<title>(.*?)</title>", r_get.text) else r_get.text[:100]}
    except Exception as e:
        out["get_page"] = {"error": str(e)}

    await client.aclose()
    return out


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
