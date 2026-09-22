#!/usr/bin/env python3
"""Build the Fish Finder landing page as a single self-contained HTML file.

Reads the screenshot (base64 encoded), stitches it into the template, and
writes /root/fish-finder/landing/index.html.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCREENSHOT_B64 = (HERE / "screenshot-b64.txt").read_text().strip()

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fish Finder — Where to fish tomorrow, before you leave the dock</title>
<meta name="description" content="Northeast tuna and striper predictions from Long Island Sound to the offshore canyons. Built by a Cobia 28 captain out of Old Saybrook, CT.">
<meta property="og:title" content="Fish Finder — Northeast Tuna & Striper Predictions">
<meta property="og:description" content="Where to fish tomorrow, before you leave the dock. Free beta.">
<meta property="og:type" content="website">
<style>
  :root {
    --bg:            #0a1a2e;
    --bg-panel:      #12283d;
    --bg-panel-2:    #16324a;
    --border:        #1e3a56;
    --text:          #e8f1f8;
    --text-secondary:#a4bcd1;
    --text-muted:    #6c8296;
    --accent:        #4fc3f7;
    --accent-warm:   #ffd54f;
    --heat-hot:      #ff6b3d;
    --heat-cold:     #2a4a6b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    background: linear-gradient(180deg, #061527 0%, var(--bg) 50%, #08192b 100%);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    line-height: 1.55;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }
  .container { max-width: 1080px; margin: 0 auto; padding: 0 20px; }

  /* --- top nav --- */
  nav {
    position: sticky; top: 0; z-index: 20;
    background: rgba(6, 21, 39, 0.85);
    backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--border);
    padding: 14px 20px;
  }
  nav .container {
    display: flex; align-items: center; justify-content: space-between;
  }
  .logo {
    font-size: 20px; font-weight: 800; letter-spacing: -0.3px;
    color: var(--text);
  }
  .logo span { color: var(--accent); }
  nav a.cta {
    background: var(--accent); color: #06253a;
    text-decoration: none; font-weight: 700; font-size: 14px;
    padding: 9px 16px; border-radius: 6px; transition: transform 0.15s;
  }
  nav a.cta:hover { transform: translateY(-1px); }

  /* --- hero --- */
  header.hero { padding: 80px 20px 60px; text-align: center; }
  .eyebrow {
    display: inline-block;
    color: var(--accent); font-size: 12.5px; font-weight: 700;
    letter-spacing: 2px; text-transform: uppercase;
    background: rgba(79, 195, 247, 0.10);
    padding: 6px 14px; border-radius: 20px;
    border: 1px solid rgba(79, 195, 247, 0.3);
    margin-bottom: 20px;
  }
  h1 {
    font-size: clamp(36px, 5.5vw, 60px);
    font-weight: 800; line-height: 1.05; letter-spacing: -1.2px;
    margin-bottom: 20px;
  }
  h1 .highlight { color: var(--accent-warm); }
  .subtitle {
    color: var(--text-secondary);
    font-size: clamp(16px, 2vw, 20px);
    max-width: 680px; margin: 0 auto 32px;
    line-height: 1.55;
  }
  .hero-ctas {
    display: flex; gap: 14px; justify-content: center; flex-wrap: wrap;
    margin-top: 12px;
  }
  .btn {
    display: inline-block; padding: 14px 28px;
    font-size: 15px; font-weight: 700; text-decoration: none;
    border-radius: 6px; transition: transform 0.15s, box-shadow 0.15s;
    border: none; cursor: pointer;
  }
  .btn-primary { background: var(--accent); color: #06253a; }
  .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(79, 195, 247, 0.3); }
  .btn-ghost {
    background: transparent; color: var(--text);
    border: 1.5px solid var(--border);
  }
  .btn-ghost:hover { border-color: var(--accent); color: var(--accent); }

  /* --- screenshot --- */
  .screenshot-wrap {
    margin: 44px auto 0; max-width: 940px; padding: 0 8px;
  }
  .screenshot-wrap img {
    width: 100%; display: block;
    border-radius: 10px;
    border: 1px solid var(--border);
    box-shadow: 0 30px 80px rgba(0, 0, 0, 0.6),
                0 0 0 1px rgba(79, 195, 247, 0.10);
  }
  .screenshot-caption {
    text-align: center; color: var(--text-muted);
    font-size: 12.5px; margin-top: 14px;
  }

  /* --- section --- */
  section { padding: 70px 20px; }
  section h2 {
    font-size: clamp(26px, 3.5vw, 36px);
    font-weight: 800; text-align: center;
    letter-spacing: -0.6px; margin-bottom: 14px;
  }
  section .lede {
    text-align: center; color: var(--text-secondary);
    font-size: 16px; max-width: 640px; margin: 0 auto 44px;
  }

  /* --- feature grid --- */
  .features {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 22px;
  }
  .feature {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 24px 22px;
    transition: transform 0.2s, border-color 0.2s;
  }
  .feature:hover {
    transform: translateY(-3px);
    border-color: rgba(79, 195, 247, 0.35);
  }
  .feature .icon {
    display: inline-block; font-size: 28px; margin-bottom: 12px;
  }
  .feature h3 {
    font-size: 17px; font-weight: 700; margin-bottom: 8px;
  }
  .feature p {
    font-size: 14px; color: var(--text-secondary); line-height: 1.6;
  }

  /* --- signals block --- */
  .signals-band {
    background: var(--bg-panel);
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    padding: 60px 20px;
  }
  .signals-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 14px; max-width: 900px; margin: 0 auto;
  }
  .signal {
    text-align: center;
    padding: 18px 12px;
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .signal .sig-emoji { font-size: 24px; display: block; margin-bottom: 6px; }
  .signal .sig-name {
    font-size: 13px; font-weight: 700; color: var(--text);
  }
  .signal .sig-desc {
    font-size: 11.5px; color: var(--text-muted); margin-top: 4px;
  }

  /* --- story --- */
  .story {
    max-width: 720px; margin: 0 auto;
    background: var(--bg-panel);
    border-left: 3px solid var(--accent-warm);
    border-radius: 6px;
    padding: 28px 30px;
    font-size: 16px; line-height: 1.7;
    color: var(--text-secondary);
  }
  .story b { color: var(--text); font-weight: 700; }
  .story .signoff { color: var(--text-muted); font-size: 14px; margin-top: 16px; font-style: italic; }

  /* --- signup form --- */
  .signup-card {
    max-width: 520px; margin: 0 auto;
    background: linear-gradient(180deg, var(--bg-panel-2) 0%, var(--bg-panel) 100%);
    border: 1px solid rgba(79, 195, 247, 0.25);
    border-radius: 12px;
    padding: 32px 28px;
    text-align: center;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
  }
  .signup-card h3 {
    font-size: 22px; margin-bottom: 8px;
  }
  .signup-card p {
    color: var(--text-secondary);
    font-size: 14px; margin-bottom: 22px;
  }
  form.signup {
    display: flex; gap: 8px; flex-wrap: wrap;
  }
  form.signup input[type="email"] {
    flex: 1 1 220px;
    padding: 12px 14px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-size: 15px;
    outline: none;
    transition: border-color 0.15s;
  }
  form.signup input[type="email"]:focus {
    border-color: var(--accent);
  }
  form.signup button {
    background: var(--accent); color: #06253a;
    font-weight: 700; font-size: 15px;
    padding: 12px 22px;
    border: none; border-radius: 6px;
    cursor: pointer;
    transition: transform 0.15s;
  }
  form.signup button:hover { transform: translateY(-1px); }
  .form-note {
    font-size: 12px; color: var(--text-muted);
    margin-top: 12px;
  }

  /* --- footer --- */
  footer {
    padding: 40px 20px 60px;
    text-align: center;
    color: var(--text-muted);
    font-size: 13px;
    border-top: 1px solid var(--border);
  }
  footer .container { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 16px; }
  footer a { color: var(--text-secondary); text-decoration: none; }
  footer a:hover { color: var(--accent); }

  /* --- responsive tweaks --- */
  @media (max-width: 600px) {
    header.hero { padding: 60px 20px 40px; }
    section { padding: 50px 20px; }
    .story { padding: 22px 20px; font-size: 15px; }
    footer .container { flex-direction: column; text-align: center; }
  }
</style>
</head>
<body>

<nav>
  <div class="container">
    <div class="logo">Fish<span>Finder</span></div>
    <a href="#signup" class="cta">Join the beta</a>
  </div>
</nav>

<header class="hero">
  <div class="container">
    <div class="eyebrow">🎣 Free beta · Northeast USA</div>
    <h1>Where to fish <span class="highlight">tomorrow</span>,<br>before you leave the dock.</h1>
    <p class="subtitle">
      Tuna and striper heat-map predictions from Long Island Sound out to the offshore canyons.
      Built by a Cobia 28 captain out of Old Saybrook — the same tool I use to plan my own trips.
    </p>
    <div class="hero-ctas">
      <a href="#signup" class="btn btn-primary">Get early access</a>
      <a href="#how-it-works" class="btn btn-ghost">See how it works</a>
    </div>
    <div class="screenshot-wrap">
      <img src="data:image/jpeg;base64,SCREENSHOT_B64_HERE" alt="Fish Finder map showing zone heat scores for the Northeast US coast">
      <div class="screenshot-caption">Interactive chart: every zone scored 1–10 for tomorrow · updated nightly · fits your boat's range from your home port</div>
    </div>
  </div>
</header>

<section id="how-it-works">
  <div class="container">
    <h2>Every morning, one answer.</h2>
    <p class="lede">Not a wall of data. Not a weather dump. A specific spot, backed by every signal that matters — matched to your boat and tomorrow's conditions.</p>
    <div class="features">
      <div class="feature">
        <span class="icon">🗺️</span>
        <h3>27 zones, scored 1–10</h3>
        <p>Every named tuna and striper spot from LI Sound to Veatch Canyon gets a fresh heat score every morning. Ranked by what's biting AND what should be biting.</p>
      </div>
      <div class="feature">
        <span class="icon">⛽</span>
        <h3>Fits your boat</h3>
        <p>Enter your home port, tank size, and MPG. Every zone shows nm, fuel round-trip, and % of tank — with a red flag on anything past your safe range.</p>
      </div>
      <div class="feature">
        <span class="icon">🌊</span>
        <h3>Real weather cap</h3>
        <p>Wind and wave thresholds you set. Days that break the cap are marked stay-home. The picker automatically points you at the next fishable day.</p>
      </div>
      <div class="feature">
        <span class="icon">🐋</span>
        <h3>Whales &amp; bait signals</h3>
        <p>Whale/dolphin sightings from CRESLI &amp; Viking Fleet. Bait intel from captain reports. Where the bait piles up, the tuna follow — usually within days.</p>
      </div>
      <div class="feature">
        <span class="icon">🌡️</span>
        <h3>SST + chlorophyll edges</h3>
        <p>The 68–72°F break line and the blue-green chlorophyll edge, both overlaid on the chart. Fish the water in between — that's the tuna sweet spot.</p>
      </div>
      <div class="feature">
        <span class="icon">📚</span>
        <h3>Gets sharper over time</h3>
        <p>Every night the picks + conditions get archived. Feedback on what actually bit tunes the model. A year from now it'll know your patch better than any static chart.</p>
      </div>
    </div>
  </div>
</section>

<div class="signals-band">
  <div class="container">
    <h2>Eight signals, one score.</h2>
    <p class="lede">Everything below feeds a single effective-heat number that decides tomorrow's pick — the way you'd think about it if you had the time to check every source.</p>
    <div class="signals-list">
      <div class="signal"><span class="sig-emoji">📊</span><div class="sig-name">Live catch reports</div><div class="sig-desc">14+ captain sources</div></div>
      <div class="signal"><span class="sig-emoji">🌡️</span><div class="sig-name">SST breaks</div><div class="sig-desc">68–72°F edge</div></div>
      <div class="signal"><span class="sig-emoji">🌿</span><div class="sig-name">Chlorophyll edges</div><div class="sig-desc">Blue-green line</div></div>
      <div class="signal"><span class="sig-emoji">🐋</span><div class="sig-name">Whale sightings</div><div class="sig-desc">Bait proxy</div></div>
      <div class="signal"><span class="sig-emoji">🐟</span><div class="sig-name">Bait intel</div><div class="sig-desc">Sand eels, squid, bunker</div></div>
      <div class="signal"><span class="sig-emoji">📅</span><div class="sig-name">Seasonal priors</div><div class="sig-desc">Historical migration</div></div>
      <div class="signal"><span class="sig-emoji">🌗</span><div class="sig-name">Moon &amp; pressure</div><div class="sig-desc">Bite-window triggers</div></div>
      <div class="signal"><span class="sig-emoji">🌊</span><div class="sig-name">Tides &amp; currents</div><div class="sig-desc">Structure feeding</div></div>
    </div>
  </div>
</div>

<section>
  <div class="container">
    <h2>Why I built this.</h2>
    <div class="story">
      I fish out of Old Saybrook, CT in a 28' Cobia. Every trip is a bet: fuel, time, hope.
      I got tired of running 60 miles to a spot that "should have been good" because the report I read was three weeks old.
      <br><br>
      So I started keeping notes — where the whales were, what temperature the breaks sat at, what the bait was that week.
      Then I got tired of doing it by hand and built <b>Fish Finder</b>: a nightly-updated chart that predicts where to fish tomorrow, before I leave the dock.
      <br><br>
      It's <b>free</b>, it fits <b>your boat</b>, and it works for anybody fishing between Long Island Sound and the offshore canyons.
      If you want in on the beta, drop your email below.
      <div class="signoff">— Randy, Old Saybrook CT</div>
    </div>
  </div>
</section>

<section id="signup">
  <div class="container">
    <div class="signup-card">
      <h3>Get on the beta list</h3>
      <p>I'll email you when it's live. No spam, no ads, no reselling your email. Ever.</p>
      <form class="signup" action="FORM_ENDPOINT_HERE" method="POST">
        <input type="email" name="email" placeholder="you@example.com" required>
        <input type="hidden" name="_subject" value="New Fish Finder beta signup">
        <input type="hidden" name="_captcha" value="false">
        <input type="text" name="_honey" style="display:none">
        <button type="submit">Send it</button>
      </form>
      <div class="form-note">Free beta · Northeast US · Randy Spargo, Old Saybrook CT</div>
    </div>
  </div>
</section>

<footer>
  <div class="container">
    <div>© 2026 Fish Finder · Built by a captain, for captains.</div>
    <div><a href="mailto:CONTACT_EMAIL_HERE">Contact Randy</a></div>
  </div>
</footer>

</body>
</html>
"""

# Substitutions
html = (TEMPLATE
        .replace("SCREENSHOT_B64_HERE", SCREENSHOT_B64)
        # Formsubmit.co uses the email directly. First submission triggers
        # a verification email; after Randy confirms, it starts forwarding.
        # For the launch checklist, we'll swap in his email.
        .replace("FORM_ENDPOINT_HERE", "https://formsubmit.co/rspargo57@gmail.com")
        .replace("CONTACT_EMAIL_HERE", "rspargo57@gmail.com"))

out = HERE / "index.html"
out.write_text(html)
print(f"Wrote {out} ({len(html):,} bytes)")
