"""
RashiChatbot AI API Backend — Multi-Provider Scraper
Chains multiple free AI providers with automatic fallback:
  1. Groq API (fastest, free)
  2. Google Gemini (free)
  3. OpenRouter (free tier)
  4. Original free-ai-online.com scraper (last resort)
"""
import asyncio
import uuid
import os
import logging
from typing import Optional, Dict, Any, List
import httpx

logger = logging.getLogger("rashi_scraper")

# ═══════════════════════════════════════════════════════════
# Environment-based API Keys
# ═══════════════════════════════════════════════════════════
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")


class MultiProviderAI:
    """
    Multi-provider AI engine with automatic failover.
    Tries legitimate free APIs first, falls back to scraper.
    """

    # Context IDs mapping (kept for backward compat)
    MODELS: Dict[str, int] = {
        "default": 2121,
        "chatgpt": 2121,
        "gpt-4": 2121,
        "deepseek": 1742,
        "grok": 25,
        "gpt5": 62,
    }

    # Groq model mapping
    GROQ_MODELS = {
        "default": "llama-3.3-70b-versatile",
        "chatgpt": "llama-3.3-70b-versatile",
        "gpt-4": "llama-3.3-70b-versatile",
        "deepseek": "llama-3.1-8b-instant",
        "grok": "llama-3.3-70b-versatile",
        "gpt5": "llama-3.3-70b-versatile",
    }

    # Gemini model mapping
    GEMINI_MODELS = {
        "default": "gemini-2.0-flash",
        "chatgpt": "gemini-2.0-flash",
        "gpt-4": "gemini-2.0-flash",
        "deepseek": "gemini-2.0-flash",
        "grok": "gemini-2.0-flash",
        "gpt5": "gemini-2.0-flash",
    }

    def __init__(self):
        self.session_id: Optional[str] = None
        self.rest_nonce: Optional[str] = None
        self.lock = asyncio.Lock()
        self.scraper_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Referer": "https://www.free-ai-online.com/free-ai-no-login-unlimited/",
            "Origin": "https://www.free-ai-online.com",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def get_valid_session(self, client: httpx.AsyncClient, force_refresh: bool = False):
        """Fetch or reuse existing valid session and nonce (for scraper fallback)."""
        async with self.lock:
            if not force_refresh and self.session_id and self.rest_nonce:
                return self.session_id, self.rest_nonce

            logger.info("Initializing new session token from free-ai-online.com...")
            try:
                response = await client.post(
                    "https://www.free-ai-online.com/wp-json/mwai/v1/start_session",
                    headers=self.scraper_headers,
                    timeout=15.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    self.session_id = data.get("sessionId")
                    self.rest_nonce = data.get("restNonce")
                    logger.info("Session successfully initialized: %s", self.session_id)
                    return self.session_id, self.rest_nonce
                else:
                    raise RuntimeError(f"Failed to fetch session, status={response.status_code}")
            except Exception as e:
                logger.error("Error in get_valid_session: %s", e)
                raise

    # ─── Provider 1: Groq ───────────────────────────────────
    async def _ask_groq(
        self, prompt: str, model: str, system_prompt: Optional[str],
        messages_history: Optional[List[Dict]], client: httpx.AsyncClient
    ) -> Optional[str]:
        if not GROQ_API_KEY:
            return None
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if messages_history:
                messages.extend(messages_history)
            messages.append({"role": "user", "content": prompt})

            groq_model = self.GROQ_MODELS.get(model.lower(), "llama-3.3-70b-versatile")
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": groq_model,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 300,
                },
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"].strip()
                logger.info("Groq response OK (%s)", groq_model)
                return reply
            else:
                logger.warning("Groq returned %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("Groq error: %s", e)
        return None

    # ─── Provider 2: Google Gemini ──────────────────────────
    async def _ask_gemini(
        self, prompt: str, model: str, system_prompt: Optional[str],
        messages_history: Optional[List[Dict]], client: httpx.AsyncClient
    ) -> Optional[str]:
        if not GEMINI_API_KEY:
            return None
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if messages_history:
                messages.extend(messages_history)
            messages.append({"role": "user", "content": prompt})

            gemini_model = self.GEMINI_MODELS.get(model.lower(), "gemini-2.0-flash")
            resp = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                headers={
                    "Authorization": f"Bearer {GEMINI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": gemini_model,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 300,
                },
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"].strip()
                logger.info("Gemini response OK (%s)", gemini_model)
                return reply
            else:
                logger.warning("Gemini returned %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("Gemini error: %s", e)
        return None

    # ─── Provider 3: OpenRouter ─────────────────────────────
    async def _ask_openrouter(
        self, prompt: str, system_prompt: Optional[str],
        messages_history: Optional[List[Dict]], client: httpx.AsyncClient
    ) -> Optional[str]:
        if not OPENROUTER_API_KEY:
            return None
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if messages_history:
                messages.extend(messages_history)
            messages.append({"role": "user", "content": prompt})

            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "meta-llama/llama-3.3-70b-instruct:free",
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 300,
                },
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"].strip()
                logger.info("OpenRouter response OK")
                return reply
            else:
                logger.warning("OpenRouter returned %s", resp.status_code)
        except Exception as e:
            logger.warning("OpenRouter error: %s", e)
        return None

    # ─── Provider 4: Original Scraper (Last Resort) ─────────
    async def _ask_scraper(
        self, prompt: str, model: str, system_prompt: Optional[str],
        messages_history: Optional[List[Dict]], client: httpx.AsyncClient
    ) -> Optional[str]:
        try:
            full_prompt = prompt
            if system_prompt:
                full_prompt = f"{system_prompt.strip()}\n\nUser: {prompt}"

            session_id, rest_nonce = await self.get_valid_session(client)
            context_id = self.MODELS.get(model.lower(), 2121)

            formatted_messages = []
            if messages_history:
                for msg in messages_history:
                    formatted_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

            payload = {
                "botId": "default",
                "customId": None,
                "session": session_id,
                "chatId": str(uuid.uuid4())[:8],
                "contextId": context_id,
                "messages": formatted_messages,
                "newMessage": full_prompt,
                "stream": False,
            }
            req_headers = self.scraper_headers.copy()
            req_headers["X-WP-Nonce"] = rest_nonce

            response = await client.post(
                "https://www.free-ai-online.com/wp-json/mwai-ui/v1/chats/submit",
                headers=req_headers,
                json=payload,
                timeout=30.0,
            )

            if response.status_code in [401, 403]:
                session_id, rest_nonce = await self.get_valid_session(client, force_refresh=True)
                payload["session"] = session_id
                req_headers["X-WP-Nonce"] = rest_nonce
                response = await client.post(
                    "https://www.free-ai-online.com/wp-json/mwai-ui/v1/chats/submit",
                    headers=req_headers,
                    json=payload,
                    timeout=30.0,
                )

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    reply = data.get("reply", "").strip()
                    logger.info("Scraper response OK")
                    return reply
        except Exception as e:
            logger.warning("Scraper fallback error: %s", e)
        return None

    # ═══════════════════════════════════════════════════════
    # Main Ask Method — Chains all providers
    # ═══════════════════════════════════════════════════════
    async def ask(
        self,
        prompt: str,
        model: str = "default",
        system_prompt: Optional[str] = None,
        messages_history: Optional[List[Dict[str, str]]] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        """
        Send a chat query through the provider chain.
        Returns dict with 'success', 'reply', 'model', 'provider'.
        """
        should_close = False
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
            should_close = True

        try:
            # 1. Try Groq (fastest)
            reply = await self._ask_groq(prompt, model, system_prompt, messages_history, client)
            if reply:
                return {"success": True, "reply": reply, "model": model, "provider": "groq"}

            # 2. Try Gemini
            reply = await self._ask_gemini(prompt, model, system_prompt, messages_history, client)
            if reply:
                return {"success": True, "reply": reply, "model": model, "provider": "gemini"}

            # 3. Try OpenRouter
            reply = await self._ask_openrouter(prompt, system_prompt, messages_history, client)
            if reply:
                return {"success": True, "reply": reply, "model": model, "provider": "openrouter"}

            # 4. Try original scraper (last resort)
            reply = await self._ask_scraper(prompt, model, system_prompt, messages_history, client)
            if reply:
                return {"success": True, "reply": reply, "model": model, "provider": "scraper"}

            raise RuntimeError("All AI providers failed. Set GROQ_API_KEY or GEMINI_API_KEY env vars.")

        finally:
            if should_close:
                await client.aclose()


# Global singleton instance
scraper_instance = MultiProviderAI()
