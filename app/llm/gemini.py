import time
import json
import logging
from typing import Any
from app.config import settings
from app.common.tracer import tracer

logger = logging.getLogger(__name__)


class GroqProvider:
    """LLM provider using Groq's API (OpenAI-compatible)."""
    
    def __init__(self, model_name: str = "llama-3.3-70b-versatile"):
        from groq import Groq
        self.client = Groq(api_key=settings.groq_api_key)
        self.model_name = model_name
        self.max_retries = 3
    
    async def generate_structured(self, prompt: Any, schema: Any) -> Any:
        schema_json = schema.model_json_schema()
        schema_name = getattr(schema, "__name__", str(schema))
        
        has_images = False
        current_model = self.model_name
        
        if isinstance(prompt, list):
            has_images = any(isinstance(p, dict) and 'mime_type' in p for p in prompt)
            
            if has_images:
                # Switch to Groq's free vision model
                current_model = "llama-3.2-90b-vision-preview"
                content_array = []
                for p in prompt:
                    if isinstance(p, str):
                        content_array.append({"type": "text", "text": p})
                    elif isinstance(p, dict) and 'mime_type' in p and 'data' in p:
                        import base64
                        b64 = base64.b64encode(p['data']).decode('utf-8') if isinstance(p['data'], bytes) else p['data']
                        content_array.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{p['mime_type']};base64,{b64}"
                            }
                        })
                content_array.append({"type": "text", "text": f"\n\nIMPORTANT: Return ONLY a valid JSON object matching exactly this schema. No markdown, no backticks:\n{json.dumps(schema_json)}"})
                user_message = {"role": "user", "content": content_array}
                prompt_text = "[Multimodal input with images]"
            else:
                text_parts = [p for p in prompt if isinstance(p, str)]
                prompt_text = "\n".join(text_parts)
                prompt_text += f"\n\nIMPORTANT: Return ONLY a valid JSON object matching exactly this schema. No markdown, no backticks:\n{json.dumps(schema_json)}"
                user_message = {"role": "user", "content": prompt_text}
        else:
            prompt_text = str(prompt)
            prompt_text += f"\n\nIMPORTANT: Return ONLY a valid JSON object matching exactly this schema. No markdown, no backticks:\n{json.dumps(schema_json)}"
            user_message = {"role": "user", "content": prompt_text}
        
        start_time = time.perf_counter()
        for attempt in range(self.max_retries):
            try:
                kwargs = {
                    "model": current_model,
                    "messages": [
                        {"role": "system", "content": "You are a precise data extraction assistant. Always respond with valid JSON only."},
                        user_message
                    ],
                    "temperature": 0.1,
                }
                
                # Vision models on Groq sometimes don't support json_object format flag yet, 
                # so we only enforce it for text models.
                if not has_images:
                    kwargs["response_format"] = {"type": "json_object"}
                    
                response = self.client.chat.completions.create(**kwargs)
                raw_text = response.choices[0].message.content
                
                # Clean up markdown if present
                raw_text = raw_text.strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                if raw_text.startswith("```"):
                    raw_text = raw_text[3:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                raw_text = raw_text.strip()
                
                data = json.loads(raw_text)
                parsed_obj = schema(**data)
                
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                tracer.log_llm_call(
                    provider="groq",
                    model=current_model,
                    prompt=prompt_text,
                    response=parsed_obj,
                    latency_ms=elapsed_ms,
                    status="SUCCESS",
                    schema_name=schema_name
                )
                return parsed_obj
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                if "429" in str(e) and attempt < self.max_retries - 1:
                    wait_time = 10 * (attempt + 1)
                    logger.warning(f"Groq Rate limited (attempt {attempt + 1}/{self.max_retries}). Retrying in {wait_time}s...")
                    tracer.log_llm_call(
                        provider="groq",
                        model=self.model_name,
                        prompt=prompt_text,
                        response=None,
                        latency_ms=elapsed_ms,
                        status="RETRYING_429",
                        error=f"Attempt {attempt + 1} failed with 429: {str(e)}",
                        schema_name=schema_name
                    )
                    time.sleep(wait_time)
                else:
                    tracer.log_llm_call(
                        provider="groq",
                        model=self.model_name,
                        prompt=prompt_text,
                        response=None,
                        latency_ms=elapsed_ms,
                        status="ERROR",
                        error=str(e),
                        schema_name=schema_name
                    )
                    raise
    
    async def generate_text(self, prompt: str) -> str:
        start_time = time.perf_counter()
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                )
                result_text = response.choices[0].message.content
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                tracer.log_llm_call(
                    provider="groq",
                    model=self.model_name,
                    prompt=prompt,
                    response=result_text,
                    latency_ms=elapsed_ms,
                    status="SUCCESS"
                )
                return result_text
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                if "429" in str(e) and attempt < self.max_retries - 1:
                    wait_time = 10 * (attempt + 1)
                    logger.warning(f"Groq Rate limited (attempt {attempt + 1}/{self.max_retries}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    tracer.log_llm_call(
                        provider="groq",
                        model=self.model_name,
                        prompt=prompt,
                        response=None,
                        latency_ms=elapsed_ms,
                        status="ERROR",
                        error=str(e)
                    )
                    raise


