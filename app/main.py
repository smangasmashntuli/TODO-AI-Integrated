from fastapi import FastAPI
from .database import engine, run_migrations
from .models import Base
from . import routes, routes_ai

app = FastAPI(title="TODO API", version="1.0.0")

Base.metadata.create_all(bind=engine)
run_migrations()
app.include_router(routes.router)
app.include_router(routes_ai.router)

@app.get("/")
def root():
    return {"message": "TODO API is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)


