# Setup instructions for Claude Code

You are Claude Code, and the user asked you to install the Artifact Library from this repository. Do every
step yourself. Ask the user only where this file says so, and use the AskUserQuestion tool for those
questions. Keep the user posted in one short line per step.

The install folder is `~/.claude/artifact-library`. The Raycast extension expects the scripts there, so
don't choose another folder.

## 1. Check the basics

Run these and stop with a clear message if one fails:

- `uname` must print `Darwin`. The library only works on macOS.
- `/usr/bin/python3 --version` must work. If it doesn't, tell the user to run `xcode-select --install`
  and to start the setup again afterwards.
- `node --version` must print v22 or newer. If it is missing or older, tell the user to install
  Node from nodejs.org (or `brew install node`) and to start again.
- `ls -d /Applications/Raycast.app` must find Raycast. If it doesn't, tell the user to install it from
  raycast.com and to start again.
- Optional: Google Chrome, Chromium, Brave or Edge in `/Applications`. It renders the thumbnails.
  Without one, every artifact gets a title card instead of a screenshot. Mention this, but go on.

## 2. Get the code

- If `~/.claude/artifact-library` doesn't exist:
  `git clone https://github.com/alexnb98/raycast-claude-artifact-library.git ~/.claude/artifact-library`
- If it exists, update it: `git -C ~/.claude/artifact-library pull --ff-only`

## 3. Install the Claude Code hook

Run `/usr/bin/python3 ~/.claude/artifact-library/scripts/install.py`. It installs Pillow for macOS's
Python if needed, creates `~/Documents/Artifacts`, and adds a PostToolUse hook on the `Artifact` tool to
`~/.claude/settings.json` (with a backup next to it). It prints JSON. If it has an `error`, show that
error to the user and stop.

Tell the user that new Claude Code sessions pick up the hook. They may need to restart Claude Code
before the first publish is caught.

## 4. Ask about categories

Categories are optional. They sort artifacts by the folder they were made in (for example Work and
Personal). Raycast then shows a coloured tag and a category filter, and Recording mode can hide private
categories during screen recordings.

First, collect folder ideas for the questions below: list the project folders in `~/.claude/projects`.
Each folder name is a path with `/` turned into `-`. Turn them back into real paths, keep the ones that
exist, and group them by their parent folder.

Then ask with AskUserQuestion:

1. "Do you want to sort your artifacts into categories?"
   - "No categories" (Recommended for a first try): skip to step 5 and don't write a config file.
   - "Work and Personal": two categories, `work` and `personal`.
   - "Let me name them": ask for the names in a follow-up question, at most 6.
2. For each category except the last one: "Which folders hold your <label> projects?" Offer up to 4 of
   the folder groups you found as options (multiSelect), and let the user type others. The last
   category gets the remaining folders only if the user names some. An artifact from an unlisted
   folder gets no category.
3. Optional, only if they chose categories: "Any words in a title that should put an artifact in a
   category, whatever folder it came from?" (for example a client's name or "wedding"). Skip it if the
   user doesn't want this.
4. If there are 2 or more categories: "Which categories should Recording mode hide?" (multiSelect,
   with "None" as an option). Personal categories are the usual choice.

Write `~/Documents/Artifacts/config.json` in this shape. Names are lowercase letters, digits and
hyphens. `folders` are absolute paths or start with `~`. `description` is one short line that helps
Ask Haiku pick the category. Don't put personal details into it beyond what the user told you.

```json
{
  "categories": [
    {"name": "work", "label": "Work", "description": "my job projects",
     "folders": ["~/code/acme"], "words": [], "private": false},
    {"name": "personal", "label": "Personal", "description": "travel, home, side projects",
     "folders": ["~/Documents/personal"], "words": ["wedding"], "private": true}
  ]
}
```

Check it with `/usr/bin/python3 ~/.claude/artifact-library/scripts/artlib.py categories`.

## 5. Import artifacts that already exist

Ask with AskUserQuestion: "Import the artifacts you already published from Claude Code?"
("Yes, import them" (Recommended) / "No, start empty").

If yes:

1. Call the Artifact tool with `action: "list"`, `scope: "mine"` and `limit: 200`. The import only keeps
   artifacts that appear in such a list, so deleted ones stay out. If the result says more exist, list
   the next page too.
2. Run `/usr/bin/python3 ~/.claude/artifact-library/scripts/backfill.py scan` and report the numbers it
   prints.
3. If there are categories, run `/usr/bin/python3 ~/.claude/artifact-library/scripts/artlib.py recategorize`.
4. Start the thumbnails in the background (they can take a few minutes):
   `/usr/bin/python3 ~/.claude/artifact-library/scripts/backfill.py render`.

Artifacts whose page isn't on this Mac anymore get a title card. That's fine.

## 6. Install the Raycast extension

1. `cd ~/.claude/artifact-library/raycast && npm install`
2. Run `npx ray develop` in the background, wait until its output says `built extension successfully`,
   then stop it. Raycast keeps the extension after that. If Raycast shows a dialog, tell the user to
   accept it.
3. Tell the user to open Raycast and type "Search Artifacts".

## 7. Ask Haiku (optional)

Ask Haiku (⌘↵ in Search Artifacts) turns a vague sentence like "the deck about pricing from last month"
into a search. It needs a long-lived Claude token, and only the user may handle that token. Never run
`claude setup-token` yourself, and never ask the user to paste the token into this chat.

Tell the user to do this:

1. Open Terminal and run `claude setup-token`. It opens the browser to log in, then prints a token that
   starts with `sk-ant-oat`.
2. Copy the token.
3. In Raycast, open Search Artifacts, press ⌘K, choose "Configure Command", and paste it into
   "Claude token (for Ask Haiku)". Raycast stores it encrypted.

## 8. Shortcut (optional)

Suggest a global hotkey: ⌃⌥A ("A" for artifacts). It rarely clashes with other apps. The user sets it
in Raycast settings (⌘,) → Extensions → Artifact Library → Search Artifacts → Record Hotkey.

## 9. Finish

Run `/usr/bin/python3 ~/.claude/artifact-library/scripts/install.py --check` and
`/usr/bin/python3 ~/.claude/artifact-library/scripts/artlib.py search` (it lists the newest
artifacts). Then tell the user, briefly:

- what was installed and where,
- how many artifacts were imported (if any),
- that every artifact they publish from Claude Code from now on appears in Raycast by itself,
- whether Ask Haiku and the hotkey are still to do.

Uninstall, if the user ever asks: `/usr/bin/python3 ~/.claude/artifact-library/scripts/install.py --remove`,
remove the extension in Raycast, then delete `~/.claude/artifact-library`. The library data in
`~/Documents/Artifacts` stays until the user deletes it.
