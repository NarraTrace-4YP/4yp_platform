# Setting up the server (admins)

This takes about 30 minutes, most of it waiting. You need:

- the server (Ubuntu 24.04; 4 cores, 8 GB memory, 240 GB disk is plenty),
- control of the domain's DNS,
- admin rights on the GitHub organisation.

## 1. Create the GitHub repo

1. In the organisation, create a new repo. **Make it public** unless you have a reason not
   to:
   - On GitHub's free organisation plan, rules like "pull requests need a review and
     passing checks before merging" can only be enforced on public repos.
   - GitHub Actions minutes are unlimited for public repos.
   - Airflow can read a public repo without a token.

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
     `Airflow DAGs load`, `Server config is valid`. These appear after the checks have run
     once, so open one small pull request first.
   - Turn on **Block force pushes**.

If the repo has to be private: create a fine-grained token (GitHub **Settings > Developer
settings > Fine-grained tokens**) with read-only **Contents** access to just this repo, and
put it in `GITHUB_TOKEN` in `.env` (step 4). The merge rules above won't be enforced on a
private repo on the free plan.

## 2. Point the web addresses at the server

In your DNS, add two **A records** pointing at the server's IP address:

- `airflow.<domain>`
- `mlflow.<domain>`

## 3. Check who's in `server/team.txt`

Everyone listed gets a login. Admins (marked `admin`) also get sudo and Docker. Each person
needs at least one SSH key on their GitHub account, because that's how they log in.
**Your own key must be there before you run setup**: once setup finishes, logging in with a
password or as root is switched off.

## 4. Run the setup script

Log in to the fresh server as root, then:

```
git clone https://github.com/<org>/<repo>.git /opt/dhr
cd /opt/dhr
bash server/setup.sh
```

It asks for three things: your domain, the GitHub repo (it suggests the one you cloned)
and an email address that Let's Encrypt uses only to warn about expiring certificates.
They're saved in `/opt/dhr/.env`, so running the script again doesn't ask again. Then it:

1. generates the passwords and keys,
2. updates Ubuntu, turns on automatic security updates and adds 4 GB of swap,
3. installs Docker,
4. creates an SSH login for everyone in `team.txt`,
5. switches off password and root logins,
6. turns on the firewall (only SSH and web traffic allowed in),
7. builds and starts the services (the first build takes 5 to 10 minutes),
8. creates everyone's Airflow, MLflow and database logins.

**Before closing the root session**, open a new terminal and check you can get in:

```
ssh <your-username>@<server-address>
cat ~/ACCOUNTS.txt
```

Your username is your GitHub username in lower case.

## 5. Check everything works

1. Open `https://airflow.<domain>` and log in.
2. Find the `environment_check` DAG, switch it on, and click **Trigger**.
3. When it finishes, open the task's log. You should see three `OK` lines: Python
   packages, Database, MLflow. The log also shows the Git commit it ran.
4. Open `https://mlflow.<domain>`. The `environment-check` experiment should have one run,
   tagged with that commit.

## Day to day

- **Add someone:** add their GitHub username to `server/team.txt`, merge, then on the
  server: `cd /opt/dhr && sudo git pull && sudo server/team.sh sync`.
- **Remove someone:** delete their line, merge, then:
  `sudo /opt/dhr/server/team.sh remove <github-username>`.
- **Someone lost their password:** delete `/home/<user>/ACCOUNTS.txt` and run
  `sudo /opt/dhr/server/team.sh sync`. That gives them a new one.
- **Server setup or Python packages changed:** `sudo /opt/dhr/server/update.sh`.
  Pipeline code needs nothing: Airflow picks it up from GitHub by itself.
- **See what's running:** `cd /opt/dhr && docker compose ps`
- **Read a service's logs:** `docker compose logs --tail 100 <service>`
  (services: `postgres`, `airflow-apiserver`, `airflow-scheduler`, `airflow-dag-processor`,
  `airflow-triggerer`, `mlflow`, `caddy`)
- **Admin logins** for Airflow and MLflow (username `admin`) are in `/opt/dhr/.env`.

## Not backed up

There are no backups, by choice. DHRC-100 can be downloaded again from Hugging Face, and
code and the wiki live on GitHub. The database and MLflow's run history exist only on the
server.

## Later: connecting Blythe

Airflow already has the SSH add-on it needs to send jobs to Blythe. Before setting it up,
check with SCRTP that automated logins from an outside server are allowed. Then add an
Airflow connection (**Admin > Connections**, type SSH) holding a key that's allowed on
Blythe.
