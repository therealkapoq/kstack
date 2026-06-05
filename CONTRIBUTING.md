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

1. Go to https://github.com/therealkapoq/kstack
2. Click the branch dropdown near the top-left (it says `main`), type a new
   branch name like `add-your-skill-name`, and click
   "Create branch: add-your-skill-name from main".
3. Make sure the dropdown now shows your new branch, not `main`.
4. Click the "Add file" button (top-right of the file list), then
   "Upload files".
5. Drag your entire skill folder into the upload box. GitHub preserves the
   folder structure, so your files land inside a folder named after your skill.
6. Scroll to "Commit changes", leave "Commit directly to the add-... branch"
   selected, enter a short message like "Add your-skill-name skill", and click
   "Commit changes".
7. A green "Compare & pull request" button appears. Click it. (No button? Open
   the "Pull requests" tab, then "New pull request".)
8. Confirm the page reads base: `main` and compare: `add-...`, add a title and a
   one-sentence description of what your skill does, then click
   "Create pull request".

That's it. A maintainer will review your pull request and merge it.

## Making changes after you open a pull request

You do not open a new pull request to make changes. Just commit again to the
same `add-...` branch and your open pull request updates automatically.

The one rule: before you commit any change, make sure the branch dropdown shows
your `add-...` branch, not `main`.

- Edit an existing file: open the file, click the pencil icon (top-right of the
  file view), make your changes, then "Commit changes" to the `add-...` branch.
- Add more files: "Add file" then "Upload files" (or "Create new file"), commit
  to the `add-...` branch.
- Delete a file: open the file, click the trash-can icon, commit to the branch.
- Rename or move a file: open the file, click the pencil icon, edit the filename
  at the top (include a `/` to move it into a folder), commit.

If a maintainer leaves review comments, fix them the same way and the changes
land in the same pull request. When a maintainer uses GitHub's "Suggest changes"
feature on a line, you can accept their exact wording with the green
"Commit suggestion" button.

## Questions

Open an issue on the repository and a maintainer will help.
