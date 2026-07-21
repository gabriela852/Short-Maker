# Posting to YouTube — one-time setup

Shorts Maker can post a finished short **straight to your YouTube channel** — the video, its
thumbnail, and an auto-written title and description — so you never download-and-re-upload by hand.

Before the first post, YouTube needs you to do a **one-time setup** so the app is allowed to upload
to *your* channel and nobody else's. It takes about 10 minutes. You only ever do it once.

Think of it like giving your own house key to a trusted helper: you go to the locksmith (Google),
cut one key (a credentials file), and hand it to Shorts Maker. After that it can carry things in for
you without asking again.

---

## The one important catch — read this first

YouTube has a safety rule: **any app that uploads on your behalf must be reviewed by Google before
its uploads are allowed to be public.** Until that review passes, every short you post lands on your
channel as **Private** — and (this part surprises people) you *can't* just flip it to Public in
YouTube Studio. It stays private until the review is done.

So there are really **two milestones**:

1. **Connect your account** (steps below) → you can post shorts as **private drafts** immediately.
2. **Pass the one-time app review** (last section) → your posts can go **public** with one click.

You can do step 1 now and step 2 whenever you're ready. Nothing about the app changes in between —
the "Post to YouTube" button works the whole time; it's only the *public vs private* that the review
unlocks.

---

## Part A — Create your Google credentials (~10 min, once)

You'll do this at **https://console.cloud.google.com** signed in with the **same Google account that
owns your YouTube channel**.

### 1. Make a project
- Top-left, click the project dropdown → **New Project**.
- Name it something like `Shorts Maker` → **Create**. Wait a few seconds, then make sure it's
  selected in that same dropdown.

### 2. Turn on the YouTube service
- In the search bar at the top, type **YouTube Data API v3** and open it.
- Click **Enable**.

### 3. Set up the consent screen (who's allowed to sign in)
- In the left menu, go to **APIs & Services → OAuth consent screen** (newer consoles call this
  **Google Auth Platform → Branding / Audience**).
- If asked, choose **External** and fill in the basics: an app name (e.g. `Shorts Maker`), your
  email as the support email, and your email again as the developer contact. Save.
- **Important — publish it:** find **Publishing status** and click **Publish app** / move it to
  **In production**. (It will still say "unverified" — that's fine and expected. Publishing here just
  means your sign-in doesn't expire every 7 days. If you leave it in *Testing*, you'd have to
  reconnect the app every week.)

### 4. Create the key file
- Left menu → **APIs & Services → Credentials**.
- Click **+ Create Credentials → OAuth client ID**.
- **Application type: Desktop app.** Name it anything → **Create**.
- A box pops up — click **Download JSON**.

### 5. Drop the key into Shorts Maker
- Find the file you just downloaded (it's named something like
  `client_secret_1234-abcd.apps.googleusercontent.com.json`).
- **Rename it to exactly:** `client_secret.json`
- **Move it into your Shorts Maker folder** — the same folder that has `app.py` and `start.bat`:
  `Documents\Claude\Projects\Youtube Conten Creation\Shorts Maker`

That's the whole Google side. This file is private — it stays on your computer and is never uploaded
to GitHub (the app is set up to keep it out).

---

## Part B — Connect the app (30 seconds)

1. Start Shorts Maker as usual.
2. Go to the **History** tab. You'll see a **Connect YouTube** button at the top.
3. Click it. Your browser opens and asks you to pick your Google account and allow access.
   - You'll see a **"Google hasn't verified this app"** warning. That's expected for your own
     personal app. Click **Advanced → Go to Shorts Maker (unsafe)** → **Continue/Allow**.
   - ("Unsafe" just means Google hasn't formally reviewed it yet — it's your own app talking to your
     own channel.)
4. When the tab says "Connected," go back to Shorts Maker. It now shows **✅ YouTube connected**.

You're done. Every short now has a **▶ Post to YouTube** button.

---

## Part C — Making posts go public (the one-time app review)

Until Google reviews your app, posted shorts stay **Private**. To unlock public posting you request a
one-time **API audit**:

- Google's form is linked from
  **https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits**
- You describe that it's a personal tool that uploads your own videos to your own channel.
- When it's approved, nothing changes on your end — the next short you post can be **Public**
  right away.

Until then, a good workflow is: **Post to YouTube** (lands as a private draft), review it in YouTube
Studio, and — if you want it public before the audit clears — publish that one by re-uploading it in
Studio the normal way. Once the audit passes, that extra step goes away.

---

## Troubleshooting

- **"YouTube isn't set up yet."** — `client_secret.json` isn't in the Shorts Maker folder, or it's
  named differently. Re-check step 5.
- **The app asks me to reconnect after a week.** — Your consent screen is still in *Testing*. Do
  step 3's "Publish app" and reconnect once.
- **"Thumbnail needs a phone-verified channel."** — YouTube only allows custom thumbnails once your
  channel is phone-verified (youtube.com/verify). The video still posts fine without it.
- **"Daily upload limit reached."** — Google allows about 6 API posts per day per app. Try tomorrow.
