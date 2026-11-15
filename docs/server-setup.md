# Setting up the server (admins)

This takes about 30 minutes, most of it waiting. You need:

- the server (Ubuntu 24.04 or newer; 4 cores, 8 GB memory, 240 GB disk is plenty),
- control of the domain's DNS,
- admin rights on the GitHub organisation.

## 1. Create the GitHub repo

1. In the organisation, create a new repo. **Make it public** unless you have a reason not
   to:
   - On GitHub's free organisation plan, rules like "pull requests need a review and
     passing checks before merging" can only be enforced on public repos.
   - GitHub Actions minutes are unlimited for public repos.

   Nothing sensitive goes in this repo: no data, no passwords. The wiki goes in a separate
   **private** repo, because it contains material from the reviews.
2. Push the contents of this folder as the first commit:

   ```
   git init -b main
   git add -A
   git commit -m "Shared environment"
   git remote add origin https://github.com/<org>/<repo>.git
   git push -u origin main
   ```

3. Add the merge rules: repo **Settings > Rules > Rulesets > New branch ruleset**.
   - Target: the default branch.
   - Turn on **Require a pull request before merging**, with 1 required approval.
   - Turn on **Require status checks to pass**, and add: `Python tests and lint`,
     `Windmill scripts are valid`, `Server config is valid`. These appear after the checks
     have run once, so open one small pull request first.
   - Turn on **Block force pushes**.

## 2. Point the web addresses at the server

In your DNS, add two **A records** pointing at the server's IP address:

- `windmill.<domain>`
- `mlflow.<domain>`

## 3. Check who's in `server/team.txt`

Everyone listed gets a login. Admins (marked `admin`) also get sudo and Docker, and are
admins in Windmill. Each person needs at least one SSH key on their GitHub account,
because that's how they log in. **Your own key must be there before you run setup**: once
setup finishes, logging in with a password or as root is switched off.

## 4. Run the setup script

Log in to the fresh server as root, then:

```
git clone https://github.com/<org>/<repo>.git /opt/dhr
cd /opt/dhr
bash server/setup.sh
```

It asks for your domain, the GitHub repo (it suggests the one you cloned), an email
address that Let's Encrypt uses only to warn about expiring certificates, and a Hugging
Face token that can read DHRC-100 (press Enter to add it later). They're saved in
`/opt/dhr/.env`, so running the script again doesn't ask again. Then it:

1. generates the passwords,
2. updates Ubuntu, turns on automatic security updates and adds 4 GB of swap,
3. installs Docker,
4. creates an SSH login for everyone in `team.txt`,
5. switches off password and root logins,
6. turns on the firewall (only SSH and web traffic allowed in),
7. downloads and starts the services (the first time takes 5 to 10 minutes),
8. sets up Windmill: replaces its built-in `admin@windmill.dev` login with an admin
   account of its own, creates the `dhr` workspace and its `f/dhr` folder, stores the
   pipelines' passwords as secret variables, and creates a token for GitHub to deploy with,
9. creates everyone's Windmill, MLflow and database logins.

**Before closing the root session**, open a new terminal and check you can get in:

```
ssh <your-username>@<server-address>
cat ~/ACCOUNTS.txt
```

Your username is your GitHub username in lower case.

## 5. Let GitHub deploy to Windmill

The end of the setup output shows a token. In the repo on GitHub, open **Settings >
Secrets and variables > Actions**, and add:

- on the **Secrets** tab: a secret named `WINDMILL_TOKEN`, with the token as its value,
- on the **Variables** tab: a variable named `WINDMILL_URL`, with the value
  `https://windmill.<domain>/`.

The token is also kept in `/opt/dhr/.env` as `WINDMILL_DEPLOY_TOKEN`. Then deploy for the
first time: **Actions > deploy > Run workflow**. From now on every merge to `main` deploys
by itself.

## 6. Check everything works

1. Open `https://windmill.<domain>` and log in with the email in your `ACCOUNTS.txt`.
2. In the `dhr` workspace, open `f/dhr/load_dhrc` and click **Run**. The log should end
   with `OK` lines: downloaded, checksums, read 100 reviews, stored, logged to MLflow.
3. Open `https://mlflow.<domain>`. The `corpus-ingest` experiment should have one run,
   tagged with the Git commit that was deployed.

## Day to day

- **Add someone:** add their GitHub username to `server/team.txt`, merge, then on the
  server: `cd /opt/dhr && sudo git pull && sudo server/team.sh sync`.
- **Remove someone:** delete their line, merge, then:
  `sudo /opt/dhr/server/team.sh remove <github-username>`.
- **Someone lost their password:** delete `/home/<user>/ACCOUNTS.txt` and run
  `sudo /opt/dhr/server/team.sh sync`. That gives them a new one.
- **Server setup changed, or a setting in `.env` (such as `HF_TOKEN`):**
  `sudo /opt/dhr/server/update.sh`. Pipeline code needs nothing: merging deploys it.
- **See what's running:** `cd /opt/dhr && docker compose ps`
- **Read a service's logs:** `docker compose logs --tail 100 <service>`
  (services: `postgres`, `windmill-db`, `windmill-server`, `windmill-worker`,
  `windmill-worker-native`, `windmill-extra`, `mlflow`, `caddy`)
- **Server admin logins** (Windmill `admin@<domain>`, MLflow `admin`) are in
  `/opt/dhr/.env`.

## Upgrading Windmill

Change the version number everywhere it appears: `compose.yaml` (two image lines), both
workflow files under `.github/workflows/` (the `windmill-cli@...` lines), and the
`wmill==...` line in each script in `f/dhr`. The tests fail until they all agree. Merge,
then run `sudo /opt/dhr/server/update.sh`.

## Not backed up

There are no backups, by choice. DHRC-100 can be downloaded again from Hugging Face, and
code and the wiki live on GitHub. The database, Windmill's run history and MLflow's run
history exist only on the server.

## Later: connecting Blythe

Before setting it up, check with SCRTP that automated logins from an outside server are
allowed. Then a Windmill script can submit jobs to Blythe over SSH, with a key that's
allowed on Blythe stored as a secret Windmill variable.
