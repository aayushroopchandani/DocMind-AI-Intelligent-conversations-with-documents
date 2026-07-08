from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apis.users import router as users_router
from apis.chats import router as chats_router
from db.mongodb import init_mongodb, close_mongodb
from services.cloudinary_setup import init_cloudinary

# The ONE FastAPI app for the whole project.
# Routers (like ask_router) are registered here and their endpoints become part of this app.
app = FastAPI(title="DocMind API")

# CORS — lets the Next.js frontend (localhost:3000) call this API.
# This must be on `app`, not on a router.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users_router)
app.include_router(chats_router)


@app.on_event("startup")
async def on_startup() -> None:
    # Setup only: initializes connections/config if env vars are present.
    await init_mongodb()
    init_cloudinary()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await close_mongodb()
