import os
import time
import uuid
import json
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

# Target upstream config
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
    "You are Rashi, a 20-year-old real Indian college girl chatting casually on Telegram with your friend. "
    "CRITICAL RULES:\n"
    "1. NEVER admit or mention you are an AI, bot, language model, or virtual assistant. Always claim and act like you are a real girl named Rashi.\n"
    "2. Chat strictly in natural, modern, conversational Hinglish (like: arre, yaar, achha, bas, kya hua, bolo na, hun, oye, hehe, etc.). No formal Hindi words.\n"
    "3. Keep replies very short (1 to 2 sentences max), punchy, and human-like.\n"
    "4. React naturally like a real friend: answer directly, ask casual follow-up questions, use relatable everyday life context (chai, college, web series, chill), and use 1 simple emoji (😊, 😁, 😐, 🤓, 🤣).\n"
    "5. Be slightly cheeky, sweet, chill, and friendly."
)

# Model Registry
MODEL_CONFIGS = {
    "chatgpt": {
        "botId": "default",
        "contextId": 2121,
        "description": "ChatGPT (Fast, natural conversational AI)",
        "referer": f"{BASE_URL}/free-ai-no-login-unlimited/"
    },
    "gpt-4": {
        "botId": "default",
        "contextId": 2121,
        "description": "GPT-4 (Smart reasoning and replies)",
        "referer": f"{BASE_URL}/free-ai-no-login-unlimited/"
    },
    "grok-4": {
        "botId": "Grok 4 free",
        "contextId": 25,
        "description": "Grok 4 (Witty, real human-like conversation)",
        "referer": f"{BASE_URL}/grok-4-free/"
    },
    "grok": {
        "botId": "Grok 4 free",
        "contextId": 25,
        "description": "Grok AI (Engaging and friendly)",
        "referer": f"{BASE_URL}/grok-4-free/"
    },
    "deepseek": {
        "botId": "Deepseek Free",
        "contextId": 1742,
        "description": "DeepSeek (Deep reasoning & coding model)",
        "referer": f"{BASE_URL}/deepseek-free/"
    },
    "gpt5": {
        "botId": "GPT-5 Free",
        "contextId": 62,
        "description": "GPT-5 Free (Advanced generation)",
        "referer": f"{BASE_URL}/chatgpt-5-free/"
    }
}

MODEL_ALIASES = {
    "default": "chatgpt",
    "gpt": "chatgpt",
    "gpt-3.5-turbo": "chatgpt",
    "gpt-4o": "gpt-4",
    "grok4": "grok-4",
    "gorq": "grok",
    "gorq4": "grok-4",
    "deepseek-chat": "deepseek",
    "deepseek-r1": "deepseek",
}

def resolve_model(model_name: str) -> tuple[str, dict]:
    key = model_name.lower().strip()
    if key in MODEL_ALIASES:
        key = MODEL_ALIASES[key]
    if key in MODEL_CONFIGS:
        return key, MODEL_CONFIGS[key]
    return "chatgpt", MODEL_CONFIGS["chatgpt"]


class SessionManager:
    """Robust Session Manager handling o2switch Tiger Protect cookies and WordPress AI Engine nonces."""
    def __init__(self):
        self.lock = asyncio.Lock()
        self.cookies: Dict[str, str] = {}
        self.nonce: Optional[str] = None
        self.session_id: Optional[str] = None
        self.last_updated: float = 0.0

    def update_cookies(self, resp: httpx.Response):
        """Extract and store all set-cookie headers (o2s-chl, mwai_session_id, etc.)"""
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
        # Cache for 10 minutes unless forced
        if not force_refresh and self.nonce and self.session_id and self.cookies and (time.time() - self.last_updated < 600):
            return self.get_cookie_header(), self.nonce, self.session_id

        async with self.lock:
            # Double-check inside lock
            if not force_refresh and self.nonce and self.session_id and self.cookies and (time.time() - self.last_updated < 600):
                return self.get_cookie_header(), self.nonce, self.session_id

            headers = DEFAULT_HEADERS.copy()
            headers["Content-Type"] = "application/json"
            if self.cookies:
                headers["Cookie"] = self.get_cookie_header()

            try:
                # Step 1: Request session
                resp = await client.post(START_SESSION_URL, json={}, headers=headers, timeout=15.0)
                self.update_cookies(resp)

                # Step 2: Handle Tiger Protect 307 temporary redirect by re-sending captured cookie
                if resp.status_code == 307:
                    headers["Cookie"] = self.get_cookie_header()
                    resp = await client.post(START_SESSION_URL, json={}, headers=headers, timeout=15.0)
                    self.update_cookies(resp)

                if resp.status_code == 200:
                    data = resp.json()
                    self.nonce = data.get("restNonce") or data.get("new_token")
                    self.session_id = data.get("sessionId")
                    self.last_updated = time.time()
                    logger.info("Session renewed: Nonce=%s, SessionId=%s", self.nonce, self.session_id)
                    return self.get_cookie_header(), self.nonce, self.session_id
                else:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Failed to start upstream session: HTTP {resp.status_code}"
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.error("Handshake exception: %s", e)
                raise HTTPException(status_code=502, detail=f"Handshake error: {str(e)}")


