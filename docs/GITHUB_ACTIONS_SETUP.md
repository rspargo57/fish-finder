# Fish Finder — GitHub Actions Setup Walkthrough

**For:** Randy Spargo, with help from his wife
**Date written:** 2026-09-21
**Estimated time:** 15 minutes total (10 min for the GitHub signup + 5 for the paste steps)

## What you're doing and why

For the last week, Fish Finder's nightly build has been failing silently. The scheduled "trigger" that's supposed to update the map every night at 6 PM ET has been quitting after 90 seconds without doing the work, and no alert reached Randy's phone. We diagnosed the root cause (a self-eating bug in the deploy script's tarball packaging) and fixed it — but the trigger system it runs on is inherently fragile: a fresh cloud sandbox every night, a Claude model in the middle driving decisions, no persistent logs when things go wrong.

We're moving the nightly build to **GitHub Actions** instead. Same code, same fishing map, same everything you see — but the machine that runs the nightly job every night is now GitHub's, which is designed for exactly this kind of scheduled task. No sandbox rotation. No model in the loop. Real logs. Real email alerts if something breaks.

This walkthrough gets Randy's GitHub account set up and connected so we can flip the switch. **You (his wife) are helping with the computer parts — Randy is helping with the fishing parts.**

## Before you start

You'll need:
- Randy's email address (rspargo57@gmail.com is fine, or any other one you both have access to)
- A computer with a web browser (not a phone — some steps need a big screen)
- About 15 minutes of uninterrupted time

## Step 1 — Sign up for GitHub (5 minutes)

If Randy already has a GitHub account, skip to Step 2 and just tell Claude the username.

1. Open a web browser. Any browser is fine.
2. Go to **https://github.com/signup**
3. Enter Randy's email address.
4. Make up a password. Write it down. It doesn't need to be fancy.
5. Choose a username — something simple. `rspargo57` or `randyspargo-fishfinder` are both fine. Write it down.
6. Solve the puzzle to prove you're not a robot (GitHub will show one).
7. Click **Create account**.
8. GitHub emails a verification code. Type it in when prompted.
9. When it asks about your team size and what you'll use GitHub for, you can pick anything — those answers don't matter. Click through.
10. When it offers plans, pick **Free**. It'll show you a big page of features — just click **Continue**.
11. **You're in.** You'll see a "Welcome to GitHub" page with a green header.

**Write down the username you picked.** Claude needs it to know where to push the code.

## Step 2 — Tell Claude the GitHub username (30 seconds)

Come back to the Fish Finder Cowork chat and paste this message:

> My GitHub username is `<the username you picked>`. Ready for you to set up the repository.

Claude will then walk you through Step 3 in the chat — but the steps are documented below in case you want to preview them.

## Step 3 — Grant Claude access to push the code (2 minutes)

Claude will send Randy an invitation to a new GitHub repository called `fish-finder`. To accept:

1. In the chat, Claude will paste a URL like `https://github.com/<username>/fish-finder`. Click it.
2. GitHub will show the repository with the Fish Finder code inside.
3. Nothing else to do on this step — Claude will have already pushed everything via GitHub's public API.

## Step 4 — Cloudflare secrets (handled by Claude)

Claude is adding the two Cloudflare secrets (`CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID`) to the repository directly via the GitHub API.
You don't need to click through this yourself.

If you want to double-check they landed, go to:
`https://github.com/<username>/fish-finder/settings/secrets/actions`
and you should see both secret names listed (values are never visible again
once saved — that's normal).

## Step 5 — Test the nightly build manually (2 minutes)

Before waiting for the scheduled 6 PM ET fire, let's prove it works:

1. Still on the repository page, click the **Actions** tab (top of the page).
2. In the left sidebar, click **Nightly build + deploy**.
3. On the right, click the **Run workflow** dropdown button.
4. Click the green **Run workflow** confirmation button.
5. Wait ~30 seconds, then refresh. You'll see a new run appear with a spinning yellow circle.
6. Come back in 20 minutes. The circle should be a green checkmark. If it's a red X, something failed — paste the run URL into the chat and Claude will investigate.

## Step 6 — Turn off the old Cowork trigger (30 seconds — Randy does this)

Once we've had two successful nightly builds in a row on GitHub Actions, Randy tells Claude:

> Turn off the old Cowork nightly trigger.

Claude will disable it. The stale-build banner on the map stays as a failsafe — if a GitHub Actions run ever fails, the banner turns yellow/orange/red just like before.

## What if something goes wrong

- **GitHub sign-up says the username is taken:** try `randyspargo1957` or add any suffix. Doesn't matter what.
- **Claude can't push to the repo:** GitHub sometimes requires you to accept a Terms of Service popup the first time. Log into GitHub in a browser, click through anything that pops up, then tell Claude to retry.
- **The Actions tab is missing:** GitHub sometimes hides it on brand-new accounts. Reload the page. If it's still missing, click **Settings** → **Actions** and enable them.
- **The workflow says "success" but the map didn't update:** paste the workflow run URL into the chat. Claude will read the logs and diagnose.

## What DOESN'T change

The map itself. The URL. The way Randy uses the site every morning. The prediction model. Everything you see stays exactly the same. The only difference is which computer runs the nightly job.

## Contact

If anything is confusing at any step, paste what you're seeing into the Cowork chat with Claude — even a screenshot of what's on the screen — and Claude will walk you through the next click.
