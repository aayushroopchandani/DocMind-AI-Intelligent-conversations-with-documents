def format_documents(documents):
    formatted_chunks = []

    for document in documents:
        source = document.metadata.get("source", "Unknown document")
        page = document.metadata.get("page", 0) + 1

        formatted_chunks.append(
            f"""
Source: {source}
Page: {page}

Content:
{document.page_content}
""".strip()
        )

    return "\n\n---\n\n".join(formatted_chunks)