# Museable — ai-muse-d chaitin'

**A multiplayer group chat; Muse builds consensus as you talk.**

Each person in a group chat gets their own workspace, and a local model
(**Muse Glimmer**) quietly turns what they say into cards in that workspace:
checklists, tables, charts, maps, metrics and notes. Different people end up
with different "views" of the same conversation.

When the conversation reaches a natural conclusion — a wrap-up or consensus
moment, with something built in everyone's workspace — the model synthesizes
all of them into one shared app by itself. No one has to press anything.

## Run

Requires [uv](https://docs.astral.sh/uv), Node.js + npm and [dolt](https://docs.dolthub.com/introduction/installation). `uv run app` also needs `llama-server` (`brew install llama.cpp`). No API key and no `.env` needed; `.env.example` only lists optional port/model overrides.


```bash
uv run demo   # watch the included Street Feast example — no model download
uv run app    # use the app yourself — Muse Glimmer, downloaded once (~17 GB) if needed
```


## Try it yourself (`uv run app`, http://localhost:3000)

The homepage opens on a blank chat ("My first group chat") and immediately walks you through it with a step-by-step overlay tour (spotlighting each control). Reopen it anytime with the **↺ Tutorial** button:

1. **Pick who you're speaking as** in the members bar above the chat — click a member chip. Add a person with **＋ Add person** in that same members bar. You're simulating group members locally; there are no accounts. Every chat starts with blank workspaces.
2. **Pick or create a group chat.** ＋ New group chat lets you name it and choose who's in it. Each chat has its own people and its own shared workspace set.
3. **Just chat — the model decides when to build.** Talk to each other normally. Casual chat is saved silently: the model stays quiet and changes nothing. When someone shares something concrete — facts, numbers, dates, places, options, tasks, worries, decisions — the model grows *that speaker's* workspace on the right on its own. (The "Try saying:" chips, like "Make a checklist…", are a fine way to nudge it, but they're not commands — a plain statement of fact works too.)
4. **There is no merge step.** Keep chatting. When the group reaches a natural wrap-up and everyone has cards, the model merges everyone's workspaces into the shared (✦ AI) app by itself. If you're impatient, the small **Merge now (optional)** button in the workspace column forces it immediately — it's an override, not a required step.

Switch people in the members bar to see how the same conversation became a different app for each of them, and add/remove people from the chat as plans change.

Manual controls if you want them: add cards by hand under the workspace, click History to time-travel to any earlier commit, and open Debug to see every model call that built a card.

`uv run demo` (http://localhost:3000/demo) is a read-only watch-through of a finished Street Feast example — press Play or step message-by-message to see what the model built, then go try it live.
