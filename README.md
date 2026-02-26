# 🐠•°•°•°•°°~ Mnemo Crew °:•.🐠*.•🪸.•:°

Welcome to the Mnemo Crew project, powered by [crewAI](https://crewai.com). This template is designed to give an AI assistant to work with. check the roadmap to see where is the current work right now.

## What is Mnemo ?

The main goal of this project is to provide an AI assistant with specialized memory, enabling it to remember our conversations, preferences, or anecdotes useful in its work.

### how does it remember

To achieve this, two types of memory are being developed: a traditional RAG memory (knowledge, think of it as a library) and a second, anecdotal memory, comparable to a general-purpose memory. based

This all-purpose memory is formed in two stages: a memory.md file that acts as a user-readable truth table, and a database storing this information as chunks, stored in plain text for keyword searches, and stored as vectors for vectorized searches. This second search allows the concepts of the anecdote to be linked by proximity. This is exactly what happens in a RAG (Relationship Access Group), but this time geared towards preserving context about the user's preferences.

### Understanding the crew

It's an assembly of crews, each with its own objective: the conversational crew can search two types of memory: short-term, session-based, and long-term, based on a Markdown file with a database for indexing. I'm using FTS5 as a search engine for vectors and key word.

The consolidation crew is used to synchronize and maintain the database and the Markdown file.

## Installation

I'll prepare for phase 3 a script to simplify installation. But right now, two steps to make it work :
```bash
python3 src/Mnemo/init_db.py 
crewai run
```

first run of crewai should create a python environment to make it work, with needed dependancies.

At this moment, you can ingest documents into your crew with :
```
python -m Mnemo.main ingest "documents/Artificial Intelligence A Modern Approach.pdf" 
```


## Support

For support, questions, or feedback regarding the Mnemo Crew or crewAI.
- crewAI's [documentation](https://docs.crewai.com)
- there is no Discord yet, depends of how much people will contribute to it.

Let's create wonders together.
