

# Whatsapp AI Chatbot

<div align="center">

<img src="https://raw.githubusercontent.com/Koodattu/ucs-llm-voice-image-edit/main/assets/gls.png" style="height: 200px;" />
    
</div>


## Overview

Whats-AI-Assistant is an AI-powered chatbot that was initially developed as a proof of concept demo at the [GPT Lab Seinäjoki](https://www.tuni.fi/en/research/gpt-lab-seinajoki) AI Research Initiative, and its development continued in Tampere University's [AIming for productivity](https://projektit.seamk.fi/alykkaat-teknologiat/vailla-tuottavuutta/) ERDF project (403708). It integrates with WhatsApp using the Neonize framework, leveraging OpenAI, Azure OpenAI, OpenRouter, or local llama.cpp models to generate responses based on chat history and file content. The assistant supports multiple languages and can summarize conversations for improved interaction quality.

<div align="center">

**This demo showcases AI-driven conversational interactions and automation, leveraging WhatsApp as a primary interface. It's made only as a proof-of-concept and for educational demo purposes.**

</div>

## Features

- **WhatsApp Integration**: Uses Neonize to interact with WhatsApp users.
- **Multi-Provider LLM Support**: Works with OpenAI, Azure OpenAI, OpenRouter, or local llama.cpp servers.
- **Watchdog Filter**: A secondary LLM call filters out irrelevant or inappropriate messages before the main response.
- **File Ingestion**: Accepts PDF, DOCX, and TXT files from admins — content is extracted and used as context for all subsequent responses.
- **Admin Commands**: Runtime configuration via WhatsApp commands (edit prompts, manage files, pause/resume bot).
- **Persistent Storage**: Fetches previous conversation history and saves all conversations in an SQLite database.
- **Rate Limiting**: Prevents spam by limiting responses per user per time window.
- **Conversation Context**: Injects recent message history into the LLM prompt for coherent multi-turn conversations.

## Installation

### Prerequisites

- Python 3.10+
- Virtual environment (optional but recommended)
- For local models: [llama.cpp](https://github.com/ggml-org/llama.cpp) with a GGUF model

### Setup

```bash
# Clone the repository
git clone https://github.com/GPT-Laboratory/whatsapp-ai-chatbot
cd whatsapp-ai-chatbot

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`

# Install dependencies
pip install -r requirements.txt
```

### Configuration

1. **Environment Variables**:
   - Copy `example.env` to `.env` and configure:
     ```bash
     cp example.env .env
     ```
   - Set your `ASSISTANT_NAME` and `ADMIN_NUMBERS` (comma-separated phone numbers in international format, e.g. `+358123456789`)
   - Choose your LLM provider and fill in the corresponding keys

2. **LLM Provider Options**:

   | Provider | Env vars | Notes |
   |----------|----------|-------|
   | `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` | Standard OpenAI API |
   | `azure` | `AZURE_ENDPOINT`, `AZURE_DEPLOYMENT_NAME`, `AZURE_SUBSCRIPTION_KEY`, `AZURE_API_VERSION` | Azure OpenAI Service |
   | `openrouter` | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | OpenRouter gateway |
   | `llamacpp` | `LLAMACPP_BASE_URL`, `LLAMACPP_MODEL` | Local llama.cpp server (OpenAI-compatible) |

3. **Using llama.cpp (local models)**:
   ```bash
   # Install llama.cpp from https://github.com/ggml-org/llama.cpp
   # Download a GGUF model (e.g. from HuggingFace)
   # Start the server:
   llama-server -m qwen2.5-7b-instruct-q4_k_m.gguf --port 8080 -ngl 99
   
   # In .env:
   LLM_PROVIDER=llamacpp
   LLAMACPP_BASE_URL=http://localhost:8080/v1
   LLAMACPP_MODEL=qwen2.5-7b-instruct-q4_k_m
   ```

## Usage

Start the assistant with:

```bash
python main.py
```

Neonize will print a QR code in the console to link your WhatsApp account.

### Admin Commands

Send these from your admin number (configured in `ADMIN_NUMBERS`):

| Command | Description |
|---------|-------------|
| `!commands` | List all available commands |
| `!files` | List uploaded files |
| `!removefile <ID or name>` | Remove a file |
| `!prompts` | Show current editable prompts |
| `!editprompt <name> <content>` | Edit a public prompt |
| `!renamebot <name>` | Change the bot's name |
| `!reset` | Clear conversation history for your number |
| `!pause` | Pause the bot |
| `!resume` | Resume the bot |
| `!permanentstop` | Stop the bot permanently |

### File Upload

Admins can send PDF, DOCX, or TXT files. The content is extracted, saved to `converted/`, and injected into the LLM context for all subsequent responses.

## File Structure

- `.env` - Stores environment variables such as API keys.
- `config.py` - Contains configuration for database paths, model selection, and language settings.
- `database.py` - Handles message storage using SQLite.
- `llm.py` - Manages interactions with all LLM providers (OpenAI, Azure, OpenRouter, llama.cpp).
- `whatsapp.py` - Listens to messages, handles commands, file ingestion, and AI responses.
- `prompts.py` - System prompts (public editable / private).
- `main.py` - Initializes and runs the WhatsApp assistant.
- `requirements.txt` - Lists the required dependencies.
- `LICENSE` - MIT license.

## Acknowledgments

This project utilizes the [Neonize](https://github.com/krypton-byte/neonize) Python library, which acts as a wrapper for [Whatsmeow](https://github.com/tulir/whatsmeow), enabling the WhatsApp automation. Use at your own risk.

## Contributing

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature-name`
3. Commit your changes: `git commit -m "Add feature"`
4. Push to the branch: `git push origin feature-name`
5. Open a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Funding

<div align="center">

<img src="assets/eu_logo.jfif" alt="Co-funded by the European Union" style="height: 180px;" />

</div>

The implementation work was supported by Tampere University's [AIming for productivity](https://projektit.seamk.fi/alykkaat-teknologiat/vailla-tuottavuutta/) project (403708), co-funded by the European Union from its European Regional Development Fund (ERDF).
