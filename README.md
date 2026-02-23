# FlowerShop 🌷

> Seasonal tulip sales management system for March 8th in Izmail, Ukraine.
> Built in **2 hours**. Yes, hours. Put your Jira ticket down.

> **Russian version / Русская версия:** [README_RU.md](README_RU.md)

---

## What is this?

A web app to manage tulip orders, stock, courier routes, and payments for a
one-day flower sale.

**No microservices. No Docker. No Kubernetes. No GraphQL. No TypeScript.
No React. No Redux. No Webpack. No CI/CD pipeline. No test coverage badge.
No SonarQube. No architectural review. No sprint planning. No standup.**

Just Python, SQLite, and a prayer.

It worked perfectly. The tulips were delivered. Everyone got paid.

---

## Stack

| Component | Technology | Why not use X? |
|-----------|-----------|----------------|
| Backend | Python / Flask | Because we're not solving distributed consensus here |
| Database | SQLite (one file) | ACID, zero config, fits on a USB stick |
| Frontend | HTMX + Tailwind CDN | Because `npm install` takes longer than this whole project |
| Sharing | ngrok | Because setting up a VPS for 4 users is a personality disorder |

---

## Requirements

| What | Version |
|------|---------|
| Python | 3.11+ |
| ngrok | any (free account) |
| OS | Windows / macOS / Linux |
| Brain cells | ≥ 3 |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Three packages. `requirements.txt` has three lines.
Your `node_modules` has 47,000 files for a TODO app. Think about that.

### 2. Install and authorise ngrok

```bash
ngrok config add-authtoken YOUR_TOKEN
```

This is the most "DevOps" this project gets. Enjoy it.

### 3. Launch

```bash
bash start.sh
```

That's it. The whole deployment pipeline is one bash script.
Senior engineers with 10 years of experience are currently setting up
a Helm chart to do the same thing.

---

## Daily launch

```bash
bash start.sh
```

Expected output:

```
FlowerShop — starting...
  tulip_varieties: already seeded
Starting Flask on port 5000...
Opening ngrok tunnel...

==============================================
  FlowerShop is running!
----------------------------------------------
  Local:  http://localhost:5000
  Team:   https://xxxx-xx-xx.ngrok-free.app
==============================================
```

Copy the `Team:` URL. Send it to the group chat. Done.
No Ansible playbook required.

---

## Sharing with the team

1. Run `bash start.sh`
2. Copy the `Team: https://...` URL
3. Send it to Telegram / Viber

> The URL changes on every restart (free ngrok plan).
> If your DevOps brain is screaming — this app runs for 6 hours once a year.
> A static IP would cost more than the tulips.

---

## Adding to iPhone home screen

1. Open the link in **Safari**
2. Tap **Share** → **"Add to Home Screen"**
3. Tap **"Add"**

Full-screen app. Zero App Store review. Zero React Native. Zero Expo.
Shipped in 15 seconds.

---

## Team roles

| Role | Link | What they do |
|------|------|-------------|
| Operator | `/orders/new` | Takes orders from panicking husbands |
| Assembler | `/orders?status=confirmed` | Wraps tulips at 6am |
| Courier | `/courier/<route_id>` | Drives around with flowers |
| Admin | `/` | Watches the numbers go up |

No RBAC. No JWT. No OAuth2. No SSO. There are 4 users. They know each other.
The "authentication system" is trusting people on a local network.
Sometimes simple is correct.

---

## Backups

SQLite file gets copied hourly to `data/backups/`. 7-day retention.
Manual backup: copy `data/flower_shop.db` to a USB stick like it's 2003.

A proper backup strategy was considered and rejected on the grounds
that the entire dataset is about 200 rows.

---

## Stopping

`Ctrl+C`.

Not `kubectl delete deployment flowershop --namespace=production`.
Just `Ctrl+C`.

---

## Project structure

```
app.py              — entry point (also the "infrastructure layer")
config.py           — 3 lines of config (no YAML, no TOML, no XML)
database/
  schema.sql        — the "ORM" (it's just SQL, and it's beautiful)
  seed.py           — puts tulips in the database
  db.py             — sqlite3.connect(), nothing more, nothing less
routes/             — Flask blueprints
services/           — business logic, separated for people who read books
templates/          — HTML that actually renders in < 200ms
static/             — literally nothing interesting
data/               — the database. one file. 2MB. gitignored.
docs-ai/            — written for an AI assistant, do not touch
```

---

## Key business rules

| Rule | The human version |
|------|-----------------|
| R1 | Price at order time is sacred. Changing prices won't rewrite history. |
| R2 | Can't sell flowers you don't have. Revolutionary concept. |
| R3 | No refunds on pre-payments. Read the sign. |
| R4 | Every payment is logged. No "I paid already" disputes. |
| R5 | Cancel order → stock comes back. Basic math. |
| R6 | Can't cancel after courier left. The flowers are in the van. |

---

## FAQ

**Q: Why not use Django?**
A: Because we didn't need an admin panel, an ORM, 47 middleware layers,
and a migrations folder that predates the project.

**Q: Why SQLite and not PostgreSQL?**
A: Because we have 4 concurrent users and SQLite handles 100k writes/sec.
PostgreSQL would be correct. SQLite is more correct.

**Q: Where's the test coverage?**
A: The test suite is called "March 8th". It ran once. All orders delivered.
100% pass rate.

**Q: Where's the authentication?**
A: There isn't any. It's 4 people in the same room. Adding authentication
would take longer than the entire sales season.

**Q: Is this production-ready?**
A: It ran in production. It worked. Flowers were delivered. Money was collected.
Define "production-ready".

**Q: Why not TypeScript/Next.js/Vite?**
A: The app was built in 2 hours. Your TypeScript config file alone takes
2 hours to set up.

---

## Running locally

```bash
python app.py        # start dev server
pytest tests/        # run tests (there are some, we're not savages)
```
