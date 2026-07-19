# Start here

You can try OpenHealth in 60 seconds without installing anything. Everything beyond that is optional: your own data, an AI agent, Telegram. Each level is self-contained - stop wherever you like.

---

## Level 0. Just look around (60 seconds, install nothing)

1. On the GitHub repository page, click the green **Code** button, then **Download ZIP**.
2. Unpack the archive and open the `ui/web` folder inside.
3. Launch the dashboard:
   - **macOS:** double-click `OpenHealth.command`. If macOS says it "cannot be opened because it is from an unidentified developer" - right-click the file, choose **Open**, then **Open** again. You only need to do this once.
   - **Windows / Linux:** double-click `index.html` - it opens in your browser.

The dashboard opens with demo data: recovery, trends, correlations and the report, so you can see how everything works. None of your data is there yet, and nothing is sent anywhere.

You need an internet connection: fonts and the animation library load from a CDN when the page opens.

---

## Level 1. Your own data (10 minutes)

You will need the Terminal, but only for one command. On macOS, Python is already there - nothing to install.

1. Open Terminal (on macOS: Spotlight, type "Terminal").
2. Type `bash`, add a space, and drag the `setup.sh` file from the OpenHealth folder into the Terminal window. Press Enter.

Or, if you have not downloaded the repository yet, one command:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/igindin/openhealth/main/setup.sh)
```

The script will:

- check Python (and tell you in plain words what to do if it is missing);
- create a local data folder (`data/` inside the OpenHealth folder);
- ask whether you have an Apple Health export and import it (just drag the file into the window);
- build the dashboard data and launch the dashboard.

You can run the script as many times as you want: it never breaks or duplicates anything.

**Where to get an Apple Health export:** on your iPhone, open the Health app, tap your avatar in the top right corner, then "Export All Health Data" at the bottom. You get a ZIP - send it to your computer (AirDrop, email) and point the script at it.

**Wearing WHOOP:** the connection goes through OAuth, run `python3 -m openhealth whoop-auth-url` (see the Run section in the README).

---

## Level 2. AI agent (optional)

The most interesting part of OpenHealth is talking to an agent: "log that I slept badly and had two coffees", "how is my recovery this week?". You need one of these agents on your machine:

- **Claude Code** - https://claude.com/claude-code
- **Codex** - https://github.com/openai/codex

Once installed, the agent buttons in the dashboard ("generate insight", "re-run correlations", per-marker deep dives) come alive on their own - the dashboard finds the agent on your machine. In the terminal you get commands like `/checkin`, `/log`, `/insights`.

---

## Level 3. Telegram, calendar and the rest (optional)

- **Telegram bot** (check-ins and notes straight from your phone): [docs/TELEGRAM.md](./TELEGRAM.md)
- **Google Calendar, Todoist and other integrations:** [docs/INTEGRATIONS.md](./INTEGRATIONS.md)

---

## FAQ

**I am on Windows. What works?**
The demo dashboard - fully: double-click `ui/web/index.html`. Level 1 works too, but the `OpenHealth.command` launcher is macOS-only: run `bash setup.sh` from Git Bash or WSL, and start the server with `python ui/web/server.py`. If you are not comfortable with a terminal, the demo level is the smooth path on Windows for now.

**macOS refuses to open OpenHealth.command ("unidentified developer").**
That is the standard protection for files downloaded from the internet. Right-click the file, choose "Open", then confirm "Open". Once is enough. No right button - click while holding Control. Fallback: double-click `index.html`, the demo works that way too.

**macOS offers to install "command line developer tools".**
That happens if Python has never been used on this Mac. It is Apple's official installer - accept it, it takes a couple of minutes. Or install Python from https://www.python.org/downloads/.

**Where is my data and who can see it?**
Everything stays local, on your computer: raw sources and the database live in `data/` inside the OpenHealth folder, the dashboard export in `ui/web/data.local.json`. Both paths are git-ignored - they never reach the repository or the internet. The dashboard server listens on `127.0.0.1` only, so nothing outside your machine can connect. No telemetry.

**I have no tracker (WHOOP, Apple Watch). Is there a point?**
Yes. The core of the system is the journal: daily check-ins against a catalog of 200+ behaviors, notes, lab PDFs. The agent works on top of any records. Recovery correlations appear later, once you have an HRV/sleep source, but the journal and hypotheses are useful without one.

**How do I update?**
If you downloaded a ZIP - download a new one and move the `data/` folder (and `ui/web/data.local.json`) from the old folder into the new one. If you cloned with git - just `git pull`: your data lives in paths git never touches. Running `bash setup.sh` again after an update is safe.

**Is this medical advice?**
No. OpenHealth is a self-tracking tool. It does not diagnose or prescribe, and it phrases findings as cautious hypotheses to test. For medical questions, see a clinician.

---

If OpenHealth turned out useful — a GitHub star is the single best way to help others discover it. [github.com/igindin/openhealth](https://github.com/igindin/openhealth) ⭐
