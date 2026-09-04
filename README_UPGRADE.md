# AWAB AI 🤖📄

An AI-powered PDF assistant that allows users to upload PDF documents, ask questions about their content, and receive intelligent answers using Retrieval-Augmented Generation (RAG).

AWAB AI combines **Google Gemini**, **FAISS**, **Sentence Transformers**, and **PyMuPDF** to understand uploaded documents and provide context-aware answers through an interactive web interface.

## ✨ Features

* 📄 Upload and analyze PDF documents
* 💬 Ask questions about uploaded documents
* 🤖 AI-powered answers using Google Gemini
* 🔎 Retrieval-Augmented Generation (RAG)
* 🧠 Semantic document search
* 📚 Support for multiple PDFs in a conversation
* 📁 Conversation folders
* 📌 Pin conversations
* ✏️ Rename conversations
* 🗑️ Delete conversations
* 🏷️ Automatic conversation title suggestions
* 📤 Export conversations
* 🌙 Dark and Light mode
* 🌐 Arabic RTL interface support
* ⚡ Real-time AI responses using Server-Sent Events (SSE)
* 🔐 Session-based conversations

## 🛠️ Technologies

* **Python**
* **FastAPI**
* **Google Gemini**
* **Sentence Transformers**
* **FAISS**
* **PyMuPDF**
* **SQLite**
* **OpenCV** — not required for the core application
* **Jinja2**
* **Uvicorn**
* **python-multipart**
* **python-dotenv**

## 🧠 How It Works

AWAB AI uses a Retrieval-Augmented Generation architecture to answer questions based on the user's uploaded documents.

### 1. Upload PDF

The user uploads one or more PDF documents.

### 2. Extract Text

**PyMuPDF** extracts readable text from the uploaded PDF files.

### 3. Split the Document

The extracted text is divided into smaller chunks that can be efficiently processed and searched.

### 4. Create Embeddings

**Sentence Transformers** converts the text chunks into numerical vector representations.

The project uses:

```text
paraphrase-multilingual-MiniLM-L12-v2
```

This multilingual embedding model helps the system work with different languages, including Arabic.

### 5. Store Vectors

The generated embeddings are stored and searched using **FAISS**.

### 6. Ask a Question

When the user asks a question, the question is converted into an embedding and compared with the document vectors.

### 7. Retrieve Relevant Context

AWAB AI retrieves the most relevant document chunks related to the user's question.

### 8. Generate the Answer

The retrieved context is sent to **Google Gemini**, which generates the final answer based on the document content.

### 9. Stream the Response

The generated answer can be streamed to the browser using **Server-Sent Events (SSE)** for a real-time chat experience.

## 🏗️ Architecture

```text
User
 │
 ▼
Web Interface
 │
 ▼
FastAPI
 │
 ├── PDF Upload
 │
 ├── Conversation Management
 │
 └── Chat
       │
       ▼
   PDF Text Extraction
       │
       ▼
   Text Chunking
       │
       ▼
Sentence Transformers
       │
       ▼
      FAISS
       │
       ▼
Relevant Document Chunks
       │
       ▼
   Google Gemini
       │
       ▼
   AI Response
       │
       ▼
   Web Interface
```

## 📁 Project Structure

```text
Awab-AI-PDF/
│
├── app/
│   ├── ai/
│   │   └── gemini.py
│   │
│   ├── database/
│   │   └── chat_database.py
│   │
│   ├── services/
│   │   ├── file_manager.py
│   │   ├── pdf_reader.py
│   │   ├── search_engine.py
│   │   └── vector_store.py
│   │
│   ├── templates/
│   │   ├── chat.html
│   │   ├── index.html
│   │   └── success.html
│   │
│   └── main.py
│
├── main.py
├── requirements.txt
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile
├── Procfile
├── railway.json
└── README.md
```

## 📦 Installation

Clone the repository:

```bash
git clone https://github.com/awabwdbashry-sketch/Awab-AI-PDF.git
```

Enter the project directory:

```bash
cd Awab-AI-PDF
```

Install the required dependencies:

```bash
pip install -r requirements.txt
```

## 🔑 Environment Variables

Create a `.env` file based on `.env.example`.

The application requires a Google Gemini API key.

Example:

```env
GOOGLE_API_KEY=your_api_key_here
```

Never commit your real API key to GitHub.

## ▶️ Running the Application

Start the FastAPI application using Uvicorn:

```bash
uvicorn app.main:app --reload
```

The application will then be available through the local development server.

## 💬 Main Application Functions

AWAB AI provides several conversation operations, including:

* Create a new conversation
* Upload documents
* Open existing conversations
* Ask questions
* Stream AI responses
* Rename conversations
* Delete conversations
* Organize conversations into folders
* Pin conversations
* Export conversations

## ⚡ Real-Time AI Chat

The chat interface uses **Server-Sent Events (SSE)** to stream AI responses to the browser.

Instead of waiting for the entire response to be generated, users can see the answer appear progressively in the chat interface.

## 📄 PDF Processing

The PDF processing pipeline is based on **PyMuPDF**.

The system:

1. Validates the uploaded PDF.
2. Extracts its text.
3. Splits the content into searchable chunks.
4. Generates embeddings.
5. Stores the vectors.
6. Retrieves relevant chunks when a question is asked.

## 🔎 Semantic Search

AWAB AI does not rely only on exact keyword matching.

The application uses vector embeddings to find document sections that are semantically related to the user's question.

This allows questions to be understood even when the exact words do not appear in the document.

## 🌍 Multilingual Support

The embedding model used by AWAB AI is multilingual:

```text
paraphrase-multilingual-MiniLM-L12-v2
```

The interface also supports:

* Arabic
* Right-to-left (RTL) layout
* Dark mode
* Light mode

## 🗄️ Database

AWAB AI uses **SQLite** for conversation and session data.

The database stores information required to manage conversations and user sessions.

The application uses a session-based approach rather than requiring a traditional user account system.

## 🚀 Deployment

The project includes deployment configuration for cloud hosting platforms.

Included deployment-related files:

```text
Dockerfile
Procfile
railway.json
```

These files allow the project to be prepared for deployment in supported hosting environments.

## 📋 Requirements

* Python 3.9+
* Google Gemini API key
* Internet connection
* A modern web browser
* Sufficient storage for uploaded documents and vector data

## 🔐 Security Notes

* Keep your Gemini API key private.
* Never commit `.env` files containing real credentials.
* Do not expose private API keys in frontend code.
* Use environment variables for sensitive configuration.

## 📌 Project Status

AWAB AI is an active AI document-assistant project focused on:

* Document understanding
* RAG-based question answering
* AI-powered PDF analysis
* Semantic search
* Real-time conversations

## 📄 License

This project is available for educational and personal use.
