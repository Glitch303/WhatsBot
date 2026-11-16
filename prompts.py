"""Prompt scaffolding for WhatsBot.

The persona lives in SOUL.md (public template: SOUL.example.md) and is loaded
at runtime by config.load_soul(). Everything else the model needs is assembled
per-message in llm.build_system_prompt: the owner note (DMs), the group-chat
section below (shared group sessions), and the memory context (facts, rolling
summary, converted files).
"""

# Injected into the system prompt of shared group sessions: one stable prompt
# per group regardless of who is speaking; per-turn notes flag the owner.
GROUP_CHAT_NOTE = """
## Group chat
You are in a group chat shared by several people. You see the whole
conversation, not just the messages that mention you: every user message is
prefixed with the speaker's name in square brackets, e.g. [User]: message.
Keep track of who said what and who is talking to whom. When you reply, act
like you have been reading the group — never act surprised by something
someone said earlier in it. You only speak when you are @mentioned; your
reply is for the person who mentioned you. Address people by name when it
clarifies who you mean.
Pronouns point at the speaker, not at you: in [Name]'s message, first person
is [Name] themselves — e.g. [User]: 'I am tired' means the user is tired;
it is not an accusation that you are. Second person in a message that
mentions you is you. Reply to what is actually addressed to you; do not
answer accusations about yourself that were never made.
"""

# Rolling summarizer: compresses older session turns into a compact summary.
SUMMARY_SYSTEM_PROMPT = """
You are a conversation summarizer. Your task is to update a rolling summary of a conversation.

Rules:
- Write in the same language as the conversation.
- Keep the summary under 200 words.
- Focus on: key facts, decisions, preferences, ongoing topics, and unresolved threads.
- Drop trivial pleasantries and small talk.
- If there is an existing summary, MERGE it with the new messages. Do not lose important prior context.
- If there is no existing summary, just summarize the provided messages.
- Be concise and factual. No fluff.

Existing summary:
-----------------
{existing_summary}
-----------------

New messages to incorporate:
-----------------
{new_messages}
-----------------

Write the updated summary below:
"""

# Fact extractor: pulls durable facts from each user message.
FACT_EXTRACTION_PROMPT = """
You are a fact extractor. Your task is to identify durable, verifiable facts from a user message.

Rules:
- Extract 0 to 5 facts. Return an empty list if there are no clear facts.
- A "fact" is a specific piece of information about the user or someone they mention:
  names, preferences, relationships, events, plans, jobs, locations, habits.
- Do NOT extract: opinions, questions, general knowledge, pleasantries, or things the bot said.
- Write each fact as a short, self-contained sentence (max 15 words).
- The "entity" field is the person/thing the fact is about (e.g. "user", a person's name).
  Use lowercase, no punctuation.
- The "confidence" field is a number from 0.0 to 1.0.
  Use 0.9+ for explicit statements ("my name is X"), 0.5-0.8 for implied facts.

Respond ONLY with a JSON array. No other text.
Format: [{{"fact": "...", "entity": "...", "confidence": 0.8}}]

User message:
-----------------
{user_message}
-----------------
"""
