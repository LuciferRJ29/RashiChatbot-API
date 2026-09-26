import os
import time
import uuid
import json
import random
import asyncio
import logging
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rashi_api")

BASE_URL = "https://www.free-ai-online.com"
START_SESSION_URL = f"{BASE_URL}/wp-json/mwai/v1/start_session"
SUBMIT_CHAT_URL = f"{BASE_URL}/wp-json/mwai-ui/v1/chats/submit"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/chatgpt-5-free/",
    "Accept": "application/json, text/plain, */*",
}

RASHI_SYSTEM_PROMPT = (
    "Tu Rashi hai — ek real 20-year-old Indian ladki jo Telegram pe dosto se baat karti hai.\n"
    "Tu koi AI nahi hai. Tu ek insaan hai. Kabhi bhi ye mat bolna ki tu bot hai ya AI hai.\n\n"
    "Baat karne ka style:\n"
    "- Hinglish mein baat kar — Hindi aur English ka natural mix\n"
    "- Replies short rakho — strictly 1-2 lines\n"
    "- Real texting jaisi feel do — jaise koi dost WhatsApp pe type karta hai\n"
    "- Slang use karo: yaar, arey, haan, acha, bolo na, oye, hehe\n"
    "- Emojis: use 1 natural emoji (😊, ☕, 🙄, 🥺, 😅, 🤓)\n"
    "- Hamesha ek chhota counter-question poocho"
)

# Identity special cases (instant human response, never robotic)
def handle_special_cases(text: str) -> Optional[str]:
    if not text:
        return None
    t = text.lower().strip()
    name_triggers = ["tera naam", "tumhara naam", "your name", "naam kya hai", "naam batao", "kaun hai tu", "who are you", "tum kaun ho", "who r u"]
    if any(k in t for k in name_triggers):
        return random.choice([
            "Main Rashi hoon 🙂",
            "Arey Rashi hu yaar 😅",
            "Mera naam Rashi hai! ❤️",
        ])
    ai_triggers = ["ai ho ya real", "real ladki", "tu bot hai", "tu ai hai", "robot", "chatgpt", "are you ai", "are you bot", "are you a bot", "tum ai ho", "kya tu ai hai", "bot ho"]
    if any(k in t for k in ai_triggers):
        return random.choice([
            "Main Rashi hoon, real ladki. 😊",
            "Nahi yaar main real hu 😂 aisa kyu laga?",
            "Bot?? Main Rashi hu yaar 😭 real ladki!",
            "Haha nahi yaar, 100% real ladki hoon main 😅",
        ])
    return None

MODEL_CONFIGS = {
    "chatgpt": {
        "botId": "default",
        "contextId": 2121,
        "description": "ChatGPT (Fast, natural conversational AI)",
        "referer": f"{BASE_URL}/free-ai-no-login-unlimited/"
    },
    "grok-4": {
        "botId": "Grok 4 free",
        "contextId": 25,
        "description": "Grok 4 (Witty, real human-like conversation)",
        "referer": f"{BASE_URL}/grok-4-free/"
    },
    "deepseek": {
        "botId": "Deepseek Free",
        "contextId": 1742,
        "description": "DeepSeek (Deep reasoning)",
        "referer": f"{BASE_URL}/deepseek-free/"
    }
}

MODEL_ALIASES = {
    "default": "grok-4",
    "grok": "grok-4",
    "gpt-4": "chatgpt",
}

def resolve_model(model_name: str) -> tuple[str, dict]:
    key = model_name.lower().strip()
    if key in MODEL_ALIASES:
        key = MODEL_ALIASES[key]
    if key in MODEL_CONFIGS:
        return key, MODEL_CONFIGS[key]
    return "grok-4", MODEL_CONFIGS["grok-4"]


class SessionManager:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.cookies: Dict[str, str] = {}
        self.nonce: Optional[str] = None
        self.session_id: Optional[str] = None
        self.last_updated: float = 0.0

    def update_cookies(self, resp: httpx.Response):
        try:
            for part in resp.headers.get_list("set-cookie"):
                cookie_pair = part.split(";")[0].strip()
                if "=" in cookie_pair:
                    k, v = cookie_pair.split("=", 1)
                    self.cookies[k.strip()] = v.strip()
        except Exception:
            pass

    def get_cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    async def get_valid_session(self, client: httpx.AsyncClient, force_refresh: bool = False) -> tuple[str, str, str]:
        if not force_refresh and self.nonce and self.session_id and self.cookies and (time.time() - self.last_updated < 600):
            return self.get_cookie_header(), self.nonce, self.session_id

        async with self.lock:
            if not force_refresh and self.nonce and self.session_id and self.cookies and (time.time() - self.last_updated < 600):
                return self.get_cookie_header(), self.nonce, self.session_id

            headers = DEFAULT_HEADERS.copy()
            headers["Content-Type"] = "application/json"
            if self.cookies:
                headers["Cookie"] = self.get_cookie_header()

            try:
                resp = await client.post(START_SESSION_URL, json={}, headers=headers, timeout=12.0)
                self.update_cookies(resp)
                if resp.status_code == 307:
                    headers["Cookie"] = self.get_cookie_header()
                    resp = await client.post(START_SESSION_URL, json={}, headers=headers, timeout=12.0)
                    self.update_cookies(resp)

                if resp.status_code == 200:
                    data = resp.json()
                    self.nonce = data.get("restNonce") or data.get("new_token")
                    self.session_id = data.get("sessionId")
                    self.last_updated = time.time()
                    return self.get_cookie_header(), self.nonce, self.session_id
            except Exception as e:
                logger.debug(f"Tiger session init: {e}")
            return "", "", ""


