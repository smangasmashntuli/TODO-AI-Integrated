from pathlib import Path

from dotenv import load_dotenv

# Load .env early so services and models see environment variables like GEMINI_API_KEY
load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .database import engine, run_migrations
from .models import Base
from . import routes, routes_ai

app = FastAPI(title="TODO API", version="1.0.0")

Base.metadata.create_all(bind=engine)
run_migrations()
app.include_router(routes.router)
app.include_router(routes_ai.router)

# Plain HTML/CSS/JS frontend, served from the same origin (no CORS needed).
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")

@app.get("/")
def root():
    return {"message": "TODO API is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)


