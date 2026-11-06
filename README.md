# Museable — ai-muse-d chaitin'

**A multiplayer group chat; Muse builds consensus as you talk.**

Each person builds their own workspace -- shared understanding observed as an app.
A local model (**Muse Glimmer**) turns the conversation into cards in that
person's workspace: checklists, tables, charts, maps, metrics and notes.

Different people end up with different "views" of the same conversation.
At given points in time, everyone's workspaces get merged into one shared app.

## Run

Requires [uv](https://docs.astral.sh/uv), Node.js + npm and [dolt](https://docs.dolthub.com/introduction/installation). `uv run app` also needs `llama-server` (`brew install llama.cpp`). No API key and no `.env` needed; `.env.example` only lists optional port/model overrides.


```bash
uv run demo   # watch the included Street Feast example — no model download
uv run app    # use the app yourself — Muse Glimmer, downloaded once (~17 GB) if needed
```


## Try it yourself (`uv run app`, http://localhost:3000)

The homepage opens on a blank chat ("My first group chat") and immediately walks you through it with a step-by-step overlay tour (spotlighting each control). Reopen it anytime with the **↺ Tutorial** button:

1. **Pick who you're speaking as** (top bar), or **add a person** with ＋ Add person. You're simulating group members locally; there are no accounts. Every chat starts with blank workspaces.
2. **Pick or create a group chat.** ＋ New group chat lets you name it and choose who's in it. Each chat has its own people and its own shared workspace set.
3. **Send a message that asks for something.** Use the "Ask the model to build" chips or your own words. Muse Glimmer reads the chat and builds cards into the speaker's workspace on the right. Plain chatter just gets saved — the model stays quiet until there's something to build.
4. **Switch people** to see how the same conversation became a different app for each of them, add/remove people from the chat as plans change, then hit **✦ Merge everyone's workspaces** to build the shared app.

Manual controls if you want them: add cards by hand under the workspace, click History to time-travel to any earlier commit, and open Debug to see every model call that built a card.

`uv run demo` (http://localhost:3000/demo) is a read-only watch-through of a finished Street Feast example — press Play or step message-by-message to see what the model built, then go try it live.
