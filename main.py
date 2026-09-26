import time
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from scraper import scraper_instance

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rashi_api")

# Default personality for Rashi
RASHI_SYSTEM_PROMPT = (
    "Act as Rashi, a sweet, smart and friendly Indian girl chatting with friends. "
    "Reply naturally in Hinglish/Hindi/English mix like a real friend. "
    "Keep replies short, warm and charming (1-2 sentences). "
)

class ChatMessage(BaseModel):
    role: str = "user"
    content: str

class ChatRequest(BaseModel):
    prompt: Optional[str] = None
    messages: Optional[List[ChatMessage]] = None
    model: str = Field(default="chatgpt", description="Model: chatgpt, deepseek, grok, gpt5")
    system_prompt: Optional[str] = Field(default=RASHI_SYSTEM_PROMPT, description="Custom system persona instructions")

class ChatResponse(BaseModel):
    success: bool
    reply: str
    model: str
    response_time_ms: int

# OpenAI-compatible schemas
class OpenAIChatCompletionRequest(BaseModel):
    model: str = "chatgpt"
    messages: List[Dict[str, Any]]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False

_shared_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    global _shared_client
    if hasattr(app.state, "client") and app.state.client is not None:
        return app.state.client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(connect=10.0, read=35.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )
    return _shared_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = get_http_client()
    import os
    if os.getenv("GROQ_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("OPENROUTER_API_KEY"):
        logger.info("✅ Multi-Provider AI ready (Groq/Gemini key active)!")
    else:
        try:
            await scraper_instance.get_valid_session(app.state.client)
        except Exception:
            logger.info("ℹ️ Running in API mode. Set GROQ_API_KEY for ultra-fast LLaMA responses.")
    yield
    if hasattr(app.state, "client") and app.state.client and not app.state.client.is_closed:
        await app.state.client.aclose()


app = FastAPI(
    title="RashiChatbot AI API Backend",
    description="High-performance scraped AI API powered by free-ai-online.com with OpenAI compatibility",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "RashiChatbot AI API Backend",
        "version": "1.0.0",
        "source": "free-ai-online.com scraper",
        "endpoints": {
            "GET /api/chat": "Quick query endpoint (?query=...&model=chatgpt)",
            "POST /api/chat": "JSON body chat endpoint",
            "POST /v1/chat/completions": "OpenAI-compatible chat completions endpoint",
            "GET /docs": "Interactive Swagger API documentation",
        },
    }

@app.get("/api/chat", response_model=ChatResponse)
async def chat_get(
    query: str = Query(..., description="User message text"),
    model: str = Query("chatgpt", description="chatgpt, deepseek, grok, gpt5"),
    system_prompt: Optional[str] = Query(RASHI_SYSTEM_PROMPT, description="Persona instruction"),
):
    """Simple GET endpoint for quick queries and chatbot integrations."""
    start_time = time.time()
    try:
        result = await scraper_instance.ask(
            prompt=query,
            model=model,
            system_prompt=system_prompt,
            client=get_http_client(),
        )
        elapsed_ms = int((time.time() - start_time) * 1000)
        return ChatResponse(
            success=True,
            reply=result["reply"],
            model=model,
            response_time_ms=elapsed_ms,
        )
    except Exception as e:
        logger.error("Chat GET error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat", response_model=ChatResponse)
async def chat_post(req: ChatRequest):
    """POST endpoint with support for conversation history and custom personas."""
    start_time = time.time()

    prompt_text = req.prompt
    history = []

    if req.messages:
        # Extract prompt from last message if not explicitly provided
        for m in req.messages[:-1]:
            history.append({"role": m.role, "content": m.content})
        if not prompt_text and req.messages:
            prompt_text = req.messages[-1].content

    if not prompt_text:
        raise HTTPException(status_code=400, detail="Either 'prompt' or 'messages' must be provided.")

    try:
        result = await scraper_instance.ask(
            prompt=prompt_text,
            model=req.model,
            system_prompt=req.system_prompt,
            messages_history=history,
            client=get_http_client(),
        )
        elapsed_ms = int((time.time() - start_time) * 1000)
        return ChatResponse(
            success=True,
            reply=result["reply"],
            model=req.model,
            response_time_ms=elapsed_ms,
        )
    except Exception as e:
        logger.error("Chat POST error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/chat/completions")
async def openai_completions(req: OpenAIChatCompletionRequest):
    """
    OpenAI-compatible chat completions endpoint.
    Allows seamless drop-in replacement for OpenAI SDK / LangChain / custom bots.
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="Messages array cannot be empty")

    system_prompt = RASHI_SYSTEM_PROMPT
    history = []
    user_prompt = ""

    for msg in req.messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            system_prompt = content
        elif role == "user":
            user_prompt = content
            history.append({"role": role, "content": content})
        else:
            history.append({"role": role, "content": content})

    # Remove the last user prompt from history to avoid duplicate
    if history and history[-1]["content"] == user_prompt:
        history.pop()

    try:
        result = await scraper_instance.ask(
            prompt=user_prompt,
            model=req.model,
            system_prompt=system_prompt,
            messages_history=history,
            client=get_http_client(),
        )
        reply = result["reply"]

        return {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": reply,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(user_prompt.split()),
                "completion_tokens": len(reply.split()),
                "total_tokens": len(user_prompt.split()) + len(reply.split()),
            },
        }
    except Exception as e:
        logger.error("OpenAI completions error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
