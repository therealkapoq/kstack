# Contributing to the kstack

The kstack is a collection of open-source AI skills for running an advertising
agency on Amazon, Walmart, and Target, primarily against Kapoq's MCP server
(and other tools). Inspired by Garry Tan's gstack.

Every contribution is welcome, and you do not need to know git or the command
line. The whole process can be done in your browser on github.com.

## What a skill looks like

Each skill is a single top-level folder named in kebab-case (lowercase words
separated by hyphens), for example `demo-only-diagnosing-kapoq-data`.

A skill folder must contain a `SKILL.md` file with YAML frontmatter at the top:

```markdown
---
name: your-skill-name
description: Use when ... (one or two sentences describing exactly when an agent should reach for this skill, and what it does)
---

The body of the skill goes here.
```

Requirements:

- `name` in the frontmatter must exactly match the folder name (kebab-case).
- `description` should start with "Use when ..." and describe the trigger
  conditions clearly, since that is what an agent reads to decide whether to
  load the skill.
- Optional supporting files (reference docs, scripts, sample data, assets) live
  inside the same skill folder. Keep everything the skill needs self-contained
  in its folder.

## Before you contribute, please check

- No secrets. This repository is public. Do not include API keys, tokens,
  passwords, connection strings, or `.env` files.
- No customer data. Strip real brand names, account IDs, customer
  identifiers, and any private numbers. Use anonymized or demo examples.
- Your skill passes the skill-consistency-review check.  You can download that
  skill right here from the kstack and use it to review the consistency of the
  output produced by your skill.  This skill-consistency-review skill will
  generate a report with actionable fixes if your skill does not pass.
- License. kstack is licensed under AGPL-3.0. By contributing you agree your
  contribution is released under the same license.

## How to add a skill in your browser (no git required)

This is done entirely in your browser with no git, and you do not need any
special access. You make your own copy of the kstack (called a "fork"), add your
skill to it, and then open a pull request asking us to pull it into the real
kstack.

1. Go to https://github.com/therealkapoq/kstack
2. Click the "Fork" button near the top-right, then click "Create fork". GitHub
   makes your own copy at `github.com/your-username/kstack` and sends you there.
3. Make sure the top-left of the page now shows `your-username/kstack`, not
   `therealkapoq/kstack`. Everything below happens in your copy.
4. Click the "Add file" button (top-right of the file list), then
   "Upload files".
5. Drag your entire skill folder into the upload box. GitHub preserves the
   folder structure, so your files land inside a folder named after your skill.
6. Scroll to "Commit changes", leave "Commit directly to the `main` branch"
   selected, enter a short message like "Add your-skill-name skill", and click
   "Commit changes".
7. Go back to the top page of your fork. A banner appears saying your branch is
   "1 commit ahead". Click the "Contribute" button in that banner, then
   "Open pull request". (No banner? Open the "Pull requests" tab, then
   "New pull request".)
8. Confirm the page reads base repository: `therealkapoq/kstack` base: `main`,
   and head repository: `your-username/kstack` compare: `main`. Add a title and
   a one-sentence description of what your skill does, then click
   "Create pull request".

That's it. A maintainer will review your pull request and merge it.

## Making changes after you open a pull request

You do not open a new pull request to make changes. Just commit again to your
fork's `main` branch and your open pull request updates automatically.

The one rule: before you commit any change, make sure you are in your own copy.
The top-left of the page should read `your-username/kstack`, not
`therealkapoq/kstack`.

- Edit an existing file: open the file, click the pencil icon (top-right of the
  file view), make your changes, then "Commit changes".
- Add more files: "Add file" then "Upload files" (or "Create new file"), then
  commit.
- Delete a file: open the file, click the trash-can icon, then commit.
- Rename or move a file: open the file, click the pencil icon, edit the filename
  at the top (include a `/` to move it into a folder), then commit.

If a maintainer leaves review comments, fix them the same way and the changes
land in the same pull request. When a maintainer uses GitHub's "Suggest changes"
feature on a line, you can accept their exact wording with the green
"Commit suggestion" button.

## Questions

Open an issue on the repository and a maintainer will help.
