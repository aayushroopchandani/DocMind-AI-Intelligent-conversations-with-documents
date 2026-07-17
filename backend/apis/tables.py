"""Read APIs for normalized PDF tables."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from apis.deps import current_user_id, verify_internal_secret
from db import crud
from db.models.structured_table import StructuredTable, StructuredTableList


router = APIRouter(prefix="/tables", tags=["tables"])


@router.get("", response_model=StructuredTableList)
async def list_tables(
    document_id: str = Query(..., min_length=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> StructuredTableList:
    try:
        tables, total = await crud.list_document_tables(
            user_id=user_id,
            document_id=document_id,
            offset=offset,
            limit=limit,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return StructuredTableList(
        document_id=document_id,
        tables=[StructuredTable(**table) for table in tables],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{table_id}", response_model=StructuredTable)
async def get_table(
    table_id: str,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> StructuredTable:
    try:
        table = await crud.get_structured_table(user_id=user_id, table_id=table_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return StructuredTable(**table)
