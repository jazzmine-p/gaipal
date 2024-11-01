import os
import yaml
import warnings
from modules.data_loader import process_pdf
from modules.config.constants import *
from modules.helpers import setup_logging

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_community.llms import LlamaCpp
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langchain.schema import StrOutputParser

from langchain.schema.runnable import Runnable, RunnablePassthrough, RunnableConfig
from langchain.callbacks.base import BaseCallbackHandler

import chainlit as cl
import chainlit.data as cl_data
from typing import Optional

warnings.filterwarnings("ignore")
logger = setup_logging(chatbot_log_dir, "app.log")

# Load configurations
with open(config_chatbot_dir, "r") as config_file:
    config = yaml.safe_load(config_file)
embedding_config = config["embedding_model"]["sentence-transformers"]
retriever_config = config["retriever"]
llama_config = config["llama_cpp"]

# Embedding
embedding_model = HuggingFaceEmbeddings(
    model_name=embedding_config,
    model_kwargs={
        "device": "cpu",
        "token": huggingface_token,
        "trust_remote_code": True,
    },
)

all_splits = process_pdf(chatbot_dir)

# Vectorstore
if os.path.exists("data/vectorstore/chroma.sqlite3"):
    vectorstore = Chroma(
        persist_directory="data/vectorstore", embedding_function=embedding_model
    )
else:
    vectorstore = Chroma.from_documents(
        documents=all_splits,
        embedding=embedding_model,
        persist_directory="data/vectorstore",
    )

logger.info("Vector store created")
print("Vector store created")

# Retrieval
retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 3})

logger.info("Model loading...")
print("Model loading...")
model = LlamaCpp(
    model_path="C:/Users/pnhua/Downloads/tinyllama-1.1b-chat-v1.0.Q4_K_S.gguf",
    n_batch=512,
    n_ctx=2048,
    f16_kv=True,
    verbose=True,
    n_threads=2,
    temperature=0.7,
)

# ChatOpenAI(model_name="gpt-4o-mini", streaming=True)

logger.info("Model loaded")
print("Model loaded")


# Functions to handle chatbot
@cl.set_starters
async def set_starters():
    """
    Set starter messages for the chatbot.
    """
    # Return Starters only if the chat is new

    return [
        cl.Starter(
            label="GenAI Usage in Academia?",
            message="How is GenAI currently be used in academia?",
            # icon="",
        ),
        cl.Starter(
            label="GenAI Policy across Businesses?",
            message="What are the different policies that businesses have regarding the use of Generative AI?",
            # icon="",
        ),
        cl.Starter(
            label="?",
            message="?",
            # icon="",
        ),
    ]


@cl.on_chat_start
async def on_chat_start():
    template = """
            "system\n"
            "You are an AI assistant for Generative AI Policy Insights, developed by the Boston University's GenAI Task Force. Your main mission is to help users understand how different organizations perceive and make policies regarding the use of GenAI. Answer the user's question using the provided context that is relevant. The context is ordered by relevance. "
            "If you don't know the answer, do your best without making things up. If you cannot answer, just say you don't have enough relevant information to answer the questions. Keep the conversation flowing naturally. "
            "Always cite the source of the information. Use the source context that is most relevant. "
            "Keep the answer concise, yet professional and informative. Avoid sounding repetitive or robotic.\n"
            "\n\n"
            "user\n"
            "Context:\n{context}\n\n"
            "Question: {input}\n"
            "\n\n"
            "assistant"
    """
    prompt = ChatPromptTemplate.from_template(template)

    def format_docs(docs):
        return "\n\n".join([d.page_content for d in docs])

    runnable = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | prompt
        | model
        | StrOutputParser()
    )

    cl.user_session.set("runnable", runnable)


# Function to handle incoming messages
@cl.on_message
async def on_message(message: cl.Message):
    runnable = cl.user_session.get("runnable")  # type: Runnable
    msg = cl.Message(content="")

    class PostMessageHandler(BaseCallbackHandler):
        """
        Callback handler for handling the retriever and LLM processes.
        Used to post the sources of the retrieved documents as a Chainlit element.
        """

        def __init__(self, msg: cl.Message):
            BaseCallbackHandler.__init__(self)
            self.msg = msg
            self.sources = set()  # To store unique pairs

        def on_retriever_end(self, documents, *, run_id, parent_run_id, **kwargs):
            for d in documents:
                source_page_pair = (d.metadata["source"], d.metadata["page"])
                self.sources.add(source_page_pair)

        def on_llm_end(self, response, *, run_id, parent_run_id, **kwargs):
            if len(self.sources):
                sources_text = "\n".join(
                    [f"{source}#page={page}" for source, page in self.sources]
                )
                self.msg.elements.append(
                    cl.Text(name="Sources", content=sources_text, display="inline")
                )

    async for chunk in runnable.astream(
        message.content,
        config=RunnableConfig(
            callbacks=[cl.LangchainCallbackHandler(), PostMessageHandler(msg)]
        ),
    ):
        await msg.stream_token(chunk)

    await msg.send()


@cl.password_auth_callback
def auth_callback(username: str, password: str):
    # Fetch the user matching username from your database
    # and compare the hashed password with the value stored in the database
    if (username, password) == ("admin", "admin"):
        return cl.User(
            identifier="admin", metadata={"role": "admin", "provider": "credentials"}
        )
    else:
        return None
