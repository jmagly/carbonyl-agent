# Working with Google and Other Bot-Protected Sites

Google bot-detects carbonyl out of the box. The fingerprint is convincing (Chrome-like TLS, Chrome-like ClientHello), but the behavioral signal is not — no mouse history, no session continuity, fresh cookies on every run. The result is a redirect to `/sorry/index?continue=...` with a reCAPTCHA challenge instead of search results.

This is the fingerprinting *working correctly*. Google sees enough Chrome-shape to take carbonyl seriously, then trips on the absence of session state. The intended workaround is the v2026.5.3 cookie-import feature: log in once with your host browser, import the session, and carbonyl rides on that authenticated cookie jar.

## Reproducing the challenge

Run a vanilla carbonyl against a Google search and watch the URL rewrite:

```bash
~/.local/share/carbonyl/bin/x86_64-unknown-linux-gnu/carbonyl \
  --no-sandbox \
  "https://www.google.com/search?q=carbonyl+browser"
```

You will land on `https://www.google.com/sorry/index?continue=...` with the "I'm not a robot" checkbox rendered as pixel art. Search results never appear.

## The cookie-import workflow

1. Log into Google in your daily-driver Firefox or Chrome and close it before importing.
2. Import the session into a named carbonyl session:

   ```bash
   carbonyl-agent cookies import \
     --from firefox \
     --domain google.com \
     --persist-to-session google-session
   ```

3. Run carbonyl against that session. The Google cookies carry enough authentication and behavioral history to bypass the challenge:

   ```bash
   carbonyl-agent run \
     --session google-session \
     --url "https://www.google.com/search?q=your+query"
   ```

Search results render in the carbonyl viewport like any other page.

## When this pattern applies

The cookie-import bypass works for sites whose bot challenge is triggered by missing session signal rather than missing fingerprint signal. In practice that covers most "you look real but I don't recognize you" cases:

- **Google** — search, Gmail, Workspace (challenge after a few queries)
- **LinkedIn** — feed, profile views (challenge on cold session)
- **X / Twitter** — logged-in feed, search beyond the public timeline
- **Reddit** — logged-in feed, age-gated subreddits
- **Most publishers behind a soft paywall** — NYT, WaPo, FT when the cookie says "subscriber"

For sites that hard-fingerprint (advanced bot vendors like PerimeterX, DataDome at strict tiers, Cloudflare Turnstile in strict mode), cookie-import alone is not enough — see the persona + uinput backend documentation in the main README for the trusted-input path.

## Caveats

- Imported sessions inherit the trust *and* the access of the originating browser profile. Do not import a domain you would not hand a copy of your live session to.
- Sessions expire. When challenges come back, re-import.
- Sensitive domains (banks, payment processors, primary email, SSO providers) are default-refused by `cookies import`. The `--allow-sensitive` flag opens a second gate that requires typing the domain string. Use it only when you actually understand the blast radius.
- The audit log at `~/.local/share/carbonyl-agent/cookie-imports.log` records cookie *names*, never *values*. All written files are mode `0600`.

## Related

- `carbonyl-agent cookies import --help` — full CLI reference
- README "Cookie import from host browser" section — source matrix, libsecret requirements, `[cookies]` extra
- README "Bot Detection Flags" section — UA spoof, webdriver suppression, FedCM/One-Tap suppression
- README "Persona-Bound Egress (W3B)" section — when you need wreq + uinput in addition to cookies
