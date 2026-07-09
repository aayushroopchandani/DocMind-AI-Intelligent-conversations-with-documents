"""Document status endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apis.deps import current_user_id, verify_internal_secret
from db import crud
from db.models.document import NodeData

router = APIRouter(prefix="/documents", tags=["documents"])


class NodeIngestionStatusResponse(BaseModel):
    document_id: str
    status: Literal["ready", "not_ready"]


class DocumentNodesResponse(BaseModel):
    document_id: str
    user_id: str
    nodes: NodeData


@router.get("/{document_id}/nodes", response_model=DocumentNodesResponse)
async def get_document_nodes(
    document_id: str,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> DocumentNodesResponse:
    try:
        document = await crud.get_document_nodes(
            user_id=user_id,
            document_id=document_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentNodesResponse(**document)


@router.get("/{document_id}/nodes/status", response_model=NodeIngestionStatusResponse)
async def get_nodes_ingestion_status(
    document_id: str,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> NodeIngestionStatusResponse:
    try:
        status = await crud.get_nodes_ingestion_status(
            user_id=user_id,
            document_id=document_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if status is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return NodeIngestionStatusResponse(document_id=document_id, status=status)
