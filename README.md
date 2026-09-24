# RashiChatbot AI API Backend 🚀

Reverse-engineered, zero-key, high-speed AI Chat API backend created by scraping `https://www.free-ai-online.com`. It provides free AI chat access (ChatGPT, DeepSeek, Grok, etc.) with automatic session management and an **OpenAI-compatible** API endpoint.

---

## ✨ Features

- **No API Key Required**: No sign up, login, or billing needed.
- **Smart Session Token Lifecycle**: Automatically fetches and maintains WordPress AI Engine (`MWAI`) session cookies and `restNonce` tokens with instant refresh on expiration.
- **Multiple Models**:
  - `chatgpt` (Default fast conversational model)
  - `deepseek`
  - `grok`
  - `gpt5`
- **OpenAI Compatible Endpoint**: Use standard OpenAI libraries by setting `base_url="http://YOUR_HOST/v1"` and any dummy API key!
- **FastAPI Powered**: Async, high concurrency, interactive Swagger documentation at `/docs`.

---

## 📡 API Endpoints

### 1. Simple GET Query
Ideal for chatbots, cURL, or quick integrations:
```http
GET /api/chat?query=Tumhara%20naam%20kya%20hai&model=chatgpt
```

**Response:**
```json
{
  "success": true,
  "reply": "Mera naam Rashi hai! Aap kaise ho?",
  "model": "chatgpt",
  "response_time_ms": 780
}
```

### 2. JSON POST Query
Supports conversation history and custom system prompts:
```http
POST /api/chat
Content-Type: application/json

{
  "prompt": "Mujhe ek accha code example do Python me",
  "model": "chatgpt",
  "system_prompt": "You are an expert Python tutor."
}
```

### 3. OpenAI-Compatible (`/v1/chat/completions`)
Drop-in replacement for OpenAI SDK:
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="rashi"
)

response = client.chat.completions.create(
    model="chatgpt",
    messages=[
        {"role": "user", "content": "Kaisi ho Rashi?"}
    ]
)
print(response.choices[0].message.content)
```

---

## 🚀 Running Locally

```bash
cd RashiChatbot-API
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
Open [http://localhost:8000/docs](http://localhost:8000/docs) in your browser.

---

## ☁️ Deployment

### Option 1: Koyeb / Render / Railway
1. Push this folder to a GitHub repository (e.g. `RashiChatbot-API`).
2. Deploy as a Web Service.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

### Option 2: Docker
```bash
docker build -t rashi-api .
docker run -p 8000:8000 rashi-api
```
