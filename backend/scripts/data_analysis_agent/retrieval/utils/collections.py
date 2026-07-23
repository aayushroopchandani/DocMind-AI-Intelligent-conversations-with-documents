from typing import Final


STRUCTURED_TABLES_COLLECTION: Final = "structured_tables"
TABLE_PAYLOAD_INDEXES: Final[tuple[str, ...]] = (
    "content_type",
    "table_id",
    "document_id",
    "user_id",
    "chat_id",
    "node_id",
)
