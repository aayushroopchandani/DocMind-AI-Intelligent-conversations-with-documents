from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from utils.pydantic_schemas import DocMindResponse
from scripts.chat_with_pdf import ask_question

# APIRouter groups related endpoints.
# main.py creates the FastAPI `app` and registers this router with
# app.include_router(router) — that is where the endpoints become live.
router = APIRouter()


class AskRequest(BaseModel):
    question: str


@router.post("/ask", response_model=DocMindResponse)
async def ask(body: AskRequest) -> DocMindResponse:
    try:
        response = ask_question(body.question)
        return response

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail="Server side error")
