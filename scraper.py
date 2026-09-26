"""
RashiChatbot AI API Backend — Fast Tiger Scraper Engine
High-speed Grok-4 reverse-engineered scraper with session persistence.
"""
import asyncio
import uuid
import os
import time
import logging
from typing import Optional, Dict, Any, List
import httpx

logger = logging.getLogger("rashi_scraper")

BASE_URL = "https://www.free-ai-online.com"
START_SESSION_URL = f"{BASE_URL}/wp-json/mwai/v1/start_session"
SUBMIT_CHAT_URL = f"{BASE_URL}/wp-json/mwai-ui/v1/chats/submit"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/grok-4-free/",
    "Accept": "application/json, text/plain, */*",
}


class MultiProviderAI:
    def __init__(self):
        self.session_id: Optional[str] = None
        self.nonce: Optional[str] = None
        self.last_init_time: float = 0.0
        self.lock = asyncio.Lock()
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
                resp = await client.post(START_SESSION_URL, json={}, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    self.session_id = data.get("sessionId")
                    self.nonce = data.get("restNonce") or data.get("new_token")
                    self.last_init_time = time.time()
                    return self.session_id, self.nonce
            except Exception as e:
                logger.error(f"Error in get_valid_session: {e}")
            return None, None

    async def _ask_scraper(
        self, prompt: str, model: str, system_prompt: Optional[str],
        messages_history: Optional[List[Dict]]
    ) -> Optional[str]:
        try:
            client = self.get_client()
            session_id, nonce = await self.get_valid_session()
            if not (session_id and nonce):
                session_id, nonce = await self.get_valid_session(force_refresh=True)
                if not (session_id and nonce):
                    return None

            sys_p = system_prompt or (
                "Tu Rashi hai — 20 saal ki sweet, smart aur witty Indian college girl. "
                "Replies strictly 1 line (4-10 words max) in Hinglish. Never repeat user name."
            )
            full_prompt = f"[Instruction: {sys_p}]\n\nUser: {prompt}"

            formatted = []
            if messages_history:
                for m in messages_history[-4:]:
                    formatted.append({"role": m.get("role", "user"), "content": m.get("content", "")})

            payload = {
                "botId": "Grok 4 free",
                "customId": None,
                "session": session_id,
                "chatId": f"rashi_{uuid.uuid4().hex[:8]}",
                "contextId": 25,
                "messages": formatted,
                "newMessage": full_prompt,
                "stream": False,
            }
            req_headers = DEFAULT_HEADERS.copy()
            req_headers["X-WP-Nonce"] = nonce
            req_headers["Content-Type"] = "application/json"

            for attempt in range(2):
                resp = await client.post(SUBMIT_CHAT_URL, headers=req_headers, json=payload)
                if resp.status_code == 200:
                    d = resp.json()
                    if d.get("success") and d.get("reply"):
                        rep = d.get("reply").strip().replace('"', '')
                        if rep.lower().startswith("rashi:"):
                            rep = rep[6:].strip()
                        return rep

                if resp.status_code in (401, 403) or (resp.status_code == 200 and not resp.json().get("success")):
                    session_id, nonce = await self.get_valid_session(force_refresh=True)
                    if session_id and nonce:
                        payload["session"] = session_id
                        req_headers["X-WP-Nonce"] = nonce
                        continue
        except Exception as e:
            logger.warning(f"Tiger scraper error: {e}")
        return None

    async def ask(
        self,
        prompt: str,
        model: str = "default",
        system_prompt: Optional[str] = None,
        messages_history: Optional[List[Dict[str, str]]] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        reply = await self._ask_scraper(prompt, model, system_prompt, messages_history)
        if reply:
            return {"success": True, "reply": reply, "model": "grok-4", "provider": "tiger_scraper"}
        return {"success": False, "reply": "Bas yahi hoon, batao kya baat hai? 😊", "model": "default", "provider": "fallback"}


scraper_instance = MultiProviderAI()
