from fastapi import FastAPI
from .database import engine
from .models import Base
from . import routes

app = FastAPI(title="TODO API", version="1.0.0")

Base.metadata.create_all(bind=engine)
app.include_router(routes.router)

@app.get("/")
def root():
    return {"message": "TODO API is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)


