from fastapi import UploadFile, File
import uuid, os

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

router = APIRouter()

class UploadResponse(BaseModel):
    filename: str
    num_pages: int

@router.post("/upload-pdf",response_model=UploadResponse):
async def upload_pdf(file: UploadFile = File(...)) -> UploadResponse:
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDFs allowed")

    document_id = str(uuid.uuid4())
    path = os.path.join(UPLOAD_DIR, f"{document_id}.pdf")

