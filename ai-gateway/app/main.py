import os
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File
from pydantic import BaseModel
import httpx

app = FastAPI(
    title="LMS AI Content & Assessment Engine - API Gateway",
    description="API Gateway for Module A, B, C, D running entirely on self-hosted open-source models.",
    version="1.0.0"
)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

class GenerateRequest(BaseModel):
    prompt: str
    model: str = "llama3"

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "ai-gateway"}

@app.post("/api/v1/generate")
async def generate_content(request: GenerateRequest):
    """
    Generate content using local Ollama instance.
    This supports Module A (Summarisation, Glossary) and Module B (Quiz-Gen).
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": request.model,
                    "prompt": request.prompt,
                    "stream": False
                },
                timeout=60.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Ollama service unavailable: {str(e)}")

@app.post("/api/v1/ingest/document")
async def ingest_document(file: UploadFile = File(...)):
    """
    Module A: Intelligent Document Ingestion
    Upload PDF/PPT and enqueue for processing.
    """
    if not file.filename.endswith(('.pdf', '.pptx')):
        raise HTTPException(status_code=400, detail="Only PDF and PPTX files are supported")
    
    # Placeholder for async Celery task
    task_id = "task_" + os.urandom(8).hex()
    
    return {
        "status": "queued",
        "task_id": task_id,
        "filename": file.filename,
        "message": "Document ingested successfully. Processing started via queue."
    }

@app.post("/api/v1/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Module C: Multimedia Intelligence
    Upload Audio/Video for Whisper transcription.
    """
    # Placeholder for Whisper integration
    task_id = "task_" + os.urandom(8).hex()
    
    return {
        "status": "queued",
        "task_id": task_id,
        "filename": file.filename,
        "message": "Media uploaded. Whisper transcription task queued."
    }