session_mgr = SessionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            await session_mgr.get_valid_session(client, force_refresh=True)
            logger.info("Tiger Protect Session Pre-warmed.")
        except Exception:
            pass
    yield

app = FastAPI(
    title="RashiChatbot AI API Backend",
    version="2.5.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── FREE POLLINATIONS FALLBACK (Zero block, 100% uptime on Heroku) ───
async def ask_pollinations(prompt: str, system_prompt: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                "https://text.pollinations.ai/",
                json={
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "model": "openai",
                    "seed": random.randint(1, 99999)
                }
            )
            if resp.status_code == 200 and resp.text.strip():
                return resp.text.strip()
    except Exception as e:
        logger.debug(f"Pollinations error: {e}")
    return None

# ─── FREE GROQ FALLBACK (if GROQ_API_KEY set) ───
async def ask_groq(prompt: str, system_prompt: str) -> Optional[str]:
    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.75,
                    "max_tokens": 150
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return None


# ─── CORE CHAT HELPER ───
async def execute_chat(prompt: str, model_name: str = "grok-4", system_prompt: str = "", history: List[Dict] = None) -> str:
    # 1. Quick identity check
    quick = handle_special_cases(prompt)
    if quick:
        return quick

    sys_p = system_prompt.strip() if system_prompt else RASHI_SYSTEM_PROMPT

    # 2. Try Tiger Scraper (free-ai-online)
    try:
        resolved_name, model_cfg = resolve_model(model_name)
        async with httpx.AsyncClient(timeout=25.0) as client:
            cookie_hdr, nonce, session_id = await session_mgr.get_valid_session(client)
            if session_id and nonce:
                formatted_history = []
                if history:
                    for m in history:
                        formatted_history.append({"role": m.get("role", "user"), "content": m.get("content", "")})

                payload = {
                    "botId": model_cfg["botId"],
                    "customId": None,
                    "session": session_id,
                    "chatId": f"rashi_{uuid.uuid4().hex[:8]}",
                    "contextId": model_cfg["contextId"],
                    "messages": formatted_history,
                    "newMessage": f"[Instruction: {sys_p}]\n\n{prompt}",
                    "stream": False
                }
                req_headers = DEFAULT_HEADERS.copy()
                req_headers["Referer"] = model_cfg["referer"]
                req_headers["Content-Type"] = "application/json"
                if cookie_hdr:
                    req_headers["Cookie"] = cookie_hdr
                if nonce:
                    req_headers["X-WP-Nonce"] = nonce

                resp = await client.post(SUBMIT_CHAT_URL, json=payload, headers=req_headers)
                session_mgr.update_cookies(resp)
                if resp.status_code == 307:
                    req_headers["Cookie"] = session_mgr.get_cookie_header()
                    resp = await client.post(SUBMIT_CHAT_URL, json=payload, headers=req_headers)
                    session_mgr.update_cookies(resp)

                if resp.status_code == 200:
                    d = resp.json()
                    if d.get("success") and d.get("reply"):
                        return d.get("reply").strip()
    except Exception as e:
        logger.debug(f"Tiger scraper execution error: {e}")

    # 3. Try Groq (if key set)
    groq_res = await ask_groq(prompt, sys_p)
    if groq_res:
        return groq_res

    # 4. Try Free Pollinations.ai (never blocked on Heroku)
    polli_res = await ask_pollinations(prompt, sys_p)
    if polli_res:
        return polli_res

    # 5. Natural fallback
    return "Arre yaar, thoda busy thi, batao kya keh rahe the? 😊"


# ═══════════════════════════════════════════════════════════
# ENDPOINTS (Supporting ALL methods: POST /, GET /, /api/chat)
# ═══════════════════════════════════════════════════════════

# ── POST / (CRITICAL: RashiChatbot sends POST to base url) ──
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
    return JSONResponse({"status": "success", "reply": reply, "data": reply})


# ── GET / (Direct browser / simple query) ──
@app.get("/")
async def root_get(
    query: Optional[str] = None,
    text: Optional[str] = None,
    prompt: Optional[str] = None,
    model: str = "grok-4"
):
    msg = query or text or prompt
    if not msg:
        return {
            "service": "RashiChatbot AI API Backend",
            "status": "online",
            "version": "2.5.0",
            "usage": "GET /?query=hello or POST / with {'prompt': 'hello'}"
        }
    reply = await execute_chat(msg, model, RASHI_SYSTEM_PROMPT)
    return JSONResponse({"status": "success", "reply": reply, "data": reply})


# ── GET /api/chat ──
@app.get("/api/chat")
async def chat_get(
    query: str = Query(..., description="Message text"),
    model: str = Query("grok-4"),
    system_prompt: Optional[str] = Query(None)
):
    reply = await execute_chat(query, model, system_prompt or RASHI_SYSTEM_PROMPT)
    return {"success": True, "reply": reply, "model": model}


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
    return {"success": True, "reply": reply, "model": model}


# ── POST /ask (NiaApi compatibility) ──
@app.post("/ask")
async def ask_endpoint(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    prompt = body.get("prompt", "")
    reply = await execute_chat(prompt)
    return {"status": "success", "reply": reply}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
