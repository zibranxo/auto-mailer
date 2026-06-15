# Walkthrough: Multi-Provider Auto-Switching

We have completely overhauled the API connection layer to support multiple LLM providers simultaneously! This ensures your script achieves 100% uptime, even if a provider enforces a strict daily rate limit.

### What Changed:
1. **Dynamic Provider Pool**: The script now scans your `.env` file and loads a pool of `LLMProvider` objects. It still reads your standard `LLM_API_KEY` and `LLM_BASE_URL` as your Primary Provider, but it will also actively hunt for numbered fallbacks like `LLM_2_API_KEY`, `LLM_3_API_KEY`, etc.
2. **Zero-Downtime Cascade**: If your script hits a `429 Too Many Requests` limit on any provider, it instantly quarantines that specific provider for 600 seconds and *automatically routes the generation to the next healthy provider in the pool*. You don't lose the retry, and you don't drop the company.
3. **Independent Throttling**: The 2.5s slowmode pacing has been moved inside the `LLMProvider` object, meaning your worker threads can now hit multiple different providers concurrently without artificial bottlenecks across platforms.

### How to Configure Fallbacks
To add providers like Groq, OpenRouter, and Google Gemini to your fallback pool, simply edit your `.env` file to look like this:

```env
# Primary Provider (e.g. NVIDIA NIM)
LLM_API_KEY=nvapi-your-key-here
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=meta/llama-3.3-70b-instruct

# Fallback Provider 1 (e.g. Groq)
LLM_2_API_KEY=gsk_your_groq_key_here
LLM_2_BASE_URL=https://api.groq.com/openai/v1
LLM_2_MODEL=llama-3.3-70b-versatile

# Fallback Provider 2 (e.g. OpenRouter)
LLM_3_API_KEY=sk-or-v1-your_openrouter_key
LLM_3_BASE_URL=https://openrouter.ai/api/v1
LLM_3_MODEL=meta-llama/llama-3.3-70b-instruct
```

The script will automatically load all 3, use the Primary until it rate-limits, and gracefully fail over as needed!
