# Shorts Maker

Turns one of your long YouTube videos into a short: it reads the transcript,
asks Claude to find the most engaging 30-60 second moment (a real hook + payoff,
not just "loud parts"), then cuts that piece, reframes it to vertical (9:16),
and burns in captions - similar to what CapCut/Descript do automatically.

## How to run it

Double-click **start.bat** in this folder. It opens `http://127.0.0.1:5050`
in your browser automatically. Leave the black window open while you use it -
closing it stops the app.

## First-time setup: the API key

The first time you run it, you'll need an Anthropic API key (this is what lets
the app ask Claude which moment is the best one to clip).

1. Go to https://console.anthropic.com/settings/keys and create a key.
2. Paste it into the "Anthropic API key" box in the app and click Save.
3. It's saved locally in this folder's `.env` file - it only lives on this
   computer, never sent anywhere except to Anthropic when the app asks for
   a recommendation.

Each video you analyze costs a small fraction of a cent (it's just sending the
transcript, not the video itself).

## How to use it

1. Paste a link to one of **your own** YouTube videos.
2. Click "Find the best moment" - this downloads the video and its captions,
   then asks Claude to suggest 3 candidate clips with a reason for each.
3. Pick one and click "Make this short" - it cuts, reframes to vertical, and
   adds captions.
4. Preview it right in the browser, then click Download.

## Notes / limits

- Only works on videos that have captions on YouTube (auto-generated captions
  are fine - most videos have these by default).
- Only use this on videos you own or have the rights to - downloading other
  creators' videos can run into YouTube's terms of service and copyright.
- Downloaded videos and generated shorts are stored in `data/downloads` and
  `data/outputs` inside this folder. Delete them any time to free up space.
- If you ever need to reinstall ffmpeg (the video engine this uses):
  `winget install Gyan.FFmpeg`