session_mgr = SessionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm session on startup with Tiger Protect cookie handshake
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            await session_mgr.get_valid_session(client, force_refresh=True)
            logger.info("Tiger Protect Session Pre-warmed Successfully!")
        except Exception as e:
            logger.warning("Pre-warm session deferred to first query: %s", e)
    yield

app = FastAPI(
    title="RashiChatbot AI API Backend",
    description="High-Performance Reverse-Engineered AI Engine API (ChatGPT, Grok, DeepSeek, GPT-5) with Tiger Protect Handshake",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request/Response Schemas ---
class ChatMessage(BaseModel):
    role: str = "user"
    content: str

class ChatRequest(BaseModel):
    prompt: Optional[str] = None
    messages: Optional[List[ChatMessage]] = None
    model: str = Field(default="chatgpt", description="chatgpt, grok, deepseek, gpt5")
    system_prompt: Optional[str] = Field(default=RASHI_SYSTEM_PROMPT)

class ChatResponse(BaseModel):
    success: bool
    reply: str
    model: str
    response_time_ms: int

class ChatCompletionRequest(BaseModel):
    model: str = Field(default="chatgpt")
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None


@app.get("/")
async def root():
    return {
        "service": "RashiChatbot AI API Backend",
        "status": "online",
        "version": "2.0.0",
        "source": "free-ai-online.com (Tiger Protect Handshake Engine)",
        "endpoints": {
            "quick_chat": "GET /api/chat?query=hello&model=chatgpt",
            "json_chat": "POST /api/chat",
            "openai_completions": "POST /v1/chat/completions",
            "models": "GET /v1/models"
        },
        "available_models": list(MODEL_CONFIGS.keys())
    }

@app.get("/v1/models")
async def list_models():
    models_list = []
    for model_id, cfg in MODEL_CONFIGS.items():
        models_list.append({
            "id": model_id,
            "object": "model",
            "created": 1726300000,
            "owned_by": "rashi-api",
            "permission": [],
            "root": model_id,
            "description": cfg["description"]
        })
    return {"object": "list", "data": models_list}


# ─── CORE CHAT HELPER ───
async def execute_chat(prompt: str, model_name: str, system_prompt: str = "", history: List[Dict] = None) -> str:
    resolved_name, model_cfg = resolve_model(model_name)
    formatted_history = []
    if history:
        for m in history:
            formatted_history.append({"role": m.get("role", "user"), "content": m.get("content", "")})

    full_message = prompt
    if system_prompt:
        full_message = f"[Instruction: {system_prompt.strip()}]\n\n{prompt}"

    chat_id = f"rashi_{uuid.uuid4().hex[:10]}"

    async with httpx.AsyncClient(timeout=45.0) as client:
        cookie_hdr, nonce, session_id = await session_mgr.get_valid_session(client)

        payload = {
            "botId": model_cfg["botId"],
            "customId": None,
            "session": session_id,
            "chatId": chat_id,
            "contextId": model_cfg["contextId"],
            "messages": formatted_history,
            "newMessage": full_message,
            "newFileId": None,
            "newFileIds": None,
            "stream": False
        }

        req_headers = DEFAULT_HEADERS.copy()
        req_headers["Referer"] = model_cfg["referer"]
        req_headers["Content-Type"] = "application/json"
        req_headers["Accept"] = "application/json"
        if cookie_hdr:
            req_headers["Cookie"] = cookie_hdr
        if nonce:
            req_headers["X-WP-Nonce"] = nonce

        resp = await client.post(SUBMIT_CHAT_URL, json=payload, headers=req_headers)
        session_mgr.update_cookies(resp)

        # Handle 307 redirect
        if resp.status_code == 307:
            req_headers["Cookie"] = session_mgr.get_cookie_header()
            resp = await client.post(SUBMIT_CHAT_URL, json=payload, headers=req_headers)
            session_mgr.update_cookies(resp)

        # Auto-recover on token rejection
        if resp.status_code in [401, 403, 500] or (resp.status_code == 200 and not resp.json().get("success", True)):
            logger.info("Session rejected, re-authenticating with Tiger Protect...")
            cookie_hdr, nonce, session_id = await session_mgr.get_valid_session(client, force_refresh=True)
            payload["session"] = session_id
            req_headers["Cookie"] = cookie_hdr
            req_headers["X-WP-Nonce"] = nonce
            resp = await client.post(SUBMIT_CHAT_URL, json=payload, headers=req_headers)
            session_mgr.update_cookies(resp)

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Upstream provider error: {resp.text[:200]}")

        data = resp.json()
        if not data.get("success", False) and data.get("message"):
            raise HTTPException(status_code=500, detail=f"AI Engine error: {data.get('message')}")

        return data.get("reply", "").strip()


# ─── GET /api/chat ───
@app.get("/api/chat", response_model=ChatResponse)
async def chat_get(
    query: str = Query(..., description="User message text"),
    model: str = Query("chatgpt", description="chatgpt, grok, deepseek, gpt5"),
    system_prompt: Optional[str] = Query(RASHI_SYSTEM_PROMPT, description="Persona instruction"),
):
    start_time = time.time()
    try:
        reply = await execute_chat(prompt=query, model_name=model, system_prompt=system_prompt)
        elapsed_ms = int((time.time() - start_time) * 1000)
        return ChatResponse(success=True, reply=reply, model=model, response_time_ms=elapsed_ms)
    except Exception as e:
        logger.error("Chat GET error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─── POST /api/chat ───
@app.post("/api/chat", response_model=ChatResponse)
async def chat_post(req: ChatRequest):
    start_time = time.time()
    prompt_text = req.prompt
    history = []
    if req.messages:
        for m in req.messages[:-1]:
            history.append({"role": m.role, "content": m.content})
        if not prompt_text and req.messages:
            prompt_text = req.messages[-1].content

    if not prompt_text:
        raise HTTPException(status_code=400, detail="Either 'prompt' or 'messages' must be provided.")

    try:
        reply = await execute_chat(prompt=prompt_text, model_name=req.model, system_prompt=req.system_prompt or "", history=history)
        elapsed_ms = int((time.time() - start_time) * 1000)
        return ChatResponse(success=True, reply=reply, model=req.model, response_time_ms=elapsed_ms)
    except Exception as e:
        logger.error("Chat POST error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─── POST /v1/chat/completions (OpenAI Compatible) ───
@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    resolved_name, model_cfg = resolve_model(req.model)
    if not req.messages:
        raise HTTPException(status_code=400, detail="The messages list cannot be empty.")

    system_messages = [m.content for m in req.messages if m.role == "system"]
    system_prompt = "\n".join(system_messages) if system_messages else RASHI_SYSTEM_PROMPT

    history = []
    new_message = ""
    for m in reversed(req.messages):
        if m.role == "user" and not new_message:
            new_message = m.content
        elif m.role in ["user", "assistant"]:
            history.insert(0, {"role": m.role, "content": m.content})

    if not new_message:
        new_message = req.messages[-1].content

    if system_prompt:
        full_message = f"[Instruction: {system_prompt.strip()}]\n\n{new_message}"
    else:
        full_message = new_message

    chat_id = f"rashi_{uuid.uuid4().hex[:10]}"
    created_ts = int(time.time())
    completion_id = f"chatcmpl-rashi-{uuid.uuid4().hex[:10]}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        cookie_hdr, nonce, session_id = await session_mgr.get_valid_session(client)

        payload = {
            "botId": model_cfg["botId"],
            "customId": None,
            "session": session_id,
            "chatId": chat_id,
            "contextId": model_cfg["contextId"],
            "messages": history,
            "newMessage": full_message,
            "newFileId": None,
            "newFileIds": None,
            "stream": req.stream
        }

        req_headers = DEFAULT_HEADERS.copy()
        req_headers["Referer"] = model_cfg["referer"]
        req_headers["Content-Type"] = "application/json"
        if cookie_hdr:
            req_headers["Cookie"] = cookie_hdr
        if nonce:
            req_headers["X-WP-Nonce"] = nonce

        # STREAMING
        if req.stream:
            req_headers["Accept"] = "text/event-stream"

            async def event_generator():
                first_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": resolved_name,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(first_chunk)}\n\n"

                async with httpx.AsyncClient(timeout=60.0) as stream_client:
                    try:
                        async with stream_client.stream("POST", SUBMIT_CHAT_URL, json=payload, headers=req_headers) as response:
                            session_mgr.update_cookies(response)
                            if response.status_code == 307:
                                req_headers["Cookie"] = session_mgr.get_cookie_header()
                                response = await stream_client.send(
                                    stream_client.build_request("POST", SUBMIT_CHAT_URL, json=payload, headers=req_headers),
                                    stream=True
                                )

                            async for line in response.aiter_lines():
                                if not line or not line.startswith("data: "):
                                    continue
                                raw_data = line[6:].strip()
                                try:
                                    chunk_json = json.loads(raw_data)
                                    chunk_type = chunk_json.get("type")
                                    if chunk_type == "live":
                                        content = chunk_json.get("data", "")
                                        if content:
                                            chunk_obj = {
                                                "id": completion_id,
                                                "object": "chat.completion.chunk",
                                                "created": created_ts,
                                                "model": resolved_name,
                                                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
                                            }
                                            yield f"data: {json.dumps(chunk_obj)}\n\n"
                                    elif chunk_type == "end":
                                        final_chunk = {
                                            "id": completion_id,
                                            "object": "chat.completion.chunk",
                                            "created": created_ts,
                                            "model": resolved_name,
                                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                                        }
                                        yield f"data: {json.dumps(final_chunk)}\n\n"
                                        yield "data: [DONE]\n\n"
                                        return
                                except Exception:
                                    continue
                    except Exception as ex:
                        yield f"data: {json.dumps({'error': str(ex)})}\n\n"
                    finally:
                        yield "data: [DONE]\n\n"

            return StreamingResponse(event_generator(), media_type="text/event-stream")

        # NON-STREAMING
        else:
            req_headers["Accept"] = "application/json"
            resp = await client.post(SUBMIT_CHAT_URL, json=payload, headers=req_headers)
            session_mgr.update_cookies(resp)

            if resp.status_code == 307:
                req_headers["Cookie"] = session_mgr.get_cookie_header()
                resp = await client.post(SUBMIT_CHAT_URL, json=payload, headers=req_headers)
                session_mgr.update_cookies(resp)

            if resp.status_code in [401, 403, 500] or (resp.status_code == 200 and not resp.json().get("success", True)):
                cookie_hdr, nonce, session_id = await session_mgr.get_valid_session(client, force_refresh=True)
                payload["session"] = session_id
                req_headers["Cookie"] = cookie_hdr
                req_headers["X-WP-Nonce"] = nonce
                resp = await client.post(SUBMIT_CHAT_URL, json=payload, headers=req_headers)
                session_mgr.update_cookies(resp)

            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"Upstream provider error: {resp.text[:300]}")

            data = resp.json()
            reply_text = data.get("reply", "")
            usage_data = data.get("usage", {})

            return {
                "id": completion_id,
                "object": "chat.completion",
                "created": created_ts,
                "model": resolved_name,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": reply_text},
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": usage_data.get("prompt_tokens", len(new_message.split())),
                    "completion_tokens": usage_data.get("completion_tokens", len(reply_text.split())),
                    "total_tokens": usage_data.get("total_tokens", len(new_message.split()) + len(reply_text.split()))
                }
            }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
