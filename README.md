Kapoq's open source skills that can be used with agents to manage your Amazon, Walmart, and Target brands.  The kstack is inspired by Garry Tan's gstack (https://github.com/garrytan/gstack/blob/main/README.md).

## How to download a skill (no git required)

Each skill is a single top-level folder in this repository, for example
`daily-brand-check` or `weekly-report`. You do not need git or the command line
to grab one — the whole thing can be done in your browser.

1. Go to https://github.com/therealkapoq/kstack
2. Click the green "Code" button near the top-right of the file list, then
   click "Download ZIP". Your browser downloads a file named `kstack-main.zip`.
3. Open the downloaded ZIP file. On most computers you just double-click it to
   unzip it, which gives you a folder called `kstack-main`.
4. Open the `kstack-main` folder. Inside it you'll see one folder per skill.
   Find the one you want, for example `daily-brand-check`.
5. Start a Claude session and tell it to install the skill, pointing it at that
   skill's folder. Claude copies the skill into place for you, no manual file
   shuffling required. Here are some sample installation prompts for Mac, Linux,
   and Windows:
   - Mac or Linux: "Install the skill in
     ~/Downloads/kstack-main/daily-brand-check"
   - Windows: "Install the skill in
     C:\Users\YourName\Downloads\kstack-main\daily-brand-check"

That's it. The next time you start your agent, the skill is available to use.

Want to add a skill? See [CONTRIBUTING.md](CONTRIBUTING.md) — you can do the whole thing in your browser, no git required.
