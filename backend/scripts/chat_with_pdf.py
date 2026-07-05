from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_openai import ChatOpenAI
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from pathlib import Path
import sys
import os
from ingest import ingest_pdf
import qdrant_manager



# Add parent directory of scripts to sys.path to allow importing backend modules
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)


load_dotenv()

pdf_path = Path("sample_pdfs/Building Machine Learning Systems with Python - Second Edition.pdf")

# embedding model 
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    dimensions=1536
)

collection_name = "doc_mind_chat_pdf"

# Connect to Qdrant/Ingest if needed (ingest_pdf handles internal checks)
pdf_absolute_path = str(Path(parent_dir) / pdf_path)
ingest_pdf(pdf_path=pdf_absolute_path, collection_name=collection_name)

# Get the vector store
vector_store = qdrant_manager.get_vector_store(
    collection_name=collection_name,
    embedding=embeddings
)

# retriever 
retriever = vector_store.as_retriever(
    search_kwargs={"k": 5}
)

# multi query retriever 
llm_for_multi_query = ChatOpenAI(
    model="gpt-5-nano"
)
query = "tell me about classification"


# multi_query_retriever = MultiQueryRetriever.from_llm(
#     retriever=retriever,
#     llm=llm_for_multi_query
# )

docs = retriever.invoke(query)

print(docs[0].page_content)

