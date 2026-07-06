from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_openai import ChatOpenAI
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_core.messages import HumanMessage, SystemMessage
from pathlib import Path
import sys
import os
from scripts.ingest import ingest_pdf
import qdrant_manager
from utils.format_document import format_documents
from utils.prompts import get_system_message, get_human_message
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from utils.pydantic_schemas import DocMindResponse


# Add parent directory of scripts to sys.path to allow importing backend modules
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)


load_dotenv()



# embedding model 
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    dimensions=1536
)


main_llm = ChatOpenAI(
    model="google/gemini-2.5-flash",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

main_llm_structured = main_llm.with_structured_output(DocMindResponse)


def ask_question(question: str) -> dict:
    collection_name = "doc_mind_chat_pdf"

    pdf_path = Path("sample_pdfs/Building Machine Learning Systems with Python - Second Edition.pdf")

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

    multi_query_retriever = MultiQueryRetriever.from_llm(
        retriever=retriever,
        llm=llm_for_multi_query
    )

    reterived_docs = multi_query_retriever.invoke(question)
    formatted_docs = format_documents(reterived_docs)

    system_message = get_system_message()
    human_message = get_human_message(formatted_docs, question)

    prompt = ChatPromptTemplate.from_messages([
        ("system", "{system}"),
        ("human", "{human}")
    ])

    chain = prompt | main_llm_structured
    result = chain.invoke({
        "system": system_message,
        "human":human_message
    })
    return result


# print(ask_question(question="tell me about regulariztion"))