import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.telegram.bot import init_bot
from app.jobs.worker import JobWorker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up FlatHunter...")
    bot, dp = await init_bot()
    
    # Start bot polling in background
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    # Start worker loop in background
    worker = JobWorker()
    worker_task = asyncio.create_task(worker.run())
    
    yield
    
    # Shutdown
    logger.info("Shutting down FlatHunter...")
    polling_task.cancel()
    worker_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan, title="FlatHunter V0")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