class GeminiProvider:
    """LLM provider using Google's Gemini API."""
    
    def __init__(self, model_name: str = "gemini-2.5-flash-lite"):
        import google.generativeai as genai
        genai.configure(api_key=settings.gemini_api_key)
        self.model_name = model_name
        self.model = genai.GenerativeModel(model_name)
        self.max_retries = 3
    
    async def generate_structured(self, prompt: Any, schema: Any) -> Any:
        import google.generativeai as genai
        schema_json = schema.model_json_schema()
        schema_name = getattr(schema, "__name__", str(schema))
        
        instruction = f"\n\nIMPORTANT: Return ONLY a valid JSON object matching exactly this schema. Do not include markdown formatting or backticks:\n{json.dumps(schema_json)}"
        
        if isinstance(prompt, str):
            full_prompt = [prompt + instruction]
        elif isinstance(prompt, list):
            full_prompt = prompt + [instruction]
        else:
            full_prompt = [prompt, instruction]
        
        start_time = time.perf_counter()
        for attempt in range(self.max_retries):
            try:
                response = self.model.generate_content(
                    full_prompt,
                    generation_config=genai.GenerationConfig(
                        response_mime_type="application/json",
                    ),
                )
                data = json.loads(response.text)
                parsed_obj = schema(**data)
                
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                tracer.log_llm_call(
                    provider="gemini",
                    model=self.model_name,
                    prompt=full_prompt,
                    response=parsed_obj,
                    latency_ms=elapsed_ms,
                    status="SUCCESS",
                    schema_name=schema_name
                )
                return parsed_obj
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                if "429" in str(e) and attempt < self.max_retries - 1:
                    wait_time = 32 * (attempt + 1)
                    logger.warning(f"Gemini Rate limited (attempt {attempt + 1}/{self.max_retries}). Retrying in {wait_time}s...")
                    tracer.log_llm_call(
                        provider="gemini",
                        model=self.model_name,
                        prompt=full_prompt,
                        response=None,
                        latency_ms=elapsed_ms,
                        status="RETRYING_429",
                        error=f"Attempt {attempt + 1} failed with 429: {str(e)}",
                        schema_name=schema_name
                    )
                    time.sleep(wait_time)
                else:
                    tracer.log_llm_call(
                        provider="gemini",
                        model=self.model_name,
                        prompt=full_prompt,
                        response=None,
                        latency_ms=elapsed_ms,
                        status="ERROR",
                        error=str(e),
                        schema_name=schema_name
                    )
                    raise

    async def generate_text(self, prompt: str) -> str:
        start_time = time.perf_counter()
        for attempt in range(self.max_retries):
            try:
                response = self.model.generate_content(prompt)
                result_text = response.text
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                tracer.log_llm_call(
                    provider="gemini",
                    model=self.model_name,
                    prompt=prompt,
                    response=result_text,
                    latency_ms=elapsed_ms,
                    status="SUCCESS"
                )
                return result_text
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                if "429" in str(e) and attempt < self.max_retries - 1:
                    wait_time = 32 * (attempt + 1)
                    logger.warning(f"Gemini Rate limited (attempt {attempt + 1}/{self.max_retries}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    tracer.log_llm_call(
                        provider="gemini",
                        model=self.model_name,
                        prompt=prompt,
                        response=None,
                        latency_ms=elapsed_ms,
                        status="ERROR",
                        error=str(e)
                    )
                    raise


def get_llm_provider():
    """Factory function to get the configured LLM provider."""
    if settings.llm_provider == "groq":
        return GroqProvider()
    else:
        return GeminiProvider()
