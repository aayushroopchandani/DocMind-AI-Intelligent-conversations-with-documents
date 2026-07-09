from qdrant_client.models import Filter, FieldCondition, MatchValue
from operation_on_nodes import get_node_scope
from db import crud

async def searching_for_chunks_with_node_id(node_id: str,doc_id : str m, user_id: str ) -> list[dict]:
    """
    Searching for chunks with node id
    """
    
    document = await crud.get_document_nodes(
        user_id = user_id,
        document_id = doc_id,
    )

    if document is None:
        raise ValueError(f"Document with id {doc_id} not found") 

    nodes = document.get("nodes",{}).get("nodes",[])        

    if not nodes:
        raise ValueError(f"Nodes not found for document with id {doc_id}")


    scope_node_ids = get_node_scope(nodes,node_id)
    
        