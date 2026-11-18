# Setting Up the Shared Server

This guide explains how to deploy and maintain the CS407 DHR project's shared server. It is intended for project administrators.

The server hosts the project's database, Windmill pipelines, MLflow experiment tracking and web interfaces. Docker Compose manages these services, while GitHub Actions automatically deploys approved changes.

Initial setup takes approximately 30 minutes, including time spent downloading and starting the services.

## Before you start

You will need:

- **An Ubuntu server:** Ubuntu 24.04 or newer, with approximately 4 CPU cores, 8 GB RAM and 240 GB of storage.
- **Domain access:** Permission to modify the domain's DNS records so the web interfaces point to the server.
- **GitHub organisation access:** Administrative permissions to create repositories, configure branch protection and manage GitHub Actions.
- **SSH access:** A public SSH key added to your GitHub account.

The setup scripts handle most of the server configuration automatically, including installing Docker, creating user accounts, configuring security settings and starting the services.

**Important:** The setup disables SSH password authentication and direct root login. Make sure your GitHub SSH key is configured correctly before proceeding, and verify your personal account works before closing the initial root session.

## 1. Create the GitHub repository

The repository stores the platform's code and configuration, including its Python pipelines, Docker configuration and deployment scripts.

All changes should go through pull requests so they can be tested and reviewed before reaching the shared server.

### Create the repository

Create a new repository in the project's GitHub organisation.

**Use a public repository unless there is a specific reason to make it private.**

On GitHub's free organisation plan, the project benefits from two features available to public repositories:

- Branch rules can enforce pull request reviews and passing checks before changes are merged.
- GitHub Actions provides unlimited minutes for public repositories.

No sensitive information should be committed. Passwords, tokens and datasets are managed separately.

The generated wiki will also be stored in a separate **private repository**, since it contains material extracted from the reviews.

### Push the initial code

From the project folder, initialise Git and push the files to the new repository:

```bash
git init -b main
git add -A
git commit -m "Shared environment"
git remote add origin https://github.com/<org>/<repo>.git
git push -u origin main
```

Replace `<org>` and `<repo>` with the GitHub organisation and repository names.

### Configure branch protection

In the repository, navigate to:

**Settings > Rules > Rulesets > New branch ruleset**

Create a ruleset targeting the default branch, `main`, with the following requirements:

| Setting | Configuration |
|---|---|
| Require a pull request before merging | Enabled |
| Required approvals | 1 |
| Require status checks to pass | Enabled |
| Block force pushes | Enabled |

Add these three required status checks:

- `Python tests and lint`
- `Windmill scripts are valid`
- `Server config is valid`

GitHub only offers these checks after they have run at least once. If they do not appear, create a small pull request to trigger the workflows, then return to the ruleset and add them.

These rules prevent changes from being merged without passing the automated checks and receiving a review from another team member.

## 2. Configure the domain

The server exposes three web interfaces:

- **Landing page:** Provides links to the project's services and applications.
- **Windmill:** Runs and manages the processing pipelines.
- **MLflow:** Displays experiment runs, model settings and results.

Each interface needs a domain name pointing to the server.

In your DNS provider's control panel, create three **A records**, all pointing to the server's public IPv4 address:

| Host | Purpose | Points to |
|---|---|---|
| `@` | Main landing page (`<domain>`) | Server IP |
| `windmill` | `windmill.<domain>` | Server IP |
| `mlflow` | `mlflow.<domain>` | Server IP |

Most DNS providers, including Namecheap, use `@` to represent the root domain.

The server uses Caddy to route requests to the appropriate service and obtain HTTPS certificates from Let's Encrypt.

DNS changes may take some time to propagate, so check that the records resolve to the correct address before troubleshooting any connection problems.

## 3. Configure team access

The file `server/team.txt` defines who has access to the shared server and which permissions they receive.

Each listed person gets:

- A Linux user account for SSH access.
- A Windmill account.
- An MLflow account.
- A PostgreSQL account.

Users marked `admin` also receive sudo and Docker permissions on the server, as well as administrator privileges in Windmill.

