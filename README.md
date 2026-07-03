# The Hour Exchange

A community time-banking platform where neighbors swap skills using time instead of cash. One hour of work equals one Hour Token, whether you are debugging code, tutoring calculus, or picking up groceries.

The frontend is built with a clean, earthy UI theme (cream and forest green) using human-centric copy rather than dense tech jargon. Under the hood, it features built-in guardrails for user safety, identity verification, and reputation accountability.

---

## Design System

* **Background:** Warm cream (`#FAF9F6` / `stone-50`)
* **Headers & Primary Actions:** Forest green (`#064E3B` / `emerald-800`)
* **Badges & Highlights:** Terracotta (`#C2410C` / `orange-700`)
* **Body Text:** Charcoal (`#1C1917` / `stone-800`)

---

## Local Setup

Run these commands to get the platform up and running on your local machine:

### 1. Move to the project directory

```bash
cd /Users/ishaanagarwal/.gemini/antigravity/scratch/chronocommunity

```

### 2. Set up and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate

```

### 3. Install dependencies

```bash
pip install Flask Flask-SQLAlchemy

```

### 4. Boot up the server

```bash
python app.py

```

The app will spin up locally at `[http://127.0.0.1:5001/](http://127.0.0.1:5001/)`.

---

## Testing

To run the automated integration tests for holding balances, tier status upgrades, cancellations, and skill endorsements:

```bash
python test_app.py

```

---

## Live Demo & Feature Walkthrough

The local SQLite database comes pre-seeded with five test accounts (all passwords are `password123`):

* **`alice`** (Tier 2: In-Home Verified) • 15 Tokens
* **`bob`** (Tier 2: In-Home Verified) • 12 Tokens
* **`charlie`** (Tier 1: Public/Digital Only) • 10 Tokens
* **`diana`** (Tier 1: Public/Digital Only) • 8 Tokens
* **`elena`** (Tier 2: In-Home Verified) • 20 Tokens

### Flow 1: Identity & Community Vouching

1. Log in as **`charlie`** and view your **My Neighborhood Card** (profile). You will see a **Tier 1** status badge and a vouch progress bar at **0/2**.
2. Log out and log back in as **`alice`** (Tier 2). Find Charlie's profile and click the **Vouch for Neighbor** button. The meter updates to **1/2**.
3. Log out and log in as **`bob`** (Tier 2). Go to Charlie's profile and click **Vouch for Neighbor**. Because he received two vouches from verified Tier 2 users, Charlie's profile automatically upgrades to **Tier 2: In-Home Friendly**.
4. *Shortcut:* Use the **Evaluation Panel Override Controls** at the bottom of the screen to instantly toggle any user's verification state.

### Flow 2: Escrow Engine (The Secure Holding Balance)

1. Log in as **`alice`**. Go to **The Town Square** marketplace, find Charlie's listing for **"Help with High School Calculus"** (Cost: 1 Hour Token), and click **Lend a Hand**.
2. Check the **Our Shared History** tab. Under **Swaps In Progress**, you will see a card showing **1 Hour Token locked in holding**.
3. Open an incognito window or separate browser tab and log in as **`charlie`**. You will see his active token balance has dropped to 9 because his token is currently held in escrow.
4. On Alice's screen, click **Confirm We’re All Set!** to signal completion.
5. On Charlie's screen, go to Swaps In Progress and also click **Confirm We’re All Set!**.
6. Once both users have confirmed, the 1 held token transfers to Alice's balance (updating her total to 16). Charlie is then prompted to leave feedback.

### Flow 3: Feedback & Skill Endorsements

1. In Charlie's session, click **Leave a Warm Note** on the completed Calculus swap under the history tab.
2. Rate it **5 Stars**, leave a quick review, and submit.
3. Pull up Alice’s public profile card. The 5-star review will show up on her wall, and her **"Learning & Growing"** counter under her skill endorsements will increment by 1.

### Flow 4: Accountability & Late Cancellations

1. Log in as **`elena`**. Go to your history and find an active swap you committed to: **"Everyday Errand: Grocery Delivery"** (starts in 4 hours).
2. Click **Cancel Commitment**.
3. A browser modal will warn you that canceling less than 24 hours in advance will negatively impact your community reputation. Click OK.
4. Elena’s public **Neighbor Reliability Score** drops instantly, and her late-cancellation counter increments.
5. The 2 tokens locked in escrow for the task are automatically refunded to her active balance.
