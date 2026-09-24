import asyncio
import uuid
import logging
from typing import Optional, Dict, Any, List
import httpx

logger = logging.getLogger("rashi_scraper")

class FreeAIOnlineScraper:
    """
    Reverse-engineered async client for free-ai-online.com
    Extracts session tokens (restNonce + sessionId) from WordPress AI Engine (MWAI)
    and dispatches queries without requiring any account, login, or official API keys.
    """
    BASE_URL = "https://www.free-ai-online.com"
    START_URL = f"{BASE_URL}/wp-json/mwai/v1/start_session"
    SUBMIT_URL = f"{BASE_URL}/wp-json/mwai-ui/v1/chats/submit"

    # Context IDs mapping to models configured on the website
    MODELS: Dict[str, int] = {
        "default": 2121,
        "chatgpt": 2121,
        "gpt-4": 2121,
        "deepseek": 1742,
        "grok": 25,
        "gpt5": 62,
    }

    def __init__(self):
        self.session_id: Optional[str] = None
        self.rest_nonce: Optional[str] = None
        self.lock = asyncio.Lock()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": f"{self.BASE_URL}/free-ai-no-login-unlimited/",
            "Origin": self.BASE_URL,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def get_valid_session(self, client: httpx.AsyncClient, force_refresh: bool = False) -> tuple[str, str]:
        """Fetch or reuse existing valid session and nonce."""
        async with self.lock:
            if not force_refresh and self.session_id and self.rest_nonce:
                return self.session_id, self.rest_nonce

            logger.info("Initializing new session token from free-ai-online.com...")
            try:
                response = await client.post(
                    self.START_URL,
                    headers=self.headers,
                    timeout=15.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    self.session_id = data.get("sessionId")
                    self.rest_nonce = data.get("restNonce")
                    logger.info("Session successfully initialized: %s", self.session_id)
                    return self.session_id, self.rest_nonce
                else:
                    raise RuntimeError(f"Failed to fetch session, status={response.status_code}, text={response.text[:200]}")
            except Exception as e:
                logger.error("Error in get_valid_session: %s", e)
                raise

    async def ask(
        self,
        prompt: str,
        model: str = "default",
        system_prompt: Optional[str] = None,
        messages_history: Optional[List[Dict[str, str]]] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        """
        Send a chat query to free-ai-online and get the response.
        Handles auto-recovery if the session or nonce expires.
        """
        should_close_client = False
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True)
            should_close_client = True

        try:
            # Build full message with optional persona
            full_prompt = prompt
            if system_prompt:
                full_prompt = f"{system_prompt.strip()}\n\nUser: {prompt}"

            session_id, rest_nonce = await self.get_valid_session(client)
            context_id = self.MODELS.get(model.lower(), 2121)

            # Format previous history if provided
            formatted_messages = []
            if messages_history:
                for msg in messages_history:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    formatted_messages.append({"role": role, "content": content})

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

            req_headers = self.headers.copy()
            req_headers["X-WP-Nonce"] = rest_nonce

            response = await client.post(
                self.SUBMIT_URL,
                headers=req_headers,
                json=payload,
                timeout=30.0,
            )

            # If token expired or forbidden, refresh once and retry
            if response.status_code in [401, 403]:
                logger.warning("Session/nonce rejected (%s). Refreshing...", response.status_code)
                session_id, rest_nonce = await self.get_valid_session(client, force_refresh=True)
                payload["session"] = session_id
                req_headers["X-WP-Nonce"] = rest_nonce

                response = await client.post(
                    self.SUBMIT_URL,
                    headers=req_headers,
                    json=payload,
                    timeout=30.0,
                )

            if response.status_code != 200:
                raise RuntimeError(f"Free-AI-Online returned HTTP {response.status_code}: {response.text[:300]}")

            data = response.json()
            if not data.get("success"):
                error_msg = data.get("message", "Unknown error from free-ai-online backend")
                raise RuntimeError(f"Chat API error: {error_msg}")

            reply_text = data.get("reply", "")
            return {
                "success": True,
                "reply": reply_text.strip(),
                "model": model,
                "responseId": data.get("responseId"),
                "usage": data.get("usage", {}),
            }

        finally:
            if should_close_client:
                await client.aclose()


# Global singleton instance
scraper_instance = FreeAIOnlineScraper()
