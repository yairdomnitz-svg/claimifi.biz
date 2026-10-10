# Claimifi.biz — Historical Fact-Checker

**Domain:** claimifi.biz

An AI-powered site that fact-checks historical claims made in YouTube videos, using Grok (xAI)
constrained to a fixed list of academic, archival, and fact-checking domains.

## Files (all at repository root)

```
main.py                FastAPI backend; serves the pages and the API
index.html             Landing page (/)
app.html               Analyzer (/app)
pricing.html           Free vs Pro, with the Subscribe button (/pricing)
login.html             Log in, sign up, forgot password (/login)
profile.html           Profile, plan, password, delete account (/profile)
callback.html          Where email links land (/auth/callback)
reset-password.html    New password after a reset link (/reset-password)
privacy.html           Privacy notice (/privacy)
404.html               Page-not-found page, for browsers
styles.css             Shared stylesheet
app.js                 Analyzer logic, plus the header status badge on every page
account.js             Pricing, account, email-link and reset pages
favicon.svg            Logo mark: tab icon and header brand
apple-touch-icon.png   iOS home-screen icon
og-image.png           1200x630 social preview
google*.html           Search Console verification file
requirements.txt       Pinned dependencies
Procfile               Start command fallback
.python-version        Pins the Python version for the Railway build
.env.example           Template for local .env
```

There is no build step: the CSS and JS are served as-is. The pages reference
them as `/styles.css?v=<hash>`, where the hash is derived from the contents of
the CSS, the JS and the logo at startup, so a deploy invalidates a visitor's
cached copy automatically.

The server fills a few markers in every page as it serves it: `__SITE_URL__`
(canonical, Open Graph and JSON-LD URLs follow `SITE_URL`), `data-accounts` and
`data-billing` on `<html>` (links to the account and pricing pages stay hidden until
Supabase and Stripe are configured), and the `<!--contact-...-->` comments, which become
the Contact links from `CONTACT_EMAIL`.

## Colour and type

Colours and the typeface are tokens at the top of `styles.css`. The page is plain
white, and the accents come from the logo: the gradient `#A461F6` → `#7887F2`
and the check's `#B4D5F8`. Those three are used as fills only. Text accents are
deeper versions of the same hues, because the logo colours are too light to read
as text on white.

Every element uses one typeface, currently Lato. To change it, edit `--font` in
`styles.css` and the Google Fonts `<link>` in both `index.html` and `app.html`.

To compare fonts on the real pages before changing anything, add `?font=` to any
URL, for example `https://claimifi.biz/?font=inter`. A switcher appears in the
bottom-left corner with the shortlist: Lato, Inter, Plus Jakarta Sans, DM Sans,
Manrope, Outfit, Space Grotesk, Poppins and Nunito Sans. The choice carries
across pages until the switcher is closed, and the address bar keeps a link to
the current pick. Visitors who never open a `?font=` link see no change.

## Railway deployment

The service builds with **Railpack**, which auto-detects Python from `requirements.txt`.
No Dockerfile and no `railway.toml` are needed (Config-as-Code is deprecated).

### 1. Variables

