import google.generativeai as genai
from app.config import settings
from typing import Any

# Configure Gemini with the API key from settings
genai.configure(api_key=settings.gemini_api_key)

class GeminiProvider:
    def __init__(self, model_name: str = "gemini-1.5-pro-latest"):
        self.model = genai.GenerativeModel(model_name)
    
    async def generate_structured(self, prompt: str, schema: Any) -> Any:
        response = self.model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        # Parse the JSON string back to the pydantic object
        import json
        data = json.loads(response.text)
        return schema(**data)

    async def generate_text(self, prompt: str) -> str:
        # Stub for text generation
        response = self.model.generate_content(prompt)
        return response.text