### Check the team list

Before running setup, open `server/team.txt` and make sure it contains the correct GitHub usernames and administrator assignments.

Each person must have at least one SSH public key registered with GitHub. The setup script retrieves these keys to configure SSH access.

You can add a key through [GitHub's SSH key settings](https://github.com/settings/keys).

**Make sure your own key is registered before setup.** Once the script disables password authentication and root login, you will need your personal SSH account to access the server.

Server usernames are derived from GitHub usernames and converted to lowercase. For example, `TroubleBubble1` becomes `troublebubble1`.

## 4. Run the server setup script

Connect to the new Ubuntu server as root.

Clone the repository into `/opt/dhr` and run the setup script:

```bash
git clone https://github.com/<org>/<repo>.git /opt/dhr
cd /opt/dhr
bash server/setup.sh
```

The script asks for a few configuration values:

| Setting | Purpose |
|---|---|
| Domain name | Used to configure the web interfaces and HTTPS. |
| GitHub repository | Identifies the project's source repository. The script suggests the repository you cloned. |
| Contact email | Used by Let's Encrypt to send certificate expiry notifications. |
| Hugging Face token | Allows the pipelines to download the DHRC datasets. |

The Hugging Face token is optional during initial setup. Press Enter to skip it and configure it later.

The settings are saved in `/opt/dhr/.env`. If you rerun the setup script, it reuses the saved values instead of asking for them again.

### What the script configures

The setup script performs the following steps.

**1. Generates credentials**

Creates passwords and other credentials needed by the platform's services.

**2. Prepares Ubuntu**

Updates system packages, enables automatic security updates and configures 4 GB of swap space.

Swap provides additional memory capacity if RAM becomes exhausted, although it is much slower than physical memory.

**3. Installs Docker**

Installs the tools needed to run the services defined in `compose.yaml`.

**4. Creates user accounts**

Creates an SSH account for each person in `server/team.txt`, using their GitHub SSH keys for authentication.

Admins also receive sudo and Docker access.

**5. Secures SSH**

Disables password-based SSH authentication and direct root login. Users must connect through their personal accounts using their registered SSH keys.

**6. Configures the firewall**

Restricts incoming connections to SSH and web traffic. The database and other internal services are not exposed directly to the internet.

**7. Starts the platform**

Downloads and starts the Docker services, including PostgreSQL, Windmill, MLflow and Caddy.

The first startup typically takes 5-10 minutes because the container images need to be downloaded.

**8. Configures Windmill**

The script prepares Windmill for the project by:

- Replacing the default `admin@windmill.dev` login with a project administrator account.
- Creating the `dhr` workspace and its `f/dhr` folder.
- Adding the pipeline credentials as secret variables.
- Generating a deployment token for GitHub Actions.

**9. Creates team credentials**

Creates each member's Windmill, MLflow and PostgreSQL accounts, then writes their login details to a private `ACCOUNTS.txt` file in their home directory.

### Verify SSH access before disconnecting

**Do not close the root session until you have confirmed your personal account works.**

Open a second terminal and connect using your GitHub username in lowercase:

```bash
ssh <your-username>@<server-address>
```

Then read your account details:

```bash
cat ~/ACCOUNTS.txt
```

If both commands work, your user account has been configured successfully.

If the SSH connection fails, use the original root session to investigate before disconnecting. Otherwise, you risk losing administrative access to the server.

## 5. Configure GitHub Actions deployment

The server is now running, but GitHub needs permission to deploy pipeline changes to Windmill.

The deployment workflow uses an API token generated during setup. GitHub stores this token as a repository secret, keeping it out of the source code.

### Add the deployment credentials

At the end of setup, the script displays the Windmill deployment token.

The token is also stored in `/opt/dhr/.env` under:

`WINDMILL_DEPLOY_TOKEN`

In GitHub, open:

**Settings > Secrets and variables > Actions**

Add the following values:

| Type | Name | Value |
|---|---|---|
| Secret | `WINDMILL_TOKEN` | The deployment token generated during setup |
| Variable | `WINDMILL_URL` | `https://windmill.<domain>/` |

The secret authenticates GitHub Actions with Windmill. The URL tells the workflow where to deploy the scripts.

### Run the first deployment

Open the repository's **Actions** tab and select:

**deploy > Run workflow**

The workflow uploads the scripts, flows and applications in `f/dhr` to the Windmill workspace.

Once this first deployment has completed, future changes merged into `main` will deploy automatically.

### Initialise the landing-page application

The project landing page embeds the O1 Windmill application.

Windmill assigns each public application a share ID, which is specific to the Windmill installation. The landing page therefore needs to discover the ID rather than use a hardcoded one.

After the first deployment finishes, run this command on the server:

```bash
sudo /opt/dhr/server/update.sh
```

The script finds the O1 application's public share ID and writes it to the ignored runtime configuration used by the landing page.

Later versions of the application retain the same app-level share ID. Subsequent runs of `server/update.sh` refresh and verify this configuration.

### Enable Windmill-to-GitHub synchronisation

Team members can edit scripts directly in Windmill's web editor. These changes take effect immediately, but they also need to be recorded in GitHub.

The **windmill to github** workflow checks for changes approximately every 15 minutes. If it finds any, it opens a pull request so they can be reviewed and merged into the repository.

GitHub must be configured to allow workflows to create pull requests.

First, open the GitHub organisation's:

**Settings > Actions > General > Workflow permissions**

Enable:

**Allow GitHub Actions to create and approve pull requests**

Then enable the same setting in the repository's:

**Settings > Actions > General**

Although the permission mentions approving pull requests, the project's workflow only creates them. It does not approve or merge them automatically. A team member must still review and approve the changes.

This keeps GitHub and Windmill synchronised without bypassing the project's review requirements.

## 6. Verify the deployment

Once setup and the first deployment are complete, check each service.

These checks confirm that the web interfaces are accessible, the pipelines can run, and results are being recorded correctly.

### Check Windmill

Open:

`https://windmill.<domain>`

Sign in using the email address and password in your `ACCOUNTS.txt`.

Select the `dhr` workspace, open `f/dhr/O1/load_reviews/load_reviews` and click **Run**.

The script should download the DHRC-100 dataset from Hugging Face, verify its checksums, read the 100 reviews and store them in PostgreSQL.

The log should finish with successful `OK` messages covering:

- Downloading the dataset.
- Verifying checksums.
- Reading 100 reviews.
- Storing the documents.
- Recording the run in MLflow.

A successful run confirms that Windmill, the database, dataset access and experiment logging are working together.

### Check MLflow

Open:

`https://mlflow.<domain>`

Sign in using your credentials.

Find the `corpus-ingest` experiment. It should contain a run from the dataset loading step.

The run should include a tag identifying the Git commit used for the deployment.

This confirms that results are being logged and linked to the code version that produced them.

### Check the landing page

Open:

`https://<domain>/o1/`

The O1 application should appear embedded in the page without asking for a separate Windmill login.

Then open the main landing page:

`https://<domain>`

Check that the page loads and its links to the project services work.

If all these checks pass, the shared environment is ready for the team to use.

---

## 7. Day-to-day administration

Most routine changes do not require repeating the initial setup.

The commands below cover team membership, configuration updates, service monitoring and troubleshooting.

### Adding a team member

1. Add the person's GitHub username to `server/team.txt`.
2. Commit and merge the change.
3. Update the server's repository and synchronise the accounts:

   ```bash
   cd /opt/dhr
   sudo git pull
   sudo server/team.sh sync
   ```

The synchronisation script creates the new user's accounts and configures their SSH access.

They can then log in and read their credentials from `~/ACCOUNTS.txt`.

### Removing a team member

Remove their entry from `server/team.txt` and merge the change.

Then run:

```bash
sudo /opt/dhr/server/team.sh remove <github-username>
```

This removes the user's access through the team management script.

### Resetting a user's password

If someone loses their credentials, delete their `ACCOUNTS.txt` file and rerun the team synchronisation:

```bash
sudo rm /home/<user>/ACCOUNTS.txt
sudo /opt/dhr/server/team.sh sync
```

The script generates new credentials for the affected account and writes them to a new account file.

### Updating server configuration

If the server setup or an environment setting changes, run:

```bash
sudo /opt/dhr/server/update.sh
```

This applies server-side updates, including changes to settings in `/opt/dhr/.env`, such as `HF_TOKEN`.

**You do not need this command for ordinary pipeline changes.** Changes to the Windmill code deploy automatically when their pull requests are merged into `main`.

### Checking the running services

To see the state of the Docker containers:

```bash
cd /opt/dhr
docker compose ps
```

This shows which services are running and whether any have stopped.

### Reading service logs

To inspect the most recent 100 log lines for a service:

```bash
docker compose logs --tail 100 <service>
```

Available services include:

| Service | Purpose |
|---|---|
| `postgres` | Main project database. |
| `windmill-db` | Windmill's own application database. |
| `windmill-server` | Windmill's web interface and API. |
| `windmill-worker` | Executes Windmill jobs. |
| `windmill-worker-native` | Executes native Windmill workloads. |
| `windmill-extra` | Additional Windmill services. |
| `mlflow` | Experiment tracking server. |
| `caddy` | HTTPS and routing to the web services. |

For example, to investigate a Windmill worker problem:

```bash
docker compose logs --tail 100 windmill-worker
```

### Administrator credentials

The server's administrative credentials are stored in:

`/opt/dhr/.env`

This includes the Windmill administrator account (`admin@<domain>`) and the MLflow administrator account (`admin`).

Keep this file private. It contains sensitive configuration values and must never be committed to GitHub.

## 8. Upgrading Windmill

Windmill is used in several places throughout the platform, so all components must use compatible versions.

The project deliberately pins versions to keep local development, deployment and the shared server consistent.

To upgrade Windmill, update the version in every relevant location:

1. **`compose.yaml`:** Update the two Windmill container image references.
2. **GitHub Actions:** Update the `windmill-cli@...` references in both workflow files under `.github/workflows/`.
3. **Python scripts:** Update the `wmill==...` dependency in each applicable script in `f/dhr`.

The automated tests check that these versions agree. They will fail if one location has been updated but another still uses an older version.

Once all versions match and the tests pass, merge the changes into `main`.

Then update the server:

```bash
sudo /opt/dhr/server/update.sh
```

This applies the new server configuration and updates the relevant services.

## 9. Data persistence and backups

**The platform currently has no backup system. This is a deliberate decision, but it means some information could be permanently lost if the server fails.**

The main sources have other copies:

- **Source datasets:** DHRC-100 can be downloaded again from Hugging Face.
- **Project code:** Stored in GitHub.
- **Generated wiki:** Stored in its separate GitHub repository.

However, the following data exists only on the shared server:

- The PostgreSQL database and its processed results.
- Windmill's execution and run history.
- MLflow's experiment and run history.

If the server or its storage is lost, these records will not be recoverable from a backup.

Some processing could be repeated using the source datasets and versioned code, but the original database contents and historical run records would be lost.

This limitation should be kept in mind when deciding where to store important results.

## 10. Future work: Connecting Blythe

The shared server handles pipeline execution, data storage and experiment tracking. It does not have the GPU resources needed to run the larger LLMs used by the project.

We intend to use Blythe, the university's HPC system, for GPU-intensive inference.

The aim is for Windmill to submit work to Blythe, monitor its progress and retrieve the results, while PostgreSQL and MLflow remain on the shared server.

### Proposed connection

One possible approach is to submit SLURM jobs over SSH from Windmill.

This would require:

1. An SSH key authorised to access Blythe.
2. The key to be stored securely as a Windmill secret variable.
3. A Windmill script to submit jobs and monitor their status.

Before implementing this, we need confirmation from the university's HPC team (SCRTP) that automated SSH connections from an external server are permitted.

An HTTP interface is also being considered as an alternative.

**Blythe integration is not part of the initial server setup.** The existing platform can run its data processing pipelines independently while the GPU connection is being arranged.