| Variable | Required | Notes |
| --- | --- | --- |
| `ANALYSIS_ENABLED` | No | Master switch for everything that costs money. Defaults to `true`. Set it to `false` to pause: `/api/analyze` then returns `503` with `"reason": "analysis_disabled"` and the page says analysis is paused. |
| `DAILY_BUDGET_USD` | No | Hard ceiling on estimated spend per UTC day for free analyses. Default `2.0`; `0` disables. Costed from xAI's own `usage` block against `MODEL_PRICING`, so a title check and a 25k-token transcript are not priced alike. Each call holds its worst-case cost while it runs, so concurrent calls cannot all pass the same check; the overshoot is at most one call. In-process, so it resets on redeploy — the request ceilings stay underneath it. |
| `PRO_DAILY_BUDGET_USD` | No | The same ceiling for Pro analyses, as a separate pool so a busy free tier never turns a subscriber away. Default `10.0`; `0` disables. |
| `XAI_API_KEY` | **Yes** | From https://console.x.ai. Without it `/api/analyze` returns `503` with `"reason": "no_api_key"` and the page says so. There is no demo mode: a fact-checker must never show invented verdicts. |
| `GROK_MODEL` | No | Defaults to `grok-4.3`. `grok-4` is no longer on xAI's published model list and the dated `grok-4-0709` snapshot was retired on 2026-05-15, so pin a documented id. |
| `ALLOWED_ORIGINS` | No | Comma-separated. Defaults to `SITE_URL` and its `www.` form — **not** `*`, because `/api/analyze` is unauthenticated and costs money per call. `*` alone is accepted; `*` mixed with explicit origins is refused at startup. |
| `SITE_URL` | No | Canonical origin for `robots.txt` and `sitemap.xml`. Defaults to `https://claimifi.biz` — **set this on any other deploy** or the sitemap advertises the wrong host. |
| `TRANSCRIPT_TIMEOUT` | No | Total budget for one transcript fetch, in seconds. Default `45`. |
| `TRANSCRIPT_HTTP_TIMEOUT` | No | Ceiling on any single YouTube call, in seconds. Default `10`. The real bound is `TRANSCRIPT_TIMEOUT`: one fetch issues several calls and the thread cannot be cancelled once started, so the session tracks a wall-clock deadline across all of them. |
| `TRANSCRIPT_WORKERS` | No | Concurrent transcript fetches allowed in flight. Default `8`. Beyond this, `/api/analyze` returns 503 rather than starting work nobody is waiting for. |
| `GLOBAL_RATE_LIMIT_REQUESTS` | No | Analyses per window across *all* callers, so many IPs cannot together bypass the per-IP limit. Default `300`. `0` disables. |
| `GLOBAL_RATE_LIMIT_WINDOW` | No | Window for the global limit, in seconds. Default `3600`. |
| `MAX_RATE_BUCKETS` | No | Hard cap on rate-limit buckets held in memory. Default `20000`. |
| `TRUSTED_PROXY_HOPS` | No | How many proxies append to `X-Forwarded-For` before the request arrives. Default `1` (Railway alone). Set to `2` if you put a CDN such as Cloudflare in front — otherwise Railway's rightmost hop is the *CDN's* address, every visitor shares one rate-limit bucket, and the per-IP limit locks out the whole audience at once. |
| `GROK_TIMEOUT` | No | Seconds to wait on xAI. Default `120`. |
| `GROK_MAX_TOKENS` | No | Output budget per free analysis. Default `8000`. A reply cut off here returns 502 rather than being reported as malformed. |
| `GROK_MAX_TOKENS_PRO` | No | Output budget per Pro analysis, which checks up to 20 claims in depth. Default `20000`. |
| `GROK_TIMEOUT_PRO` | No | Seconds to wait on xAI for a Pro analysis. Default `200`. The page waits 270 s in all, so keep this plus `TRANSCRIPT_TIMEOUT` below that. |
| `GROK_TEMPERATURE` | No | Default `0.2`. |
| `MAX_TRANSCRIPT_CHARS` | No | Transcript characters sent to Grok. Default `100000`. |
| `XAI_BASE_URL` | No | Default `https://api.x.ai/v1`. |
| `LOG_LEVEL` | No | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. Default `INFO`. An unrecognised value logs a warning and falls back instead of failing to boot. |
| `RATE_LIMIT_REQUESTS` | No | Analyses per IP per window. Default `10`. `0` disables. |
| `PRO_RATE_LIMIT_REQUESTS` | No | Analyses per Pro account per `RATE_LIMIT_WINDOW`. Default `30`; `0` disables. Pro is metered per account, not per IP, and is outside the global limit. |
| `CONTACT_EMAIL` | No | Shown as the Contact link in the footers and on the privacy page. Defaults to `yair.claimifi@gmail.com`; set it empty to show no contact link. |
| `RATE_LIMIT_WINDOW` | No | Window in seconds. Default `600`. |
| `EMAIL_RATE_LIMIT_REQUESTS` | No | Account emails (sign-up, confirmation resend, password reset, email change) per IP per `EMAIL_RATE_LIMIT_WINDOW`. Default `5`; `0` disables. Supabase's email quota is shared by the whole project, so without this one visitor could use it up and block everyone's resets. |
| `EMAIL_RATE_LIMIT_PER_ADDRESS` | No | The same, per email address. Default `3`; `0` disables. A refusal reads the same whether or not the address has an account. |
| `EMAIL_RATE_LIMIT_WINDOW` | No | Window for both, in seconds. Default `3600` (at least `60`). |
| `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` | See below | Residential proxy for transcript fetching. |
| `WEBSHARE_RETRIES` | No | Retries when an exit node is blocked; each one rotates to a fresh IP. Default `2`. Webshare's own default is 10, which can occupy a worker thread for two minutes. |
| `WEBSHARE_IP_LOCATIONS` | No | Country codes (`nl,de,gb`) to pin the exit pool nearer the deploy region. Empty uses the full pool. |
| `PROXY_URL` | See below | Alternative to Webshare: any `http://user:pass@host:port`. |
| `SUPABASE_URL` | For accounts | The Supabase project URL, `https://<ref>.supabase.co`. The account API stays off until this and `SUPABASE_SECRET_KEY` are both set. See [Accounts](#accounts-supabase). |
| `SUPABASE_SECRET_KEY` | For accounts | A **secret** key (`sb_secret_...`), used only by the server. It is the one key type Supabase accepts each visitor's IP from; with any other key, Supabase's per-IP auth limits are shared by every visitor to the site. |
| `STRIPE_SECRET_KEY` | For payments | Stripe secret key: `sk_test_...` while testing, `sk_live_...` once live. Payments stay off until this, `STRIPE_WEBHOOK_SECRET` and both Supabase variables are set. See [Payments](#payments-stripe). |
| `STRIPE_WEBHOOK_SECRET` | For payments | The `whsec_...` signing secret of the webhook endpoint that points at `/api/stripe/webhook`. Locally, the one `stripe listen` prints. |

Do **not** set `PORT` yourself — Railway injects it and must match the domain's target port.

### 2. Settings → Deploy

- **Custom Start Command:**
  ```
  uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*" --timeout-keep-alive 120
  ```
  `--timeout-keep-alive 120` keeps long analyses from being cut off. Rate limiting
  counts `X-Forwarded-For` hops from the right — anything further left is supplied by
  the caller and can be forged — and takes the `TRUSTED_PROXY_HOPS`-th one. The default
  of `1` is the address Railway itself observed. **Raise it to `2` before enabling
  Cloudflare's proxy** (or any other CDN) in front of the deploy. IPv6 addresses are
  bucketed by `/64`, since a residential customer holds the whole block.
- **Healthcheck Path:** `/health`
- **Serverless:** leave disabled. An analysis can run for a minute or more; cold starts on
  top of that push requests past the client timeout.

### 3. Networking

Add `claimifi.biz` as a custom domain and point DNS at the CNAME Railway shows under
*Show DNS records*. The domain stays in "Waiting for DNS update" until that record
resolves. Use *Generate Domain* to get a `*.up.railway.app` URL for testing in the meantime.

### The transcript problem (read this)

YouTube blocks requests from datacenter IP ranges, which includes all of Railway. Transcript
fetching works on your laptop and then fails in production with an IP-block error. There is no
code-side workaround; requests have to leave from a residential IP.

**Setting up Webshare:**

1. Create an account at https://www.webshare.io
2. Buy a **Residential** package. Not "Proxy Server", not "Static Residential" —
   `youtube-transcript-api` only supports the rotating residential tier, and the
   other two will not work.
3. Copy the **Proxy Username** and **Proxy Password** from
   https://dashboard.webshare.io/proxy/settings — these are proxy credentials,
   not your account login.
4. Add them as `WEBSHARE_PROXY_USERNAME` and `WEBSHARE_PROXY_PASSWORD` in the
   Railway **Variables** tab, then click **Deploy** to apply.

The username is combined with the location filter and a `-rotate` suffix
automatically, so a fresh exit IP is used per request. `PROXY_URL` is available
for any other provider. Until one is configured, URL analysis returns a 502
explaining the block, while title-only analysis is unaffected because it never
touches YouTube.

`GET /health` reports `transcript_proxy_configured` and `transcript_proxy_kind` so you can confirm it took effect.

### Accounts (Supabase)

The server side of email-and-password accounts is in place: sign-up with email
confirmation, log in and out, and password reset, through Supabase Auth. This server
makes every call to Supabase and keeps the session in HttpOnly cookies, so the browser
never sees a token. Until both variables are set every `/api/auth` route answers `503`
with `"reason": "auth_unavailable"`, `/auth/confirm` answers `404`, the Sign in links stay
hidden, and `/login` and `/profile` say accounts aren't available yet.

To connect Supabase:

1. Create a project at https://supabase.com. The free tier is enough.
2. In **Authentication → URL Configuration**, set **Site URL** to `https://claimifi.biz`
   and add `https://claimifi.biz/auth/callback` under **Redirect URLs**. Add
   `http://localhost:8000/auth/callback` too for local work.
3. In **Project Settings → API Keys**, create a **secret** key. In Railway, set
   `SUPABASE_URL` to the project URL and `SUPABASE_SECRET_KEY` to that key, then deploy.
   `GET /health` should report `auth_configured: true` and `auth_forwards_client_ip: true`.
4. In **Authentication → Emails → SMTP Settings**, connect an email provider such as
   Resend, Postmark or SendGrid. Supabase's built-in sender is only meant for testing:
   it sends about **2 emails an hour** for the whole project, which a few sign-ups and
   password resets use up.

New projects require email confirmation, so an account cannot log in until its link is
opened. Supabase's default email templates work unchanged. To keep links on this site's
own domain instead, point the templates at `/auth/confirm`:

- **Confirm signup:** `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email`
- **Reset password:** `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=recovery`

Every email link is built from `SITE_URL`, so a preview deploy on another address needs
its own `SITE_URL`, and that address's `/auth/callback` added to the Redirect URLs.
Passwords need at least 8 characters, plus whatever stricter rules are set in Supabase.

**The account pages.** Email links end on three paths, all served by `account.js`:

- `/auth/callback` — Supabase's default templates arrive here with the new session in
  the URL fragment (`#access_token=…&refresh_token=…&type=recovery`). Remove it from the
  address bar, then `POST` both tokens to `/api/auth/session`, which checks them with
  Supabase and sets the cookies. A failed link arrives with `error_code` in the fragment,
  or in the query when it came through `/auth/confirm`.
- `/reset-password` — where a reset link ends up, signed in: `POST /api/auth/password`.
  For its first hour a session opened by an email link may set a password without the
  current one (Supabase marks it `amr: otp`); any other session sends `current_password`.
- `/profile` — where a confirmation link ends up (`?welcome=1`). Signed out, it sends the
  visitor to `/login?next=…` and back. `/login` sends a signed-in visitor on to `/profile`.
- `/account` — the old single account page; it redirects (308) to `/profile`, keeping the query.

Every `POST` takes JSON, from the site's own origin. Errors carry a string `detail` to
show as-is, and a `reason` code for the page to branch on (`email_not_confirmed`,
`invalid_credentials`, `weak_password`, `same_password`, `otp_expired`,
`current_password_required`, `active_subscription`, …).

**Deleting an account** (`POST /api/auth/delete`, `{"password"}`) is refused with `409`
and `active_subscription` while a Pro plan still renews: it has to be cancelled first, and
the account page says so in a dialog. A cancelled plan with paid time left doesn't block
it; deleting the Stripe customer ends that plan at once, with no refund.

### Payments (Stripe)

One paid plan, **Pro**, at $7.99 a month or $70.99 a year. It is built on the accounts
above, so it needs Supabase too. Visitors pay on Stripe's own Checkout page and manage
their plan in Stripe's Customer Portal, so no Stripe script runs on this site.

- **Prices live in Stripe, not here.** The server finds them by **lookup key**:
  `pro_monthly` and `pro_yearly`. To change a price, add a new price in Stripe, move the
  lookup key to it, and archive the old one. No code change and no deploy.
- **The plan is stored on the Supabase user**, in `app_metadata.billing`, which only this
  server can write. Only the webhook writes it, and it always re-reads the subscription
  from Stripe first, so a repeated or out-of-order event cannot leave the wrong plan.
- **Access** holds while the subscription is `active`, `trialing` or `past_due` (Stripe
  retrying a failed renewal). In code, `_plan_for(user)` returns `"pro"` or `"free"`.
  `/api/analyze` reads it from the session cookie on every call.

**What Pro unlocks.** A free analysis checks the 5 most significant claims, each with a
verdict, a 1–2 sentence explanation and its sources, and reports how many checkable
claims the model found in all. A Pro analysis checks up to 20, and adds for each claim a
confidence score (0–100), a category, *what the video says* next to *what scholarship
says*, competing interpretations, and where to dig deeper (a trusted domain and a search
phrase, never an invented title). For the whole video it adds the most significant
errors, what the video leaves out, and metrics. The metrics are computed by the server
from the verdicts, not asked of the model: an accuracy score (Supported 1, Mixed ½,
Unsupported 0, Insufficient Evidence left out), counts by verdict and by category, and
the average confidence. Pro has its own output budget, timeout, rate limit and daily
budget (the `*_PRO` variables above).

To connect Stripe:

1. In the Stripe Dashboard (test mode first), create a product with two recurring
   prices, $7.99 monthly and $70.99 yearly, with the lookup keys `pro_monthly` and
   `pro_yearly`.
2. Turn on the Customer Portal (**Settings → Billing → Customer portal**), with plan
   switching between the two prices and cancellation.
3. Add a webhook endpoint at `https://claimifi.biz/api/stripe/webhook` for
   `checkout.session.completed` and `customer.subscription.created`, `.updated` and
   `.deleted`.
4. In Railway, set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`, then deploy.
   `GET /health` should report `billing_configured: true`.

Locally, run `stripe listen --forward-to localhost:8000/api/stripe/webhook` and use the
`whsec_...` it prints. Checkout comes back to `/profile?checkout=success`, and a cancelled
checkout to `/pricing?checkout=cancelled`.

Starting a checkout first expires any checkout the customer still has open, so two tabs
(or a monthly and a yearly click) cannot both be paid and leave two subscriptions.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Landing page |
| `GET /app` | Analyzer; accepts `?q=` to prefill the input |
| `GET /pricing`, `/login`, `/profile`, `/auth/callback`, `/reset-password`, `/privacy` | Plans, log in, profile, email-link landing, new password, privacy |
| `GET /health` | Liveness + configuration status |
| `GET /api/config` | Tells the frontend whether live analysis is available |
| `POST /api/analyze` | `{"url": "..."}` (max 2000 chars) or `{"title": "..."}` (3–300 chars) |
| `GET /api/auth/me` | Whether accounts are on, and who is signed in |
| `POST /api/auth/signup` | `{"email", "password"}`; sends the confirmation email |
| `POST /api/auth/login` | `{"email", "password"}`; sets the session cookies |
| `POST /api/auth/logout` | Ends the session in this browser |
| `POST /api/auth/forgot-password` | `{"email"}`; sends a reset link, with the same reply whether or not the account exists |
| `POST /api/auth/password` | `{"password", "current_password"}`; sets a new password for whoever is signed in |
| `POST /api/auth/delete` | `{"password"}`; deletes the account, once any renewing plan is cancelled |
| `POST /api/auth/resend` | `{"email"}`; sends the confirmation email again |
| `POST /api/auth/session`, `GET /auth/confirm` | Turn an email link into a session |
| `GET /api/billing/status` | Whether payments are on, the prices from Stripe, and the signed-in user's plan |
| `POST /api/billing/checkout` | `{"interval": "monthly" \| "yearly"}`; answers `{"url"}` for Stripe Checkout |
| `POST /api/billing/portal` | Answers `{"url"}` for the Stripe Customer Portal |
| `POST /api/stripe/webhook` | Stripe's notifications, signature-checked |
| `GET /robots.txt`, `/sitemap.xml` | SEO |

`POST /api/analyze` failure codes: `400` malformed input, or a bare video ID sent as a
title; `403` age-restricted video; `404` no captions / video unavailable; `422` empty or
too-short transcript, or a pydantic validation error (whose `detail` is a **list**, not a
string); `429` per-IP rate limit, and nothing else; `503` analysis paused (carrying
`"reason": "analysis_disabled"`), no `XAI_API_KEY` (carrying `"reason": "no_api_key"`), the
day's `DAILY_BUDGET_USD` or the global limit reached, xAI rate-limiting the service, or too
many fetches in flight; `502` upstream failure; `504` timeout.

Paused, unconfigured and out-of-budget are checked before anything is spent, so they cost
neither a rate-limit slot nor a transcript fetch.

Only `502`, `503` and `504` refund the caller's rate-limit slot. A `404` for a captionless
video is an answer about the video the caller chose, and charging for it is what keeps the
transcript path metered at all. A `502` that xAI still billed — a reply cut off at
`GROK_MAX_TOKENS`, empty, or malformed — is not refunded either, and it counts against
`DAILY_BUDGET_USD` like any other call, reasoning tokens included.

The response carries `basis: "transcript" | "title"`. A `title` analysis never read the
video, and the page marks it as such — do not present the two identically. A transcript
analysis reads the video's English captions when it has any, and otherwise whatever
captions it does have; the analysis itself is always written in English.

The response also carries `plan` (`"free"` or `"pro"`) and `claims_limit`. A free
response adds `claims_found`; a Pro response adds `metrics`, `key_errors`, `omissions`
and the per-claim fields above. Fields that belong to the other plan are `null`.

Every page answers `HEAD` as well as `GET`. A browser that asks for an unknown path gets
the HTML 404 page; an API caller still gets `{"detail": "Not found."}`.

The interactive API docs (`/docs`, `/redoc`, `/openapi.json`) are disabled.

## Local run

```bash
pip install -r requirements.txt
cp .env.example .env     # then fill in XAI_API_KEY
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000
